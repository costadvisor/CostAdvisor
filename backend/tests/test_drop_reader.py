"""Shared drop reader (Wave 3, SCRUM-74 unit 1).

Two halves:

* **Primitive tests** run always, on synthetic input. They pin the behaviour
  of each verified defect handler in isolation.
* **Drop tests** skip when `sample_idea/costadvisor-data/` is absent, so the
  suite still passes in a checkout without the data.

Per the drop's own README — *"Row counts will change. The shape will not.
Build against the shape, not the numbers."* — nothing here asserts a row
count. The drop tests assert invariants and cross-file relationships, which
hold at any size.
"""
from __future__ import annotations

import pytest

from app.services.drop import (
    MIDDLE_DOT,
    coalesce,
    drop_available,
    find_margin_line,
    is_blank,
    is_fixed_line,
    is_priceable,
    normalize_cell,
    normalize_object_list,
    parse_bool,
    parse_int,
    parse_number,
    proxy_status_pair,
    resolve_margin,
    unpriceable_lines,
)

needs_drop = pytest.mark.skipif(
    not drop_available(), reason="costadvisor-data drop not present in this checkout"
)


# ── Primitives ───────────────────────────────────────────────────────────────

def test_literal_middle_dot_converges_with_the_real_one():
    """Trap 2. The same combo arrives in two encodings across the drop, and
    36 formula_ids use both — so the two must normalise to one value or any
    grouping splits those formulas in half."""
    real = f"F22-LAR-EU{MIDDLE_DOT}CN"
    literal = "F22-LAR-EU\\u00b7CN"
    assert normalize_cell(literal) == real
    assert normalize_cell(real) == real
    assert normalize_cell(literal) == normalize_cell(real)


def test_na_is_north_america_not_missing():
    """Trap 1 at the primitive level: the highest-consequence silent loss in
    the whole load is "NA" being read as a null."""
    assert not is_blank("NA")
    assert normalize_cell("NA") == "NA"
    assert is_blank("") and is_blank("   ") and is_blank(None)


def test_parse_number_handles_the_drops_numeric_quirks():
    assert parse_number("−27.7") == -27.7      # trap 8: U+2212, not a hyphen
    assert parse_number("-27.7") == -27.7
    assert parse_number("12.5%") == 12.5
    assert parse_number("+3") == 3.0
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("") is None
    assert parse_number(None) is None


def test_parse_number_raises_rather_than_nulling_garbage():
    """A malformed value must never be indistinguishable from a missing one."""
    with pytest.raises(ValueError):
        parse_number("not-a-number")


def test_parse_int_rejects_non_integral():
    assert parse_int("42") == 42
    assert parse_int("") is None
    with pytest.raises(ValueError):
        parse_int("42.5")


def test_parse_bool():
    assert parse_bool("True") is True
    assert parse_bool("false") is False
    assert parse_bool("") is None
    with pytest.raises(ValueError):
        parse_bool("maybe")


def test_fixed_sentinel_is_recognised():
    """Trap 5: `fixed` is a sentinel on the margin/fixed lines, deliberately
    absent from type_codes.csv. A naive FK check calls it a broken reference."""
    assert is_fixed_line("fixed")
    assert is_fixed_line("  FIXED ")
    assert not is_fixed_line("CPO-MY")
    assert not is_fixed_line("")


def test_normalize_object_list_survives_mixed_shapes():
    """Trap 19: compliance[] is dicts + bare strings, applications[] is dicts
    + nulls. A bare string must keep its value, not be dropped."""
    out = normalize_object_list(
        [{"flag": "EUDR"}, "EPA registered (USA)", None],
        text_key="flag",
    )
    assert out == [{"flag": "EUDR"}, {"flag": "EPA registered (USA)"}]

    assert normalize_object_list(None, text_key="x") == []
    assert normalize_object_list({"a": 1}, text_key="x") == [{"a": 1}]


def test_coalesce_picks_the_first_present_name():
    """`substitution[]` states its body under `body` or `desc`; choosing one
    name silently loses the other set."""
    assert coalesce({"desc": "d"}, "body", "desc") == "d"
    assert coalesce({"body": "b", "desc": "d"}, "body", "desc") == "b"
    assert coalesce({"body": ""}, "body", "desc") is None
    assert coalesce({}, "body", "desc") is None


# ── Authority rule 1: margin ─────────────────────────────────────────────────

def _line(seq, label, weight, type_code="fixed", **extra):
    return {"seq": seq, "label": label, "weight_pct": weight,
            "type_code": type_code, **extra}


def test_margin_comes_from_the_line_not_the_header():
    """The header and the line disagree on 13.8% of combos. The line wins
    because the weights sum to exactly 100 including it — if the header were
    right, the recipe would not close."""
    combo = {"margin_pct": 16.0}
    lines = [
        _line(1, "Palm oil", 84.0, type_code="CPO-MY"),
        _line(2, "Supplier margin", 17.0),
    ]
    resolved = resolve_margin(combo, lines)
    assert resolved.margin_pct == 17.0
    assert resolved.source == "line"
    assert resolved.header_value == 16.0
    assert resolved.disagrees  # the losing value is retained, not discarded


def test_margin_falls_back_to_the_header_only_without_a_line():
    resolved = resolve_margin({"margin_pct": 12.0}, [])
    assert resolved.margin_pct == 12.0
    assert resolved.source == "header"
    assert not resolved.disagrees


def test_margin_absent_is_a_real_answer():
    """The 17 flat-shaped records carry no margin at all — None here is a
    fact about the data, not a parse failure."""
    resolved = resolve_margin({"margin_pct": ""}, [_line(1, "Feedstock", 100.0, "CPO-MY")])
    assert resolved.margin_pct is None
    assert resolved.source == "absent"


def test_margin_line_matched_by_token_not_exact_label():
    """There is no bare "Margin" literal anywhere; labels are "Supplier
    margin" plus 53 annotated variants."""
    assert find_margin_line([_line(3, "Supplier margin purity premium", 9.0)]) is not None
    assert find_margin_line([_line(3, "Performance IP margin", 9.0)]) is not None
    assert find_margin_line([_line(1, "Palm oil", 90.0, type_code="CPO-MY")]) is None


def test_an_indexed_line_mentioning_margin_is_not_a_margin_line():
    """The token match is scoped to fixed lines, so an indexed line whose
    label happens to contain the word cannot be mistaken for the margin."""
    assert find_margin_line([_line(1, "Margin-grade feedstock", 90.0, type_code="CPO-MY")]) is None


# ── Authority rule 2: proxy_status ───────────────────────────────────────────

def test_proxy_status_keeps_both_readings():
    """736 lines disagree, carrying 18% of cost weight, and neither source is
    right — the drop's own w_proxy/coverage_tier came from the line, while
    the registry is better informed elsewhere. Collapsing to one is the
    failure mode."""
    pair = proxy_status_pair(
        {"type_code": "CPO-MY", "proxy_status": "direct"},
        {"proxy_status": "proxy"},
    )
    assert pair.line == "direct"
    assert pair.registry == "proxy"
    assert not pair.agrees
    assert pair.is_proxy_either_way  # the conservative read


def test_proxy_status_agrees_when_both_say_the_same():
    pair = proxy_status_pair(
        {"type_code": "CPO-MY", "proxy_status": "proxy"}, {"proxy_status": "proxy"}
    )
    assert pair.agrees and pair.is_proxy_either_way


def test_fixed_lines_carry_no_proxy_question():
    """A fixed line has no type-code to disagree with, so it must not be
    counted as a disagreement."""
    pair = proxy_status_pair({"type_code": "fixed", "proxy_status": ""}, None)
    assert pair.line is None and pair.registry is None
    assert pair.agrees
    assert not pair.is_proxy_either_way


# ── Authority rule 3: loadable means priceable ───────────────────────────────

def test_is_priceable_reflects_resolution_not_schema_validity():
    ok = [{"resolution": "resolved"}, {"resolution": "fixed"}]
    assert is_priceable(ok)

    blocked = ok + [{"resolution": "no_series"}]
    assert not is_priceable(blocked)
    assert unpriceable_lines(blocked) == [{"resolution": "no_series"}]

    ambiguous = ok + [{"resolution": "ambiguous"}]
    assert not is_priceable(ambiguous)


def test_a_combo_with_no_lines_is_not_priceable():
    assert not is_priceable([])


# ── Against the real drop ────────────────────────────────────────────────────

@needs_drop
def test_every_registered_spec_reads_and_types():
    from app.services.drop import all_specs, read_table

    for spec in all_specs():
        rows = read_table(spec.name)
        assert rows, f"{spec.name} read empty"
        first = rows[0]
        for column in spec.key_columns:
            assert column in first, f"{spec.name} missing key column {column}"
        # Declared typed columns really are coerced (or None), never left as
        # a raw string.
        for column in spec.numeric_columns + spec.int_columns:
            for row in rows:
                assert row[column] is None or isinstance(row[column], (int, float))
        for column in spec.bool_columns:
            for row in rows:
                assert row[column] is None or isinstance(row[column], bool)


@needs_drop
def test_north_america_survives_the_read():
    """Trap 1 end to end. Asserts the value is present as a string, not that
    a particular number of rows carry it."""
    from app.services.drop import read_table

    regions = {r["source_region"] for r in read_table("index_commodities")}
    assert "NA" in regions

    feed_regions = {r["region"] for r in read_table("index_feeds")}
    assert "NA" in feed_regions


@needs_drop
def test_no_literal_escape_survives_and_ids_stay_joinable():
    """Trap 2 end to end: after normalisation the two encodings are one, and
    combos↔combo_lines join with no orphans in either direction."""
    from app.services.drop import read_table

    combos = read_table("combos")
    lines = read_table("combo_lines")

    assert not [c for c in combos if "\\u00b7" in c["combo_id"]]
    assert not [l for l in lines if "\\u00b7" in l["combo_id"]]

    combo_ids = {c["combo_id"] for c in combos}
    line_ids = {l["combo_id"] for l in lines}
    assert not line_ids - combo_ids, "combo_lines referencing an unknown combo"


@needs_drop
def test_combo_id_is_prefixed_by_its_formula_id():
    """The parse rule the loader depends on. Holds only after the middle-dot
    normalisation — which is the point."""
    from app.services.drop import read_table

    for combo in read_table("combos"):
        assert combo["combo_id"].startswith(combo["formula_id"])


@needs_drop
def test_issues_register_round_trips_against_the_manifest():
    """The cheapest possible detector for an encoding or quoting bug in the
    reader: the manifest counts the same file independently. Count-agnostic —
    it asserts the two agree, whatever they are."""
    from app.services.drop import verify_issue_summary

    report = verify_issue_summary()
    assert report["matches"], report["mismatches"]
    assert report["observed_total"] == report["declared_total"] > 0


@needs_drop
def test_is_priceable_reproduces_the_shipped_loadable_flag():
    """`loadable` is a pricing-completeness flag, not a schema one. Deriving
    it independently and matching every row is what proves that reading —
    and is why a loader must not use it as a validity gate."""
    from app.services.drop import read_table

    lines_by_combo: dict[str, list[dict]] = {}
    for line in read_table("combo_lines"):
        lines_by_combo.setdefault(line["combo_id"], []).append(line)

    for combo in read_table("combos"):
        derived = is_priceable(lines_by_combo.get(combo["combo_id"], []))
        assert derived == bool(combo["loadable"]), combo["combo_id"]


@needs_drop
def test_margin_resolves_from_the_line_wherever_one_exists():
    """Shape assertion: every combo that has a margin line takes its margin
    from that line, and the header is retained even when it disagrees."""
    from app.services.drop import read_table

    lines_by_combo: dict[str, list[dict]] = {}
    for line in read_table("combo_lines"):
        lines_by_combo.setdefault(line["combo_id"], []).append(line)

    saw_disagreement = False
    for combo in read_table("combos"):
        lines = lines_by_combo.get(combo["combo_id"], [])
        resolved = resolve_margin(combo, lines)
        if find_margin_line(lines) is not None:
            assert resolved.source == "line"
            assert resolved.margin_pct is not None
            if resolved.disagrees:
                saw_disagreement = True
                assert resolved.header_value is not None  # loser retained

    assert saw_disagreement, "expected the known header/line margin conflict"


@needs_drop
def test_weights_close_at_exactly_100_including_margin():
    """The invariant the margin rule rests on. Combos with no lines are
    excluded — they have nothing to close."""
    from app.services.drop import read_table

    lines_by_combo: dict[str, list[dict]] = {}
    for line in read_table("combo_lines"):
        lines_by_combo.setdefault(line["combo_id"], []).append(line)

    for combo_id, lines in lines_by_combo.items():
        total = sum(l["weight_pct"] or 0 for l in lines)
        assert abs(total - 100.0) < 0.01, f"{combo_id} sums to {total}"


@needs_drop
def test_resolves_to_is_blank_only_for_ambiguous():
    """Trap 12: `no_series` still names a real series — it means the target
    has no numbers, not that there is no target."""
    from app.services.drop import read_table

    for row in read_table("type_codes"):
        if is_blank(row["resolves_to"]):
            assert row["resolution"] == "ambiguous", row["type_code"]


@needs_drop
def test_decision_forms_read_as_tables():
    """They live in decisions/ rather than tables/, and the reader routes on
    the spec so a caller never has to know."""
    from app.services.drop import read_table

    regions = read_table("region_basis")
    assert regions and all("region" in r for r in regions)

    basis = read_table("index_basis")
    assert basis and all("commodity_key" in r for r in basis)
