"""Scrum 30 — matching a price-list row to a cost model, and committing it.

The parse is not the hard part: `quote_extraction.extract_quote` already does
tables-with-a-full-text-fallback, per-field confidence and locators, and is
reused here unchanged. The hard part is that an ActualPrice is keyed on
(cost_model, year, quarter), so before a row can land it needs two things the
document does not reliably state: WHICH of this team's products it is about,
and WHICH period it applies to.

Both are proposed here and neither is assumed. An unmatched row stays
unmatched; a row with no date stays period-less. Those are answers, and a
reviewer can give the missing one — whereas a guess that lands silently on the
wrong product corrupts a gap with nothing on screen to show for it.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy.orm import Session, joinedload

from app.models.cost_model import CostModel
from app.models.price_data import ActualPrice
from app.models.price_list import (
    MATCH_AMBIGUOUS, MATCH_EXACT, MATCH_FUZZY, MATCH_UNMATCHED,
    PriceListRow, PriceListRun,
)
from app.models.product import Product

# A fuzzy match has to be clearly better than the runner-up to be a match at
# all. Two products scoring 0.81 and 0.79 is not an 0.81 match, it is a
# question — so the gap matters as much as the score.
FUZZY_MIN_SCORE = 0.72
AMBIGUITY_MARGIN = 0.06
MAX_CANDIDATES = 5


def _norm(text: str | None) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed.

    Product names in a price list carry grade suffixes and punctuation the
    catalogue does not ("AE-40 (50% solids)" vs "Acrylic Emulsion AE-40"), so
    comparing raw strings matches almost nothing.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t}


def _score(extracted: str, candidate_name: str, candidate_code: str | None) -> float:
    """0..1. A code appearing as a whole token is treated as strong evidence.

    Price lists lead with the code far more often than the full name, and a
    code is short enough that raw string similarity underrates it badly —
    "AE-40" against "Acrylic Emulsion AE-40" scores about 0.35 on characters
    while being an unambiguous identification.
    """
    ex_norm = _norm(extracted)
    if not ex_norm:
        return 0.0

    best = SequenceMatcher(None, ex_norm, _norm(candidate_name)).ratio()

    code_norm = _norm(candidate_code)
    # Word-boundary substring, NOT set membership: normalising "AE-40" turns the
    # hyphen into a space, so the code is two tokens and a single-token check
    # never fires. Hyphenated codes are the norm, so that check would have
    # matched almost nothing while looking like it worked.
    if code_norm and f" {code_norm} " in f" {ex_norm} ":
        best = max(best, 0.95)

    # Token containment: every word of the candidate's name present in the
    # extracted string (in any order) is a match the character ratio misses
    # when the list adds trailing detail.
    cand_tokens = _tokens(candidate_name)
    if cand_tokens and cand_tokens <= _tokens(extracted):
        best = max(best, 0.9)

    return best


def candidate_cost_models(db: Session, team_id: uuid.UUID, supplier_id: int | None) -> list[CostModel]:
    q = (
        db.query(CostModel)
        .options(joinedload(CostModel.product), joinedload(CostModel.supplier))
        .filter(CostModel.team_id == team_id)
    )
    if supplier_id is not None:
        q = q.filter(CostModel.supplier_id == supplier_id)
    return q.all()


def match_row(extracted_name: str | None, candidates: list[CostModel]) -> tuple[uuid.UUID | None, str, list[dict]]:
    """(matched_cost_model_id, confidence, ranked candidates).

    Returns a candidate list even when it matched, so a reviewer disagreeing
    with an exact match has somewhere to go without re-querying.
    """
    if not extracted_name or not candidates:
        return None, MATCH_UNMATCHED, []

    ex_norm = _norm(extracted_name)
    scored: list[tuple[float, CostModel, bool]] = []
    for cm in candidates:
        product: Product | None = cm.product
        if product is None:
            continue
        exact = ex_norm == _norm(product.name) or (
            bool(product.formula) and ex_norm == _norm(product.formula)
        )
        scored.append((1.0 if exact else _score(extracted_name, product.name, product.formula), cm, exact))

    if not scored:
        return None, MATCH_UNMATCHED, []

    scored.sort(key=lambda t: t[0], reverse=True)
    ranked = [
        {
            "cost_model_id": str(cm.id),
            "product_name": cm.product.name if cm.product else None,
            "product_reference": cm.product.formula if cm.product else None,
            "supplier_name": cm.supplier.name if cm.supplier else None,
            "region": cm.region,
            "score": round(score, 3),
        }
        for score, cm, _ in scored[:MAX_CANDIDATES]
    ]

    top_score, top_cm, top_exact = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    if top_exact:
        # An exact tie means two of this team's cost models genuinely carry the
        # same product name — the document cannot tell them apart and neither
        # can we, so it is a question rather than a coin flip.
        if len(scored) > 1 and scored[1][2]:
            return None, MATCH_AMBIGUOUS, ranked
        return top_cm.id, MATCH_EXACT, ranked

    if top_score >= FUZZY_MIN_SCORE:
        if top_score - runner_up < AMBIGUITY_MARGIN:
            return None, MATCH_AMBIGUOUS, ranked
        return top_cm.id, MATCH_FUZZY, ranked

    return None, MATCH_UNMATCHED, ranked


def _field_value(fields: dict, name: str):
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, dict) else None


def derive_period(fields: dict) -> tuple[int | None, int | None]:
    """The quarter a price-list row applies to, from the document's own dates.

    Preference order is deliberate: `valid_from` is when the list takes effect,
    which is the period the price is *for*. `quote_date` is when the document
    was written, a decent proxy. `valid_until` is last, because a list valid
    until the end of a quarter usually started in an earlier one — using it
    first would file a Q1 price under Q2.

    Returns (None, None) when the document states no date at all. That is the
    honest answer, not a default to today: filing a price under the wrong
    quarter moves a gap without saying so.
    """
    for key in ("valid_from", "quote_date", "valid_until"):
        raw = _field_value(fields, key)
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        return d.year, (d.month - 1) // 3 + 1
    return None, None


def build_rows(db: Session, run: PriceListRun, lines: list[dict]) -> list[PriceListRow]:
    candidates = candidate_cost_models(db, run.team_id, run.supplier_id)
    rows: list[PriceListRow] = []
    for idx, fields in enumerate(lines):
        name = _field_value(fields, "product_reference")
        matched_id, confidence, ranked = match_row(name, candidates)
        year, quarter = derive_period(fields)
        rows.append(PriceListRow(
            run_id=run.id,
            line_index=idx,
            fields=fields,
            matched_cost_model_id=matched_id,
            match_confidence=confidence,
            match_candidates=ranked or None,
            period_year=year,
            period_quarter=quarter,
        ))
    return rows


class CommitError(Exception):
    """A row that cannot be committed, with the reason a reviewer can act on."""


def commit_row(
    db: Session,
    row: PriceListRow,
    run: PriceListRun,
    user_id: uuid.UUID,
    *,
    cost_model_id: uuid.UUID | None = None,
    year: int | None = None,
    quarter: int | None = None,
    price: float | None = None,
) -> ActualPrice:
    """Write one row as an ActualPrice. Overrides win over what was extracted.

    Upsert on (cost_model, year, quarter), matching what the manual price path
    and the CSV upload both already do — a price list re-issued for the same
    quarter is a correction, not a duplicate.
    """
    if row.status == "committed":
        raise CommitError("Already committed")

    target_id = cost_model_id or row.matched_cost_model_id
    if target_id is None:
        raise CommitError("No cost model matched — pick one before committing")

    cm = (
        db.query(CostModel)
        .filter(CostModel.id == target_id, CostModel.team_id == run.team_id)
        .first()
    )
    # Not just a 404: an id from another team must not be writable through a
    # row this team happens to own. RLS covers the read, this covers the intent.
    if cm is None:
        raise CommitError("That cost model does not belong to this team")

    use_year = year if year is not None else row.period_year
    use_quarter = quarter if quarter is not None else row.period_quarter
    if use_year is None or use_quarter is None:
        raise CommitError("No period on this row — the document did not state one")
    if not 1 <= use_quarter <= 4:
        raise CommitError("Quarter must be between 1 and 4")

    use_price = price if price is not None else _field_value(row.fields, "price")
    if use_price is None:
        raise CommitError("No price on this row")
    use_price = float(use_price)

    existing = (
        db.query(ActualPrice)
        .filter(
            ActualPrice.cost_model_id == cm.id,
            ActualPrice.year == use_year,
            ActualPrice.quarter == use_quarter,
        )
        .first()
    )
    # An incoterm the document did not state falls back to the cost model's
    # own, exactly as the CSV upload path does.
    incoterm = _field_value(row.fields, "incoterm") or cm.incoterm
    named_place = _field_value(row.fields, "named_place")

    if existing:
        existing.price = use_price
        existing.uploaded_by = user_id
        existing.source_file = run.filename
        existing.incoterm = incoterm
        if named_place is not None:
            existing.named_place = named_place
        ap = existing
    else:
        ap = ActualPrice(
            cost_model_id=cm.id,
            uploaded_by=user_id,
            year=use_year,
            quarter=use_quarter,
            price=use_price,
            incoterm=incoterm,
            named_place=named_place,
            source_file=run.filename,
        )
        db.add(ap)

    db.flush()
    row.matched_cost_model_id = cm.id
    row.period_year = use_year
    row.period_quarter = use_quarter
    row.status = "committed"
    row.actual_price_id = ap.id
    row.reviewed_by = user_id
    row.reviewed_at = datetime.now(timezone.utc)
    return ap


def refresh_run_status(run: PriceListRun) -> None:
    run.status = "reviewed" if all(r.status != "pending" for r in run.rows) else "extracted"
