"""Polymorphic dimension model + alias layer (Wave 3, SCRUM-77 / INT-3 · W3-C).

Procurement questions cut sideways across the library — which of our products
touch EUDR, everything feeding this end market — and the model knew products
and formulas but not the vocabulary a buyer slices by. This makes that
vocabulary a first-class, queryable thing.

**One polymorphic `dimension_term` + `dimension_alias` + one assertion join,
not a table pair per facet.** The facets are not a closed set — the drop
already implies functionality (two disjoint schemes), industry/end market,
compliance flag, supply region and substitution risk, and each new one under
four-table-pairs costs a migration plus an endpoint, while the cross-cutting
query becomes a union over every shape ever built.

`region.py` is the precedent: a vocabulary that used to be free text on several
tables became managed rows, and nothing downstream was rewritten because the
stable natural key stayed the thing the app matches on.

Three things the assertion join needs that a plain tag table does not:

* **`subject_code` NOT NULL, `template_id` nullable** — the same rule as the
  editorial blocks, for the same measured reason: a hard FK drops the
  template-less keys at import and does not raise.
* **Nullable `region`** — assertions arrive keyed to a formula, but the question
  is asked per formula x region ("which of my *EU* combos touch EUDR", and EUDR
  is an EU claim). `FormulaTemplateComponent.region` already carries exactly
  this semantic (NULL = every region, set = region-specific, the resolver
  prefers the specific row), so it is reused rather than re-invented.
* **Platform assertion vs team override** — `team_id IS NULL` = our claim, set =
  the team's, with an `origin_id` back-link and uniqueness re-scoped by partial
  indexes, the `chemical_families` / `formula_templates` fork convention.

Tenancy is **platform-readable with team forks**, never strict tenant: under
strict tenant every platform term is invisible to every team, so the facet is
empty for everyone on day one and the bug looks like a loader failure.
"""
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ── The kinds, and why each one is (or is not) a kind ────────────────────────
#
# Measured against the drop rather than taken from the source's headings:
#
#   functionality          41 controlled terms, 384 tagged formulas, 41 distinct
#                          values used, ZERO strays. Mechanical.
#   functionality_family   a SECOND naming scheme (22 terms) carried by
#                          FAMILY_FUNCTIONALITY_DEFAULT / SUBFAMILY_*_OVERRIDE
#                          with **zero overlap** with the 41-term taxonomy.
#                          Loading both under one kind produces a facet with two
#                          disjoint halves and no way to tell which half a user
#                          is filtering on — so it is a separate kind, and the
#                          crosswalk between them is a judgement call for the
#                          decision file, not loader code.
#   industry               19 controlled targets; the raw side is 204 distinct
#                          free-text strings of which only 16 match exactly and
#                          10 more differ by case/whitespace — 178 (87.3%) need
#                          an analyst mapping.
#   compliance_flag        the raw side is 239 distinct labels, many of them full
#                          sentences ("Acrylamide monomer is classified as a
#                          probable human carcinogen (IARC Group 2A)"). A term
#                          table over those yields near-unique terms, which is
#                          not a facet — so terms come from the decision file and
#                          the raw labels are alias candidates in the report.
#   supply_region          the 7 REGS.json codes. The raw side is dirtier than
#                          the vocabulary (LATAM alongside LA, EMEA, Global).
#   substitution_risk      the bounded part of a substitution entry. The titles
#                          are NOT a kind: 385 distinct titles over 526 entries
#                          is ~1 term per row.
#
# Also deliberately not a kind: application `items[]` — 1,293 distinct values
# over 1,323 assertions, 28 of them reused. The facet there is the application's
# `industry`, which is the `industry` kind above.
KIND_FUNCTIONALITY = "functionality"
KIND_FUNCTIONALITY_FAMILY = "functionality_family"
KIND_INDUSTRY = "industry"
KIND_COMPLIANCE_FLAG = "compliance_flag"
KIND_SUPPLY_REGION = "supply_region"
KIND_SUBSTITUTION_RISK = "substitution_risk"

DIMENSION_KINDS = (
    KIND_FUNCTIONALITY,
    KIND_FUNCTIONALITY_FAMILY,
    KIND_INDUSTRY,
    KIND_COMPLIANCE_FLAG,
    KIND_SUPPLY_REGION,
    KIND_SUBSTITUTION_RISK,
)

# Same four as the editorial blocks, plus the producer entity this story owns.
SUBJECT_TYPES = ("formula", "index", "subfamily", "family", "producer")

# Where a row came from. `decision_file` is the analyst-owned path; `loader` is
# a mechanical load from a controlled vocabulary. Keeping them distinct is what
# makes "re-importing the decision file is what changes the DB" checkable.
SOURCES = ("taxonomy", "loader", "decision_file", "api")

_WS = re.compile(r"\s+")


def normalize_value(raw: str) -> str:
    """The matching form of a raw value.

    Case and whitespace only — nothing clever. 10 of the 204 raw industry
    strings differ from a taxonomy term by nothing else, so this alone resolves
    them; the remaining 178 are a judgement call and must not be guessed at by
    a normaliser that strips punctuation until something matches.
    """
    return _WS.sub(" ", str(raw or "").strip()).casefold()


class DimensionTerm(Base):
    __tablename__ = "dimension_terms"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('functionality','functionality_family','industry',"
            "'compliance_flag','supply_region','substitution_risk')",
            name="ck_dimension_term_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = platform (our vocabulary). Set = a team's own term.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension_terms.id", ondelete="SET NULL"), nullable=True)

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # The stable natural key the app matches on — `regions.code`'s role.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    aliases: Mapped[list["DimensionAlias"]] = relationship(
        back_populates="term", cascade="all, delete-orphan")


class DimensionAlias(Base):
    """A raw value that resolves to a term.

    `kind` is carried here as well as on the term so the uniqueness index and
    the resolution lookup are one indexed read — a raw string only ever means
    something *within* a facet ("Industrial" is an industry string, not a
    functionality).
    """
    __tablename__ = "dimension_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension_terms.id", ondelete="CASCADE"),
        nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    raw_value: Mapped[str] = mapped_column(String(400), nullable=False)
    # Case/whitespace-normalised form; the uniqueness key.
    normalized: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    term: Mapped[DimensionTerm] = relationship(back_populates="aliases")


class DimensionAssertion(Base):
    """"This subject carries this term", optionally only in one region."""
    __tablename__ = "dimension_assertions"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('formula','index','subfamily','family','producer')",
            name="ck_dimension_assertion_subject_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = our claim; set = the team's override.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension_assertions.id", ondelete="SET NULL"),
        nullable=True)

    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension_terms.id", ondelete="CASCADE"),
        nullable=False, index=True)

    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # NULL = applies to every region — `FormulaTemplateComponent.region`'s exact
    # semantic, reused deliberately.
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Convenience joins, never identity.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True)
    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True)
    subfamily_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True)

    # The audit trail for the hit: what the source actually said, and which
    # alias matched it. A bare list of product names cannot be checked by the
    # person who has to act on it.
    raw_value: Mapped[str | None] = mapped_column(String(400), nullable=True)
    matched_alias_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension_aliases.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")
    # Free-form provenance for a claim that came from more than one file — e.g.
    # which of the two compliance sources named it.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    term: Mapped[DimensionTerm] = relationship()
    matched_alias: Mapped[DimensionAlias | None] = relationship()


class UnresolvedValue(Base):
    """A raw value the load could not resolve to a term.

    **The analyst's work queue, and how anyone checks the load actually
    worked.** Not swallowed and not guessed: 178 of 204 raw industry strings
    land here on a first load, and `INDUSTRY_RULES.json` cannot classify them
    (all 19 of its regexes serialised to `{}` and the mockup that held the
    originals is not in this repo), so an analyst mapping is the only path.
    """
    __tablename__ = "dimension_unresolved"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_value: Mapped[str] = mapped_column(String(400), nullable=False)
    normalized: Mapped[str] = mapped_column(String(400), nullable=False)
    # How many source assertions were blocked by this one unresolved value —
    # which is what makes the queue rankable rather than alphabetical.
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # A couple of example subjects, so an analyst can see it in context.
    sample_subjects: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
