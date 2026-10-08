"""The index-layer loader (app/services/content_drop/indexes.py).

The load runs once for the module inside one transaction and is rolled back at
the end, so these tests hold whether or not `seed_content_drop.py` has already
committed the drop: they assert the state the load leaves, not how many rows it
happened to create.

Expected numbers, names and labels come from the drop at test time
(`tests/content_drop_expect.py` and the drop's index files), never from
constants: the drop changes with every pull.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, text

import seed_content_drop
from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex
from app.models.index_dossier import IndexDossier, IndexProducerRole, VolatilityCalibration
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.models.index_seasonality import IndexSeasonalFactor
from app.models.producer import Producer, ProducerAlias
from app.services.drop.report import LoadReport
from app.services.content_drop import indexes as loader
from app.services.content_drop import reader
from app.services.index_dossier import (
    DEFAULT_MIN_POINTS, DEFAULT_RUNGS, _all_dispersions, active_calibration, build_ladder,
)
from app.services.producers import match_form
from tests import content_drop_expect as expect

needs_drop = pytest.mark.skipif(not reader.drop_available(),
                                reason="content drop not extracted (set CONTENT_DROP_DIR)")

LEGACY_FIELDS = ("name", "provider", "category", "frequency", "unit", "value_kind",
                 "base_period", "source_region", "proxy_logic", "scrape_enabled")


@pytest.fixture(scope="module")
def loaded():
    """One load for the module; everything is rolled back afterwards."""
    if not reader.drop_available():
        pytest.skip("content drop not extracted (set CONTENT_DROP_DIR)")
    token = bypass_rls_var.set(True)
    session = SessionLocal()
    try:
        legacy = {
            ci.id: tuple(getattr(ci, f) for f in LEGACY_FIELDS)
            for ci in session.query(CommodityIndex).filter(CommodityIndex.commodity_key.is_(None))
        }
        report = loader.load(session, LoadReport(title="test"))
        session.flush()
        yield {"db": session, "report": report, "legacy": legacy}
    finally:
        session.rollback()
        session.close()
        bypass_rls_var.reset(token)


def _series(db, key) -> CommodityIndex:
    return db.query(CommodityIndex).filter(CommodityIndex.commodity_key == key).one()


def _points(db, key, kind):
    return [
        (int(y), int(m), float(v)) for y, m, v in
        db.query(IndexMonthlyValue.year, IndexMonthlyValue.month, IndexMonthlyValue.value)
        .filter(IndexMonthlyValue.commodity_id == _series(db, key).id,
                IndexMonthlyValue.kind == kind)
        .order_by(IndexMonthlyValue.year, IndexMonthlyValue.month)
    ]


def _months_after(period: tuple[int, int], n: int) -> list[tuple[int, int]]:
    year, month = period
    out = []
    for _ in range(n):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        out.append((year, month))
    return out


def _card_base_name(card: dict) -> str:
    """A card's own name without its "— multi-region" tail."""
    return str(card["name"]).split(" — ")[0].strip()


def _parenthesised_separator_company() -> tuple[str, set[str]]:
    """The company whose dossier name carries the producer separator inside
    its parenthetical (a list of its sites, say), and the series whose dossier
    names it. Read from the drop, so no company name lives in this file."""
    cards = reader.indexes_file("INDEXES")
    owners, _conflicts, _ = loader.dossier_owners(cards, reader.raw("FIDX"))
    found: dict[str, set[str]] = {}
    for key, (_slug, payload) in owners.items():
        for _role, field_name in loader.PRODUCER_ROLE_FIELDS:
            for el in payload.get(field_name) or []:
                name = el.get("n") if isinstance(el, dict) else None
                if name and any(loader.MULTI_SEPARATOR in part
                                for part in loader.dossier_parts(name)):
                    found.setdefault(name.split(" (")[0].strip(), set()).add(key)
    assert found, "no dossier producer with a separator inside parentheses"
    company = sorted(found)[0]
    keys = {key for key, (_s, payload) in owners.items()
            for _r, field_name in loader.PRODUCER_ROLE_FIELDS
            for el in payload.get(field_name) or []
            if isinstance(el, dict) and str(el.get("n") or "").startswith(company)}
    return company, keys


# ── Pure rules ───────────────────────────────────────────────────────────────

def test_names_and_regions_derive_from_the_key_without_inventing():
    assert loader.humanise_key("inorg-chem-ppi") == "Inorg Chem PPI"
    assert loader.humanise_key("zinc-gl") == "Zinc — Global"
    assert loader.humanise_key("silicon-metal-cn") == "Silicon Metal — CN"
    assert loader.humanise_key("rutile") == "Rutile"
    assert loader.source_region("lab-eu") == "EU"
    assert loader.source_region("hides-gl") == "GL"
    assert loader.source_region("turmeric-wpi-in") == "IN"
    # Suffixes that are sources or places, not regions.
    for key in ("inorg-chem-ppi", "silicon-metal-mb", "lng-jkm-asia", "coal-au", "brent"):
        assert loader.source_region(key) is None


def test_a_proxy_card_never_names_the_series_it_borrows():
    cards = {
        # Made-up card names; the keys are the shapes the drop uses.
        "naphtha": {"name": "Test feed (crude proxy)", "regKeys": {"multi": "brent"}},
        "crude-oil": {"name": "Test Feed — multi-region",
                      "regKeys": {"Global": "brent", "NA": "wti"}},
    }
    refs, dead = loader.card_refs(cards, {"brent": [], "wti": []})
    assert dead == []
    owner = loader.owner_card(refs["brent"])
    assert owner.slug == "crude-oil"
    assert loader.series_name("brent", owner) == "Test Feed — Global"
    assert loader.series_name("wti", loader.owner_card(refs["wti"])) == "Test Feed — NA"


def test_proxy_note_keeps_admin_params():
    assert loader._proxy_logic(None, None) is None
    assert loader._proxy_logic(None, "stand-in") == {"note": "stand-in"}
    admin = {"base_index": "brent", "operation": "ratio", "note": "old"}
    assert loader._proxy_logic(admin, "new") == {**admin, "note": "new"}


def test_an_overlong_access_tier_is_dropped_not_truncated():
    long = "x" * (loader.ACCESS_TIER_MAX + 1)
    payload = {"roleExtra": {"access": long, "type": "Spot"}, "access": "Free"}
    fitted, dropped = loader.fit_access_tier(payload)
    assert dropped == long
    assert fitted["roleExtra"] == {"access": None, "type": "Spot"}
    assert fitted["access"] is None
    assert payload["roleExtra"]["access"] == long, "the shared drop must not be mutated"
    short = {"roleExtra": {"access": "Free"}}
    assert loader.fit_access_tier(short) == (short, None)


def test_the_harness_finds_the_loader():
    module = seed_content_drop.resolve_loader("indexes")
    assert module is loader


@needs_drop
def test_the_data_anchors_agree_with_the_drop():
    """The anchors the files do not state: the last actual month is the one
    the independent helper counts from FIDX, and the forecast starts the month
    after it."""
    assert loader.DATA_AS_OF == expect.last_actual_month()
    assert loader.FORECAST_START == _months_after(loader.DATA_AS_OF, 1)[0]
    assert max(len(v) for v in reader.raw("FIDX").values()) == loader.HISTORY_MONTHS
    assert max(len(v) for v in reader.raw("FFORE").values()) == loader.FORECAST_MONTHS


# ── Series and numbers ───────────────────────────────────────────────────────

@needs_drop
def test_one_series_per_fidx_key_with_its_history_and_forecast(loaded):
    db = loaded["db"]
    fidx, ffore = reader.raw("FIDX"), reader.raw("FFORE")
    counts = expect.index_counts()
    rows = {ci.commodity_key: ci for ci in db.query(CommodityIndex)
            .filter(CommodityIndex.commodity_key.in_(list(fidx)))}
    assert len(rows) == len(fidx) == counts["series"]
    by_kind = {
        (cid, kind): n for cid, kind, n in
        db.query(IndexMonthlyValue.commodity_id, IndexMonthlyValue.kind,
                 func.count(IndexMonthlyValue.id))
        .group_by(IndexMonthlyValue.commodity_id, IndexMonthlyValue.kind)
    }
    for key, ci in rows.items():
        assert by_kind.get((ci.id, "actual")) == len(fidx[key]), key
        assert by_kind.get((ci.id, "forecast"), 0) == len(ffore.get(key) or []), key
        assert ci.value_kind == "index_level"
        assert ci.scrape_enabled is False
        assert ci.base_period == ("2023-01" if fidx[key][0] == 100 else None), key
    ids = [ci.id for ci in rows.values()]
    assert db.query(func.count(IndexMonthlyValue.id)).filter(
        IndexMonthlyValue.commodity_id.in_(ids)).scalar() == counts["monthly_points"]


@needs_drop
def test_spot_check_elec_eu_and_iron_scrap_na_against_fidx(loaded):
    db = loaded["db"]
    fidx, ffore = reader.raw("FIDX"), reader.raw("FFORE")
    for key in ("elec-eu", "iron-scrap-na"):
        actual = _points(db, key, "actual")
        assert actual[0][:2] == (2023, 1) and actual[-1][:2] == expect.last_actual_month()
        assert [v for _, _, v in actual] == [float(v) for v in fidx[key]]
        forecast = _points(db, key, "forecast")
        assert [(y, m) for y, m, _ in forecast] == \
            _months_after(expect.last_actual_month(), len(ffore[key]))
        assert [v for _, _, v in forecast] == [float(v) for v in ffore[key]]


@needs_drop
def test_series_names_are_readable_unique_and_carry_the_source(loaded):
    db = loaded["db"]
    names = [n for (n,) in db.query(CommodityIndex.name)]
    assert len(names) == len(set(names))
    # Names, providers and notes are read from the drop's own index files.
    meta = reader.raw("INDEX_SOURCE_META")
    cards = reader.indexes_file("INDEXES")
    crude, lab = cards["crude-oil"], cards["lab"]
    brent = _series(db, "brent")
    assert brent.name == f"{_card_base_name(crude)} — Global"
    assert brent.provider == (meta.get("brent") or {}).get("agency")
    assert brent.proxy_logic and brent.proxy_logic["note"]
    assert brent.category == crude["cat"].split(" · ")[0].strip()
    assert _series(db, "elec-eu").name
    assert _series(db, "lab-eu").name == f"{_card_base_name(lab)} — EU"
    assert _series(db, "lab-eu").source_region == "EU"
    # No META entry, no card: named from the key, provider NULL, never guessed.
    ppi = _series(db, "inorg-chem-ppi")
    assert ppi.name.startswith("Inorg Chem PPI") and ppi.category is None
    no_meta = sorted(set(reader.raw("FIDX")) - set(meta))
    assert no_meta
    for key in no_meta:
        assert _series(db, key).provider is None, key


@needs_drop
def test_source_metadata_is_taken_as_given(loaded):
    """Every series' frequency is stored as authored, whatever its wording
    (an annual price stepped onto months included), and a series whose source
    entry carries no proxy (no `proxy` key, or `proxy: null`) gets no proxy
    note."""
    db = loaded["db"]
    meta = reader.raw("INDEX_SOURCE_META")
    fidx = reader.raw("FIDX")
    without_proxy = [k for k in fidx if k in meta and not meta[k].get("proxy")]
    no_proxy_key = [k for k in fidx if k in meta and "proxy" not in meta[k]]
    assert without_proxy and no_proxy_key
    for key in fidx:
        if key not in meta:
            continue
        series = _series(db, key)
        assert series.frequency == loader._clean(meta[key].get("freq")), key
        if key in without_proxy:
            assert not (series.proxy_logic or {}).get("note"), key
    for key in no_proxy_key:
        assert len(_points(db, key, "actual")) == len(fidx[key]), key


@needs_drop
def test_rows_seeded_before_the_drop_are_never_matched_or_touched(loaded):
    db = loaded["db"]
    for ci in db.query(CommodityIndex).filter(CommodityIndex.id.in_(list(loaded["legacy"]))):
        assert ci.commodity_key is None
        assert tuple(getattr(ci, f) for f in LEGACY_FIELDS) == loaded["legacy"][ci.id]


# ── The forecast vintage ─────────────────────────────────────────────────────

@needs_drop
def test_the_forecast_vintage_is_confirmed_for_this_drop(loaded):
    report = loaded["report"]
    assert loader.ffore_digest(reader.raw("FFORE")) == loader.FORECAST_VINTAGE_FFORE_SHA256
    check = report.table(loader.CHECK_FORECAST_VINTAGE)
    assert (check.unchanged, check.skipped, check.changed) == (1, [], 0)
    # The vintage is after the last actual month it forecasts from.
    assert loader.FORECAST_VINTAGE > loader.DATA_AS_OF


def test_a_changed_forecast_asks_for_its_vintage_to_be_confirmed():
    # Made-up series and values: any forecast other than the recorded one.
    report = loader.IndexLoadReport(title="vintage")
    check = loader._check_forecast_vintage({"made-up-series": [100, 101]}, report)
    assert check.changed == 0 and check.unchanged == 0
    assert [key for key, _why in check.skipped] == ["FFORE"]
    assert "FORECAST_VINTAGE" in check.skipped[0][1]
    assert any("UNCONFIRMED" in note for note in report.notes)
    # Layout does not matter, content does.
    assert loader.ffore_digest({"b": [1.5], "a": [2]}) == loader.ffore_digest({"a": [2], "b": [1.5]})


# ── The resolution join ──────────────────────────────────────────────────────

@needs_drop
def test_every_fcovered_tag_resolves_to_a_series_with_its_actuals(loaded):
    db = loaded["db"]
    fcovered, fidx = reader.raw("FCOVERED"), reader.raw("FIDX")
    rows = {tc.code: tc for tc in db.query(TypeCode).filter(TypeCode.code.in_(list(fcovered)))}
    assert len(rows) == len(fcovered) == expect.index_counts()["type_codes"]
    for code, tc in rows.items():
        assert tc.resolution == "resolved"
        assert tc.resolves_to.commodity_key == fcovered[code], code
        assert len(_points(db, fcovered[code], "actual")) == len(fidx[fcovered[code]]), code
        assert tc.label, code
        assert tc.source_n_lines and tc.source_n_lines > 0
    assert db.query(TypeCode).filter(TypeCode.code.ilike("fixed")).count() == 0


@needs_drop
def test_a_type_code_label_is_the_drops_own_line_label(loaded):
    db = loaded["db"]
    usage = loader.tag_usage(reader.raw("FORMULA_COMBOS"))
    labels = {line[1] for rec in reader.raw("FORMULA_COMBOS").values()
              for combo in rec.get("combos") or [] for line in combo.get("lines") or []
              if len(line) > 2 and line[2] == "CPO-MY"}
    tc = db.query(TypeCode).filter(TypeCode.code == "CPO-MY").one()
    assert tc.label in labels
    assert tc.source_n_formulas == usage["CPO-MY"]["n_formulas"]


@needs_drop
def test_a_leftover_fixed_type_code_is_removed(loaded):
    db = loaded["db"]
    savepoint = db.begin_nested()
    try:
        db.add(TypeCode(code="fixed", resolution="ambiguous"))
        db.flush()
        key_to_id = {ci.commodity_key: ci.id for ci in db.query(CommodityIndex)
                     .filter(CommodityIndex.commodity_key.isnot(None))}
        diff = loader._load_type_codes(db, reader.raw("FCOVERED"), key_to_id,
                                       loader.tag_usage(reader.raw("FORMULA_COMBOS")))
        assert diff.deleted == 1
        assert db.query(TypeCode).filter(TypeCode.code == "fixed").count() == 0
    finally:
        savepoint.rollback()


# ── Cards and dossiers ───────────────────────────────────────────────────────

@needs_drop
def test_one_card_per_live_region_and_dead_keys_reported(loaded):
    db, report = loaded["db"], loaded["report"]
    fidx, cards = reader.raw("FIDX"), reader.indexes_file("INDEXES")
    live = {f"{slug}|{r}" for slug, c in cards.items() for r, k in c["regKeys"].items() if k in fidx}
    dead = {f"{slug}|{r}" for slug, c in cards.items() for r, k in c["regKeys"].items() if k not in fidx}
    assert live
    stored = {c.feed_key: c for c in db.query(IndexCard).filter(IndexCard.feed_key.in_(list(live)))}
    assert set(stored) == live
    assert {key for key, _ in report.table("index_cards").skipped} == dead
    lab = cards["lab"]
    defaults = set(lab.get("defaultRegs") or [])
    for region, series_key in lab["regKeys"].items():
        card = stored[f"lab|{region}"]
        assert card.commodity.commodity_key == series_key
        assert card.is_default_region is (region in defaults)
        assert card.region_label == lab["regLabels"][region]
    assert stored["urea|multi"].region == "multi"
    assert stored["crude-oil|Global"].commodity.commodity_key == "brent"


@needs_drop
def test_dossiers_go_to_the_series_the_card_names(loaded):
    db = loaded["db"]
    cards = reader.indexes_file("INDEXES")

    def dossier(key):
        return db.query(IndexDossier).filter(
            IndexDossier.commodity_id == _series(db, key).id,
            IndexDossier.region.is_(None)).one()

    # elec-eu gets its own card's dossier, not the multi-region "electricity" one.
    own = [d.get("cat") for d in cards["elec-eu"]["upstreamDrivers"]]
    assert [d.category for d in sorted(dossier("elec-eu").drivers, key=lambda d: d.sort_order)] == own
    # brent is crude-oil's Global series, not the naphtha proxy card's.
    crude = loader.effective_payload(cards["crude-oil"], "Global")
    assert [c.label for c in sorted(dossier("brent").chain, key=lambda c: c.position)] == \
        [el.get("a") or el.get("l") for el in crude["chain"] if el.get("a") or el.get("l")]
    # iron-scrap-eu carries the EU override, on its own series.
    eu = loader.effective_payload(cards["iron-scrap-na"], "EU")
    assert len(dossier("iron-scrap-eu").drivers) == len(eu.get("upstreamDrivers") or [])
    for d in db.query(IndexDossier).filter(IndexDossier.access_tier.isnot(None)):
        assert len(d.access_tier) <= loader.ACCESS_TIER_MAX
    owners, conflicts, _ = loader.dossier_owners(cards, reader.raw("FIDX"))
    assert ("naphtha", "brent") in conflicts and ("electricity", "elec-eu") in conflicts
    assert db.query(IndexDossier).filter(
        IndexDossier.commodity_id.in_([_series(db, k).id for k in owners])).count() >= len(owners)


@needs_drop
def test_a_rerun_restores_a_dossier_producer_deleted_elsewhere(loaded):
    """Producer rows are shared (other loaders and tests delete them, and the
    roles go by CASCADE). A rerun must put the dossier's roles back rather
    than call the dossier unchanged."""
    db = loaded["db"]
    drop_ids = [ci.id for ci in db.query(CommodityIndex)
                .filter(CommodityIndex.commodity_key.isnot(None))]
    role = (db.query(IndexProducerRole).join(IndexDossier)
            .filter(IndexDossier.commodity_id.in_(drop_ids))
            .order_by(IndexProducerRole.raw_name).first())
    assert role is not None
    raw_name, kind, dossier_id = role.raw_name, role.role, role.dossier_id
    savepoint = db.begin_nested()
    try:
        db.execute(text("DELETE FROM producers WHERE id = :i"), {"i": str(role.producer_id)})
        db.expire_all()
        healed = loader.load(db, LoadReport(title="heal"))
        db.flush()
        assert healed.table("dossiers").updated >= 1
        assert db.query(IndexProducerRole).filter(
            IndexProducerRole.dossier_id == dossier_id, IndexProducerRole.role == kind,
            IndexProducerRole.raw_name == raw_name).count() >= 1
    finally:
        savepoint.rollback()
        db.expire_all()


def test_a_separator_inside_parentheses_does_not_split_a_company():
    # Made-up names: the rule, not the drop's companies.
    assert loader.dossier_parts("Maker A (Site One / Site Two)") == [
        "Maker A (Site One / Site Two)"]
    # Outside parentheses it still splits, as `split_raw_name` does.
    assert loader.dossier_parts("Maker B / Maker C") == ["Maker B", "Maker C"]
    assert loader.dossier_parts("Maker D / Maker E (Site)") == ["Maker D", "Maker E (Site)"]
    assert loader.dossier_parts("Maker F / Maker G (Grade)") == ["Maker F", "Maker G (Grade)"]
    assert loader.dossier_parts("  ") == []


@needs_drop
def test_dossier_producers_settle_in_one_load(loaded):
    """From no dossier producers at all, one load is enough: the next changes
    nothing. The naive `" / "` split turned a name with a separator inside its
    parenthetical into a stray producer ending in ")" and an alias behind the
    bare company, which a dossier loaded earlier only picked up on the second
    run."""
    db = loaded["db"]
    company, keys = _parenthesised_separator_company()
    drop_ids = [ci.id for ci in db.query(CommodityIndex)
                .filter(CommodityIndex.commodity_key.isnot(None))]
    savepoint = db.begin_nested()
    try:
        db.execute(text(
            "DELETE FROM producers WHERE id IN (SELECT r.producer_id FROM index_producer_roles r "
            "JOIN index_dossiers d ON d.id = r.dossier_id "
            "WHERE d.commodity_id = ANY(:ids))"), {"ids": drop_ids})
        db.expire_all()
        first = loader.load(db, LoadReport(title="from no producers"))
        db.flush()
        assert first.table("dossiers").updated >= 1

        names = [n for (n,) in db.query(Producer.name).join(
            IndexProducerRole, IndexProducerRole.producer_id == Producer.id).join(
            IndexDossier).filter(IndexDossier.commodity_id.in_(drop_ids)).distinct()]
        unbalanced = [n for n in names if n.count("(") != n.count(")")]
        assert unbalanced == []
        for key in sorted(keys):
            held = {p for (p,) in db.query(Producer.name).join(
                IndexProducerRole, IndexProducerRole.producer_id == Producer.id).join(
                IndexDossier).filter(
                IndexDossier.commodity_id == _series(db, key).id,
                IndexProducerRole.raw_name.like(f"{company}%"))}
            assert held == {company}, key
        # The plain company name names one company.
        assert {a.producer_id for a in db.query(ProducerAlias)
                .filter(ProducerAlias.match_key == match_form(company))} == {
            db.query(Producer.id).filter(Producer.name == company).scalar()}

        again = loader.load(db, LoadReport(title="again"))
        db.flush()
        assert again.changed == 0, again.render()
    finally:
        savepoint.rollback()
        db.expire_all()


# ── Generators and idempotency ───────────────────────────────────────────────

@needs_drop
def test_generators_ran_over_the_loaded_series(loaded):
    db = loaded["db"]
    for key in ("elec-eu", "iron-scrap-na", "brent"):
        assert db.query(IndexSeasonalFactor).filter(
            IndexSeasonalFactor.commodity_id == _series(db, key).id,
            IndexSeasonalFactor.region.is_(None)).count() == 12
    active = active_calibration(db)
    dispersions = _all_dispersions(db, min_points=DEFAULT_MIN_POINTS)
    assert active.n_series == len(dispersions) >= expect.index_counts()["series"]
    ladder = build_ladder(list(dispersions.values()), n_rungs=DEFAULT_RUNGS)
    stored = [float(b.dispersion) for b in sorted(active.breakpoints, key=lambda b: b.rung)]
    assert stored == pytest.approx([round(v, 4) for v in ladder], abs=1e-4)
    assert db.query(VolatilityCalibration).filter(
        VolatilityCalibration.is_active.is_(True)).count() == 1


@needs_drop
def test_a_second_load_changes_nothing(loaded):
    db = loaded["db"]
    active_before = active_calibration(db).id
    again = loader.load(db, LoadReport(title="again"))
    db.flush()
    assert again.changed == 0, again.render()
    assert active_calibration(db).id == active_before
