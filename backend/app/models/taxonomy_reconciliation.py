import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TaxonomyReconciliation(Base):
    """One analyst decision aliasing a drop-sourced family/subfamily name onto
    an existing platform ChemicalFamily/Subfamily (sheet-roundtrip decision-file
    pattern — mirrors DimensionAlias). Never renames or creates the platform
    row itself: the analyst creates the target first via the existing taxonomy
    CRUD (Scrum 55/68) if it genuinely doesn't exist, then aliases the drop
    name onto it here — a decision file records a mapping onto ontology a
    human already created, it never invents ontology.

    Platform-level, no team_id, no RLS — same reasoning as DimensionAlias/
    Producer: this reconciles the shared catalog against a drop's naming, not
    a team's private data.
    """

    __tablename__ = "taxonomy_reconciliations"
    __table_args__ = (
        # A family-level row's drop_subfamily is always '' (NOT NULL, never
        # NULL) so this constraint actually holds — Postgres treats every NULL
        # as distinct, which would silently defeat it (same rule Unit 3b's
        # `variant` column documents).
        UniqueConstraint("level", "drop_family", "drop_subfamily",
                          name="uq_taxonomy_reconciliation_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # "family" | "subfamily"
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    drop_family: Mapped[str] = mapped_column(String(200), nullable=False)
    drop_subfamily: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True
    )
    subfamily_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    family = relationship("ChemicalFamily")
    subfamily = relationship("Subfamily")
