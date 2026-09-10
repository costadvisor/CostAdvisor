"""SCRUM-79 / MON-1 — contracts + clauses, negotiation windows, market signals,
one threshold home, and the contracts.* permission category.

Revision ID: mon1a2b3c4d5e
Revises: cat3c1a2b3c4d
Create Date: 2026-08-28

Three tenancy shapes in one migration, each matching an existing precedent:

* contracts / clauses / coverage / windows / window-products — **strict tenant**
  (`h8i9j0k1l2m3` shape, as used by `cost_model_notes`). Contract prices and
  notice dates are the most sensitive rows in the product.
* market_signals — **platform-readable with team forks** (`tx1a2b3c4d5e` shape,
  as used by `formula_templates`): `team_id IS NULL` is a platform-curated
  signal visible to every team; a team's own analyst entries stay private.

⚠️ `has_permission` applies the **plan ceiling before roles**, so a brand-new
permission key that is not in a team's plan is denied for every non-super-admin
regardless of role. The new keys are therefore added to the Dream Plan, to the
SuperAdmin platform role, and to each team's existing Owner/Admin roles — a
member with any custom role assigned skips the membership fallback entirely, so
without the role grants the team owner would have been locked out of the
feature this migration ships.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "mon1a2b3c4d5e"
down_revision: Union[str, None] = "cat3c1a2b3c4d"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = (
    "team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)

_STRICT_TENANT_TABLES = [
    "contracts",
    "contract_clauses",
    "contract_cost_models",
    "negotiation_windows",
    "negotiation_window_cost_models",
]

CONTRACT_PERMS = {
    "contracts.view": ("View Contracts", "contracts", "view"),
    "contracts.edit": ("Edit Contracts", "contracts", "edit"),
    "contracts.delete": ("Delete Contracts", "contracts", "delete"),
}


def upgrade() -> None:
    # ── Contracts ────────────────────────────────────────────────────────────
    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Integer(),
                  sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("term_start", sa.Date(), nullable=True),
        sa.Column("term_end", sa.Date(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notice_days", sa.SmallInteger(), nullable=True),
        sa.Column("notice_deadline", sa.Date(), nullable=True),
        sa.Column("price_review_cadence", sa.String(length=20), nullable=True),
        sa.Column("indexation_formula_version_id", sa.Integer(),
                  sa.ForeignKey("formula_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_contracts_team_id", "contracts", ["team_id"])
    # The radar's primary query is "which contracts are approaching notice",
    # which is why the deadline is stored and indexed rather than recomputed.
    op.create_index("ix_contracts_notice_deadline", "contracts", ["notice_deadline"])

    op.create_table(
        "contract_clauses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clause_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("deadline_date", sa.Date(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_contract_clauses_team_id", "contract_clauses", ["team_id"])
    op.create_index("ix_contract_clauses_contract_id", "contract_clauses", ["contract_id"])

    op.create_table(
        "contract_cost_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("share_pct", sa.Numeric(6, 2), nullable=True),
        sa.UniqueConstraint("contract_id", "cost_model_id", name="uq_contract_cost_model"),
    )
    op.create_index("ix_contract_cost_models_team_id", "contract_cost_models", ["team_id"])
    op.create_index("ix_contract_cost_models_contract_id", "contract_cost_models", ["contract_id"])
    op.create_index("ix_contract_cost_models_cost_model_id", "contract_cost_models", ["cost_model_id"])

    # ── Negotiation windows ──────────────────────────────────────────────────
    op.create_table(
        "negotiation_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("driver", sa.String(length=24), nullable=False),
        sa.Column("driver_key", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_supplier_id", sa.Integer(),
                  sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scope_contract_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scope_cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=True),
        sa.Column("scope_commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("opens_on", sa.Date(), nullable=False),
        sa.Column("closes_on", sa.Date(), nullable=True),
        sa.Column("close_basis", sa.String(length=24), server_default="unknown", nullable=False),
        sa.Column("state", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("coverage", sa.String(length=12), server_default="covered", nullable=False),
        sa.Column("threshold_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("threshold_unit", sa.String(length=12), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("team_id", "driver_key", name="uq_window_team_driver_key"),
        sa.CheckConstraint(
            "coverage IN ('covered','partial','unknown')", name="ck_window_coverage",
        ),
        sa.CheckConstraint(
            "state IN ('open','closed','dismissed')", name="ck_window_state",
        ),
        # The unit must travel with the value: a bare number could be percent or
        # money, and the two are not comparable.
        sa.CheckConstraint(
            "threshold_value IS NULL OR threshold_unit IS NOT NULL",
            name="ck_window_threshold_unit_with_value",
        ),
    )
    op.create_index("ix_negotiation_windows_team_id", "negotiation_windows", ["team_id"])
    op.create_index("ix_negotiation_windows_driver_key", "negotiation_windows", ["driver_key"])

    op.create_table(
        "negotiation_window_cost_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("window_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("negotiation_windows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_model_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exposure_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("via_proxy", sa.Boolean(), nullable=True),
        sa.UniqueConstraint("window_id", "cost_model_id", name="uq_window_cost_model"),
    )
    op.create_index("ix_nwcm_team_id", "negotiation_window_cost_models", ["team_id"])
    op.create_index("ix_nwcm_window_id", "negotiation_window_cost_models", ["window_id"])
    op.create_index("ix_nwcm_cost_model_id", "negotiation_window_cost_models", ["cost_model_id"])

    # ── Market signals (platform-readable + team forks) ──────────────────────
    op.create_table(
        "market_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("origin", sa.String(length=24), server_default="manual", nullable=False),
        sa.Column("signal_type", sa.String(length=32), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("supplier_id", sa.Integer(),
                  sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("region", sa.String(length=20),
                  sa.ForeignKey("regions.code"), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("as_of_inferred", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_market_signals_team_id", "market_signals", ["team_id"])

    # ── RLS ──────────────────────────────────────────────────────────────────
    for table in _STRICT_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR {_MEMBER_OF})
        """)

    op.execute("ALTER TABLE market_signals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE market_signals FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON market_signals AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
    """)

    # ── One threshold home ───────────────────────────────────────────────────
    # The original ticket specified a per-team setting with a 10% default while
    # `alert_subscriptions.threshold_pct` already existed per subscription with a
    # default of 5. Building both as specified would leave two live thresholds
    # with different defaults and no rule for which wins. Reconciliation:
    # team default, per-subscription override, one accessor. The existing column
    # is migrated (made nullable = "inherit") rather than left stranded, and
    # existing rows keep their explicit value so nothing silently re-tunes.
    op.add_column("teams", sa.Column(
        "default_threshold_pct", sa.Numeric(6, 2), server_default="10.0", nullable=False))
    op.add_column("teams", sa.Column(
        "default_threshold_unit", sa.String(length=12), server_default="pct", nullable=False))
    op.alter_column("alert_subscriptions", "threshold_pct", nullable=True)
    op.add_column("alert_subscriptions", sa.Column(
        "threshold_unit", sa.String(length=12), nullable=True))

    # ── Window-scoped subscriptions ──────────────────────────────────────────
    op.add_column("alert_subscriptions", sa.Column(
        "supplier_id", sa.Integer(),
        sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True))
    op.add_column("alert_subscriptions", sa.Column(
        "contract_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=True))

    # ── contracts.* permissions ──────────────────────────────────────────────
    conn = op.get_bind()
    import uuid as _uuid

    perm_ids = {}
    for key, (label, category, action) in CONTRACT_PERMS.items():
        existing = conn.execute(
            sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}
        ).scalar()
        if existing:
            perm_ids[key] = str(existing)
            continue
        pid = str(_uuid.uuid4())
        perm_ids[key] = pid
        conn.execute(sa.text("""
            INSERT INTO permissions (id, key, label, category, action)
            VALUES (:id, :key, :label, :category, :action)
        """), {"id": pid, "key": key, "label": label,
               "category": category, "action": action})

    dream_plan = conn.execute(
        sa.text("SELECT id FROM plans WHERE name = 'Dream Plan'")
    ).scalar()
    if dream_plan:
        for pid in perm_ids.values():
            conn.execute(sa.text("""
                INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:p, :q)
                ON CONFLICT DO NOTHING
            """), {"p": str(dream_plan), "q": pid})

    superadmin_role = conn.execute(
        sa.text("SELECT id FROM roles WHERE team_id IS NULL AND name = 'SuperAdmin'")
    ).scalar()
    if superadmin_role:
        for pid in perm_ids.values():
            conn.execute(sa.text("""
                INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)
                ON CONFLICT DO NOTHING
            """), {"r": str(superadmin_role), "p": pid})

    # Existing team roles. A member with ANY custom role assigned skips the
    # membership-role fallback entirely, so an Owner who has the "Owner" role
    # assigned would otherwise be denied a permission that did not exist when
    # that role was seeded. Member is deliberately NOT granted: contract prices
    # and notice dates are the sensitivity this category exists to separate.
    for role_name, keys in (
        ("Owner", list(CONTRACT_PERMS)),
        ("Admin", ["contracts.view", "contracts.edit"]),
    ):
        roles = conn.execute(sa.text(
            "SELECT id FROM roles WHERE team_id IS NOT NULL AND name = :n"
        ), {"n": role_name}).fetchall()
        for (role_id,) in roles:
            for key in keys:
                conn.execute(sa.text("""
                    INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)
                    ON CONFLICT DO NOTHING
                """), {"r": str(role_id), "p": perm_ids[key]})


def downgrade() -> None:
    conn = op.get_bind()
    for key in CONTRACT_PERMS:
        pid = conn.execute(
            sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}
        ).scalar()
        if not pid:
            continue
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"),
                    {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM plan_permissions WHERE permission_id = :p"),
                    {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": str(pid)})

    op.drop_column("alert_subscriptions", "contract_id")
    op.drop_column("alert_subscriptions", "supplier_id")
    op.drop_column("alert_subscriptions", "threshold_unit")
    # Anything null on the way back down would violate the restored NOT NULL,
    # so give it the old default explicitly rather than letting the ALTER fail.
    op.execute("UPDATE alert_subscriptions SET threshold_pct = 5.0 WHERE threshold_pct IS NULL")
    op.alter_column("alert_subscriptions", "threshold_pct", nullable=False)
    op.drop_column("teams", "default_threshold_unit")
    op.drop_column("teams", "default_threshold_pct")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON market_signals")
    for table in _STRICT_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_index("ix_market_signals_team_id", table_name="market_signals")
    op.drop_table("market_signals")

    op.drop_index("ix_nwcm_cost_model_id", table_name="negotiation_window_cost_models")
    op.drop_index("ix_nwcm_window_id", table_name="negotiation_window_cost_models")
    op.drop_index("ix_nwcm_team_id", table_name="negotiation_window_cost_models")
    op.drop_table("negotiation_window_cost_models")

    op.drop_index("ix_negotiation_windows_driver_key", table_name="negotiation_windows")
    op.drop_index("ix_negotiation_windows_team_id", table_name="negotiation_windows")
    op.drop_table("negotiation_windows")

    op.drop_index("ix_contract_cost_models_cost_model_id", table_name="contract_cost_models")
    op.drop_index("ix_contract_cost_models_contract_id", table_name="contract_cost_models")
    op.drop_index("ix_contract_cost_models_team_id", table_name="contract_cost_models")
    op.drop_table("contract_cost_models")

    op.drop_index("ix_contract_clauses_contract_id", table_name="contract_clauses")
    op.drop_index("ix_contract_clauses_team_id", table_name="contract_clauses")
    op.drop_table("contract_clauses")

    op.drop_index("ix_contracts_notice_deadline", table_name="contracts")
    op.drop_index("ix_contracts_team_id", table_name="contracts")
    op.drop_table("contracts")
