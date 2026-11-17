"""News search profiles (Wave 4, SCRUM-213 / NEWS-1).

One table for both the family's base profile (product_line_id NULL) and
per-product-line overrides, plus two join tables so profiles point at real
producers (and inherit their aliases) and real regions (by regions.code,
like every other region FK).

Tenancy: platform reference data, no team_id, no RLS. Mirrors producers and
commodity_indexes. Profiles drive platform editorial content and are read by
a system task (the monthly sweep, SCRUM-214).

Revision ID: nsp1a2b3c4d5e
Revises: lv5a1b2c3d4e5
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "nsp1a2b3c4d5e"
down_revision: Union[str, None] = "lv5a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LIST_COLUMNS = (
    "terms", "synonyms", "cas_numbers", "exclude_terms",
    "event_types", "preferred_sites", "blocked_sites",
)
_PROVENANCE = "'imported', 'ai_draft', 'human_edited', 'human_approved'"


def upgrade() -> None:
    op.create_table(
        "search_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", sa.Integer,
                  sa.ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_line_id", sa.Integer,
                  sa.ForeignKey("product_lines.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("provenance", sa.String(20), nullable=False, server_default="human_edited"),
        *[sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
          for name in _LIST_COLUMNS],
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("min_relevance", sa.Float, nullable=True),
        sa.Column("lookback_days", sa.Integer, nullable=True),
        sa.Column("owner_editor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"provenance IN ({_PROVENANCE})", name="ck_search_profiles_provenance"),
        sa.CheckConstraint("min_relevance IS NULL OR (min_relevance >= 0 AND min_relevance <= 1)",
                           name="ck_search_profiles_min_relevance"),
        sa.CheckConstraint("lookback_days IS NULL OR lookback_days > 0",
                           name="ck_search_profiles_lookback_days"),
    )
    op.create_index("ix_search_profiles_family_id", "search_profiles", ["family_id"])
    op.create_index("uq_search_profiles_family_base", "search_profiles", ["family_id"],
                    unique=True, postgresql_where=sa.text("product_line_id IS NULL"))
    op.create_index("uq_search_profiles_family_line", "search_profiles",
                    ["family_id", "product_line_id"], unique=True,
                    postgresql_where=sa.text("product_line_id IS NOT NULL"))

    op.create_table(
        "search_profile_producers",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("search_profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("producer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("producers.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "search_profile_regions",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("search_profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("region_code", sa.String(20),
                  sa.ForeignKey("regions.code", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("search_profile_regions")
    op.drop_table("search_profile_producers")
    op.drop_index("uq_search_profiles_family_line", table_name="search_profiles")
    op.drop_index("uq_search_profiles_family_base", table_name="search_profiles")
    op.drop_index("ix_search_profiles_family_id", table_name="search_profiles")
    op.drop_table("search_profiles")
