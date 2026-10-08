"""The Strategy layer — playbooks as platform reference, team state as an overlay
(demo build, spec D7).

Two populations, two tenancy shapes, same split as the rest of the app:

    platform (no team_id, no RLS — the `commodity_indexes` precedent)
        GemstoneCategory      the 8 lever families, fixed order
        StrategicObjective    the 7 objectives a buyer ticks
        Playbook              one per pre-September product line (`category.id`
                              in the drop = the report slug, e.g. "coagulants")
        PlaybookLever         4,945 authored levers, with their defaults
        PlaybookLeverObjective / PlaybookObjective

    team (strict tenant, `tenant_isolation` policy like `products`)
        StrategyRecord, TeamObjective, LeverScore, CustomLever, StrategyAction

**Team state overlays the defaults; it never copies them.** A team with no rows
here sees the authored levers and objectives as shipped — nothing is seeded per
team. A `LeverScore` carries only what the team changed; a NULL field means
"use the playbook's default". That is also why Intelligence stays read-only: no
team action writes a platform row.

**`PlaybookLever.id` anchors team state.** `LeverScore.lever_id` and
`StrategyAction.lever_id` point at it, so the playbook loader must upsert levers
by `lever_code` and never delete-and-reinsert — a reinsert mints new ids and
cascades every team's scores away.

The drop names gemstones and objectives by their display names ("Volume
Concentration", "Cost Reduction"). The tables key them by short codes; the
constants below are the one mapping, and the migration seeds from a frozen copy
of the same lists. Loaders import `GEMSTONE_CODE_BY_NAME` /
`OBJECTIVE_CODE_BY_NAME` rather than inventing their own.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# (code, display name, colour) in the mockup's GEMSTONE_ORDER. The order is the
# display order of the lever groups on the Opportunities tab.
GEMSTONES: list[tuple[str, str, str]] = [
    ("VC", "Volume Concentration", "#0F6E56"),
    ("PC", "Pricing & Conditions", "#0B6E6E"),
    ("GS", "Global Sourcing", "#146B8C"),
    ("CM", "Category Management", "#3D6B99"),
    ("PS", "Product Spec Improvement", "#BA7517"),
    ("JP", "Joint Process Improvement", "#A0522D"),
    ("RR", "Relationship Restructuring", "#534AB7"),
    ("DF", "Differentiation", "#8C3A6B"),
]
GEMSTONE_CODE_BY_NAME = {name: code for code, name, _ in GEMSTONES}

# (code, display name, description) — the mockup's OBJECTIVES_ENUM, in order.
OBJECTIVES: list[tuple[str, str, str]] = [
    ("cost_reduction", "Cost Reduction",
     "Unit price, TCO, index-linked mechanisms, rebates/discounts"),
    ("cash_working_capital", "Cash / Working Capital",
     "Payment terms (DPO), inventory optimization, consignment stock, VMI"),
    ("supply_security", "Supply Security",
     "Dual sourcing, geographic diversification, contingency planning"),
    ("quality", "Quality",
     "Spec consistency, compliance, COA/traceability"),
    ("innovation", "Innovation",
     "Joint development, early supplier involvement, access to new chemistries"),
    ("sustainability_esg", "Sustainability & ESG",
     "Carbon footprint, EcoVadis, circular feedstocks"),
    ("simplification", "Simplification",
     "SKU/spec rationalization, supplier base rationalization"),
]
OBJECTIVE_CODE_BY_NAME = {name: code for code, name, _ in OBJECTIVES}

# Lever statuses in use across the 168 playbooks, plus "Approved", which the
# mockup allows and no playbook ships.
LEVER_STATUSES = ("Identified", "Under evaluation", "Approved", "Actioned", "Rejected")
ACTION_STATUSES = ("Not started", "In progress", "Done", "Blocked")
PRIORITIES = ("High", "Medium", "Low")

_LEVER_STATUS_SQL = ", ".join(f"'{s}'" for s in LEVER_STATUSES)
_ACTION_STATUS_SQL = ", ".join(f"'{s}'" for s in ACTION_STATUSES)
_PRIORITY_SQL = ", ".join(f"'{s}'" for s in PRIORITIES)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Platform ─────────────────────────────────────────────────────────────────

class GemstoneCategory(Base):
    """One of the 8 lever families. Seeded by the migration, not a loader."""

    __tablename__ = "gemstone_categories"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)


class StrategicObjective(Base):
    """One of the 7 objectives. Seeded by the migration, not a loader."""

    __tablename__ = "strategic_objectives"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class Playbook(Base):
    """A category strategy playbook, keyed by the drop's `category.id`.

    The strategy unit is a pre-September product line named by its report
    (e.g. "Coagulants"), not a September line — the coagulants playbook serves
    both the PAC and the ferric chloride lines. `report_slug` is a soft key to
    `market_reports.slug` (no FK): the two tables are filled by separate
    loaders and a hard FK would make their load order load-bearing.
    """

    __tablename__ = "playbooks"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    report_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # The pre-September `Family|||Line` key the playbook was written against.
    legacy_line_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {cx, cy, badge, label, boundary} as authored.
    kraljic: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_updated: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The other authored category keys (owner, hasContent, annualSpend …) as
    # given — the drop ships them null, and a demo number is never invented.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    levers = relationship(
        "PlaybookLever", back_populates="playbook", cascade="all, delete-orphan",
        order_by="PlaybookLever.sort_order",
    )
    objectives = relationship(
        "PlaybookObjective", back_populates="playbook", cascade="all, delete-orphan",
        order_by="PlaybookObjective.sort_order",
    )


class PlaybookLever(Base):
    """One authored lever with its defaults. Team edits live in `LeverScore`."""

    __tablename__ = "playbook_levers"
    __table_args__ = (
        CheckConstraint("default_ease IS NULL OR default_ease BETWEEN 1 AND 5",
                        name="ck_pbl_default_ease"),
        CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                        name="ck_pbl_savings_score"),
        CheckConstraint(f"default_status IS NULL OR default_status IN ({_LEVER_STATUS_SQL})",
                        name="ck_pbl_default_status"),
        Index("ix_playbook_levers_playbook", "playbook_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)
    # The drop's lever id ("coagulants-1a"); unique across all playbooks.
    lever_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    gemstone_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("gemstone_categories.code"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_applies: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    default_ease: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    savings_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    default_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Buyer scales the lever suits: ["large", "mid", "small"].
    scales: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    playbook = relationship("Playbook", back_populates="levers")
    gemstone = relationship("GemstoneCategory")
    objective_links = relationship(
        "PlaybookLeverObjective", cascade="all, delete-orphan")


class PlaybookLeverObjective(Base):
    """Which objectives a lever serves (the lever's `objectives` array)."""

    __tablename__ = "playbook_lever_objectives"

    lever_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("playbook_levers.id", ondelete="CASCADE"), primary_key=True)
    objective_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("strategic_objectives.code"), primary_key=True)


class PlaybookObjective(Base):
    """An authored `{type, priority, note}` objective on a playbook — the
    pre-fill for the Key strategic objectives panel (662 rows)."""

    __tablename__ = "playbook_objectives"
    __table_args__ = (
        CheckConstraint(f"priority IS NULL OR priority IN ({_PRIORITY_SQL})",
                        name="ck_pbo_priority"),
    )

    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), primary_key=True)
    objective_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("strategic_objectives.code"), primary_key=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    playbook = relationship("Playbook", back_populates="objectives")


# ── Team state (strict tenant, RLS) ──────────────────────────────────────────

class StrategyRecord(Base):
    """A team's strategy on one playbook: owner and status. Optional — the
    landing page lists reachable playbooks whether or not a record exists."""

    __tablename__ = "strategy_records"
    __table_args__ = (
        UniqueConstraint("team_id", "playbook_slug", name="uq_strategy_record_team_playbook"),
        Index("ix_strategy_records_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Active", server_default="Active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


class TeamObjective(Base):
    """A team's answer on one of the 7 objectives for one playbook. Absent row
    = fall back to the authored `PlaybookObjective` (if any)."""

    __tablename__ = "team_objectives"
    __table_args__ = (
        UniqueConstraint("team_id", "playbook_slug", "objective_code",
                         name="uq_team_objective"),
        CheckConstraint(f"priority IS NULL OR priority IN ({_PRIORITY_SQL})",
                        name="ck_team_objective_priority"),
        Index("ix_team_objectives_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)
    objective_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("strategic_objectives.code"), nullable=False)
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


class LeverScore(Base):
    """A team's overrides on one authored lever. Every field is nullable: NULL
    means "the playbook default stands", so a partial edit never has to copy
    the defaults it did not touch."""

    __tablename__ = "lever_scores"
    __table_args__ = (
        UniqueConstraint("team_id", "lever_id", name="uq_lever_score_team_lever"),
        CheckConstraint("ease IS NULL OR ease BETWEEN 1 AND 5", name="ck_lever_score_ease"),
        CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                        name="ck_lever_score_savings_score"),
        CheckConstraint(f"status IS NULL OR status IN ({_LEVER_STATUS_SQL})",
                        name="ck_lever_score_status"),
        Index("ix_lever_scores_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    lever_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("playbook_levers.id", ondelete="CASCADE"), nullable=False)
    applies: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ease: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Impact re-score. Not in the drop (the playbook's score is the default);
    # nullable so the Impact-vs-Ease chart can move a bubble on a team's view.
    savings_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # The team's own savings estimate. The drop ships it null on every lever.
    savings_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    lever = relationship("PlaybookLever")


class CustomLever(Base):
    """An opportunity a team added itself ("add custom opportunity")."""

    __tablename__ = "custom_levers"
    __table_args__ = (
        CheckConstraint("ease IS NULL OR ease BETWEEN 1 AND 5", name="ck_custom_lever_ease"),
        CheckConstraint("savings_score IS NULL OR savings_score BETWEEN 1 AND 5",
                        name="ck_custom_lever_savings_score"),
        CheckConstraint(f"status IS NULL OR status IN ({_LEVER_STATUS_SQL})",
                        name="ck_custom_lever_status"),
        Index("ix_custom_levers_team_playbook", "team_id", "playbook_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)
    gemstone_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("gemstone_categories.code"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    applies: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ease: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    savings_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    savings_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Objective codes the team ticked on the add-opportunity form.
    objectives: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    gemstone = relationship("GemstoneCategory")


class StrategyAction(Base):
    """An action created from a lever (authored or custom). Overdue is derived
    (due_date < today and status not Done), never stored."""

    __tablename__ = "strategy_actions"
    __table_args__ = (
        CheckConstraint(f"status IN ({_ACTION_STATUS_SQL})", name="ck_strategy_action_status"),
        CheckConstraint("pct_complete BETWEEN 0 AND 100", name="ck_strategy_action_pct"),
        Index("ix_strategy_actions_team_playbook", "team_id", "playbook_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    playbook_slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("playbooks.slug", ondelete="CASCADE"), nullable=False)
    lever_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("playbook_levers.id", ondelete="SET NULL"), nullable=True)
    custom_lever_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_levers.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="Not started", server_default="Not started")
    pct_complete: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    lever = relationship("PlaybookLever")
    custom_lever = relationship("CustomLever")
