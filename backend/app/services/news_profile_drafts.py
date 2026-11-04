"""Claude-drafted search profiles (Wave 4, SCRUM-213 sub-task 3).

Writing ~25 profiles from scratch is slow, so Claude drafts each one from what
the platform already knows about the family, and an editor cleans it up:

* the family's sub-families, product lines and products,
* its producers, ranked by how many of the family's products they make
  (``producer_formulas``) — so "companies to watch" comes from our data, not
  from the model's memory,
* the supply and overview sections of the market reports for its lines,
  and its existing editorial text (supplier notes, macro drivers, ...),
* the list of valid region codes, so regions come back as real codes.

The draft is returned as ``ProfileFields`` plus what could not be used (an
invalid CAS number, a producer name that does not resolve), so the editor
sees exactly what Claude suggested and what was dropped. Saving is separate:
the portal shows the draft in the form, and the bulk script saves it as
``ai_draft`` + inactive, never over a profile a person has edited.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants.trust import PROVENANCE_AI_DRAFT
from app.models.chemical_family import ChemicalFamily
from app.models.dimension import normalize_value
from app.models.editorial import EditorialBlock
from app.models.formula_template import FormulaTemplate
from app.models.market_report import MarketReportLine, MarketReportSection
from app.models.producer import Producer, ProducerFormula
from app.models.region import Region
from app.models.subfamily import Subfamily
from app.schemas.search_profile import EVENT_TYPES, ProfileFields, is_valid_cas
from app.services import anthropic_client as claude
from app.services.producers import resolve_raw_name
from app.services.search_profiles import family_lines, get_profile_row, save_profile

DRAFT_TOOL = "save_profile"
MAX_PRODUCTS = 60
MAX_PRODUCERS = 40
MAX_EDITORIAL_CHARS = 8000
MAX_REPORT_CHARS = 6000
REPORT_SECTIONS = ("supply", "overview")   # who makes it, and what it is


@dataclass
class FamilyContext:
    family: ChemicalFamily
    subfamilies: list[str]
    products: list[str]
    producers: list[tuple[Producer, int]]       # (producer, number of family products it makes)
    editorial: list[tuple[str, str]]            # (block_type, text)
    regions: list[Region]
    lines: list[str] = field(default_factory=list)
    reports: list[tuple[str, str]] = field(default_factory=list)   # (report · section, text)


def _plain_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _report_extracts(db: Session, line_ids: list[int]) -> list[tuple[str, str]]:
    """The supply and overview sections of the reports serving these lines."""
    if not line_ids:
        return []
    slugs = sorted({s for (s,) in db.query(MarketReportLine.slug)
                    .filter(MarketReportLine.product_line_id.in_(line_ids)).all()})
    if not slugs:
        return []
    rows = (db.query(MarketReportSection)
            .filter(MarketReportSection.slug.in_(slugs),
                    MarketReportSection.section_id.in_(REPORT_SECTIONS))
            .order_by(MarketReportSection.slug, MarketReportSection.ordinal).all())
    out, used = [], 0
    per_section = max(600, MAX_REPORT_CHARS // max(1, len(rows)))
    for r in rows:
        text = _plain_text(r.html)[:per_section]
        if not text or used + len(text) > MAX_REPORT_CHARS:
            continue
        out.append((f"{r.slug} · {r.section_id}", text))
        used += len(text)
    return out


@dataclass
class Draft:
    fields: ProfileFields
    rationale: str = ""
    dropped: list[str] = field(default_factory=list)
    usage: claude.Usage = field(default_factory=claude.Usage)


def gather_family_context(db: Session, family_id: int) -> FamilyContext:
    family = db.get(ChemicalFamily, family_id)
    if family is None:
        raise ValueError(f"no family {family_id}")

    subfamilies = [s.name for s in db.query(Subfamily)
                   .filter(Subfamily.family_id == family_id)
                   .order_by(Subfamily.name).all()]
    products = [t.name for t in db.query(FormulaTemplate)
                .filter(FormulaTemplate.family_id == family_id, FormulaTemplate.team_id.is_(None))
                .order_by(FormulaTemplate.name).limit(MAX_PRODUCTS).all()]

    n = func.count(ProducerFormula.id)
    producers = (db.query(Producer, n)
                 .join(ProducerFormula, ProducerFormula.producer_id == Producer.id)
                 .join(FormulaTemplate, FormulaTemplate.id == ProducerFormula.template_id)
                 .filter(FormulaTemplate.family_id == family_id, FormulaTemplate.team_id.is_(None))
                 .group_by(Producer.id).order_by(n.desc(), Producer.name)
                 .limit(MAX_PRODUCERS).all())

    editorial, used = [], 0
    blocks = (db.query(EditorialBlock)
              .filter(EditorialBlock.family_id == family_id, EditorialBlock.team_id.is_(None))
              .order_by(EditorialBlock.block_type).all())
    for b in blocks:
        v = b.current_version
        if v is None:
            continue
        text = v.body_text if v.body_text else json.dumps(v.body_json, ensure_ascii=False)
        text = (text or "").strip()[:1500]
        if not text or used + len(text) > MAX_EDITORIAL_CHARS:
            continue
        editorial.append((b.block_type, text))
        used += len(text)

    regions = db.query(Region).order_by(Region.code).all()
    lines = family_lines(db, family_id)
    return FamilyContext(family, subfamilies, products, list(producers), editorial, regions,
                         lines=[ln.name for ln in lines],
                         reports=_report_extracts(db, [ln.id for ln in lines]))


def _tool_def(region_codes: list[str]) -> dict:
    str_list = {"type": "array", "items": {"type": "string"}}
    return {
        "name": DRAFT_TOOL,
        "description": "Save the drafted news search profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "terms": {**str_list, "description": "2-5 main names to search for."},
                "synonyms": {**str_list, "description": "Other names, spellings, abbreviations, grades."},
                "cas_numbers": {**str_list, "description": "Only CAS numbers you are certain of."},
                "exclude_terms": {**str_list, "description": "Words that mark irrelevant results (consumer uses, unrelated meanings)."},
                "producers": {**str_list, "description": "Names copied exactly from the producer list given."},
                "region_codes": {"type": "array", "items": {"type": "string", "enum": region_codes or ["GLOBAL"]}},
                "event_types": {"type": "array", "items": {"type": "string", "enum": list(EVENT_TYPES)}},
                "preferred_sites": {**str_list, "description": "Domains of trade press that cover this family well."},
                "blocked_sites": {**str_list, "description": "Domains known to be noise for this family."},
                "min_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "lookback_days": {"type": "integer", "minimum": 7, "maximum": 92},
                "rationale": {"type": "string", "description": "Two or three sentences for the editor: what you focused on and what to check."},
            },
            "required": ["terms", "synonyms", "exclude_terms", "producers", "region_codes",
                         "event_types", "preferred_sites", "min_relevance", "lookback_days", "rationale"],
        },
    }


SYSTEM_PROMPT = """You draft news search profiles for CostAdvisor, a platform that helps industrial \
buyers understand what they should pay for chemicals and materials. A profile tells a monthly news \
search what to look for in one product family: the names it goes by, the producers to watch, the \
regions, the kinds of events that move supply and cost, trusted trade sources, and words that mark \
noise (for "fiberglass": boats, insulation installers, DIY).

Be precise rather than broad: a search for every possible synonym returns noise. Choose producers only \
from the list given, most important first. Only include CAS numbers you are certain of. An editor will \
review your draft, so say in the rationale what they should double-check."""


def build_prompt(ctx: FamilyContext) -> str:
    lines = [f"Product family: {ctx.family.name}" + (f" (catalogue code {ctx.family.code})" if ctx.family.code else "")]
    if ctx.subfamilies:
        lines.append("Sub-families: " + "; ".join(ctx.subfamilies))
    if ctx.lines:
        lines.append("Product lines: " + "; ".join(ctx.lines))
    if ctx.products:
        lines.append("Products in our catalogue: " + "; ".join(ctx.products))
    if ctx.producers:
        lines.append("Producers we track for this family (name — number of our products they make):")
        lines += [f"- {p.name} — {count}" for p, count in ctx.producers]
    else:
        lines.append("We have no producers linked to this family yet; leave producers empty.")
    if ctx.reports:
        lines.append("\nFrom our market reports for these lines (supply and overview):")
        lines += [f"[{where}] {text}" for where, text in ctx.reports]
    if ctx.editorial:
        lines.append("\nOur editorial notes on this family:")
        lines += [f"[{block_type}] {text}" for block_type, text in ctx.editorial]
    lines.append("\nValid region codes: " + ", ".join(f"{r.code} ({r.name})" for r in ctx.regions))
    lines.append("\nDraft the profile and call save_profile.")
    return "\n".join(lines)


def _producer_ids(db: Session, ctx: FamilyContext, names: list[str]) -> tuple[list, list[str]]:
    by_name = {normalize_value(p.name): p.id for p, _ in ctx.producers}
    ids, unmatched = [], []
    for name in names:
        pid = by_name.get(normalize_value(name))
        if pid is None:
            matches = resolve_raw_name(db, name, create=False)
            pid = matches[0].producer.id if len(matches) == 1 else None
        if pid is None:
            exact = db.query(Producer).filter(Producer.normalized_name == normalize_value(name)).first()
            pid = exact.id if exact else None
        if pid is None:
            unmatched.append(name)
        elif pid not in ids:
            ids.append(pid)
    return ids, unmatched


def draft_profile(db: Session, family_id: int, *, http_client=None) -> Draft:
    """Ask Claude for a draft. Makes one API call; writes nothing."""
    ctx = gather_family_context(db, family_id)
    resp = claude.create_message(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(ctx)}],
        tools=[_tool_def([r.code for r in ctx.regions])],
        tool_choice={"type": "tool", "name": DRAFT_TOOL},
        max_tokens=3000,
        client=http_client,
    )
    raw = resp.tool_input(DRAFT_TOOL)
    if raw is None:
        raise claude.AnthropicError("Claude did not return a draft")

    dropped: list[str] = []
    cas = [c for c in raw.get("cas_numbers") or [] if is_valid_cas(str(c).strip())]
    dropped += [f"CAS number failed its check digit: {c}" for c in raw.get("cas_numbers") or []
                if not is_valid_cas(str(c).strip())]
    producer_ids, unmatched = _producer_ids(db, ctx, raw.get("producers") or [])
    dropped += [f"Producer not found in our producer list: {n}" for n in unmatched]
    valid_regions = {r.code for r in ctx.regions}
    region_codes = [c for c in raw.get("region_codes") or [] if c in valid_regions]
    events = [e for e in raw.get("event_types") or [] if e in EVENT_TYPES]

    min_rel = raw.get("min_relevance")
    lookback = raw.get("lookback_days")
    fields = ProfileFields(
        terms=raw.get("terms") or [ctx.family.name],
        synonyms=raw.get("synonyms") or [],
        cas_numbers=cas,
        exclude_terms=raw.get("exclude_terms") or [],
        event_types=events,
        preferred_sites=raw.get("preferred_sites") or [],
        blocked_sites=raw.get("blocked_sites") or [],
        producer_ids=producer_ids,
        region_codes=region_codes,
        min_relevance=min(1.0, max(0.0, float(min_rel))) if min_rel is not None else None,
        lookback_days=min(92, max(7, int(lookback))) if lookback is not None else None,
    )
    return Draft(fields=fields, rationale=str(raw.get("rationale") or ""), dropped=dropped,
                 usage=resp.usage)


def save_draft(db: Session, family_id: int, draft: Draft, *, force: bool = False) -> str:
    """Store a draft as ``ai_draft`` and inactive. Returns what happened.

    Never overwrites a profile a person has touched unless ``force``.
    """
    existing = get_profile_row(db, family_id)
    if existing is not None and existing.provenance != PROVENANCE_AI_DRAFT and not force:
        return "skipped (edited by a person)"
    note = "Drafted by Claude." + (f" {draft.rationale}" if draft.rationale else "")
    if draft.dropped:
        note += " Not used: " + "; ".join(draft.dropped)
    save_profile(db, family_id, draft.fields, provenance=PROVENANCE_AI_DRAFT,
                 is_active=False if existing is None else existing.is_active,
                 owner_editor_id=existing.owner_editor_id if existing else None,
                 notes=note[:4000])
    return "updated" if existing is not None else "created"
