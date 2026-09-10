"""Trigger radar (Wave 3, SCRUM-79 / MON-1).

Five feeds, one store, one output shape: a **negotiation window**.

    clause_deadline   a contract drifting toward auto-renewal past its notice date
    index_move        a driver series moving hard — grouped at the series grain
    gap               should-cost vs invoice creeping past what the team will stomach
    buy_window        the shipped cheap/expensive signal
    market_signal     a supplier announcement or disruption, live on this date

Three decisions worth stating, because getting any of them wrong is silent:

**Grouping is at the resolved-series grain, not per product per index.** Under
the three-layer model a move originates on a price series; every type code that
resolves to it inherits the move, and it lands on every cost line naming one of
those codes. One series backs roughly a quarter of the library's indexed cost
weight, so evaluating each referenced index independently — which is what
`alerts._index_move_trigger` does — produces a near-identical event across a
large slice of the book in one pass. `AlertEvent.dedup_key` dedups per
subscription/target/quarter/direction and cannot collapse across products. The
window's `driver_key` groups by driver, which is the job it exists to do.

**Coverage is tri-valued.** A comparison against a missing index value returns
falsey, so a naive threshold check reports a product whose biggest cost line has
never had a price as calm forever. A buyer told "no signal" there is worse off
than one told nothing. So the radar reports `covered` / `partial` / `unknown`
and names the unresolved type codes.

**Proxy state is read from one side.** `proxy_status` is carried both on cost
lines (`is_proxy`) and on their type-code rows, and the two disagree on a
meaningful share of indexed lines. This reads the type-code side only, so the
badge means something.

The radar never writes `cost_models.negotiation_state`. See
`SUGGESTS_NOT_SETS` below.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.alerts import AlertSubscription
from app.models.contract import Contract, ContractCostModel
from app.models.cost_model import CostModel
from app.models.index_data import CommodityIndex, IndexValue
from app.models.index_layer import TypeCode
from app.models.radar import (
    MarketSignal, NegotiationWindow, NegotiationWindowCostModel,
)
from app.services.thresholds import Threshold, effective_threshold

# How far ahead a clause deadline has to be to be worth surfacing. Wide enough
# that a quarterly review cycle cannot step over it.
CLAUSE_LOOKAHEAD_DAYS = 120

COVERAGE_COVERED = "covered"
COVERAGE_PARTIAL = "partial"
COVERAGE_UNKNOWN = "unknown"

# **Decision, written down as the ticket asks.** An opening window and
# `cost_models.negotiation_state` are the same subject from two directions: the
# window is what the data says, the flag is what the team decided. The radar
# SUGGESTS and never sets, because that flag is audit-logged human intent
# (Scrum 25) — a background job flipping it would put a decision nobody made
# into the audit trail under a real user's team. The suggestion rides on the
# window payload; a person applies it through the existing flag endpoint.
SUGGESTS_NOT_SETS = True


# ── Coverage: can we even tell whether this product moved? ───────────────────

@dataclass
class LineResolution:
    """One effective cost line, resolved as far as the index layer allows."""
    label: str
    weight: float
    commodity_id: int | None
    commodity_key: str | None
    type_code: str | None
    type_code_resolution: str | None
    via_proxy: bool | None
    resolved: bool
    reason: str | None = None


@dataclass
class ModelCoverage:
    cost_model_id: uuid.UUID
    product: str | None
    coverage: str
    lines: list[LineResolution] = field(default_factory=list)
    unresolved_codes: list[str] = field(default_factory=list)
    fallback_reason: str | None = None

    @property
    def resolved_series(self) -> set[int]:
        return {l.commodity_id for l in self.lines if l.resolved and l.commodity_id}


def _type_code_for_line(db: Session, line) -> TypeCode | None:
    """The type code behind a line, whichever way the line names one.

    A tracking line carries `type_code_id` directly. A pinned/unlinked line has
    only a commodity, so the type-code side is reached through the series —
    which is also the direction that makes the grouping work, since many codes
    resolve to one series.
    """
    if getattr(line, "type_code_id", None):
        return db.query(TypeCode).filter(TypeCode.id == line.type_code_id).first()
    if line.commodity_id:
        return (
            db.query(TypeCode)
            .filter(TypeCode.resolves_to_id == line.commodity_id)
            .order_by(TypeCode.code)
            .first()
        )
    return None


def model_coverage(db: Session, cm: CostModel) -> ModelCoverage:
    """Tri-valued coverage for one cost model, naming what could not resolve."""
    from app.services.formula_resolver import get_effective_lines

    out = ModelCoverage(
        cost_model_id=cm.id,
        product=cm.product.name if cm.product else None,
        coverage=COVERAGE_UNKNOWN,
    )
    fv = cm.current_formula
    if fv is None:
        out.fallback_reason = "no formula version"
        return out

    lines, fallback = get_effective_lines(db, fv, cm)
    out.fallback_reason = fallback

    series_keys: dict[int, str | None] = {}
    ids = {l.commodity_id for l in lines if l.commodity_id}
    if ids:
        series_keys = {
            row.id: (row.commodity_key or row.name)
            for row in db.query(CommodityIndex).filter(CommodityIndex.id.in_(ids)).all()
        }

    for line in lines:
        tc = _type_code_for_line(db, line)
        # A fixed line has nothing to resolve and is not a blind spot — it is
        # a deliberately non-indexed cost. Only an index-linked line that
        # cannot reach a series counts against coverage.
        is_index_line = line.component_type != "fixed"

        if not is_index_line:
            out.lines.append(LineResolution(
                label=line.label, weight=line.weight, commodity_id=None,
                commodity_key=None, type_code=None, type_code_resolution=None,
                via_proxy=None, resolved=True, reason="fixed line",
            ))
            continue

        unresolved_reason = None
        if tc is not None and tc.resolution in ("no_series", "ambiguous"):
            unresolved_reason = tc.resolution
        elif line.commodity_id is None:
            unresolved_reason = "index-linked line has no bound series"

        resolved = unresolved_reason is None
        if not resolved and tc is not None:
            out.unresolved_codes.append(tc.code)

        out.lines.append(LineResolution(
            label=line.label,
            weight=line.weight,
            commodity_id=line.commodity_id,
            commodity_key=series_keys.get(line.commodity_id) if line.commodity_id else None,
            type_code=tc.code if tc else None,
            type_code_resolution=tc.resolution if tc else None,
            # Type-code side only — never `line.is_proxy`.
            via_proxy=(tc.proxy_status == "proxy") if tc else None,
            resolved=resolved,
            reason=unresolved_reason,
        ))

    index_lines = [l for l in out.lines if l.reason != "fixed line"]
    if not index_lines:
        out.coverage = COVERAGE_COVERED     # an all-fixed recipe genuinely cannot move
    elif all(l.resolved for l in index_lines):
        out.coverage = COVERAGE_COVERED
    elif any(l.resolved for l in index_lines):
        out.coverage = COVERAGE_PARTIAL
    else:
        out.coverage = COVERAGE_UNKNOWN
    return out


def team_coverage(db: Session, team_id: uuid.UUID) -> list[ModelCoverage]:
    models = db.query(CostModel).filter(CostModel.team_id == team_id).all()
    return [model_coverage(db, cm) for cm in models]


# ── The index-move feed, grouped at the series grain ─────────────────────────

def _latest_two_levels(db: Session, commodity_id: int):
    """Reused verbatim from the shipped alert rule's shape — the two most recent
    quarters' level for a series, newest first."""
    rows = (
        db.query(IndexValue.year, IndexValue.quarter, func.avg(IndexValue.value))
        .filter(IndexValue.commodity_id == commodity_id)
        .group_by(IndexValue.year, IndexValue.quarter)
        .order_by(IndexValue.year.desc(), IndexValue.quarter.desc())
        .limit(2)
        .all()
    )
    return [(int(y), int(q), float(v)) for y, q, v in rows]


@dataclass
class SeriesMove:
    commodity_id: int
    commodity_key: str
    move_pct: float
    year: int
    quarter: int

    @property
    def direction(self) -> str:
        return "up" if self.move_pct > 0 else "down"


def series_move(db: Session, commodity_id: int, threshold: Threshold) -> SeriesMove | None:
    """A move on one series, or None. Returns None on missing history too — the
    *coverage* read is what reports a blind spot, so this stays a pure test of
    "did it move", never a claim that it did not."""
    levels = _latest_two_levels(db, commodity_id)
    if len(levels) < 2 or not levels[1][2]:
        return None
    (y, q, cur), (_, _, prev) = levels[0], levels[1]
    move = (cur - prev) / prev * 100
    # A currency threshold cannot be applied to an index level (base 100), so
    # the percent side is used and the window records which unit was asked for.
    if abs(move) < threshold.value:
        return None
    ci = db.query(CommodityIndex).filter(CommodityIndex.id == commodity_id).first()
    key = (ci.commodity_key or ci.name) if ci else f"series {commodity_id}"
    return SeriesMove(commodity_id, key, round(move, 2), y, q)


# ── Window upsert ────────────────────────────────────────────────────────────

@dataclass
class RadarRun:
    team_id: uuid.UUID
    opened: list[uuid.UUID] = field(default_factory=list)
    refreshed: list[uuid.UUID] = field(default_factory=list)
    closed: list[uuid.UUID] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "opened": len(self.opened),
            "refreshed": len(self.refreshed),
            "closed": len(self.closed),
        }


def _upsert_window(
    db: Session,
    run: RadarRun,
    *,
    driver: str,
    driver_key: str,
    scope_type: str,
    headline: str,
    opens_on: date,
    closes_on: date | None,
    close_basis: str,
    coverage: str,
    threshold: Threshold | None,
    evidence: dict,
    cost_models: list[tuple[uuid.UUID, float | None, bool | None]],
    scope_supplier_id: int | None = None,
    scope_contract_id: uuid.UUID | None = None,
    scope_cost_model_id: uuid.UUID | None = None,
    scope_commodity_id: int | None = None,
) -> NegotiationWindow:
    """Open a window, or refresh the one already standing for this driver.

    Refresh rather than insert: a driver that is still true next run is the
    *same* window, and a second row would double-count the same opportunity and
    re-deliver it. A dismissed window stays dismissed — a person said no.
    """
    existing = (
        db.query(NegotiationWindow)
        .filter(NegotiationWindow.team_id == run.team_id,
                NegotiationWindow.driver_key == driver_key)
        .first()
    )
    now = datetime.now(timezone.utc)

    if existing is None:
        win = NegotiationWindow(
            team_id=run.team_id, driver=driver, driver_key=driver_key,
            scope_type=scope_type, headline=headline,
            opens_on=opens_on, closes_on=closes_on, close_basis=close_basis,
            coverage=coverage,
            threshold_value=threshold.value if threshold else None,
            threshold_unit=threshold.unit if threshold else None,
            evidence=evidence, state="open", opened_at=now, last_seen_at=now,
            scope_supplier_id=scope_supplier_id, scope_contract_id=scope_contract_id,
            scope_cost_model_id=scope_cost_model_id, scope_commodity_id=scope_commodity_id,
        )
        db.add(win)
        db.flush()
        run.opened.append(win.id)
    else:
        win = existing
        if win.state == "dismissed":
            return win
        win.headline = headline
        win.closes_on = closes_on
        win.close_basis = close_basis
        win.coverage = coverage
        win.evidence = evidence
        if threshold:
            win.threshold_value = threshold.value
            win.threshold_unit = threshold.unit
        win.last_seen_at = now
        run.refreshed.append(win.id)
        db.query(NegotiationWindowCostModel).filter(
            NegotiationWindowCostModel.window_id == win.id
        ).delete(synchronize_session=False)

    for cm_id, exposure, via_proxy in cost_models:
        db.add(NegotiationWindowCostModel(
            team_id=run.team_id, window_id=win.id, cost_model_id=cm_id,
            exposure_pct=exposure, via_proxy=via_proxy,
        ))
    db.flush()
    return win


def close_elapsed_windows(db: Session, team_id: uuid.UUID, today: date) -> list[uuid.UUID]:
    """A window whose close date has passed is closed, not left standing.

    Delivery reads `state == "open"`, so this is also what makes "a window that
    has already closed does not deliver" true.
    """
    stale = (
        db.query(NegotiationWindow)
        .filter(NegotiationWindow.team_id == team_id,
                NegotiationWindow.state == "open",
                NegotiationWindow.closes_on.isnot(None),
                NegotiationWindow.closes_on < today)
        .all()
    )
    now = datetime.now(timezone.utc)
    for win in stale:
        win.state = "closed"
        win.closed_at = now
    return [w.id for w in stale]


# ── The feeds ────────────────────────────────────────────────────────────────

def _contract_deadline_for_model(db: Session, cm_id: uuid.UUID, today: date):
    """The soonest contract notice deadline covering a product, if any.

    This is what lets a gap or index-move window close on a real date instead
    of `unknown` — the product is under contract, so the negotiation window
    genuinely ends when notice can no longer be given.
    """
    row = (
        db.query(Contract)
        .join(ContractCostModel, ContractCostModel.contract_id == Contract.id)
        .filter(ContractCostModel.cost_model_id == cm_id,
                Contract.notice_deadline.isnot(None),
                Contract.notice_deadline >= today)
        .order_by(Contract.notice_deadline.asc())
        .first()
    )
    return row


def _clause_deadline_feed(db: Session, run: RadarRun, today: date, coverage_by_model: dict):
    horizon = today + timedelta(days=CLAUSE_LOOKAHEAD_DAYS)
    contracts = (
        db.query(Contract)
        .filter(Contract.team_id == run.team_id,
                Contract.notice_deadline.isnot(None),
                Contract.notice_deadline >= today,
                Contract.notice_deadline <= horizon)
        .all()
    )
    for c in contracts:
        covered = (
            db.query(ContractCostModel)
            .filter(ContractCostModel.contract_id == c.id)
            .all()
        )
        # Worst coverage across the products the contract covers: if we cannot
        # tell whether one of them is moving, the window says so.
        states = [coverage_by_model.get(cc.cost_model_id) for cc in covered]
        cov = _worst_coverage([s.coverage for s in states if s])
        unresolved = sorted({
            code for s in states if s for code in s.unresolved_codes
        })
        days = (c.notice_deadline - today).days
        supplier = c.supplier.name if c.supplier else "supplier"
        renew = "auto-renews" if c.auto_renew else "expires"
        _upsert_window(
            db, run,
            driver="clause_deadline",
            driver_key=f"clause_deadline:contract:{c.id}:{c.notice_deadline.isoformat()}",
            scope_type="contract",
            scope_contract_id=c.id,
            scope_supplier_id=c.supplier_id,
            headline=(
                f"{supplier} contract {c.reference or ''}".strip()
                + f" {renew} on {c.term_end.isoformat() if c.term_end else 'an unstated date'};"
                f" notice must be given by {c.notice_deadline.isoformat()} ({days} days)."
            ),
            opens_on=today,
            closes_on=c.notice_deadline,
            close_basis="clause_deadline",
            coverage=cov,
            threshold=None,           # a date is not a threshold
            evidence={
                "contract_id": str(c.id),
                "reference": c.reference,
                "term_start": c.term_start.isoformat() if c.term_start else None,
                "term_end": c.term_end.isoformat() if c.term_end else None,
                "auto_renew": c.auto_renew,
                "notice_days": c.notice_days,
                "notice_deadline": c.notice_deadline.isoformat(),
                "days_remaining": days,
                "price_review_cadence": c.price_review_cadence,
                "unresolved_type_codes": unresolved,
                "suggested_negotiation_state": "in_negotiation",
            },
            cost_models=[(cc.cost_model_id, float(cc.share_pct) if cc.share_pct else None, None)
                         for cc in covered],
        )


def _worst_coverage(states: list[str]) -> str:
    if not states:
        return COVERAGE_COVERED
    if COVERAGE_UNKNOWN in states:
        return COVERAGE_UNKNOWN
    if COVERAGE_PARTIAL in states:
        return COVERAGE_PARTIAL
    return COVERAGE_COVERED


def _index_move_feed(db: Session, run: RadarRun, today: date, threshold: Threshold,
                     coverage_by_model: dict):
    """One window per moving series, with every affected product attached.

    This is the collapse the ticket is about: the loop is over *series*, not
    over (product x index) pairs.
    """
    # series -> [(cost_model_id, weight share, via_proxy)]
    exposure: dict[int, list[tuple[uuid.UUID, float, bool | None]]] = {}
    codes_per_series: dict[int, set[str]] = {}
    for cov in coverage_by_model.values():
        for line in cov.lines:
            if not line.resolved or not line.commodity_id:
                continue
            exposure.setdefault(line.commodity_id, []).append(
                (cov.cost_model_id, round(line.weight * 100, 2), line.via_proxy)
            )
            if line.type_code:
                codes_per_series.setdefault(line.commodity_id, set()).add(line.type_code)

    for commodity_id, rows in exposure.items():
        move = series_move(db, commodity_id, threshold)
        if move is None:
            continue
        affected_ids = {r[0] for r in rows}
        cov = _worst_coverage([
            c.coverage for cm_id, c in coverage_by_model.items() if cm_id in affected_ids
        ])
        unresolved = sorted({
            code
            for cm_id, c in coverage_by_model.items() if cm_id in affected_ids
            for code in c.unresolved_codes
        })
        # A single close date is the quarter the move landed in — honest, and
        # not a synthesised forecast turn. Forward-looking closes need forecast
        # storage; until then the basis says what it is.
        _upsert_window(
            db, run,
            driver="index_move",
            driver_key=(
                f"index_move:series:{move.commodity_key}:"
                f"{move.year}Q{move.quarter}:{move.direction}"
            ),
            scope_type="commodity",
            scope_commodity_id=commodity_id,
            headline=(
                f"{move.commodity_key} moved {move.move_pct:+.1f}% in "
                f"Q{move.quarter} {move.year} — {len(affected_ids)} product(s) exposed."
            ),
            opens_on=today,
            closes_on=None,
            close_basis="unknown",
            coverage=cov,
            threshold=threshold,
            evidence={
                "series": move.commodity_key,
                "commodity_id": commodity_id,
                "move_pct": move.move_pct,
                "direction": move.direction,
                "year": move.year,
                "quarter": move.quarter,
                # The point of the grain: many labels, one driver.
                "type_codes_resolving_here": sorted(codes_per_series.get(commodity_id, ())),
                "resolution_path": [
                    {
                        "cost_model_id": str(cm_id),
                        "exposure_pct": exp,
                        "via_proxy": proxy,
                    }
                    for cm_id, exp, proxy in rows
                ],
                "unresolved_type_codes": unresolved,
                "close_basis_note": (
                    "no forecast storage consumed yet — a forward-looking close "
                    "is not synthesised"
                ),
                "suggested_negotiation_state": (
                    "in_negotiation" if move.direction == "up" else "under_review"
                ),
            },
            cost_models=list(rows),
        )


def _gap_feed(db: Session, run: RadarRun, today: date, threshold: Threshold,
              coverage_by_model: dict):
    """Consumes the shipped gap rule; does not restate the arithmetic."""
    from app.services.alerts import _gap_trigger

    for cm in db.query(CostModel).filter(CostModel.team_id == run.team_id).all():
        trig = _gap_trigger(db, cm, threshold.value)
        if trig is None:
            continue
        detail = trig["detail"]
        cov = coverage_by_model.get(cm.id)
        contract = _contract_deadline_for_model(db, cm.id, today)
        closes_on = contract.notice_deadline if contract else None
        _upsert_window(
            db, run,
            driver="gap",
            driver_key=f"gap:cost_model:{cm.id}:{detail['year']}Q{detail['quarter']}",
            scope_type="cost_model",
            scope_cost_model_id=cm.id,
            scope_supplier_id=cm.supplier_id,
            headline=trig["message"],
            opens_on=today,
            closes_on=closes_on,
            # A gap window on a product under contract genuinely ends when
            # notice can no longer be given.
            close_basis="clause_deadline" if contract else "unknown",
            coverage=cov.coverage if cov else COVERAGE_UNKNOWN,
            threshold=threshold,
            evidence={
                **detail,
                # Both sides are money here, so a currency threshold is
                # meaningful — unlike on the index feed.
                "threshold_unit_applicable": True,
                "contract_id": str(contract.id) if contract else None,
                "unresolved_type_codes": cov.unresolved_codes if cov else [],
                "suggested_negotiation_state": "in_negotiation",
            },
            cost_models=[(cm.id, None, None)],
        )


def _buy_window_feed(db: Session, run: RadarRun, today: date, coverage_by_model: dict):
    """Consumes the shipped `_buy_signal`; not rebuilt."""
    from app.services.alerts import _buy_window_trigger

    for cm in db.query(CostModel).filter(CostModel.team_id == run.team_id).all():
        trig = _buy_window_trigger(db, cm)
        if trig is None:
            continue
        detail = trig["detail"]
        cov = coverage_by_model.get(cm.id)
        contract = _contract_deadline_for_model(db, cm.id, today)
        _upsert_window(
            db, run,
            driver="buy_window",
            driver_key=f"buy_window:cost_model:{cm.id}:{detail['signal']}",
            scope_type="cost_model",
            scope_cost_model_id=cm.id,
            scope_supplier_id=cm.supplier_id,
            headline=trig["message"],
            opens_on=today,
            closes_on=contract.notice_deadline if contract else None,
            close_basis="clause_deadline" if contract else "unknown",
            coverage=cov.coverage if cov else COVERAGE_UNKNOWN,
            threshold=None,       # the buy signal carries its own ±3% band
            evidence={
                **detail,
                "unresolved_type_codes": cov.unresolved_codes if cov else [],
                "suggested_negotiation_state": (
                    "in_negotiation" if detail["signal"] == "cheap" else "under_review"
                ),
            },
            cost_models=[(cm.id, None, None)],
        )


def _market_signal_feed(db: Session, run: RadarRun, today: date, coverage_by_model: dict):
    """Live signals — the team's own plus the platform feed."""
    signals = (
        db.query(MarketSignal)
        .filter((MarketSignal.team_id == run.team_id) | (MarketSignal.team_id.is_(None)))
        .filter(MarketSignal.as_of_date <= today)
        .all()
    )
    for sig in signals:
        if not sig.is_live(today):
            continue
        # Products the signal touches: by supplier, or by the series it names.
        affected: list[tuple[uuid.UUID, float | None, bool | None]] = []
        if sig.supplier_id:
            affected = [
                (cm.id, None, None)
                for cm in db.query(CostModel).filter(
                    CostModel.team_id == run.team_id,
                    CostModel.supplier_id == sig.supplier_id,
                ).all()
            ]
        elif sig.commodity_id:
            for cov in coverage_by_model.values():
                for line in cov.lines:
                    if line.commodity_id == sig.commodity_id:
                        affected.append((cov.cost_model_id, round(line.weight * 100, 2),
                                         line.via_proxy))
                        break
        _upsert_window(
            db, run,
            driver="market_signal",
            driver_key=f"market_signal:{sig.id}",
            scope_type="supplier" if sig.supplier_id else (
                "commodity" if sig.commodity_id else "portfolio"),
            scope_supplier_id=sig.supplier_id,
            scope_commodity_id=sig.commodity_id,
            headline=sig.headline,
            opens_on=max(sig.as_of_date, today) if sig.as_of_date > today else today,
            closes_on=sig.expires_at,
            close_basis="signal_expiry" if sig.expires_at else "unknown",
            coverage=_worst_coverage([
                coverage_by_model[cm_id].coverage
                for cm_id, _, _ in affected if cm_id in coverage_by_model
            ]),
            threshold=None,
            evidence={
                "signal_id": str(sig.id),
                "signal_type": sig.signal_type,
                # Where it came from matters: a manually entered signal is an
                # analyst's judgement, an imported one is editorial copy whose
                # date was synthesised.
                "origin": sig.origin,
                "as_of_date": sig.as_of_date.isoformat(),
                "as_of_inferred": sig.as_of_inferred,
                "expires_at": sig.expires_at.isoformat() if sig.expires_at else None,
                "body": sig.body,
                "source_url": sig.source_url,
                "suggested_negotiation_state": "under_review",
            },
            cost_models=affected,
        )


# ── Entry point ──────────────────────────────────────────────────────────────

def run_radar(db: Session, team_id: uuid.UUID, today: date | None = None) -> RadarRun:
    """Evaluate every feed for a team and reconcile its windows.

    Commits. Never touches `cost_models.negotiation_state` — see
    `SUGGESTS_NOT_SETS`.
    """
    today = today or datetime.now(timezone.utc).date()
    run = RadarRun(team_id=team_id)

    coverage_by_model = {c.cost_model_id: c for c in team_coverage(db, team_id)}
    threshold = effective_threshold(db, team_id)

    _clause_deadline_feed(db, run, today, coverage_by_model)
    _index_move_feed(db, run, today, threshold, coverage_by_model)
    _gap_feed(db, run, today, threshold, coverage_by_model)
    _buy_window_feed(db, run, today, coverage_by_model)
    _market_signal_feed(db, run, today, coverage_by_model)

    run.closed = close_elapsed_windows(db, team_id, today)
    db.commit()
    return run


def open_windows_for_subscription(
    db: Session, sub: AlertSubscription
) -> list[NegotiationWindow]:
    """The windows one subscription should hear about.

    Only `state == "open"` — a closed or dismissed window does not deliver.
    """
    q = (
        db.query(NegotiationWindow)
        .filter(NegotiationWindow.team_id == sub.team_id,
                NegotiationWindow.state == "open")
    )
    if sub.contract_id:
        q = q.filter(NegotiationWindow.scope_contract_id == sub.contract_id)
    elif sub.supplier_id:
        q = q.filter(NegotiationWindow.scope_supplier_id == sub.supplier_id)
    elif sub.cost_model_id:
        # A product-scoped subscription wants windows about that product,
        # including a series-scoped window that reaches it — which is exactly
        # what the grouping made possible.
        q = q.join(
            NegotiationWindowCostModel,
            NegotiationWindowCostModel.window_id == NegotiationWindow.id,
        ).filter(NegotiationWindowCostModel.cost_model_id == sub.cost_model_id)
    elif sub.commodity_id:
        q = q.filter(NegotiationWindow.scope_commodity_id == sub.commodity_id)
    return q.order_by(NegotiationWindow.opened_at.desc()).all()
