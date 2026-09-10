import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Text, DateTime, ForeignKey, Integer, SmallInteger, Numeric, Boolean,
    CheckConstraint, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# A component line is one of: a commodity-index-linked share, a flat share
# (margin / conversion / "other"), or another formula used as an input
# (tiered "Lego" chaining, Scrum 58).
COMPONENT_TYPES = ("index", "fixed", "formula")


class FormulaTemplate(Base):
    __tablename__ = "formula_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Fork back-link: a team fork points at the platform original it was copied
    # from (same pattern as chemical_families.origin_id), so lineage survives a
    # rename. NULL on platform rows and on non-forked team rows.
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Catalog formula_id (e.g. "OLE-FAC-SAT") — the stable key the seed loader
    # upserts by. Unique among platform rows only; a fork keeps its origin's
    # code (same rule as chemical_families.code).
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Taxonomy spine links (family -> subfamily -> formula). subfamily_id stays
    # NULL until the reference drop carries the formula->subfamily mapping.
    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True
    )
    subfamily_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True
    )
    # Reference-drop metadata (form / coverage_tier / data_confidence /
    # region_count); SEED-2 gates low-confidence rows on this.
    catalog_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nullable since Scrum 58: a template can be defined purely as weighted
    # component lines instead of a free-form expression.
    expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables: Mapped[dict | None] = mapped_column(JSONB(astext_type=Text()), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    components = relationship(
        "FormulaTemplateComponent",
        foreign_keys="FormulaTemplateComponent.template_id",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="FormulaTemplateComponent.sort_order",
    )
    coverage = relationship(
        "FormulaRegionCoverage",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class FormulaTemplateComponent(Base):
    """One weighted line of a formula template (Scrum 58).

    A formula is a list of weighted lines plus a margin: each line explains a
    share of the cost and points at a commodity index, rides flat ("fixed"),
    or pulls in another template ("formula" — tiered chaining, resolved with a
    depth cap in services/formula_resolver.py). Weights are signed percents
    (a by-product credit can be negative) and must sum to 100 per template —
    enforced at the schema layer, not the DB, so drafts stay possible via seed
    scripts.
    """

    __tablename__ = "formula_template_components"
    __table_args__ = (
        CheckConstraint(
            "component_type IN ('index', 'fixed', 'formula')",
            name="ck_ftc_component_type",
        ),
        # Type/target coherence: each type carries exactly its own reference.
        # An index line is satisfied by a commodity OR a type code. Relaxed in
        # Scrum 74/3b: a line naming an `ambiguous` type code resolves to
        # nothing, so it has no commodity to record — and the drop has 25 such
        # lines. Under the original constraint they could not be stored at all,
        # so a load had to drop them and misreport every recipe containing them.
        CheckConstraint(
            "(component_type = 'index' AND (commodity_id IS NOT NULL OR type_code_id IS NOT NULL)"
            " AND input_template_id IS NULL)"
            " OR (component_type = 'formula' AND input_template_id IS NOT NULL AND commodity_id IS NULL)"
            " OR (component_type = 'fixed' AND commodity_id IS NULL AND input_template_id IS NULL)",
            name="ck_ftc_target_coherence",
        ),
        # Depth-1 self-reference is cheap to block here; deeper cycles are
        # blocked at write time by the resolver's chain walk.
        CheckConstraint(
            "input_template_id IS NULL OR input_template_id <> template_id",
            name="ck_ftc_no_self_reference",
        ),
        Index("ix_ftc_template_id", "template_id"),
        Index("ix_ftc_input_template_id", "input_template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("formula_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    component_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # No ondelete: deleting a commodity index or a template that other
    # formulas still reference must fail loudly, not silently orphan lines.
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id"), nullable=True
    )
    # What the source actually names (Scrum 74 / DB-5). A cost line names a
    # type code; the series is reached through it. Added ALONGSIDE
    # commodity_id rather than replacing it — the costing engine resolves via
    # commodity_id today and is deliberately untouched, while this is what
    # makes the resolution chain (line → type code → series) a real join
    # instead of something reassembled in memory.
    type_code_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("type_codes.id"), nullable=True
    )
    input_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id"), nullable=True
    )
    # NULL = template-level line set (applies to all regions — the API-authored
    # Scrum 58 lines). Set = a per-(formula x region) seeded recipe (Scrum 60):
    # the resolver prefers region-specific rows and falls back to NULL.
    region: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("regions.code"), nullable=True
    )
    # Product variant within one formula+region (Scrum 74/3b). The two variants
    # of a formula are different recipes with different margins, so the line set
    # is keyed (template, region, variant) — keyed on (template, region) alone
    # they overwrite each other and one is silently lost.
    variant: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", server_default=""
    )
    # Signed percent share of cost this line explains (credit lines < 0).
    weight_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    # "We don't have the exact index, so we lean on a close stand-in" — a
    # proxy-based line is a softer signal, and the user needs to see that.
    is_proxy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The LINE's own proxy reading (Scrum 74/3b): direct | proxy | unclassified.
    # The type-code registry states the same fact and the two disagree on a
    # material slice of the library — neither is authoritative, so both are
    # kept (see services/drop/authority.py). `is_proxy` above cannot hold this:
    # a boolean folds `unclassified` into "not a proxy", which is the reading
    # that understates exposure.
    line_proxy_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    template = relationship(
        "FormulaTemplate", foreign_keys=[template_id], back_populates="components"
    )
    input_template = relationship("FormulaTemplate", foreign_keys=[input_template_id])
    commodity = relationship("CommodityIndex")


class FormulaRegionCoverage(Base):
    """A "combo" — one formula priced in one region (Scrum 58).

    The same product is genuinely priced differently per region, so the
    per-region pricing (base price anchor + margin) lives here, keyed
    (template, region). Resolution falls back exact region → parent region →
    GLOBAL → Europe (services/formula_resolver.py).
    """

    __tablename__ = "formula_region_coverage"
    __table_args__ = (
        # Includes `variant` since Scrum 74/3b: the drop has combo pairs
        # differing only by variant (bentonite activated/natural, talc
        # treated/untreated), and the old two-column key rejected the second of
        # each pair outright.
        UniqueConstraint(
            "template_id", "region", "variant", name="uq_frc_template_region_variant"
        ),
        CheckConstraint(
            "base_quarter IS NULL OR base_quarter BETWEEN 1 AND 4",
            name="ck_frc_base_quarter",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("formula_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    region: Mapped[str] = mapped_column(
        String(20), ForeignKey("regions.code"), nullable=False
    )
    # Product variant within one formula+region (e.g. talc treated vs
    # untreated). NOT NULL DEFAULT '' rather than nullable: Postgres treats
    # every NULL as distinct in a unique constraint, so a nullable variant
    # would let two rows share (template, region, NULL) and defeat the
    # uniqueness it is part of.
    variant: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", server_default=""
    )
    base_price: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    margin_pct: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    base_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    base_quarter: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # ── Trust layer (Scrum 60) ────────────────────────────────────────────────
    # CONF-HIGH / CONF-MED / CONF-LOW. A CONF-LOW combo is a proportional-scaling
    # placeholder, not verified pricing — it loads with needs_review=True and
    # must not be treated as authoritative until an expert signs it off.
    data_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Worst retrieval tier among the combo's index inputs (free/good_proxy/
    # weak_proxy/blocked) — a combination is only as strong as its weakest input.
    coverage_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The drop's own tier metric: proxy DENSITY (P1 all-direct / P2 some proxy /
    # P3 proxy-heavy), computed over indexed weight only. A separate column
    # rather than more values in `coverage_tier` above, because they measure
    # different things — "how weak is the weakest input" and "how much of this
    # recipe leans on stand-ins" — and collapsing them loses both answers.
    proxy_density_tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # SCRUM-78: driven by `trust_grade` below, not by `data_confidence` — the
    # July sheet dropped that column, so nothing set this flag any more.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Legacy free-text reviewer. Superseded by `reviewed_by_id`: this held
    # `current_user.email`, so the record decayed the moment somebody changed
    # their address. Kept (backfilled onto the FK) rather than dropped, so an
    # old sign-off is still explicable.
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── The derived trust grade (SCRUM-78) ──────────────────────────────────
    #
    # Its own field, deliberately: either `coverage_tier` column answers "how
    # well covered is this", and the grade answers "is this worth a human's
    # time". Coverage is an *input* to the grade, so storing the grade in
    # `coverage_tier` would put an input and its own output in one column.
    trust_grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Why the grade came out that way — named type-codes and lines, not a bare
    # enum. An ungraded "low" tells a reviewer nothing about what to look at.
    trust_inputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trust_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # A sign-off is pinned to what was signed off. Change a weight or an index
    # input and the fingerprint stops matching, so the combo returns to the
    # queue instead of showing a stale green tick.
    review_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Same field name and JSONB shape as CON-5's staleness descriptor, rather
    # than a second fingerprint format for the same idea.
    review_derived_from: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The correction_plan_log entry for this combo's formula — the reasoning the
    # expert reviews against. Loaded as metadata, never re-applied (the weight
    # corrections are already baked into the source lines).
    review_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # ── Provenance (Scrum 33) — orthogonal to data_confidence: not "how much
    # do we trust it" but "how did it get here". "imported" (Scrum 59/60
    # seeding, the default for every pre-existing row) / "ai_draft" (the
    # estimator proposed it, not yet reviewed) / "human_approved" (a person
    # signed off — via mark_coverage_reviewed or estimator approval).
    provenance: Mapped[str] = mapped_column(String(16), default="imported", server_default="imported")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    template = relationship("FormulaTemplate", back_populates="coverage")
