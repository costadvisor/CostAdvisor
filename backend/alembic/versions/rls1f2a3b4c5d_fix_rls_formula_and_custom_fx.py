"""fix RLS policies for formula_templates and custom_fx_rates to use user_id membership lookup

formula_templates and custom_fx_rates had policies that checked
current_setting('app.current_team_id') which is never set by the app.
Replace with the same membership-subquery pattern used by cost_models.

Revision ID: rls1f2a3b4c5d
Revises: fxp1a2b3c4d5
Create Date: 2026-06-19 13:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'rls1f2a3b4c5d'
down_revision: Union[str, None] = 'fxp1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = f"team_id IN (SELECT team_memberships.team_id FROM team_memberships WHERE team_memberships.user_id = {_UID})"


def upgrade() -> None:
    # formula_templates: allow platform rows (team_id IS NULL) to all; team rows via membership
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON formula_templates")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON formula_templates AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
    """)

    # custom_fx_rates: restrict to teams the user belongs to
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON custom_fx_rates")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON custom_fx_rates AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
    """)


def downgrade() -> None:
    # Restore old (broken) policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON formula_templates")
    op.execute("""
        CREATE POLICY tenant_isolation ON formula_templates AS PERMISSIVE FOR ALL
        USING (
            current_setting('app.bypass_rls'::text, true) = 'on'::text
            OR team_id IS NULL
            OR (team_id)::text = current_setting('app.current_team_id'::text, true)
        )
    """)

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON custom_fx_rates")
    op.execute("""
        CREATE POLICY tenant_isolation ON custom_fx_rates AS PERMISSIVE FOR ALL
        USING (
            current_setting('app.bypass_rls'::text, true) = 'on'::text
            OR (team_id)::text = current_setting('app.current_team_id'::text, true)
        )
    """)
