"""add formula_templates, user_platform_roles, formulas permissions, Chemist role

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-06-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid as _uuid

revision: str = 'q8r9s0t1u2v3'
down_revision: Union[str, None] = 'p7q8r9s0t1u2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create formula_templates table
    op.create_table(
        'formula_templates',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('team_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('expression', sa.Text(), nullable=False),
        sa.Column('variables', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. Enable RLS — platform formulas (team_id IS NULL) are visible to all authenticated teams
    conn.execute(sa.text("ALTER TABLE formula_templates ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text("ALTER TABLE formula_templates FORCE ROW LEVEL SECURITY"))
    conn.execute(sa.text("""
        CREATE POLICY tenant_isolation ON formula_templates
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR team_id IS NULL
            OR team_id::text = current_setting('app.current_team_id', true)
        )
        WITH CHECK (
            current_setting('app.bypass_rls', true) = 'on'
            OR team_id IS NULL
            OR team_id::text = current_setting('app.current_team_id', true)
        )
    """))

    # 3. Create user_platform_roles junction table (no RLS — admin-only access)
    op.create_table(
        'user_platform_roles',
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    )

    # 4. Seed 3 new permissions for formula templates
    formula_perms = {
        'formulas.view':   ('View Formula Templates',   'formulas', 'view'),
        'formulas.edit':   ('Edit Formula Templates',   'formulas', 'edit'),
        'formulas.delete': ('Delete Formula Templates', 'formulas', 'delete'),
    }
    perm_ids = {}
    for key, (label, category, action) in formula_perms.items():
        pid = str(_uuid.uuid4())
        perm_ids[key] = pid
        conn.execute(sa.text("""
            INSERT INTO permissions (id, key, label, category, action)
            VALUES (:id, :key, :label, :category, :action)
        """), {"id": pid, "key": key, "label": label, "category": category, "action": action})

    # 5. Add formulas.* to Dream Plan
    dream_plan = conn.execute(
        sa.text("SELECT id FROM plans WHERE name = 'Dream Plan'")
    ).scalar()
    if dream_plan:
        for pid in perm_ids.values():
            conn.execute(sa.text(
                "INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:p, :q)"
            ), {"p": str(dream_plan), "q": pid})

    # 6. Add formulas.* to the SuperAdmin platform role
    superadmin_role = conn.execute(
        sa.text("SELECT id FROM roles WHERE team_id IS NULL AND name = 'SuperAdmin'")
    ).scalar()
    if superadmin_role:
        for pid in perm_ids.values():
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"
            ), {"r": str(superadmin_role), "p": pid})

    # 7. Seed Chemist platform role with formulas.view/edit/delete
    chemist_role_id = str(_uuid.uuid4())
    conn.execute(sa.text("""
        INSERT INTO roles (id, team_id, name, description)
        VALUES (:id, NULL, 'Chemist', 'Manage platform-level formula templates')
    """), {"id": chemist_role_id})
    for pid in perm_ids.values():
        conn.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"
        ), {"r": chemist_role_id, "p": pid})


def downgrade() -> None:
    conn = op.get_bind()

    # Remove Chemist platform role (cascades role_permissions)
    conn.execute(sa.text(
        "DELETE FROM roles WHERE team_id IS NULL AND name = 'Chemist'"
    ))

    # Remove formula permissions (cascades role_permissions + plan_permissions)
    conn.execute(sa.text(
        "DELETE FROM permissions WHERE category = 'formulas'"
    ))

    op.drop_table('user_platform_roles')
    op.drop_table('formula_templates')
