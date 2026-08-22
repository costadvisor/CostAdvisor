"""add_rbac_and_plans

Revision ID: 451dfc150b30
Revises: 2c697bf00541
Create Date: 2026-06-11 08:47:36.685238
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = '451dfc150b30'
down_revision: Union[str, None] = '2c697bf00541'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# All 38 permission definitions: (key, label, category, action)
PERMISSIONS = [
    # products
    ("products.view",       "View Products",          "products",    "view"),
    ("products.edit",       "Edit Products",          "products",    "edit"),
    ("products.delete",     "Delete Products",        "products",    "delete"),
    ("products.import",     "Import Products",        "products",    "import"),
    ("products.export",     "Export Products",        "products",    "export"),
    # cost_models
    ("cost_models.view",    "View Cost Models",       "cost_models", "view"),
    ("cost_models.edit",    "Edit Cost Models",       "cost_models", "edit"),
    ("cost_models.delete",  "Delete Cost Models",     "cost_models", "delete"),
    ("cost_models.export",  "Export Cost Models",     "cost_models", "export"),
    # suppliers
    ("suppliers.view",      "View Suppliers",         "suppliers",   "view"),
    ("suppliers.edit",      "Edit Suppliers",         "suppliers",   "edit"),
    ("suppliers.delete",    "Delete Suppliers",       "suppliers",   "delete"),
    ("suppliers.export",    "Export Suppliers",       "suppliers",   "export"),
    # indexes
    ("indexes.view",        "View Indexes",           "indexes",     "view"),
    ("indexes.edit",        "Edit Index Overrides",   "indexes",     "edit"),
    ("indexes.import",      "Import Index Data",      "indexes",     "import"),
    ("indexes.export",      "Export Index Data",      "indexes",     "export"),
    # prices
    ("prices.view",         "View Prices",            "prices",      "view"),
    ("prices.edit",         "Edit Prices",            "prices",      "edit"),
    ("prices.delete",       "Delete Prices",          "prices",      "delete"),
    ("prices.import",       "Import Prices",          "prices",      "import"),
    ("prices.export",       "Export Prices",          "prices",      "export"),
    # volumes
    ("volumes.view",        "View Volumes",           "volumes",     "view"),
    ("volumes.edit",        "Edit Volumes",           "volumes",     "edit"),
    ("volumes.delete",      "Delete Volumes",         "volumes",     "delete"),
    ("volumes.import",      "Import Volumes",         "volumes",     "import"),
    # fx_rates
    ("fx_rates.view",       "View FX Rates",          "fx_rates",    "view"),
    ("fx_rates.edit",       "Edit FX Rates",          "fx_rates",    "edit"),
    ("fx_rates.delete",     "Delete FX Rates",        "fx_rates",    "delete"),
    ("fx_rates.import",     "Import FX Rates",        "fx_rates",    "import"),
    # costing
    ("costing.view",        "Run Should-Cost & Analysis", "costing", "view"),
    # evolution
    ("evolution.view",      "View Evolution",         "evolution",   "view"),
    ("evolution.export",    "Export Evolution",       "evolution",   "export"),
    # briefs
    ("briefs.view",         "View Cost Briefs",       "briefs",      "view"),
    ("briefs.export",       "Export Cost Briefs",     "briefs",      "export"),
    # squeeze
    ("squeeze.view",        "View Squeeze Analysis",  "squeeze",     "view"),
    ("squeeze.export",      "Export Squeeze",         "squeeze",     "export"),
    # scenarios
    ("scenarios.view",      "View Scenarios",         "scenarios",   "view"),
    ("scenarios.edit",      "Edit Scenarios",         "scenarios",   "edit"),
    ("scenarios.delete",    "Delete Scenarios",       "scenarios",   "delete"),
]

VIEW_EXPORT_KEYS = {k for k, *_ in PERMISSIONS if k.endswith(".view") or k.endswith(".export")}
ALL_KEYS = {k for k, *_ in PERMISSIONS}
# Admin role: all except delete
ADMIN_KEYS = {k for k, *_ in PERMISSIONS if not k.endswith(".delete")}


def upgrade() -> None:
    # ── 1. Create new tables ────────────────────────────────────────────────────

    op.create_table(
        "permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("key", sa.String(100), unique=True, nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
    )

    op.create_table(
        "plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("description", sa.String(256), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "plan_permissions",
        sa.Column("plan_id", UUID(as_uuid=True),
                  sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", UUID(as_uuid=True),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )

    # Add plan_id to teams before roles (which reference teams)
    op.add_column("teams", sa.Column("plan_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_teams_plan_id", "teams", "plans", ["plan_id"], ["id"],
                          ondelete="SET NULL")

    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(256), nullable=True),
        sa.UniqueConstraint("team_id", "name", name="uq_roles_team_name"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", UUID(as_uuid=True),
                  sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "team_member_roles",
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("team_id", UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID(as_uuid=True),
                  sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )

    # ── 2. Seed permissions ─────────────────────────────────────────────────────

    conn = op.get_bind()

    import uuid as _uuid

    perm_ids: dict[str, str] = {}
    for key, label, category, action in PERMISSIONS:
        pid = str(_uuid.uuid4())
        perm_ids[key] = pid
        conn.execute(sa.text(
            "INSERT INTO permissions (id, key, label, category, action) "
            "VALUES (:id, :key, :label, :category, :action)"
        ), {"id": pid, "key": key, "label": label, "category": category, "action": action})

    # ── 3. Seed plans ────────────────────────────────────────────────────────────

    free_plan_id = str(_uuid.uuid4())
    dream_plan_id = str(_uuid.uuid4())

    conn.execute(sa.text(
        "INSERT INTO plans (id, name, description, is_default) VALUES "
        "(:id, :name, :desc, true)"
    ), {"id": free_plan_id, "name": "Free",
        "desc": "View and export access to all features"})

    conn.execute(sa.text(
        "INSERT INTO plans (id, name, description, is_default) VALUES "
        "(:id, :name, :desc, false)"
    ), {"id": dream_plan_id, "name": "Dream Plan",
        "desc": "Full access to all features"})

    # Free plan: view + export
    for key in VIEW_EXPORT_KEYS:
        conn.execute(sa.text(
            "INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:pid, :permid)"
        ), {"pid": free_plan_id, "permid": perm_ids[key]})

    # Dream plan: all
    for key in ALL_KEYS:
        conn.execute(sa.text(
            "INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:pid, :permid)"
        ), {"pid": dream_plan_id, "permid": perm_ids[key]})

    # ── 4. Assign Free plan to existing teams ────────────────────────────────────

    conn.execute(sa.text(
        "UPDATE teams SET plan_id = :plan_id WHERE plan_id IS NULL"
    ), {"plan_id": free_plan_id})

    # ── 5. Seed 3 roles per team + assign existing members ──────────────────────

    teams = conn.execute(sa.text("SELECT id FROM teams")).fetchall()

    for (team_id,) in teams:
        owner_role_id = str(_uuid.uuid4())
        admin_role_id = str(_uuid.uuid4())
        member_role_id = str(_uuid.uuid4())

        for role_id, name, desc in [
            (owner_role_id, "Owner", "Full access to all team data and settings"),
            (admin_role_id, "Admin", "Full access except delete operations"),
            (member_role_id, "Member", "View and export access only"),
        ]:
            conn.execute(sa.text(
                "INSERT INTO roles (id, team_id, name, description) "
                "VALUES (:id, :team_id, :name, :desc)"
            ), {"id": role_id, "team_id": str(team_id), "name": name, "desc": desc})

        # Owner role: all permissions
        for key in ALL_KEYS:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
            ), {"rid": owner_role_id, "pid": perm_ids[key]})

        # Admin role: all except delete
        for key in ADMIN_KEYS:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
            ), {"rid": admin_role_id, "pid": perm_ids[key]})

        # Member role: view + export
        for key in VIEW_EXPORT_KEYS:
            conn.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
            ), {"rid": member_role_id, "pid": perm_ids[key]})

        # Assign TeamMemberRole entries for existing memberships
        memberships = conn.execute(sa.text(
            "SELECT user_id, role FROM team_memberships WHERE team_id = :tid"
        ), {"tid": str(team_id)}).fetchall()

        role_map = {
            "owner": owner_role_id,
            "admin": admin_role_id,
            "member": member_role_id,
        }
        for (user_id, role_str) in memberships:
            mapped_role_id = role_map.get(role_str, member_role_id)
            conn.execute(sa.text(
                "INSERT INTO team_member_roles (user_id, team_id, role_id) "
                "VALUES (:uid, :tid, :rid) ON CONFLICT DO NOTHING"
            ), {"uid": str(user_id), "tid": str(team_id), "rid": mapped_role_id})


def downgrade() -> None:
    op.drop_table("team_member_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_constraint("fk_teams_plan_id", "teams", type_="foreignkey")
    op.drop_column("teams", "plan_id")
    op.drop_table("plan_permissions")
    op.drop_table("plans")
    op.drop_table("permissions")
