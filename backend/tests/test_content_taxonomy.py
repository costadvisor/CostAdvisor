"""The taxonomy loader (app/services/content_drop/taxonomy.py).

Both axes load as platform rows and match the independent reading of the drop
in `tests/content_drop_expect.py`: families, sub-families, product lines (by
platform handle), out-row targets and the derived placements. A second load
changes nothing, a renamed line and a renamed sub-family keep their rows, and
drift is repaired.

Every expected figure is derived from the drop at test time; no count, name
or text from the drop is written here. The module works in one transaction
and rolls it back at the end, so it leaves nothing behind whatever state the
database was in (empty, or already loaded).
"""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest
from sqlalchemy import text

from app.database import SessionLocal, bypass_rls_var
from app.models.chemical_family import ChemicalFamily
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily
from app.models.taxonomy_v2 import (
    Category, CategoryMember, CategoryPlacement, Industry, IndustryOut,
)
from app.services.drop.report import LoadReport
from app.services.content_drop import reader, reports, taxonomy
from tests import content_drop_expect as expect


def _total(report: LoadReport, table: str) -> int:
    diff = report.table(table)
    return diff.created + diff.updated + diff.unchanged


@pytest.fixture(scope="module")
def loaded():
    """One session and one transaction for the module: load once, yield,
    roll back. RLS bypass as the CLI sets it. Fails (never skips) without the
    drop."""
    reader.drop_dir()
    token = bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        report = taxonomy.load(db, LoadReport(title="test taxonomy"))
        db.flush()
        yield db, report
    finally:
        db.rollback()
        db.close()
        bypass_rls_var.reset(token)


def _families(db) -> dict[int, str]:
    return {f.id: f.name for f in db.query(ChemicalFamily)}


# ── Without a database ───────────────────────────────────────────────────────

def test_the_projection_is_the_tree_rule():
    """Members ("*" on the record line), shared refs, and chain pids."""
    projection = taxonomy.derive_placements()
    want = expect.placements()
    assert set(projection) == set(want)
    for key, p in projection.items():
        assert (p["industry"], p["fn"], p["name"], p["category"]) == want[key]


def test_chain_pids_are_placed():
    tree = reader.tree()
    chain = {(cat["id"], pid) for branch in tree["industries"].values()
             for cat in branch.get("categories") or [] for ch in cat.get("lines") or []
             if isinstance(ch, dict) and isinstance(ch.get("pids"), list) for pid in ch["pids"]}
    assert chain
    assert chain <= set(taxonomy.derive_placements())


def test_the_projection_differs_from_functionality_only_on_the_known_residuals():
    assert set(expect.functionality_residuals()) <= taxonomy.KNOWN_FUNCTIONALITY_RESIDUALS


def test_out_targets_split_on_industry_names():
    names = expect.industries()
    for row in expect.out_rows():
        want = [t for t, _ok in row.to_industries] or None
        assert taxonomy.split_out_target(row.to_raw, names) == want, (row.industry, row.pid)
    # Names that contain a comma resolve whole; lists resolve part by part.
    with_comma = [r for r in expect.out_rows() if r.split == "exact" and "," in (r.to_raw or "")]
    assert len(with_comma) == expect.out_split_summary()["exact_with_comma"]
    for r in with_comma:
        assert taxonomy.split_out_target(r.to_raw, names) == [r.to_raw]
    for r in expect.out_rows():
        if r.split == "list":
            parts = taxonomy.split_out_target(r.to_raw, names)
            assert len(parts) > 1 and all(p in names for p in parts), r.pid


def test_out_split_on_made_up_names():
    names = ["Alpha, Beta & Co", "Alpha", "Gamma"]
    split = taxonomy.split_out_target
    assert split("Alpha, Beta & Co", names) == ["Alpha, Beta & Co"]
    assert split("Alpha, Beta & Co, Gamma", names) == ["Alpha, Beta & Co", "Gamma"]
    assert split("Gamma; Alpha", names) == ["Gamma", "Alpha"]
    assert split("Gamma, Delta", names) == ["Gamma", "Delta"]
    assert split("  ", names) is None and split(None, names) is None


# ── Supply axis ──────────────────────────────────────────────────────────────

def test_families_are_the_axis_families(loaded):
    db, _ = loaded
    rows = db.query(ChemicalFamily).order_by(ChemicalFamily.sort_order).all()
    assert [r.name for r in rows] == expect.families()
    nodes = {f["family"]: f for f in reader.axis()["families"]}
    for row in rows:
        authored = {k: v for k, v in nodes[row.name].items() if k not in ("family", "subfamilies")}
        assert row.meta == (authored or None), row.name


def test_sub_families_are_the_axis_nodes(loaded):
    db, _ = loaded
    families = _families(db)
    rows = db.query(Subfamily).all()
    assert Counter((families[r.family_id], r.name) for r in rows) == Counter(expect.subfamilies())
    stored = {(families[r.family_id], r.name): sorted(r.former_names)
              for r in rows if r.former_names}
    assert stored == {k: sorted(v) for k, v in expect.subfamily_former_names().items()}
    assert sum(1 for r in rows if r.name is None) == \
        sum(1 for _f, n in expect.subfamilies() if n is None)
    # A node with no named line still loads; a line never sits under another
    # family's node.
    lineless = {(families[r.family_id], r.name) for r in rows if not r.lines}
    assert lineless == set(expect.subfamilies_without_lines(named_only=True))
    for row in rows:
        assert all(line.family_id == row.family_id for line in row.lines)


def test_product_lines_are_the_named_axis_lines_by_platform(loaded):
    db, report = loaded
    families = _families(db)
    subfamilies = {s.id: s.name for s in db.query(Subfamily)}
    rows = {r.line_key: r for r in db.query(ProductLine)}
    want = expect.product_lines()
    assert set(rows) == set(want)
    for key, line in want.items():
        row = rows[key]
        assert (row.name, row.platform, families[row.family_id]) == \
            (line.name, line.platform, line.family), key
        assert (subfamilies[row.subfamily_id] if row.subfamily_id else None) == line.subfamily, key
        assert set(row.former_keys or []) == set(line.former_keys), key
        assert row.retired_at is None, key
    # The unnamed platforms are skipped and said so; the off-axis record keys
    # and the unpublished names are not rows.
    platforms = {r.platform for r in rows.values()}
    skipped = report.table("product_lines").skipped
    for u in expect.unnamed_platforms():
        assert u["platform"] not in platforms
        assert any(u["platform"] in k for k, _why in skipped)
    assert not set(expect.off_axis_record_keys().values()) & set(rows)
    names = {r.name for r in rows.values()}
    assert not set(expect.unpublished_line_names()) & names


def test_line_columns_follow_the_axis_and_the_scope(loaded):
    db, _ = loaded
    rows = {r.line_key: r for r in db.query(ProductLine)}
    v1 = {s["line"]: s for s in expect.scope()["lines"]}
    assert {k for k, r in rows.items() if r.in_v1_scope} == set(v1) & set(rows)
    for key, row in rows.items():
        assert row.report_old_line == (v1[key].get("report_old_line") if key in v1 else None)
    single, _several = reports.derive_line_report_slugs()
    assert {k: r.report_slug for k, r in rows.items()} == {k: single.get(k) for k in rows}

    nodes = {reader.make_line_key(f["family"], ln["name"]): ln
             for f in reader.axis()["families"] for sf in f["subfamilies"]
             for ln in sf["lines"] if ln.get("name")}
    for key, row in rows.items():
        node = nodes[key]
        flags = {k: node[k] for k in taxonomy.LINE_FLAG_KEYS if k in node}
        assert row.flags == (flags or None), key
        assert row.confidence == node.get("confidence"), key
        # products[] is never stored: it is not membership.
        assert "products" not in (row.axis_meta or {}), key
        assert not set(row.axis_meta or {}) & set(taxonomy.LINE_FLAG_KEYS), key


def test_every_record_key_resolves_or_is_reported_unpublished(loaded):
    db, _ = loaded
    index = taxonomy.line_index(db)
    ids = {r.line_key: r.id for r in db.query(ProductLine)}
    for pid in expect.template_codes():
        key = expect.record_line_key(pid)
        if key is None:
            continue
        want = expect.template_line(pid)
        assert index.get(key) == (ids[want] if want else None), pid


# ── Demand axis ──────────────────────────────────────────────────────────────

def test_demand_axis_counts_and_authored_keys(loaded):
    db, report = loaded
    tree = reader.tree()
    cats = expect.categories()
    assert db.query(Industry).count() == len(expect.industries())
    assert db.query(Category).count() == len(cats)
    assert _total(report, "category_shared") == len(tree["shared"])
    assert _total(report, "category_shared_members") == sum(
        len(s.get("members") or []) for s in tree["shared"].values())
    assert _total(report, "category_members") == sum(len(c.get("members") or []) for c in cats)
    assert _total(report, "category_refs") == sum(len(c.get("ref") or []) for c in cats)
    assert _total(report, "category_build_items") == sum(len(c.get("build") or []) for c in cats)
    whole = sum(1 for c in cats for m in c.get("members") or [] if m["pids"] == "*")
    stored = db.execute(text(
        "SELECT count(*) FILTER (WHERE pids IS NULL), count(*) "
        "FROM category_members WHERE is_whole_line")).one()
    assert tuple(stored) == (whole, whole)
    # Keys with no column land in `extra`, as authored.
    by_code = {c.code: c for c in db.query(Category)}
    for cat in cats:
        extra = {k: v for k, v in cat.items() if k not in taxonomy._CATEGORY_COLUMNS}
        assert by_code[cat["id"]].extra == (extra or None), cat["id"]


def test_out_rows_keep_the_raw_target_and_resolve_it(loaded):
    db, _ = loaded
    names = {i.id: i.name for i in db.query(Industry)}
    stored = {(names[o.industry_id], o.pid): o for o in db.query(IndustryOut)}
    rows = expect.out_rows()
    assert len(stored) == len(rows)
    for r in rows:
        o = stored[(r.industry, r.pid)]
        assert o.to_industry == r.to_raw
        assert o.to_industries == ([t for t, _ok in r.to_industries] or None)


def test_placements_follow_the_tree_and_resolve_their_line(loaded):
    db, _ = loaded
    codes = {c.id: c.code for c in db.query(Category)}
    rows = db.query(CategoryPlacement).all()
    assert {(codes[p.category_id], p.pid) for p in rows} == set(expect.placements())
    index = taxonomy.line_index(db)
    current = {r.line_key for r in db.query(ProductLine)}
    for p in rows:
        assert p.product_line_id == (index.get(p.line_key) if p.line_key else None), p.pid
        if p.line_key in current:
            assert p.product_line_id is not None
    # A key that is not a current line (an unpublished one) resolves to none.
    for u in expect.unnamed_platforms():
        for key in u["todays_keys"]:
            assert all(p.product_line_id is None for p in rows if p.line_key == key)


def test_functionality_check_names_exactly_the_residuals(loaded):
    db, report = loaded
    result = taxonomy.check_functionality(db)
    assert sorted(result["mismatched"]) == expect.functionality_residuals()
    check = report.table(taxonomy.FUNCTIONALITY_CHECK)
    assert sorted(k for k, _why in check.skipped) == expect.functionality_residuals()
    assert not any("WARNING" in why for _k, why in check.skipped)
    assert check.changed == 0


# ── Reloads ──────────────────────────────────────────────────────────────────

def test_a_second_load_changes_nothing(loaded):
    db, _ = loaded
    again = taxonomy.load(db, LoadReport(title="again"))
    db.flush()
    assert again.changed == 0, again.render()


def _drop_with_axis(tmp_path, doc: dict):
    """A drop dir that is the real one except for an edited supply axis."""
    root = tmp_path / "drop"
    root.mkdir()
    for entry in reader.drop_dir().iterdir():
        if entry.name != "axis":
            (root / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    (root / "axis").mkdir()
    with open(root / "axis" / "supply_axis.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return root


def test_a_renamed_line_and_sub_family_keep_their_rows(loaded, tmp_path, monkeypatch):
    """Design §1.4: a line renamed on the same platform, and a sub-family
    renamed with its old name in `former_names`, keep their ids. Renaming
    back keeps them too."""
    db, _ = loaded
    original = reader.drop_dir()
    doc = copy.deepcopy(reader.axis())
    record_keys = {expect.record_line_key(p) for p in expect.template_codes()}
    fam, node, line = next(
        (f, sf, ln) for f in doc["families"] for sf in f["subfamilies"] if sf.get("name")
        for ln in sf["lines"]
        if ln.get("name") and ln.get("platform")
        and reader.make_line_key(f["family"], ln["name"]) in record_keys)
    old_key = reader.make_line_key(fam["family"], line["name"])
    old_node = node["name"]
    line_row = db.query(ProductLine).filter_by(line_key=old_key).one()
    node_row = db.get(Subfamily, line_row.subfamily_id)
    line_id, node_id = line_row.id, node_row.id
    counts = (db.query(ProductLine).count(), db.query(Subfamily).count())

    new_line, new_node = "Made-up renamed line", "Made-up renamed sub-family"
    new_key = reader.make_line_key(fam["family"], new_line)
    line["former_names"] = list(line.get("former_names") or []) + [line["name"]]
    line["name"] = new_line
    node["former_names"] = list(node.get("former_names") or []) + [old_node]
    node["name"] = new_node
    edited = _drop_with_axis(tmp_path, doc)

    try:
        monkeypatch.setenv(reader.CONTENT_DROP_ENV, str(edited))
        reader.reset_caches()
        renamed = taxonomy.load(db, LoadReport(title="renamed"))
        db.flush()
        assert renamed.table("product_lines").created == 0
        assert renamed.table("subfamilies").created == 0
        assert (db.query(ProductLine).count(), db.query(Subfamily).count()) == counts
        db.refresh(line_row)
        db.refresh(node_row)
        assert (line_row.id, line_row.line_key, line_row.name) == (line_id, new_key, new_line)
        assert old_key in line_row.former_keys
        assert (node_row.id, node_row.name) == (node_id, new_node)
        assert old_node in node_row.former_names
        assert line_row.subfamily_id == node_id
        # The old key still finds the line: records, tree members, reports.
        assert taxonomy.line_index(db)[old_key] == line_id
        placed = db.query(CategoryPlacement).filter(CategoryPlacement.line_key == old_key).all()
        assert all(p.product_line_id == line_id for p in placed)

        # And back again.
        monkeypatch.setenv(reader.CONTENT_DROP_ENV, str(original))
        reader.reset_caches()
        back = taxonomy.load(db, LoadReport(title="renamed back"))
        db.flush()
        assert back.table("product_lines").created == 0
        assert back.table("subfamilies").created == 0
        db.refresh(line_row)
        db.refresh(node_row)
        assert (line_row.id, line_row.line_key) == (line_id, old_key)
        assert new_key in line_row.former_keys
        assert (node_row.id, node_row.name) == (node_id, old_node)
        assert new_node in node_row.former_names
        settled = taxonomy.load(db, LoadReport(title="settled"))
        db.flush()
        assert settled.changed == 0, settled.render()
    finally:
        monkeypatch.setenv(reader.CONTENT_DROP_ENV, str(original))
        reader.reset_caches()


def test_a_fresh_demand_axis_rebuilds_and_drift_is_repaired(loaded):
    """Empty the demand axis inside the transaction, reload it from nothing,
    then damage a few rows and check the next load puts exactly those back."""
    db, _ = loaded
    for table in ("category_placements", "categories", "category_shared", "industries"):
        db.execute(text(f"DELETE FROM {table}"))
    db.expunge_all()
    fresh = taxonomy.load(db, LoadReport(title="fresh"))
    db.flush()
    assert fresh.table("industries").created == len(expect.industries())
    assert fresh.table("categories").created == len(expect.categories())
    assert fresh.table("industry_out").created == len(expect.out_rows())
    assert fresh.table("category_placements").created == len(expect.placements())
    assert sorted(taxonomy.check_functionality(db)["mismatched"]) == \
        expect.functionality_residuals()

    # Drift: a renamed category, a stray member position, a lost placement,
    # a line that fell out of the v1 scope.
    cat = (db.query(Category).join(CategoryPlacement, CategoryPlacement.category_id == Category.id)
           .order_by(Category.code).first())
    cat.name = "Renamed by hand"
    db.add(CategoryMember(category_id=cat.id, sort_order=9999, line_key="X|||Y",
                          pids=["ZZ-NOT-A-PID"], is_whole_line=False))
    db.delete(db.query(CategoryPlacement).filter(CategoryPlacement.category_id == cat.id).first())
    line = db.query(ProductLine).filter(ProductLine.in_v1_scope.is_(True)).first()
    line.in_v1_scope = False
    db.flush()

    repaired = taxonomy.load(db, LoadReport(title="repair"))
    db.flush()
    assert repaired.table("categories").updated == 1
    assert repaired.table("category_members").deleted == 1
    assert repaired.table("category_placements").created == 1
    assert repaired.table("product_lines").updated == 1
    assert repaired.changed == 4, repaired.render()

    assert taxonomy.load(db, LoadReport(title="settled")).changed == 0
