"""The product line a team product sits on (design §2.8).

A team product reaches the supply taxonomy (family › sub-family › product line)
in one of three ways, tried in this order:

1. **template** — the catalogue template the product is linked to
   (`products.formula_template_id`). A team fork stands for its platform
   original: the fork's own line if it has one, else the line of the template
   it was forked from (`origin_id`).
2. **cost_model** — the template behind one of the product's cost models: a
   formula version priced from a catalogue combo
   (`formula_versions.source_coverage_id` → `formula_region_coverage.template_id`).
   This is how a product tracked through the cost-model builder reaches its
   line (Strategy relied on it before this module existed). Cost models are
   tried oldest first (`created_at`, then id), and within one cost model the
   newest formula version first: the order `strategy.team_portfolio` used.
3. **manual** — `products.product_line_id`, the line picked for a custom
   product (one whose templates give no line).

**The first path that yields a line wins.** A template with no line (an
off-axis product, or a card-only key: "Product line not yet published") does
not stop the search; the next path is tried. If no path yields a line but a
template was found, the result carries that template's family with
`product_line_id` None and the template's `source`, so a screen can still show
the family and say the line is not yet published. With nothing at all, every
field is None and `source` is `none`.

The line row gives the family and sub-family (`product_lines.family_id`,
`.subfamily_id`); they are never read from the product.

`line_ids` lists every line the product reaches through any template path,
the primary line first: one product can reach two lines (a linked template and
a different cost-model template), and Strategy unions the playbooks of all of
them.

A bounded number of queries whatever the number of products. Runs in the
caller's session, so its row-level-security context applies (team products
and team forks are read as the caller may read them; platform rows are open
to all).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.cost_model import CostModel, FormulaVersion
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.product import Product
from app.models.product_line import ProductLine

SOURCES = ("template", "cost_model", "manual", "none")


@dataclass(frozen=True)
class EffectiveLine:
    product_line_id: int | None
    family_id: int | None
    subfamily_id: int | None
    # template | cost_model | manual | none
    source: str
    # The template the answer came from (a team fork's origin when the origin
    # gave the line). None for manual and none.
    template_id: uuid.UUID | None = None
    # Every line reached through a template path, the primary first (see the
    # module docstring). The manual line when that is the answer.
    line_ids: tuple[int, ...] = ()


NONE = EffectiveLine(None, None, None, "none")


def effective_lines(db: Session, product_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, EffectiveLine]:
    """product id → EffectiveLine, for every requested product that exists
    (and that the session may read). Unknown ids are left out."""
    ids = {pid for pid in product_ids if pid is not None}
    if not ids:
        return {}

    products = db.query(Product.id, Product.formula_template_id, Product.product_line_id).filter(
        Product.id.in_(ids)).all()
    if not products:
        return {}

    # Candidate templates per product, in path order.
    candidates: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {
        p.id: [(p.formula_template_id, "template")] if p.formula_template_id else []
        for p in products
    }
    versions = (
        db.query(CostModel.product_id, FormulaVersion.source_coverage_id)
        .join(FormulaVersion, FormulaVersion.cost_model_id == CostModel.id)
        .filter(CostModel.product_id.in_(candidates.keys()),
                FormulaVersion.source_coverage_id.isnot(None))
        .order_by(CostModel.created_at, CostModel.id,
                  FormulaVersion.base_year.desc(), FormulaVersion.base_quarter.desc())
        .all()
    )
    coverage_ids = {v.source_coverage_id for v in versions}
    coverage_template = dict(
        db.query(FormulaRegionCoverage.id, FormulaRegionCoverage.template_id)
        .filter(FormulaRegionCoverage.id.in_(coverage_ids)).all()
    ) if coverage_ids else {}
    for product_id, coverage_id in versions:
        tid = coverage_template.get(coverage_id)
        if tid and all(tid != seen for seen, _src in candidates[product_id]):
            candidates[product_id].append((tid, "cost_model"))

    # Templates, and the platform originals of team forks.
    template_ids = {tid for cands in candidates.values() for tid, _src in cands}
    templates = {
        t.id: t for t in db.query(
            FormulaTemplate.id, FormulaTemplate.team_id, FormulaTemplate.origin_id,
            FormulaTemplate.product_line_id, FormulaTemplate.family_id,
        ).filter(FormulaTemplate.id.in_(template_ids)).all()
    } if template_ids else {}
    origin_ids = {t.origin_id for t in templates.values()
                  if t.team_id is not None and t.origin_id and t.origin_id not in templates}
    if origin_ids:
        templates.update({
            t.id: t for t in db.query(
                FormulaTemplate.id, FormulaTemplate.team_id, FormulaTemplate.origin_id,
                FormulaTemplate.product_line_id, FormulaTemplate.family_id,
            ).filter(FormulaTemplate.id.in_(origin_ids)).all()
        })

    def resolved(tid: uuid.UUID) -> tuple[uuid.UUID, int | None, int | None]:
        """(template that answers, its line, its family): a fork's own line,
        else its origin's; with no line anywhere, the family and the template
        the product stands for (the origin of a fork)."""
        t = templates.get(tid)
        if t is None:
            return tid, None, None
        origin = templates.get(t.origin_id) if t.team_id is not None and t.origin_id else None
        if t.product_line_id is not None:
            return t.id, t.product_line_id, t.family_id
        if origin is not None and origin.product_line_id is not None:
            return origin.id, origin.product_line_id, origin.family_id
        family = t.family_id if t.family_id is not None else (origin.family_id if origin else None)
        return (origin or t).id, None, family

    # Line rows give family and sub-family.
    line_ids = {resolved(tid)[1] for cands in candidates.values() for tid, _src in cands}
    line_ids |= {p.product_line_id for p in products}
    line_ids.discard(None)
    lines = {
        ln.id: ln for ln in db.query(
            ProductLine.id, ProductLine.family_id, ProductLine.subfamily_id,
        ).filter(ProductLine.id.in_(line_ids)).all()
    } if line_ids else {}

    out: dict[uuid.UUID, EffectiveLine] = {}
    for p in products:
        primary = None
        family_only = None
        reached: list[int] = []
        for tid, source in candidates[p.id]:
            answer_tid, line_id, family_id = resolved(tid)
            line = lines.get(line_id) if line_id is not None else None
            if line is not None:
                if line.id not in reached:
                    reached.append(line.id)
                if primary is None:
                    primary = (line, source, answer_tid)
            elif family_only is None and family_id is not None:
                family_only = (family_id, source, answer_tid)

        if primary is not None:
            line, source, answer_tid = primary
            out[p.id] = EffectiveLine(line.id, line.family_id, line.subfamily_id, source,
                                      answer_tid, tuple(reached))
        elif p.product_line_id is not None and p.product_line_id in lines:
            line = lines[p.product_line_id]
            out[p.id] = EffectiveLine(line.id, line.family_id, line.subfamily_id, "manual",
                                      None, (line.id,))
        elif family_only is not None:
            family_id, source, answer_tid = family_only
            out[p.id] = EffectiveLine(None, family_id, None, source, answer_tid, ())
        else:
            out[p.id] = NONE
    return out


def effective_line(db: Session, product_id: uuid.UUID) -> EffectiveLine:
    """One product's effective line (NONE when the product is not found)."""
    return effective_lines(db, [product_id]).get(product_id, NONE)
