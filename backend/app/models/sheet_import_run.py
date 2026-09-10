import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, Boolean, DateTime, Text,
    ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SheetImportRun(Base):
    """One export->edit->reimport cycle for a sheet round-trip payload
    (Scrum 27b). Platform-level like IndexProjectionRun/CommodityIndex — no
    team_id, no RLS (the payloads this backs, e.g. FormulaRegionCoverage, are
    themselves platform catalog data, not tenant data).

    Importing (POST .../import) always inserts a NEW run + its diff rows and
    never mutates the underlying payload table — applying is a separate,
    explicit call (POST .../import-runs/{id}/apply), so a run is a durable,
    inspectable record of "what would change" independent of whether it ever
    gets applied.
    """

    __tablename__ = "sheet_import_runs"
    __table_args__ = (
        Index("idx_sheet_import_runs_payload", "payload_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payload_key: Mapped[str] = mapped_column(String(64), nullable=False)
    filter_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "empty" | "diffed" | "applied"
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    applied_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    diffs = relationship(
        "SheetImportRowDiff",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SheetImportRowDiff.id",
    )


class SheetImportRowDiff(Base):
    """One affected (row, column) from a SheetImportRun. `kind` distinguishes
    a real editable-column change from an edit the mechanism refuses to
    silently absorb (a readonly-column edit, an unparseable value, or a row
    key that no longer matches anything live) — only "change" rows are ever
    appliable."""

    __tablename__ = "sheet_import_row_diffs"
    __table_args__ = (
        Index("idx_sheet_import_row_diffs_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sheet_import_runs.id", ondelete="CASCADE"), nullable=False
    )
    row_key: Mapped[dict] = mapped_column(JSONB, nullable=False)
    column: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "change" | "rejected_readonly_edit" | "unmatched_key" | "invalid_value"
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    run = relationship("SheetImportRun", back_populates="diffs")
