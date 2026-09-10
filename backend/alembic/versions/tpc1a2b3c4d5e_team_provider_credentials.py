"""Scrum 26 — team_provider_credentials

Per-team, per-provider credential storage for paid index-data vendors
(Fastmarkets/Argus/ICIS). Kept as its own table rather than columns on
team_index_sources so N commodities sharing one vendor subscription share
ONE credential to rotate. The secret column stores Fernet ciphertext
(services/provider_credentials.py) — plain Text, matching the precedent set
by demo_hosts.google_refresh_token_encrypted (dem0_1a2b3c4d5e).

Revision ID: tpc1a2b3c4d5e
Revises: ip1a2b3c4d5e
Create Date: 2026-08-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "tpc1a2b3c4d5e"
down_revision: Union[str, None] = "ip1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = f"team_id IN (SELECT team_memberships.team_id FROM team_memberships WHERE team_memberships.user_id = {_UID})"


def upgrade() -> None:
    op.create_table(
        "team_provider_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unverified"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "provider"),
    )

    op.execute("ALTER TABLE team_provider_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_provider_credentials FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON team_provider_credentials AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON team_provider_credentials")
    op.execute("ALTER TABLE team_provider_credentials NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_provider_credentials DISABLE ROW LEVEL SECURITY")
    op.drop_table("team_provider_credentials")
