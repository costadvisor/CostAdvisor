"""Index layer loader v2 (Wave 3, SCRUM-74).

Loads the three-layer index model from the 2026-07 drop, idempotently, with a
per-table diff report naming inserts, updates and skips-with-reasons.

Load order follows the FK dependencies:

    commodity_indexes  (the price series)
        -> type_codes           (resolves_to a series)
        -> index_cards          (displays a series)
        -> index_monthly_values (the numbers)
    drop_issues                 (the delivered defect register)

**The drop's series become new rows; existing indexes are left alone.**
Nothing is matched by name. It is tempting — our "Brent" and the drop's
`brent` look like the same thing — but the two vocabularies are scoped
differently: ours is region-agnostic with the region on `index_values`
(Scrum 57), while the drop bakes region into the key (`ammonia-eu`,
`ammonia-in`, `ammonia-mea`). A name match would collapse three regional
series onto one row and silently repoint every cost model referencing it. So
the drop's series load as their own population, identified by
`commodity_key IS NOT NULL`, and the existing costing path is untouched.

Idempotency is by comparison, not truncate-and-reload: a second run reads
what is there, finds every field equal, and reports zero changes.

**This function never commits.** The caller owns the transaction, which is
also all a dry run is — do the work, read the report, roll back. That keeps
the loader free of `if dry_run` branches, so the reported diff is always the
diff the database actually produced rather than a prediction of it.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.drop_issue import DropIssueRecord
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard, IndexMonthlyValue, TypeCode
from app.services.drop import issues as drop_issues
from app.services.drop.normalize import is_blank
from app.services.drop.reader import read_table
from app.services.drop.report import LoadReport, TableDiff

# The drop states index levels against a fixed anchor: January 2023 = 100,
# verified exactly on every series that has history. It is not a column in the
# source, so it is recorded here rather than inferred per row.
#
# The forecast-only series have no 2023 row at all, so their base is genuinely
# undefined — they get NULL rather than a borrowed anchor, because claiming a
# base the data cannot support would make their levels look comparable to the
# others when they are not.
BASE_PERIOD = "2023-01"

_UNSET = object()


def _apply(obj, field_name: str, value, changes: list) -> None:
    """Set a field only when it actually differs, recording that it did.

    Numeric columns come back from Postgres as Decimal, so an unchanged row
    would otherwise read as an update on every run and idempotency would
    never hold.
    """
    current = getattr(obj, field_name, _UNSET)
    if current is _UNSET:
        return
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and current is not None
        and not isinstance(current, bool)
    ):
        try:
            if float(current) == float(value):
                return
        except (TypeError, ValueError):
            pass
    elif current == value:
        return
    setattr(obj, field_name, value)
    changes.append(field_name)


def _clean(value):
    return None if is_blank(value) else value


# ── 1. The price series ──────────────────────────────────────────────────────

def _series_fields(row: dict) -> dict:
    # Base period only where there is history to anchor it to.
    has_series = bool(row.get("has_series"))
    return {
        "value_kind": _clean(row.get("value_kind")),
        "base_period": BASE_PERIOD if has_series else None,
        # The source's own declared region — informational. IndexCard.region is
        # authoritative, because the series key is not a reliable region
        # indicator (`-ppi`/`-wb`/`-mb` are data sources).
        "source_region": _clean(row.get("source_region")),
        "provider": _clean(row.get("agency")),
        "unit": _clean(row.get("unit") or row.get("label_unit")),
        "currency": _clean(row.get("currency")),
        "category": _clean(row.get("category")),
        "source_url": _clean(row.get("source_url")),
        "quoted_incoterm": _clean(row.get("quoted_incoterm")),
        "quoted_named_place": _clean(row.get("quoted_named_place")),
    }


def _load_series(db: Session) -> tuple[TableDiff, dict[str, int]]:
    """Upsert by `commodity_key`. Returns the diff plus a key -> id map for
    the layers that reference it."""
    diff = TableDiff("commodity_indexes")

    existing = {
        ci.commodity_key: ci
        for ci in db.query(CommodityIndex).filter(CommodityIndex.commodity_key.isnot(None))
    }
    # `name` is UNIQUE, so a drop key colliding with an unrelated index must be
    # reported rather than allowed to raise mid-load.
    taken_names = {
        name
        for (name,) in db.query(CommodityIndex.name).filter(
            CommodityIndex.commodity_key.is_(None)
        )
    }

    for row in read_table("index_commodities"):
        key = row["commodity_key"]
        if is_blank(key):
            diff.skipped.append(("(blank)", "row has no commodity_key"))
            continue

        series = existing.get(key)
        if series is None:
            if key in taken_names:
                diff.skipped.append(
                    (key, f"an existing index is already named {key!r} — unique name conflict")
                )
                continue
            series = CommodityIndex(
                # The stable source key doubles as the name: `display_name`
                # runs to 67 chars against a 64-char column, and the
                # human-facing label belongs on the card anyway.
                name=key,
                commodity_key=key,
                # Not scraped by us — these values arrive with the drop.
                scrape_enabled=False,
                **_series_fields(row),
            )
            db.add(series)
            existing[key] = series
            diff.created += 1
            continue

        changes: list = []
        for name, value in _series_fields(row).items():
            _apply(series, name, value, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    # IDs for the dependent layers.
    db.flush()
    return diff, {key: series.id for key, series in existing.items()}


# ── 2. The resolution join ───────────────────────────────────────────────────

def _load_type_codes(db: Session, key_to_id: dict[str, int]) -> TableDiff:
    diff = TableDiff("type_codes")
    existing = {tc.code: tc for tc in db.query(TypeCode)}

    for row in read_table("type_codes"):
        code = row["type_code"]
        if is_blank(code):
            diff.skipped.append(("(blank)", "row has no type_code"))
            continue

        resolution = row["resolution"]
        target_key = row["resolves_to"]

        if is_blank(target_key):
            # Only `ambiguous` may lack a target. The DB CHECK would refuse
            # anything else anyway; catching it here names the offending row
            # instead of failing the whole transaction.
            if resolution != "ambiguous":
                diff.skipped.append(
                    (code, f"no resolves_to but resolution is {resolution!r}, not 'ambiguous'")
                )
                continue
            resolves_to_id = None
        else:
            resolves_to_id = key_to_id.get(target_key)
            if resolves_to_id is None:
                diff.skipped.append(
                    (code, f"resolves_to {target_key!r} is not a loaded series")
                )
                continue

        fields = {
            "label": _clean(row.get("label")),
            "resolves_to_id": resolves_to_id,
            "resolution": resolution,
            # The REGISTRY reading. combo_lines carries a second, disagreeing
            # one; both are kept rather than adjudicated here — see
            # services/drop/authority.py for the evidence.
            "proxy_status": _clean(row.get("proxy_status")),
            # A/B/C preserved as itself. The pre-drop seeder folded A into
            # `good_proxy` and everything else into `weak_proxy`, losing the
            # B-vs-C distinction and reading a C-ranked code — one already
            # correct by design — as a rough approximation.
            "swap_priority": _clean(row.get("swap_priority")),
            # Free prose naming a series we do not have. Never an FK: none of
            # its values correspond to a commodity_key, and the set of
            # non-null values IS the sourcing backlog.
            "ideal_index": _clean(row.get("ideal_index")),
            "registry_note": _clean(row.get("registry_note")),
            "source_n_formulas": row.get("n_formulas"),
            "source_n_lines": row.get("n_lines"),
            "source_total_weight": row.get("total_weight"),
        }

        tc = existing.get(code)
        if tc is None:
            db.add(TypeCode(code=code, **fields))
            diff.created += 1
            continue

        changes: list = []
        for name, value in fields.items():
            _apply(tc, name, value, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    db.flush()
    return diff


# ── 3. The display layer ─────────────────────────────────────────────────────

def _load_cards(db: Session, key_to_id: dict[str, int]) -> TableDiff:
    diff = TableDiff("index_cards")
    existing = {c.feed_key: c for c in db.query(IndexCard)}

    for row in read_table("index_feeds"):
        feed_key = row["feed_key"]
        if is_blank(feed_key):
            diff.skipped.append(("(blank)", "row has no feed_key"))
            continue

        commodity_id = key_to_id.get(row["series_key"])
        if commodity_id is None:
            diff.skipped.append(
                (feed_key, f"series_key {row['series_key']!r} is not a loaded series")
            )
            continue

        # NOT loaded, by design: current_value, change_pct, volatility_pct,
        # cycle_pct, card_status, has_intel_block — all recompute from the
        # series, and volatility_pct is internally contradictory in the source
        # (the same series carries 12 on one card and 55 on another), so
        # importing it would enshrine a conflict. shares_series_with is
        # likewise skipped: it derives from grouping cards by commodity_id.
        fields = {
            "feed_slug": row["feed_slug"],
            "commodity_id": commodity_id,
            # Raw source vocabulary, deliberately unmapped: `multi` and
            # `Global` are not regions, and mapping onto our region table is a
            # decision-form dependency rather than something to guess.
            "region": _clean(row.get("region")),
            "region_label": _clean(row.get("region_label")),
            "name": _clean(row.get("name")),
            "unit": _clean(row.get("unit")),
            "incoterm": _clean(row.get("incoterm")),
            "named_place": _clean(row.get("named_place")),
            "category": _clean(row.get("category")),
            "access": _clean(row.get("access")),
            "frequency": _clean(row.get("frequency")),
            "is_default_region": row.get("is_default_region"),
            "agency": _clean(row.get("agency")),
            "source_freq": _clean(row.get("source_freq")),
            "sourcing_note": _clean(row.get("sourcing_note")),
            "source_note": _clean(row.get("source_note")),
            "used_in_formulas": row.get("used_in_formulas"),
        }

        card = existing.get(feed_key)
        if card is None:
            db.add(IndexCard(feed_key=feed_key, **fields))
            diff.created += 1
            continue

        changes: list = []
        for name, value in fields.items():
            _apply(card, name, value, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    db.flush()
    return diff


# ── 4. The numbers ───────────────────────────────────────────────────────────

def _load_monthly(db: Session, key_to_id: dict[str, int]) -> TableDiff:
    diff = TableDiff("index_monthly_values")
    existing = {
        (v.commodity_id, v.year, v.month): v for v in db.query(IndexMonthlyValue)
    }

    for row in read_table("index_series"):
        series_key = row["series_key"]
        commodity_id = key_to_id.get(series_key)
        year, month = row["year"], row["month"]
        if commodity_id is None:
            diff.skipped.append(
                (f"{series_key} {year}-{month}",
                 f"series_key {series_key!r} is not a loaded series")
            )
            continue

        current = existing.get((commodity_id, year, month))
        if current is None:
            db.add(IndexMonthlyValue(
                commodity_id=commodity_id, year=year, month=month,
                value=row["value"],
                # actual | forecast, carried in-band exactly as stated. The
                # source README is explicit the two must never share an
                # average, so it is NOT NULL and every aggregate filters on it.
                kind=row["kind"],
            ))
            diff.created += 1
            continue

        changes: list = []
        _apply(current, "value", row["value"], changes)
        _apply(current, "kind", row["kind"], changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    db.flush()
    return diff


# ── 5. The delivered defect register ─────────────────────────────────────────

def _load_issues(db: Session) -> TableDiff:
    """Carried through, never recomputed. The register mirrors the current
    drop, so a finding the source has dropped is deleted here too rather than
    lingering as a stale warning."""
    diff = TableDiff("drop_issues")

    desired = {
        (i.table, i.key, i.column, i.problem): i for i in drop_issues.load_issues()
    }
    existing = {
        (r.source_table, r.source_key, r.source_column, r.problem): r
        for r in db.query(DropIssueRecord)
    }

    for key, issue in desired.items():
        record = existing.get(key)
        if record is None:
            db.add(DropIssueRecord(
                source_table=issue.table, source_key=issue.key,
                source_column=issue.column, problem=issue.problem,
                awaiting_decision=issue.awaiting_decision, blocking=issue.blocking,
            ))
            diff.created += 1
            continue

        changes: list = []
        _apply(record, "awaiting_decision", issue.awaiting_decision, changes)
        _apply(record, "blocking", issue.blocking, changes)
        diff.updated += 1 if changes else 0
        diff.unchanged += 0 if changes else 1

    for key, record in existing.items():
        if key not in desired:
            db.delete(record)
            diff.deleted += 1

    db.flush()
    return diff


# ── Entry point ──────────────────────────────────────────────────────────────

def load_index_layer(db: Session) -> LoadReport:
    """Load every index layer from the drop.

    Does not commit. A dry run is this call followed by a rollback — which is
    why there are no `if dry_run` branches inside: the report always describes
    what the database actually did.

    Verifies the issue register against the drop's own count of it first. The
    manifest counts `_issues.csv` independently, so a mismatch means the
    reader is losing or inventing rows — worth knowing before writing
    anything, and cheap because it needs no knowledge of what the rows mean.
    """
    check = drop_issues.verify_issue_summary()
    if not check["matches"]:
        raise ValueError(
            "_issues.csv does not match the manifest's own count of it — the "
            f"reader may be dropping rows: {check['mismatches']}"
        )

    report = LoadReport(title="Index layer")
    series_diff, key_to_id = _load_series(db)
    report.tables.append(series_diff)
    report.tables.append(_load_type_codes(db, key_to_id))
    report.tables.append(_load_cards(db, key_to_id))
    report.tables.append(_load_monthly(db, key_to_id))
    report.tables.append(_load_issues(db))
    return report
