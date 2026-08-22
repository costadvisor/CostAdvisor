"""Scrum 10 — close the RLS gap on roles, team_member_roles, team_invites

These three tables are team_id-scoped tenant data but have never had a
tenant_isolation policy (confirmed via grep across every existing RLS
migration) — app-layer permission checks gated them, but the DB itself would
return every team's rows to any session. `team_memberships` remains
deliberately exempt (it's the RLS bootstrap table the membership subqueries
themselves read from).

Revision ID: rls2a3b4c5d6e
Revises: rt1a2b3c4d5e
Create Date: 2026-07-25

"""
from typing import Union

from alembic import op

revision: str = "rls2a3b4c5d6e"
down_revision: Union[str, None] = "rt1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = f"team_id IN (SELECT team_memberships.team_id FROM team_memberships WHERE team_memberships.user_id = {_UID})"


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # team_invites, team_member_roles: team_id is NOT NULL on both — direct
    # membership policy, same shape as products/suppliers/cost_models etc.
    for table in ("team_invites", "team_member_roles"):
        _enable(table)
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR {_MEMBER_OF})
        """)

    # roles: team_id IS NULL means a platform role, visible to everyone (same
    # shape as the formula_templates fix in rls1f2a3b4c5d).
    _enable("roles")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON roles AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    for table in ("team_invites", "team_member_roles", "roles"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
