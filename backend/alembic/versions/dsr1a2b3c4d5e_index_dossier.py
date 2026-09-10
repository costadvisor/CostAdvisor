"""DB-7 — index dossier storage + platform volatility calibration.

Revision ID: dsr1a2b3c4d5e
Revises: dim1a2b3c4d5e
Create Date: 2026-08-28

All platform-level: no `team_id` and no RLS, following `commodity_indexes` and
`producers`. A dossier is a fact about a public price series, not tenant data —
and `index_producer_roles` FKs to unit 8's `producers`, which is why this
revision has to come after it.

Nothing here stores a computed snapshot. `index_feeds.csv`'s `current_value`,
`change_pct`, `volatility_pct`, `cycle_pct`, `card_status` and
`has_intel_block` are all recomputable from the series, and `volatility_pct` is
additionally self-contradictory (three series carry two different values across
their own cards). The volatility ladder below is **regenerated**, not imported.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dsr1a2b3c4d5e"
down_revision: Union[str, None] = "dim1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_dossiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("quote_type", sa.String(length=120), nullable=True),
        sa.Column("formula_role", sa.String(length=200), nullable=True),
        sa.Column("access_tier", sa.String(length=32), nullable=True),
        sa.Column("anchor_correlation", sa.Numeric(4, 3), nullable=True),
        sa.Column("anchor_correlation_raw", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # `region` is nullable and Postgres treats every NULL as distinct in a
        # unique constraint, so the series-wide row could otherwise be inserted
        # twice. A partial-index pair enforces it instead.
    )
    op.create_index("ix_index_dossiers_commodity_id", "index_dossiers", ["commodity_id"])
    op.execute("""
        CREATE UNIQUE INDEX uq_index_dossier_series_wide
        ON index_dossiers (commodity_id) WHERE region IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_index_dossier_series_region
        ON index_dossiers (commodity_id, region) WHERE region IS NOT NULL
    """)

    op.create_table(
        "index_drivers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("correlation", sa.Numeric(4, 3), nullable=True),
        sa.Column("lag_raw", sa.String(length=120), nullable=True),
        sa.Column("lag_days_min", sa.SmallInteger(), nullable=True),
        sa.Column("lag_days_max", sa.SmallInteger(), nullable=True),
        sa.Column("signal_raw", sa.String(length=120), nullable=True),
        # NOT a CHECK-constrained enum: the source vocabulary is 20 distinct
        # values across 66 rows ("dominant structural", "medium geopolitical"),
        # so the raw string is preserved and the strength derived from it.
        sa.Column("signal_strength", sa.String(length=16), nullable=True),
        # Sized for prose, not a percentage: the source puts sentences in this
        # field (71 chars at the longest), which a String(40) rejected.
        sa.Column("move_raw", sa.String(length=200), nullable=True),
        sa.Column("move_up", sa.Boolean(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_index_drivers_dossier_id", "index_drivers", ["dossier_id"])

    op.create_table(
        "index_chain_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(length=16), server_default="node", nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.CheckConstraint("node_type IN ('node','transform')", name="ck_chain_node_type"),
    )
    op.create_index("ix_index_chain_nodes_dossier_id", "index_chain_nodes", ["dossier_id"])

    op.create_table(
        "index_role_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flag_kind", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("flag_kind IN ('role','sustainability')", name="ck_flag_kind"),
    )
    op.create_index("ix_index_role_flags_dossier_id", "index_role_flags", ["dossier_id"])

    op.create_table(
        "index_splits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("split_type", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("split_type IN ('supply','demand')", name="ck_split_type"),
    )
    op.create_index("ix_index_splits_dossier_id", "index_splits", ["dossier_id"])

    op.create_table(
        "index_producer_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        # The FK that keeps one company master instead of two (unit 8).
        sa.Column("producer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("producers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="producer", nullable=False),
        sa.Column("share_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("share_disclosed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("location", sa.String(length=160), nullable=True),
        sa.Column("regions_raw", postgresql.JSONB(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("raw_name", sa.String(length=400), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("dossier_id", "producer_id", "role",
                            name="uq_index_producer_role"),
        sa.CheckConstraint("role IN ('producer','price_setter')",
                           name="ck_index_producer_role"),
    )
    op.create_index("ix_index_producer_roles_dossier_id", "index_producer_roles",
                    ["dossier_id"])
    op.create_index("ix_index_producer_roles_producer_id", "index_producer_roles",
                    ["producer_id"])

    op.create_table(
        "index_negotiation_pointers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dossier_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_dossiers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_index_neg_pointers_dossier_id", "index_negotiation_pointers",
                    ["dossier_id"])

    # ── The calibration ladder ───────────────────────────────────────────────
    op.create_table(
        "volatility_calibrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("method", sa.String(length=40), server_default="mom_pct_stdev",
                  nullable=False),
        sa.Column("n_rungs", sa.SmallInteger(), nullable=False),
        sa.Column("n_series", sa.Integer(), nullable=False),
        sa.Column("min_points", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("n_rungs >= 2", name="ck_calibration_rungs"),
    )
    # Only one active calibration at a time — a reader that had to choose
    # between two would be picking a percentile scale at random.
    op.execute("""
        CREATE UNIQUE INDEX uq_volatility_calibration_active
        ON volatility_calibrations ((true)) WHERE is_active
    """)

    op.create_table(
        "volatility_breakpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("calibration_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("volatility_calibrations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rung", sa.SmallInteger(), nullable=False),
        sa.Column("dispersion", sa.Numeric(10, 4), nullable=False),
        sa.UniqueConstraint("calibration_id", "rung", name="uq_volatility_breakpoint"),
    )
    op.create_index("ix_volatility_breakpoints_calibration_id",
                    "volatility_breakpoints", ["calibration_id"])


def downgrade() -> None:
    op.drop_index("ix_volatility_breakpoints_calibration_id",
                  table_name="volatility_breakpoints")
    op.drop_table("volatility_breakpoints")
    op.execute("DROP INDEX IF EXISTS uq_volatility_calibration_active")
    op.drop_table("volatility_calibrations")

    op.drop_index("ix_index_neg_pointers_dossier_id",
                  table_name="index_negotiation_pointers")
    op.drop_table("index_negotiation_pointers")
    op.drop_index("ix_index_producer_roles_producer_id", table_name="index_producer_roles")
    op.drop_index("ix_index_producer_roles_dossier_id", table_name="index_producer_roles")
    op.drop_table("index_producer_roles")
    op.drop_index("ix_index_splits_dossier_id", table_name="index_splits")
    op.drop_table("index_splits")
    op.drop_index("ix_index_role_flags_dossier_id", table_name="index_role_flags")
    op.drop_table("index_role_flags")
    op.drop_index("ix_index_chain_nodes_dossier_id", table_name="index_chain_nodes")
    op.drop_table("index_chain_nodes")
    op.drop_index("ix_index_drivers_dossier_id", table_name="index_drivers")
    op.drop_table("index_drivers")
    op.execute("DROP INDEX IF EXISTS uq_index_dossier_series_region")
    op.execute("DROP INDEX IF EXISTS uq_index_dossier_series_wide")
    op.drop_index("ix_index_dossiers_commodity_id", table_name="index_dossiers")
    op.drop_table("index_dossiers")
