"""Producer entity + alias layer (Wave 3, SCRUM-77 / INT-3 · W3-C).

**A producer is not a `Supplier` and not a dimension.** `Supplier.team_id` is
NOT NULL under the strict-tenant policy, so there is no row shape there for
BASF-as-a-company-that-exists — a supplier only exists inside a team that buys
from it. This is a platform entity with its own join, and it is what pays off
the gap Scrum 31 currently discloses on every trust score as
`resolution: "raw_supplier_name"`.

Three things measured off the drop that a tag table cannot carry:

* **`share = 0` means *not disclosed*, not zero.** 2,215 of 2,237 supplier rows
  (99.0%) carry 0, and several notes say the breakdown is not public. The flag
  is stored beside the number, or the UI ships "BASF — 0% market share".
* **Alias resolution is not a function.** 40 raw names contain `" / "`
  ("Sinopec / PetroChina", "BASF SE / Hexion / INEOS Melamines"), so one raw
  string legitimately produces N producers. A single canonical string per raw
  value cannot express that; the alias rows can.
* **`SUPPLIER_ALIASES.json` is a partial map, not the canonicalisation.** Its
  189 entries cover 185 of 901 distinct raw names (20.5%) and 691 of 2,237 rows
  (30.9%) — treating it as *the* step leaves most names unresolved and silently
  duplicated. On top of that, **45 canonical values also appear as raw names**,
  so resolution needs a fixpoint pass rather than one lookup.

Platform-only, no `team_id` and no RLS — following `commodity_indexes`, which
is the other platform reference table. A team does not fork "BASF exists"; what
a team can override is an *assertion*, and that lives on
`DimensionAssertion.team_id`.

Coordination note the ticket asks for: W3-D SUP-1/SUP-2 describe the same
platform company master and are meant to consume this one. Two masters is the
failure mode, so anything that needs a company row should FK here.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Producer(Base):
    """A company that makes things. Platform-level, one row per real company."""
    __tablename__ = "producers"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_producer_normalized_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Case/whitespace-normalised; the identity key, so "BASF" and "basf " are
    # one company rather than two.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    hq_country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    aliases: Mapped[list["ProducerAlias"]] = relationship(
        back_populates="producer", cascade="all, delete-orphan")


class ProducerAlias(Base):
    """A raw supplier string that resolves to one producer.

    Many-to-one on purpose, and a raw string that names several companies gets
    one alias row **per** company — which is how `" / "` splitting is expressed
    without pretending a single canonical name exists for it.
    """
    __tablename__ = "producer_aliases"
    __table_args__ = (
        UniqueConstraint("normalized", "producer_id", name="uq_producer_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("producers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    raw_value: Mapped[str] = mapped_column(String(400), nullable=False)
    # The FULL normalised raw string — the storage key, so
    # "BASF (Uvinul line)" keeps its own row rather than being swallowed by the
    # bare "BASF" alias and losing the qualifier.
    normalized: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    # The LOOKUP key: the same string with a trailing parenthetical dropped, so
    # both rows resolve to the one company. Two columns because a single one
    # would have to choose between preserving the qualifier and matching on it.
    match_key: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    # `split` marks an alias minted by splitting a multi-company raw string, so
    # a reviewer can see why one string produced several rows.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    producer: Mapped[Producer] = relationship(back_populates="aliases")


class ProducerFormula(Base):
    """What a producer makes — the "what does this producer actually make"
    question, and its reverse.

    Deliberately keyed on `subject_code` with a nullable `template_id`, the same
    rule as everywhere else in this drop: a hard FK would drop the
    template-less keys at import without raising.
    """
    __tablename__ = "producer_formulas"
    __table_args__ = (
        UniqueConstraint("producer_id", "subject_code", "region",
                         name="uq_producer_formula"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("producers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    subject_code: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True)
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    share_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    # The whole point: 99.0% of source rows carry 0, which means "not publicly
    # disclosed". Without this flag the UI reports every one of them as a real
    # zero market share.
    share_disclosed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")

    hq_country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The source's own `regs` array (its selling regions) and `tags`, kept as
    # given. The region *facet* is a dimension assertion; this is provenance.
    regions_raw: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # What the source string actually said, before alias resolution.
    raw_name: Mapped[str | None] = mapped_column(String(400), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="loader")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    producer: Mapped[Producer] = relationship()
