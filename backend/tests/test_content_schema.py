"""The content schema: revisions tax2a1b2c3d4e (taxonomy spine) and
cd1a2b3c4d5e (content drop).

What is checked here is the schema only, on hand-made rows: the tables exist
with the tenancy they were designed with (platform tables carry no RLS; team
tables are strict tenant with FORCE and a WITH CHECK), the keys the loaders
match on are real constraints, the CHECKs hold the fixed vocabularies, the
columns that must never exist do not exist, the fixed vocabulary is seeded,
and strategy.* is granted where the plan ceiling and the role path both need
it. No drop directory is read.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import SessionLocal, bypass_rls_var, current_user_id_var
from app.models.chemical_family import ChemicalFamily
from app.models.formula_template import (
    CARD_KINDS, SUPPLY_STATUSES, FormulaTemplate, FormulaTemplateComponent,
)
from app.models.product_line import ProductLine
from app.models.rbac import Permission, Plan, PlanPermission, Role, RolePermission
from app.models.strategy import (
    GEMSTONES, OBJECTIVES, CustomLever, GemstoneCategory, LeverScore, Playbook,
    PlaybookLever, StrategicObjective, StrategyAction, StrategyRecord, TeamObjective,
)
from app.models.subfamily import Subfamily
from app.models.taxonomy_v2 import Category, CategoryPlacement, Industry

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

PLATFORM_TABLES = (
    "chemical_families", "subfamilies", "product_lines", "content_loads",
    "industries", "categories", "category_shared",
    "category_shared_members", "category_refs", "category_members",
    "category_build_items", "industry_out", "category_placements",
    "market_reports", "market_report_sections", "market_report_lines",
    "market_report_panels", "gemstone_categories", "strategic_objectives",
    "playbooks", "playbook_levers", "playbook_lever_objectives", "playbook_objectives",
)
TEAM_TABLES = (
    "strategy_records", "team_objectives", "lever_scores", "custom_levers",
    "strategy_actions",
)
GONE_TABLES = ("supply_subfamilies", "taxonomy_reconciliations")
LINK_TABLES = (
    "formula_templates", "products", "dimension_assertions", "editorial_blocks",
    "supplier_trust_scores",
)
STRATEGY_KEYS = {"strategy.view", "strategy.edit"}


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _as_user(user_id):
    """Fresh RLS-scoped session acting as the given user (policies on)."""
    s = SessionLocal()
    bypass_rls_var.set(False)
    current_user_id_var.set(str(user_id))
    return s


def _reset_rls_context():
    current_user_id_var.set(None)
    bypass_rls_var.set(True)


def _columns(db, table) -> dict[str, tuple]:
    rows = db.execute(text(
        "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :t"),
        {"t": table}).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _constraint_def(db, table, name) -> str | None:
    return db.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = to_regclass(:t) AND conname = :n"), {"t": table, "n": name}).scalar()


@pytest.fixture
def playbook(db):
    """A throwaway platform playbook with one lever. Platform rows are not
    removed by the team CASCADE, so this cleans up after itself; deleting the
    playbook cascades any team state still pointing at it."""
    slug = f"test-pb-{_tag()}"
    pb = Playbook(slug=slug, name="Test playbook", family="Test family",
                  kraljic={"cx": 100, "cy": 65, "badge": "leverage"})
    db.add(pb)
    db.flush()
    lever = PlaybookLever(playbook_slug=slug, lever_code=f"{slug}-1a", gemstone_code="VC",
                          title="Tender by region", default_applies=True, default_ease=4,
                          savings_score=3, default_status="Identified", sort_order=0)
    db.add(lever)
    db.commit()
    yield {"slug": slug, "lever_id": lever.id}
    bypass_rls_var.set(True)
    db.rollback()
    db.execute(text("DELETE FROM playbooks WHERE slug = :s"), {"s": slug})
    db.commit()


@pytest.fixture
def family(db):
    """A made-up platform family, flushed but never committed."""
    fam = ChemicalFamily(name=f"Test family {_tag()}")
    db.add(fam)
    db.flush()
    yield fam
    db.rollback()


def _line(family_id, subfamily_id=None, **kw) -> ProductLine:
    tag = _tag()
    kw.setdefault("platform", f"PLAT-TEST-{tag}")
    kw.setdefault("name", f"Test line {tag}")
    kw.setdefault("line_key", f"Test family|||{kw['name']}")
    return ProductLine(family_id=family_id, subfamily_id=subfamily_id, **kw)


# ── Revisions ────────────────────────────────────────────────────────────────

def test_one_alembic_head_on_the_content_revisions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    assert script.get_heads() == ["cd1a2b3c4d5e"]
    assert script.get_revision("cd1a2b3c4d5e").down_revision == "tax2a1b2c3d4e"
    assert script.get_revision("tax2a1b2c3d4e").down_revision == "ai1a2b3c4d5e"


def test_no_migration_creates_a_database_extension():
    offenders = [p.name for p in VERSIONS.glob("*.py")
                 if "create extension" in p.read_text(encoding="utf-8").lower()]
    assert offenders == []


# ── Tables and tenancy ───────────────────────────────────────────────────────

def _rls_flags(db, table):
    return db.execute(text(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = :t AND relkind = 'r'"), {"t": table}).first()


def test_platform_tables_exist_without_rls(db):
    for table in PLATFORM_TABLES:
        flags = _rls_flags(db, table)
        assert flags is not None, f"{table} missing"
        assert flags == (False, False), f"{table} is platform data and must carry no RLS"
        policies = db.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}).scalar()
        assert policies == 0, f"{table} must carry no policy"


def test_superseded_tables_are_gone(db):
    for table in GONE_TABLES:
        assert db.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None, table


def test_team_tables_are_strict_tenant_with_forced_rls(db):
    for table in TEAM_TABLES:
        assert _rls_flags(db, table) == (True, True), table
        policy = db.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = :t AND policyname = 'tenant_isolation'"), {"t": table}).first()
        assert policy is not None, f"{table} has no tenant_isolation policy"
        qual, with_check = policy
        assert "team_memberships" in qual and "app.bypass_rls" in qual
        # Strict tenant: no `team_id IS NULL` escape, and writes are checked too.
        assert "team_id IS NULL" not in qual
        assert with_check and "team_memberships" in with_check
        not_null = db.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = 'team_id'"), {"t": table}).scalar()
        assert not_null == "NO", f"{table}.team_id must be NOT NULL"


# ── Columns ──────────────────────────────────────────────────────────────────

def test_taxonomy_tiers_are_platform_only(db):
    fam = _columns(db, "chemical_families")
    assert {"id", "code", "name", "sort_order", "meta"} <= set(fam)
    assert not {"team_id", "origin_id", "custom_attribute_schema"} & set(fam)

    sub = _columns(db, "subfamilies")
    assert {"id", "family_id", "name", "why", "former_names", "sort_order", "meta"} <= set(sub)
    assert sub["name"][2] == "YES", "an axis node may be unnamed"
    assert sub["family_id"][2] == "NO"
    assert not {"team_id", "origin_id", "line_key"} & set(sub)

    line = _columns(db, "product_lines")
    assert {"id", "family_id", "subfamily_id", "platform", "line_key", "name", "former_keys",
            "in_v1_scope", "report_slug", "report_old_line", "flags", "confidence",
            "axis_meta", "retired_at", "sort_order"} <= set(line)
    assert line["line_key"][2] == "NO" and line["name"][2] == "NO"
    assert line["family_id"][2] == "NO" and line["subfamily_id"][2] == "YES"
    assert "is_demo_grade" not in line and "team_id" not in line


def test_old_taxonomy_links_are_gone_and_product_line_links_exist(db):
    for table in LINK_TABLES:
        cols = _columns(db, table)
        assert "product_line_id" in cols, table
        assert "subfamily_id" not in cols, table
    assert "chemical_family_id" not in _columns(db, "products")
    assert not {"family_id", "subfamily_id"} & set(_columns(db, "commodity_indexes"))
    assert "product_line_id" in _columns(db, "category_placements")
    assert "subfamily_id" not in _columns(db, "category_placements")
    assert "product_line_id" in _columns(db, "market_report_lines")
    assert "subfamily_id" not in _columns(db, "market_report_lines")
    assert _columns(db, "industry_out")["to_industries"][0] == "jsonb"


def test_catalogue_columns(db):
    cols = _columns(db, "formula_templates")
    for column in ("cas_number", "form", "volatile", "reference_grade", "archival_note",
                   "is_group", "group_members", "absorbed_into", "full_name", "catalog_meta",
                   "card_kind", "supply_status", "supply_status_detail", "redirect_to",
                   "internal_meta", "product_line_id", "family_id"):
        assert column in cols, column
    assert cols["card_kind"][2] == "NO" and "product" in cols["card_kind"][3]
    assert cols["supply_status"][2] == "YES"
    # Internal audit fields live in internal_meta, never in their own column.
    for column in ("grade_source", "refresh", "flags_review", "is_line_only"):
        assert column not in cols, column
    assert "withdrawn_at" in _columns(db, "formula_region_coverage")


def test_maker_evidence_columns_and_nothing_that_could_leak(db):
    cols = _columns(db, "producer_formulas")
    for column in ("role", "maker_evidence", "counted", "counts_toward_floor",
                   "evidence_label", "weak_reading", "corp_group", "integration_status",
                   "integrated", "integration_basis", "origin_restriction",
                   "floor_eligible_eu", "region_uncertain", "regions_raw", "sites",
                   "row_order", "tags", "hq_country", "raw_name"):
        assert column in cols, column
    assert cols["counted"][2] == "YES", "NULL means not set, never false"
    assert cols["counts_toward_floor"][2] == "NO" and "false" in cols["counts_toward_floor"][3]
    assert cols["evidence_label"][2] == "NO" and "not_audited" in cols["evidence_label"][3]
    assert cols["row_order"][2] == "NO"
    # Quotes, sources and audit wording are not stored, so no API can serve them.
    for column in ("maker_quote", "maker_source", "count_why", "role_changed", "role_why",
                   "role_source", "integration_why", "integration_source", "resolve_note",
                   "weak_note", "browser_note", "evidence_note", "grade_note"):
        assert column not in cols, column
    assert "is_bucket" in _columns(db, "producers")


def test_content_loads_columns(db):
    cols = _columns(db, "content_loads")
    for column in ("id", "source_commit", "source_date", "source_branch", "extractor_version",
                   "drop_dir", "started_at", "finished_at", "loaded_by", "dry_run", "counts",
                   "notes"):
        assert column in cols, column
    assert cols["source_commit"][2] == "NO"


def test_widenings(db):
    def col(table, column):
        return _columns(db, table)[column]

    # Widened to what the drop carries: long agency strings, long cost-line
    # labels, long type-code labels and card fields.
    assert col("commodity_indexes", "provider")[0] == "text"
    assert col("index_cards", "agency")[0] == "text"
    assert col("commodity_indexes", "name")[1] >= 128
    assert col("commodity_indexes", "category")[1] >= 255
    assert col("formula_template_components", "name")[1] >= 255
    assert col("type_codes", "label")[1] >= 255
    assert col("index_cards", "category")[1] >= 255
    assert col("index_cards", "access")[1] >= 255
    assert col("product_lines", "confidence")[1] >= 255


# ── CHECKs on the fixed vocabularies ─────────────────────────────────────────

def test_subject_types_and_trust_grain_moved_to_product_line(db):
    for table, name in (("editorial_blocks", "ck_editorial_subject_type"),
                        ("dimension_assertions", "ck_dimension_assertion_subject_type"),
                        ("supplier_trust_scores", "ck_sts_grain")):
        definition = _constraint_def(db, table, name)
        assert definition is not None, name
        assert "'product_line'" in definition and "'subfamily'" not in definition, definition


def test_card_kind_and_supply_status_are_checked(db, tenant_a):
    def template(**kw):
        return FormulaTemplate(team_id=None, created_by=tenant_a["user_id"],
                               name=f"Test tmpl {_tag()}", **kw)
    try:
        plain = template()
        db.add(plain)
        db.flush()
        db.refresh(plain)
        assert plain.card_kind == "product" and plain.supply_status is None
        for kind in CARD_KINDS:
            db.add(template(card_kind=kind))
        for status in SUPPLY_STATUSES:
            db.add(template(supply_status=status))
        db.flush()
        for bad in ({"card_kind": "shell"}, {"supply_status": "LIVE"},
                    {"supply_status": "verified"}):
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.add(template(**bad))
                    db.flush()
    finally:
        db.rollback()


def test_cost_category_is_one_of_the_five_kinds(db, tenant_a):
    tmpl = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"],
                           name=f"Test tmpl {_tag()}")
    db.add(tmpl)
    db.flush()
    try:
        db.add(FormulaTemplateComponent(template_id=tmpl.id, name="Supplier margin " * 12,
                                        component_type="fixed", weight_pct=9,
                                        cost_category="margin"))
        db.flush()  # a 192-char label fits since the widening
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(FormulaTemplateComponent(template_id=tmpl.id, name="x",
                                                component_type="fixed", weight_pct=1,
                                                cost_category="index"))
                db.flush()
    finally:
        db.rollback()


# ── Keys the loaders match on ────────────────────────────────────────────────

def test_family_name_is_unique(db, family):
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(ChemicalFamily(name=family.name))
            db.flush()


def test_subfamily_identity_is_family_and_name_with_one_unnamed_node(db, family):
    other = ChemicalFamily(name=f"Test family {_tag()}")
    db.add(other)
    db.flush()
    db.add_all([Subfamily(family_id=family.id, name="Test node"),
                Subfamily(family_id=family.id, name=None),
                # The same name and an unnamed node are fine in another family.
                Subfamily(family_id=other.id, name="Test node"),
                Subfamily(family_id=other.id, name=None)])
    db.flush()
    for dup in ({"name": "Test node"}, {"name": None}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(Subfamily(family_id=family.id, **dup))
                db.flush()


def test_platform_and_line_key_are_unique(db, family):
    first = _line(family.id)
    db.add(first)
    db.flush()
    for dup in ({"platform": first.platform}, {"line_key": first.line_key}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(_line(family.id, **dup))
                db.flush()
    # A line with no platform handle is allowed (NULLs are distinct).
    db.add_all([_line(family.id, platform=None), _line(family.id, platform=None)])
    db.flush()


def test_a_line_cannot_sit_under_another_familys_subfamily(db, family):
    other = ChemicalFamily(name=f"Test family {_tag()}")
    db.add(other)
    db.flush()
    own = Subfamily(family_id=family.id, name="Own node")
    foreign = Subfamily(family_id=other.id, name="Foreign node")
    db.add_all([own, foreign])
    db.flush()
    line = _line(family.id, own.id)
    db.add(line)
    db.add(_line(family.id, None))
    db.flush()
    assert line.subfamily is own and line in own.lines and line.family is family
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(_line(family.id, foreign.id))
            db.flush()
    # Moving the line under the other family's node alone is refused too.
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("UPDATE product_lines SET subfamily_id = :s WHERE id = :i"),
                       {"s": foreign.id, "i": line.id})


def test_deletes_follow_the_tree(db, tenant_a):
    # Committed first, as loaded rows are. (Postgres re-checks every foreign
    # key of a row inserted in the same transaction when a cascade updates it,
    # so a family deleted in the transaction that created a template on it
    # fails on the template's line link.)
    fam = ChemicalFamily(name=f"Test family {_tag()}")
    db.add(fam)
    db.flush()
    node = Subfamily(family_id=fam.id, name="Node")
    db.add(node)
    db.flush()
    line = _line(fam.id, node.id)
    db.add(line)
    db.flush()
    tmpl = FormulaTemplate(team_id=None, created_by=tenant_a["user_id"],
                           name=f"Test tmpl {_tag()}", family_id=fam.id,
                           product_line_id=line.id)
    db.add(tmpl)
    db.commit()
    ids = {"fam": fam.id, "node": node.id, "line": line.id, "tmpl": tmpl.id}
    try:
        # A sub-family that still has lines cannot go.
        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM subfamilies WHERE id = :i"), {"i": ids["node"]})
        db.rollback()
        # A family takes its sub-families and lines with it; a template keeps
        # its row and loses both links.
        db.execute(text("DELETE FROM chemical_families WHERE id = :i"), {"i": ids["fam"]})
        db.commit()
        db.expire_all()
        assert db.get(ProductLine, ids["line"]) is None
        assert db.get(Subfamily, ids["node"]) is None
        kept = db.get(FormulaTemplate, ids["tmpl"])
        assert kept is not None and kept.product_line_id is None and kept.family_id is None
    finally:
        db.rollback()
        db.execute(text("DELETE FROM formula_templates WHERE id = :i"), {"i": ids["tmpl"]})
        db.execute(text("DELETE FROM chemical_families WHERE id = :i"), {"i": ids["fam"]})
        db.commit()


def test_placement_is_unique_per_category_and_product(db):
    tag = _tag()
    try:
        ind = Industry(name=f"Test industry {tag}", slug=f"test_industry_{tag}")
        db.add(ind)
        db.flush()
        cat = Category(industry_id=ind.id, code=f"T{tag[:5]}", name="Test category",
                       fn="Test function", status="servable")
        db.add(cat)
        db.flush()
        db.add(CategoryPlacement(industry_id=ind.id, category_id=cat.id, pid="TEST-PID",
                                 line_key="Test|||Line"))
        db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(CategoryPlacement(industry_id=ind.id, category_id=cat.id,
                                         pid="TEST-PID", line_key="Test|||Line"))
                db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(Category(industry_id=ind.id, code=f"T{tag[:5]}", name="Dup",
                                status="servable"))
                db.flush()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(Category(industry_id=ind.id, code=f"U{tag[:5]}", name="Bad status",
                                status="ratified"))
                db.flush()
    finally:
        db.rollback()


# ── Fixed vocabulary and permissions ─────────────────────────────────────────

def test_gemstones_and_objectives_are_seeded_in_order(db):
    gems = db.query(GemstoneCategory).order_by(GemstoneCategory.sort_order).all()
    assert [(g.code, g.name, g.color) for g in gems] == GEMSTONES
    objs = db.query(StrategicObjective).order_by(StrategicObjective.sort_order).all()
    assert [(o.code, o.name, o.description) for o in objs] == OBJECTIVES


def test_strategy_permissions_exist_and_are_granted(db):
    """Plan ceiling before roles: a key missing from the Dream Plan is denied
    to every non-super-admin; a member with any custom role skips the
    membership fallback, so the Owner/Admin/Member roles need the keys too."""
    rows = db.query(Permission).filter(Permission.key.in_(STRATEGY_KEYS)).all()
    assert {r.key for r in rows} == STRATEGY_KEYS
    assert {r.category for r in rows} == {"strategy"}

    def keys_for_plan(plan):
        return {p.key for p in db.query(Permission)
                .join(PlanPermission, PlanPermission.permission_id == Permission.id)
                .filter(PlanPermission.plan_id == plan.id,
                        Permission.key.in_(STRATEGY_KEYS)).all()}

    def keys_for_role(role):
        return {p.key for p in db.query(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .filter(RolePermission.role_id == role.id,
                        Permission.key.in_(STRATEGY_KEYS)).all()}

    dream = db.query(Plan).filter(Plan.name == "Dream Plan").first()
    if dream:
        assert keys_for_plan(dream) == STRATEGY_KEYS
    superadmin = db.query(Role).filter(Role.team_id.is_(None),
                                       Role.name == "SuperAdmin").first()
    if superadmin:
        assert keys_for_role(superadmin) == STRATEGY_KEYS
    team_roles = db.query(Role).filter(Role.team_id.isnot(None),
                                       Role.name.in_(("Owner", "Admin", "Member"))).all()
    for role in team_roles:
        assert keys_for_role(role) == STRATEGY_KEYS, f"{role.name} of team {role.team_id}"


def test_lever_codes_are_unique_and_scores_are_bounded(db, tenant_a, playbook):
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(PlaybookLever(playbook_slug=playbook["slug"],
                                 lever_code=f"{playbook['slug']}-1a", gemstone_code="PC",
                                 title="dup"))
            db.flush()
    db.rollback()
    db.add(LeverScore(team_id=tenant_a["team_id"], lever_id=playbook["lever_id"], ease=5,
                      status="Actioned"))
    db.commit()
    for bad in ({"ease": 6}, {"status": "Done"}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(CustomLever(team_id=tenant_a["team_id"], playbook_slug=playbook["slug"],
                                   gemstone_code="VC", title="x", **bad))
                db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(LeverScore(team_id=tenant_a["team_id"], lever_id=playbook["lever_id"]))
            db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(StrategyAction(team_id=tenant_a["team_id"], playbook_slug=playbook["slug"],
                                  title="x", status="Overdue"))
            db.flush()
    db.rollback()


# ── RLS on team state ────────────────────────────────────────────────────────

def test_strategy_team_state_is_invisible_to_a_non_member(db, tenant_a, tenant_b, playbook):
    team_a = tenant_a["team_id"]
    db.add(StrategyRecord(team_id=team_a, playbook_slug=playbook["slug"],
                          owner_user_id=tenant_a["user_id"]))
    db.add(TeamObjective(team_id=team_a, playbook_slug=playbook["slug"],
                         objective_code="cost_reduction", selected=True, priority="High"))
    db.add(LeverScore(team_id=team_a, lever_id=playbook["lever_id"], applies=True, ease=2))
    custom = CustomLever(team_id=team_a, playbook_slug=playbook["slug"], gemstone_code="GS",
                         title="Made-up custom lever")
    db.add(custom)
    db.flush()
    db.add(StrategyAction(team_id=team_a, playbook_slug=playbook["slug"],
                          lever_id=playbook["lever_id"], title="Run the tender"))
    db.commit()

    models = (StrategyRecord, TeamObjective, LeverScore, CustomLever, StrategyAction)
    s = _as_user(tenant_b["user_id"])
    try:
        for model in models:
            assert s.query(model).filter(model.team_id == team_a).count() == 0, model.__name__
        # The platform playbook stays readable to everyone.
        assert s.query(Playbook).filter(Playbook.slug == playbook["slug"]).count() == 1
    finally:
        s.close()
        _reset_rls_context()

    s = _as_user(tenant_a["user_id"])
    try:
        for model in models:
            assert s.query(model).filter(model.team_id == team_a).count() == 1, model.__name__
    finally:
        s.close()
        _reset_rls_context()


def test_a_non_member_cannot_write_into_another_teams_strategy(tenant_a, tenant_b, playbook):
    s = _as_user(tenant_b["user_id"])
    try:
        s.add(StrategyRecord(team_id=tenant_a["team_id"], playbook_slug=playbook["slug"]))
        with pytest.raises(DBAPIError):
            s.flush()
        s.rollback()
    finally:
        s.close()
        _reset_rls_context()


def test_platform_taxonomy_is_readable_by_any_member(db, tenant_b):
    """No RLS on the tiers: a plain member sees platform families, sub-families
    and lines (with RLS on and no policy, they would see none)."""
    fam = ChemicalFamily(name=f"Test family {_tag()}")
    db.add(fam)
    db.flush()
    node = Subfamily(family_id=fam.id, name="Node")
    db.add(node)
    db.flush()
    line = _line(fam.id, node.id)
    db.add(line)
    db.commit()
    try:
        s = _as_user(tenant_b["user_id"])
        try:
            assert s.get(ChemicalFamily, fam.id) is not None
            assert s.get(Subfamily, node.id) is not None
            assert s.get(ProductLine, line.id) is not None
        finally:
            s.close()
            _reset_rls_context()
    finally:
        db.rollback()
        db.execute(text("DELETE FROM chemical_families WHERE id = :i"), {"i": fam.id})
        db.commit()
