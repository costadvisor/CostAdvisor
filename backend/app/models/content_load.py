"""One row per real content load (seed_content_drop.py).

The record of which source commit the platform content in this database came
from. Dry runs write no row. The catalogue and reference snapshots use the
latest `id` as part of their version key, and `/intel/facets` reports the
source commit and date as the data version.

Platform-only: no `team_id`, no RLS.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ContentLoad(Base):
    __tablename__ = "content_loads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # From the drop's `_manifest.json`.
    source_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drop_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    loaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Only real runs are kept, so this is false on every stored row; the
    # column records the run mode explicitly rather than by absence.
    dry_run: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # The loader summary (created / updated / deleted / unchanged per table).
    counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Warnings and logged cases.
    notes: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
