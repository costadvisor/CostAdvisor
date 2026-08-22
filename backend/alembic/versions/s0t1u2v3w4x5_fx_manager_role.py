"""add FX Manager platform role (fx_rates permissions already seeded)

Revision ID: s0t1u2v3w4x5
Revises: q8r9s0t1u2v3
Create Date: 2026-06-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import uuid as _uuid

revision: str = 's0t1u2v3w4x5'
down_revision: Union[str, None] = 'q8r9s0t1u2v3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # fx_rates.* permissions already exist — fetch their IDs
    rows = conn.execute(sa.text(
        "SELECT id, key FROM permissions WHERE category = 'fx_rates'"
    )).fetchall()
    perm_ids = [str(r[0]) for r in rows]

    # Ensure fx_rates.* are in Dream Plan (ON CONFLICT DO NOTHING for idempotency)
    dream_plan = conn.execute(
        sa.text("SELECT id FROM plans WHERE name = 'Dream Plan'")
    ).scalar()
    if dream_plan:
        for pid in perm_ids:
            conn.execute(sa.text("""
                INSERT INTO plan_permissions (plan_id, permission_id)
                VALUES (:p, :q) ON CONFLICT DO NOTHING
            """), {"p": str(dream_plan), "q": pid})

    # Ensure fx_rates.* are in SuperAdmin role
    superadmin_role = conn.execute(
        sa.text("SELECT id FROM roles WHERE team_id IS NULL AND name = 'SuperAdmin'")
    ).scalar()
    if superadmin_role:
        for pid in perm_ids:
            conn.execute(sa.text("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (:r, :p) ON CONFLICT DO NOTHING
            """), {"r": str(superadmin_role), "p": pid})

    # Seed FX Manager platform role (skip if already exists)
    existing = conn.execute(sa.text(
        "SELECT id FROM roles WHERE team_id IS NULL AND name = 'FX Manager'"
    )).scalar()
    if not existing:
        fx_manager_id = str(_uuid.uuid4())
        conn.execute(sa.text("""
            INSERT INTO roles (id, team_id, name, description)
            VALUES (:id, NULL, 'FX Manager', 'Manage platform and team FX rate overrides')
        """), {"id": fx_manager_id})
        for pid in perm_ids:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"
            ), {"r": fx_manager_id, "p": pid})


def downgrade() -> None:
    conn = op.get_bind()
    # Remove FX Manager role (cascades role_permissions)
    conn.execute(sa.text(
        "DELETE FROM roles WHERE team_id IS NULL AND name = 'FX Manager'"
    ))
