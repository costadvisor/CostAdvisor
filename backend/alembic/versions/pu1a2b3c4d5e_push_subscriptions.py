"""Web push subscriptions (PWA extras)

Per-user, no team_id and no RLS — the refresh_tokens shape. A browser
subscription belongs to a person's device and a person sits on several teams,
so team RLS would be the wrong boundary; scoping is by user_id in the router,
in the filter rather than in a check after the fetch.

Revision ID: pu1a2b3c4d5e
Revises: np1a2b3c4d5e
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "pu1a2b3c4d5e"
down_revision: Union[str, None] = "np1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Unique: re-subscribing the same browser returns the same endpoint,
        # and a row per visit would notify that device once per row.
        sa.Column("endpoint", sa.Text(), nullable=False, unique=True),
        sa.Column("p256dh_key", sa.String(255), nullable=False),
        sa.Column("auth_key", sa.String(255), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])

    # No change to alert_subscriptions: `push` is a third value on the
    # existing String(10) channel column, not a parallel delivery system, and
    # it fits.


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
