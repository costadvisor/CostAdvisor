"""Index metadata + proxy-mapping fields on commodity_indexes (Scrum 57)

Adds index-level metadata (access_tier, role, retrieval_status) and the
free-data proxy layer (free_source_name, free_source_url, structured proxy_logic,
proxy_for self-FK). All nullable/additive — the seed loader populates them.
`frequency` already exists on the table and is reused.

commodity_indexes stays region-agnostic (no region column) — region continues to
live on index_values (commodity, region, year, quarter). No RLS (platform table).

Revision ID: im1a2b3c4d5e
Revises: rg1a2b3c4d5e
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "im1a2b3c4d5e"
down_revision: Union[str, None] = "rg1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reference categories (e.g. "Monomers — oxygenated & specialty") exceed the old 32.
    op.alter_column("commodity_indexes", "category", type_=sa.String(length=64))
    op.add_column("commodity_indexes", sa.Column("access_tier", sa.String(length=16), nullable=True))
    op.add_column("commodity_indexes", sa.Column("role", sa.String(length=16), nullable=True))
    op.add_column("commodity_indexes", sa.Column("retrieval_status", sa.String(length=16), nullable=True))
    op.add_column("commodity_indexes", sa.Column("free_source_name", sa.String(length=255), nullable=True))
    op.add_column("commodity_indexes", sa.Column("free_source_url", sa.String(length=512), nullable=True))
    op.add_column("commodity_indexes", sa.Column("proxy_logic", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("commodity_indexes", sa.Column("proxy_for_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_commodity_indexes_proxy_for", "commodity_indexes", "commodity_indexes",
        ["proxy_for_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_commodity_indexes_proxy_for", "commodity_indexes", type_="foreignkey")
    for col in ("proxy_for_id", "proxy_logic", "free_source_url", "free_source_name",
                "retrieval_status", "role", "access_tier"):
        op.drop_column("commodity_indexes", col)
    # Truncate back to the old width (safe if longer values were stored meanwhile).
    op.alter_column("commodity_indexes", "category", type_=sa.String(length=32),
                    postgresql_using="left(category, 32)")
