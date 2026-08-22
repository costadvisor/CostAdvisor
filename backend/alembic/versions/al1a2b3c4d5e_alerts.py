"""Scrum 24 — alerts: subscriptions + events + team slack webhook

Revision ID: al1a2b3c4d5e
Revises: nt1a2b3c4d5e
Create Date: 2026-07-25

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "al1a2b3c4d5e"
down_revision: Union[str, None] = "nt1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = f"team_id IN (SELECT team_memberships.team_id FROM team_memberships WHERE team_memberships.user_id = {_UID})"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
    """)


def upgrade() -> None:
    op.add_column("teams", sa.Column("slack_webhook_url", sa.String(length=512), nullable=True))

    op.create_table(
        "alert_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("threshold_pct", sa.Numeric(6, 2), server_default="5.0", nullable=False),
        sa.Column("channel", sa.String(length=10), server_default="email", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_alert_subscriptions_team_id", "alert_subscriptions", ["team_id"])

    op.create_table(
        "alert_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("alert_subscriptions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=10), server_default="email", nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_alert_events_team_id", "alert_events", ["team_id"])
    op.create_index("ix_alert_events_dedup_key", "alert_events", ["dedup_key"])

    _enable_rls("alert_subscriptions")
    _enable_rls("alert_events")


def downgrade() -> None:
    for t in ("alert_events", "alert_subscriptions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_alert_events_dedup_key", table_name="alert_events")
    op.drop_index("ix_alert_events_team_id", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_alert_subscriptions_team_id", table_name="alert_subscriptions")
    op.drop_table("alert_subscriptions")
    op.drop_column("teams", "slack_webhook_url")
