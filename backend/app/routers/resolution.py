"""Resolution + concentration query API (Wave 3, SCRUM-74).

The questions the three-layer index model exists to make answerable:

    GET /api/resolution/type-codes/{code}            what does this resolve to
    GET /api/resolution/series/{commodity_key}        what depends on this series
    GET /api/resolution/concentration                 the library-wide ranking
    GET /api/resolution/unpriceable                   what cannot be costed, by reason
    GET /api/resolution/combos/{template_id}/{region} why can't this combo be costed

**Platform-grain, deliberately.** `GET /api/indexes/{id}/impact` is the nearest
existing read, but it takes a `team_id` and walks that team's cost models — it
answers "which of *my* cost models use this index". These answer "what does the
platform library depend on": a different question over a different join, so
they live on their own surface rather than as a mode of that one.

Consequently there is no `team_id` parameter and no team gate: every response
is platform reference data with no tenant rows in it. Authentication is still
required, matching the other platform index reads (e.g. the projection
endpoints from Scrum 21).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.resolution import (
    ComboDiagnosisOut,
    ConcentrationOut,
    SeriesDependentsOut,
    SwapBacklogOut,
    TypeCodeChainOut,
    TypeCodeValueOut,
    UnpriceableOut,
)
from app.services.proxy_derivation import swap_backlog, type_code_value
from app.services.resolution import (
    concentration,
    diagnose_combo,
    resolve_type_code,
    series_dependents,
    unpriceable_type_codes,
)

router = APIRouter()


@router.get("/type-codes/{code}", response_model=TypeCodeChainOut)
def get_type_code_chain(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The full chain for one type code: what it resolves to, through which
    cards, whether that is a proxy, and whether it can currently be priced.

    Works for every resolution state — a code resolving through a proxy, one
    whose series has no numbers (`no_series`), and one that resolves to nothing
    at all (`ambiguous`). The last two are never collapsed: `no_series` needs a
    feed bought, `ambiguous` needs somebody to decide what the code means.
    """
    chain = resolve_type_code(db, code)
    if chain is None:
        raise HTTPException(status_code=404, detail=f"Unknown type code: {code}")
    return TypeCodeChainOut(**chain)


@router.get("/series/{commodity_key}", response_model=SeriesDependentsOut)
def get_series_dependents(
    commodity_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Which type codes resolve to this series, what share of indexed cost
    weight they carry, and which catalog lines would break if it were
    withdrawn or re-sourced.

    The ticket poses those as two questions; they are one join read two ways —
    the set of dependents IS the blast radius — so they are answered together
    rather than by two endpoints whose numbers could drift apart.
    """
    result = series_dependents(db, commodity_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown series: {commodity_key}")
    return SeriesDependentsOut(**result)


@router.get("/concentration", response_model=ConcentrationOut)
def get_concentration(
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Series ranked by the indexed cost weight funnelling into them.

    This is the reading the layer was built for: a cost breakdown that looks
    diversified can be one commodity reached through dozens of separate codes.
    SCRUM-71 rolls index exposure up through this rather than walking the
    chain itself.
    """
    return ConcentrationOut(**concentration(db, limit=limit))


@router.get("/unpriceable", response_model=UnpriceableOut)
def get_unpriceable(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every type code that cannot produce a number, grouped by why — and the
    cost weight sitting behind each group.

    Grouped rather than totalled because the three reasons need three
    different actions, and the weight is what makes the sourcing decision
    rankable. SCRUM-80's swap backlog builds on this.
    """
    return UnpriceableOut(**unpriceable_type_codes(db))


@router.get("/combos/{template_id}/{region}", response_model=ComboDiagnosisOut)
def get_combo_diagnosis(
    template_id: uuid.UUID,
    region: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Why this combo cannot be costed — naming the specific lines and the
    specific reason for each, not a bare unpriceable flag.

    Answers over the line -> type code -> series chain. A combo whose lines do
    not yet carry a type-code link reports that explicitly rather than reading
    as healthy: an empty blocker list would otherwise be indistinguishable
    from "nothing to analyse".
    """
    diagnosis = diagnose_combo(db, template_id, region)
    return ComboDiagnosisOut(
        template_id=diagnosis.template_id,
        template_code=diagnosis.template_code,
        region=diagnosis.region,
        coverage_exists=diagnosis.coverage_exists,
        priceable=diagnosis.priceable,
        reason=diagnosis.reason,
        blocking_lines=[vars(b) for b in diagnosis.blocking_lines],
        blocked_weight_pct=diagnosis.blocked_weight_pct,
        total_lines=diagnosis.total_lines,
        type_coded_lines=diagnosis.type_coded_lines,
    )


# ── Derivation + swap backlog (SCRUM-80 / FD-1) ──────────────────────────────

@router.get("/type-codes/{code}/value", response_model=TypeCodeValueOut)
def get_type_code_value(
    code: str,
    year: int = Query(...),
    quarter: int = Query(..., ge=1, le=4),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A number for a type code at a period, with the account of where it came
    from — or an explicit unresolvable state.

    Three things this never does: return a null that could read as zero, carry
    a value forward without saying so, or hand back a derived number that looks
    observed. A code that cannot be priced names the series it wanted, because
    "we need this feed and do not have it" is actionable and a bare null is not.
    """
    result = type_code_value(db, code, year, quarter)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown type code: {code}")
    return TypeCodeValueOut(
        code=result.code,
        resolution=result.resolution,
        resolvable=result.resolvable,
        value=result.value,
        provenance=None if result.provenance is None else vars(result.provenance),
        wanted_series=result.wanted_series,
        ideal_index=result.ideal_index,
        swap_priority=result.swap_priority,
        unresolvable_reason=result.unresolvable_reason,
    )


@router.get("/swap-backlog", response_model=SwapBacklogOut)
def get_swap_backlog(
    priority: str | None = Query(None, description="Filter to one A/B/C rank"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sourcing candidates ranked by the cost weight actually behind them.

    `swap_priority` is a backlog rank, not an accuracy score — A means a better
    index exists and buying it improves the number overnight, C means the proxy
    already is the right index. Ranking by live catalog weight is what makes an
    A carrying a lot of weight sort above an A carrying almost none.
    """
    backlog = swap_backlog(db, priority=priority, limit=limit)
    return SwapBacklogOut(
        total_catalog_weight=backlog.total_catalog_weight,
        entries=[vars(e) for e in backlog.entries],
    )
