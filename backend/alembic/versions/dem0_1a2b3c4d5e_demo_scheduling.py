"""add demo scheduling tables (demo_hosts, demo_blocked_slots, demo_requests)

Revision ID: dem0_1a2b3c4d5e
Revises: rls1f2a3b4c5d
Create Date: 2026-06-21 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'dem0_1a2b3c4d5e'
down_revision: Union[str, None] = 'rls1f2a3b4c5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── demo_hosts ──────────────────────────────────────────────────────────
    op.create_table(
        "demo_hosts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("slot_duration_minutes", sa.Integer, nullable=False, server_default="30"),
        sa.Column("working_days", postgresql.ARRAY(sa.Integer),
                  nullable=False, server_default=sa.text("ARRAY[0,1,2,3,4]")),
        sa.Column("working_start", sa.String(5), nullable=False, server_default="09:00"),
        sa.Column("working_end", sa.String(5), nullable=False, server_default="18:00"),
        sa.Column("google_email", sa.String(255), nullable=True),
        sa.Column("google_refresh_token_encrypted", sa.Text, nullable=True),
        sa.Column("google_token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    # ── demo_blocked_slots ───────────────────────────────────────────────────
    op.create_table(
        "demo_blocked_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("host_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demo_hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blocked_date", sa.String(10), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_demo_blocked_slots_host_date",
                    "demo_blocked_slots", ["host_id", "blocked_date"])

    # ── demo_requests ────────────────────────────────────────────────────────
    op.create_table(
        "demo_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("company", sa.String(128), nullable=False),
        sa.Column("requested_date", sa.String(10), nullable=False),
        sa.Column("requested_start", sa.String(5), nullable=False),
        sa.Column("requested_end", sa.String(5), nullable=False),
        sa.Column("visitor_timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("assigned_host_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("demo_hosts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("meet_link", sa.Text, nullable=True),
        sa.Column("calendar_event_id", sa.String(255), nullable=True),
        sa.Column("remarks", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    # Partial unique index: one active (pending/accepted) request per email
    op.execute("""
        CREATE UNIQUE INDEX demo_requests_email_active_idx
        ON demo_requests (email)
        WHERE status IN ('pending', 'accepted')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS demo_requests_email_active_idx")
    op.drop_table("demo_requests")
    op.drop_index("ix_demo_blocked_slots_host_date", table_name="demo_blocked_slots")
    op.drop_table("demo_blocked_slots")
    op.drop_table("demo_hosts")
