"""Scrum 70 (Part 1) — index_projection_runs / index_projection_points

Forecast storage for the projection service: one row per fitted/held/no-
history vintage of a (commodity, region) series, plus its future points.

Deliberately a NEW pair of tables rather than writing forward rows into
index_values: the existing buy-window trailing signal walks
_available_index_range's unfiltered max()/min() over index_values, and any
forward rows landed there would silently shift that "trailing" window. Two
separate tables make that impossible by construction, not by an added filter.

Platform-level like commodity_indexes/index_values (no team_id) — no RLS
policy needed, same as those tables.

Revision ID: ip1a2b3c4d5e
Revises: ae1a2b3c4d5e
Create Date: 2026-08-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "ip1a2b3c4d5e"
down_revision: Union[str, None] = "ae1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_projection_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=False),
        sa.Column("vintage_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("history_from_year", sa.SmallInteger(), nullable=True),
        sa.Column("history_from_quarter", sa.SmallInteger(), nullable=True),
        sa.Column("history_to_year", sa.SmallInteger(), nullable=True),
        sa.Column("history_to_quarter", sa.SmallInteger(), nullable=True),
        sa.Column("history_points_used", sa.Integer(), nullable=False),
        sa.Column("horizon_quarters", sa.Integer(), nullable=False),
        sa.Column("residual_std", sa.Numeric(14, 6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["commodity_id"], ["commodity_indexes.id"]),
        sa.ForeignKeyConstraint(["region"], ["regions.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_projection_runs_lookup",
        "index_projection_runs",
        ["commodity_id", "region", "vintage_at"],
    )

    op.create_table(
        "index_projection_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("quarter", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Numeric(14, 4), nullable=False),
        sa.Column("ci_lo", sa.Numeric(14, 4), nullable=True),
        sa.Column("ci_hi", sa.Numeric(14, 4), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["index_projection_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "year", "quarter"),
    )
    op.create_index(
        "idx_projection_points_lookup",
        "index_projection_points",
        ["run_id", "year", "quarter"],
    )


def downgrade() -> None:
    op.drop_index("idx_projection_points_lookup", table_name="index_projection_points")
    op.drop_table("index_projection_points")
    op.drop_index("idx_projection_runs_lookup", table_name="index_projection_runs")
    op.drop_table("index_projection_runs")
