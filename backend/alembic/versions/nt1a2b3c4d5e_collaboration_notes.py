"""Scrum 25 — collaboration: cost_model_notes + negotiation_state flag

Revision ID: nt1a2b3c4d5e
Revises: pf1a2b3c4d5e
Create Date: 2026-07-25

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "nt1a2b3c4d5e"
down_revision: Union[str, None] = "pf1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = f"team_id IN (SELECT team_memberships.team_id FROM team_memberships WHERE team_memberships.user_id = {_UID})"


def upgrade() -> None:
    op.add_column(
        "cost_models",
        sa.Column("negotiation_state", sa.String(length=20), server_default="none", nullable=False),
    )

    op.create_table(
        "cost_model_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_note_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_model_notes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_cost_model_notes_cost_model_id", "cost_model_notes", ["cost_model_id"])
    op.create_index("ix_cost_model_notes_team_id", "cost_model_notes", ["team_id"])

    op.execute("ALTER TABLE cost_model_notes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cost_model_notes FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON cost_model_notes AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON cost_model_notes")
    op.drop_index("ix_cost_model_notes_team_id", table_name="cost_model_notes")
    op.drop_index("ix_cost_model_notes_cost_model_id", table_name="cost_model_notes")
    op.drop_table("cost_model_notes")
    op.drop_column("cost_models", "negotiation_state")
