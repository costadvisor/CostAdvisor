"""add platform roles (User, SuperAdmin) with nullable team_id

Revision ID: c7d8e9f0a1b2
Revises: 451dfc150b30
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
import uuid as _uuid

revision = 'c7d8e9f0a1b2'
down_revision = '451dfc150b30'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Make team_id nullable so platform roles can have NULL team_id
    op.alter_column('roles', 'team_id', nullable=True)

    # 2. Drop the old non-partial unique constraint (can't express partial in old form)
    op.drop_constraint('uq_roles_team_name', 'roles', type_='unique')

    # 3. Re-create as partial indexes
    op.create_index(
        'uq_roles_team_name', 'roles', ['team_id', 'name'],
        unique=True, postgresql_where=sa.text('team_id IS NOT NULL'),
    )
    op.create_index(
        'uq_platform_role_name', 'roles', ['name'],
        unique=True, postgresql_where=sa.text('team_id IS NULL'),
    )

    # 4. Seed platform roles
    perms = {row['key']: str(row['id']) for row in
             conn.execute(sa.text("SELECT id, key FROM permissions")).mappings()}

    view_export_ids = [v for k, v in perms.items() if k.split('.')[-1] in ('view', 'export')]
    all_ids = list(perms.values())

    user_role_id = str(_uuid.uuid4())
    superadmin_role_id = str(_uuid.uuid4())

    conn.execute(sa.text("""
        INSERT INTO roles (id, team_id, name, description)
        VALUES (:id, NULL, 'User', 'Default user — view and export access')
    """), {"id": user_role_id})

    conn.execute(sa.text("""
        INSERT INTO roles (id, team_id, name, description)
        VALUES (:id, NULL, 'SuperAdmin', 'Full platform access across all resources')
    """), {"id": superadmin_role_id})

    for perm_id in view_export_ids:
        conn.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"
        ), {"r": user_role_id, "p": perm_id})

    for perm_id in all_ids:
        conn.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"
        ), {"r": superadmin_role_id, "p": perm_id})


def downgrade():
    conn = op.get_bind()

    # Remove platform roles
    conn.execute(sa.text(
        "DELETE FROM roles WHERE team_id IS NULL AND name IN ('User', 'SuperAdmin')"
    ))

    op.drop_index('uq_platform_role_name', table_name='roles')
    op.drop_index('uq_roles_team_name', table_name='roles')

    op.alter_column('roles', 'team_id', nullable=False)

    op.create_unique_constraint('uq_roles_team_name', 'roles', ['team_id', 'name'])
