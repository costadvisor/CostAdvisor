"""News search profiles (Wave 4, SCRUM-213 / NEWS-1).

A profile is the recipe for finding a family's news: terms, synonyms, CAS
numbers, exclusions, producers, regions, event types, sites, language,
relevance threshold and lookback. One table holds both kinds of row:

* ``product_line_id IS NULL``     -> the family's base profile (one per family)
* ``product_line_id IS NOT NULL`` -> an override for one product line (the
  ticket's "per-line override"; family › sub-family › product line › product),
  merged onto the base by
  ``app.services.search_profiles.get_effective_profile``.

Platform-only, no ``team_id`` and no RLS, following ``producers`` and
``commodity_indexes``: profiles drive platform editorial content, and the
monthly sweep runs as a system task. Families and product lines are
platform-only themselves (no team forks since the taxonomy spine rework).

Producers and regions are real joins, not copied strings: producers so the
existing ``producer_aliases`` come along for free (the ticket's heads-up about
reusing aliases), regions by ``regions.code`` like every other region FK.

``provenance`` reuses the editorial vocabulary (``app.constants.trust``) so a
Claude-drafted profile is ``ai_draft`` until an editor touches it, and
``is_active`` gates whether the monthly sweep (SCRUM-214) runs it at all.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer,
    String, Table, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants.trust import PROVENANCE_HUMAN_EDITED, PROVENANCE_STATES
from app.database import Base

_EMPTY_LIST = text("'[]'::jsonb")
_PROVENANCE_SQL = ", ".join(f"'{s}'" for s in PROVENANCE_STATES)

search_profile_producers = Table(
    "search_profile_producers",
    Base.metadata,
    Column("profile_id", UUID(as_uuid=True),
           ForeignKey("search_profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("producer_id", UUID(as_uuid=True),
           ForeignKey("producers.id", ondelete="CASCADE"), primary_key=True),
)

search_profile_regions = Table(
    "search_profile_regions",
    Base.metadata,
    Column("profile_id", UUID(as_uuid=True),
           ForeignKey("search_profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("region_code", String(20),
           ForeignKey("regions.code", ondelete="CASCADE"), primary_key=True),
)


def _json_list():
    return mapped_column(JSONB, nullable=False, default=list, server_default=_EMPTY_LIST)


class SearchProfile(Base):
    __tablename__ = "search_profiles"
    __table_args__ = (
        Index("ix_search_profiles_family_id", "family_id"),
        # One base profile per family, and at most one override per product line.
        Index("uq_search_profiles_family_base", "family_id", unique=True,
              postgresql_where=text("product_line_id IS NULL")),
        Index("uq_search_profiles_family_line", "family_id", "product_line_id", unique=True,
              postgresql_where=text("product_line_id IS NOT NULL")),
        CheckConstraint(f"provenance IN ({_PROVENANCE_SQL})", name="ck_search_profiles_provenance"),
        CheckConstraint("min_relevance IS NULL OR (min_relevance >= 0 AND min_relevance <= 1)",
                        name="ck_search_profiles_min_relevance"),
        CheckConstraint("lookback_days IS NULL OR lookback_days > 0",
                        name="ck_search_profiles_lookback_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False)
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="CASCADE"), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    provenance: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PROVENANCE_HUMAN_EDITED,
        server_default=PROVENANCE_HUMAN_EDITED)

    terms: Mapped[list] = _json_list()
    synonyms: Mapped[list] = _json_list()
    cas_numbers: Mapped[list] = _json_list()
    exclude_terms: Mapped[list] = _json_list()
    event_types: Mapped[list] = _json_list()
    preferred_sites: Mapped[list] = _json_list()
    blocked_sites: Mapped[list] = _json_list()

    # NULL = inherit (on an override) or use the default (on a base profile).
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    min_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    owner_editor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    family = relationship("ChemicalFamily")
    product_line = relationship("ProductLine")
    owner_editor = relationship("User")
    producers = relationship("Producer", secondary=search_profile_producers, lazy="selectin")
    regions = relationship("Region", secondary=search_profile_regions, lazy="selectin")

    @property
    def is_override(self) -> bool:
        return self.product_line_id is not None
