"""SCRUM-76 / INT-2 — editorial_blocks + editorial_block_versions, and the one
permission revision (content.* + dimensions.*).

Revision ID: edb1a2b3c4d5e
Revises: mon1a2b3c4d5e
Create Date: 2026-08-28

Tenancy: **platform-readable with team forks** — the `tx1a2b3c4d5e` /
`rls1f2a3b4c5d` shape, not the strict-tenant shape. `team_id IS NULL` is
platform content every team can read; a team editing a platform block forks it
into a team-owned row and the platform row does not mutate. Uniqueness is
re-scoped with partial indexes the way `uq_chem_fam_platform_name` /
`uq_chem_fam_team_name` do it, because a fork legitimately shares its origin's
subject key.

⚠️ The permission revision is the load-bearing half. `has_permission` applies
the **plan ceiling before roles**, so a key absent from a team's plan is denied
for every non-super-admin regardless of role — a new category that is not
plan-granted ships silently disabled. And a member with ANY custom role
assigned skips the membership-role fallback entirely, so the seeded per-team
Owner/Admin/Member roles need the grants too or the team owner is locked out of
the feature this migration ships. Both are done below. W3-B, W3-C and W3-I all
consume these keys, which is why both prefixes land in one revision.

Platform authoring gates on `has_platform_permission()` + `UserPlatformRole`,
so a **Content Editor** platform role is seeded alongside the existing Chemist
and FX Manager roles — without it, only a super admin could author platform
content.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "edb1a2b3c4d5e"
down_revision: Union[str, None] = "mon1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = (
    "team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)

CONTENT_PERMS = {
    "content.view":    ("View Editorial Content",    "content", "view"),
    "content.edit":    ("Edit Editorial Content",    "content", "edit"),
    "content.approve": ("Approve Editorial Content", "content", "approve"),
    "content.delete":  ("Delete Editorial Content",  "content", "delete"),
    "dimensions.view":   ("View Dimensions",   "dimensions", "view"),
    "dimensions.edit":   ("Edit Dimensions",   "dimensions", "edit"),
    "dimensions.delete": ("Delete Dimensions", "dimensions", "delete"),
}

# Free plan is "view and export", so only the read keys.
FREE_KEYS = ["content.view", "dimensions.view"]
# The membership fallback gives a plain member every `view` key, so the seeded
# Member role matching that is what keeps behaviour consistent either way.
MEMBER_KEYS = FREE_KEYS
ADMIN_KEYS = [k for k in CONTENT_PERMS if not k.endswith(".delete")]

CONTENT_EDITOR_ROLE = "Content Editor"


def upgrade() -> None:
    op.create_table(
        "editorial_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_code", sa.String(length=160), nullable=False),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subfamily_id", sa.Integer(),
                  sa.ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body_format", sa.String(length=8), server_default="text", nullable=False),
        sa.Column("provenance", sa.String(length=16), server_default="imported", nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("derived_from", postgresql.JSONB(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("subject_type IN ('formula','index','subfamily','family')",
                           name="ck_editorial_subject_type"),
        sa.CheckConstraint(
            "provenance IN ('imported','ai_draft','human_edited','human_approved')",
            name="ck_editorial_provenance"),
        sa.CheckConstraint("body_format IN ('text','json')", name="ck_editorial_body_format"),
    )
    op.create_index("ix_editorial_blocks_team_id", "editorial_blocks", ["team_id"])
    op.create_index("ix_editorial_blocks_subject_code", "editorial_blocks", ["subject_code"])
    # The card read: one composite index serves "every block for this subject".
    op.create_index("ix_editorial_blocks_subject", "editorial_blocks",
                    ["subject_type", "subject_code"])

    op.create_table(
        "editorial_block_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("block_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("editorial_blocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_json", postgresql.JSONB(), nullable=True),
        sa.Column("body_format", sa.String(length=8), server_default="text", nullable=False),
        sa.Column("provenance", sa.String(length=16), server_default="imported", nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("authored_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("block_id", "version_no", name="uq_ebv_block_version"),
        sa.CheckConstraint(
            "provenance IN ('imported','ai_draft','human_edited','human_approved')",
            name="ck_ebv_provenance"),
        sa.CheckConstraint("body_format IN ('text','json')", name="ck_ebv_body_format"),
        # A version must carry the body its format declares, or a row claiming
        # `json` with an empty `body_json` reads as authored and is not.
        sa.CheckConstraint(
            "(body_format = 'text' AND body_text IS NOT NULL) OR "
            "(body_format = 'json' AND body_json IS NOT NULL)",
            name="ck_ebv_body_matches_format"),
    )
    op.create_index("ix_ebv_block_id", "editorial_block_versions", ["block_id"])

    # The two tables reference each other, so both self/cross FKs are added
    # after creation rather than inline.
    op.create_foreign_key(
        "fk_editorial_blocks_current_version", "editorial_blocks",
        "editorial_block_versions", ["current_version_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_editorial_blocks_origin", "editorial_blocks", "editorial_blocks",
        ["origin_id"], ["id"], ondelete="SET NULL",
    )

    # Uniqueness re-scoped: a fork legitimately shares its origin's subject key,
    # so one global constraint would reject every fork. `region` is nullable and
    # Postgres treats every NULL as distinct in a unique index, so the wildcard
    # is folded to a literal in the expression — otherwise two wildcard blocks
    # for the same subject+type could both be inserted.
    op.execute("""
        CREATE UNIQUE INDEX uq_editorial_platform_block
        ON editorial_blocks (subject_type, subject_code, block_type, COALESCE(region, '*'))
        WHERE team_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_editorial_team_block
        ON editorial_blocks (team_id, subject_type, subject_code, block_type,
                             COALESCE(region, '*'))
        WHERE team_id IS NOT NULL
    """)

    for table in ("editorial_blocks", "editorial_block_versions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(f"""
        CREATE POLICY tenant_isolation ON editorial_blocks AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
    """)
    # Versions have no team_id of their own — visibility is transitive through
    # the parent block, the same pattern as formula_template_components.
    op.execute(f"""
        CREATE POLICY tenant_isolation ON editorial_block_versions AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR block_id IN (
            SELECT id FROM editorial_blocks
            WHERE team_id IS NULL OR {_MEMBER_OF}
        ))
    """)

    # ── The one permission revision ──────────────────────────────────────────
    conn = op.get_bind()
    import uuid as _uuid

    perm_ids = {}
    for key, (label, category, action) in CONTENT_PERMS.items():
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

    def grant_plan(plan_name, keys):
        plan = conn.execute(
            sa.text("SELECT id FROM plans WHERE name = :n"), {"n": plan_name}
        ).scalar()
        if not plan:
            return
        for key in keys:
            conn.execute(sa.text("""
                INSERT INTO plan_permissions (plan_id, permission_id) VALUES (:p, :q)
                ON CONFLICT DO NOTHING
            """), {"p": str(plan), "q": perm_ids[key]})

    grant_plan("Dream Plan", list(CONTENT_PERMS))
    grant_plan("Free", FREE_KEYS)

    def grant_role_ids(role_ids, keys):
        for role_id in role_ids:
            for key in keys:
                conn.execute(sa.text("""
                    INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)
                    ON CONFLICT DO NOTHING
                """), {"r": str(role_id), "p": perm_ids[key]})

    superadmin = conn.execute(sa.text(
        "SELECT id FROM roles WHERE team_id IS NULL AND name = 'SuperAdmin'"
    )).scalar()
    if superadmin:
        grant_role_ids([superadmin], list(CONTENT_PERMS))

    # Platform authoring needs a platform role that is not "be a super admin".
    editor = conn.execute(sa.text(
        "SELECT id FROM roles WHERE team_id IS NULL AND name = :n"
    ), {"n": CONTENT_EDITOR_ROLE}).scalar()
    if not editor:
        editor = str(_uuid.uuid4())
        conn.execute(sa.text("""
            INSERT INTO roles (id, team_id, name, description)
            VALUES (:id, NULL, :n, 'Author and approve platform editorial content')
        """), {"id": editor, "n": CONTENT_EDITOR_ROLE})
    grant_role_ids([editor], list(CONTENT_PERMS))

    for role_name, keys in (("Owner", list(CONTENT_PERMS)),
                            ("Admin", ADMIN_KEYS),
                            ("Member", MEMBER_KEYS)):
        rows = conn.execute(sa.text(
            "SELECT id FROM roles WHERE team_id IS NOT NULL AND name = :n"
        ), {"n": role_name}).fetchall()
        grant_role_ids([r[0] for r in rows], keys)


def downgrade() -> None:
    conn = op.get_bind()
    for key in CONTENT_PERMS:
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
    conn.execute(sa.text("DELETE FROM roles WHERE team_id IS NULL AND name = :n"),
                 {"n": CONTENT_EDITOR_ROLE})

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON editorial_block_versions")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON editorial_blocks")
    op.execute("DROP INDEX IF EXISTS uq_editorial_team_block")
    op.execute("DROP INDEX IF EXISTS uq_editorial_platform_block")
    op.drop_constraint("fk_editorial_blocks_origin", "editorial_blocks", type_="foreignkey")
    op.drop_constraint("fk_editorial_blocks_current_version", "editorial_blocks",
                       type_="foreignkey")
    op.drop_index("ix_ebv_block_id", table_name="editorial_block_versions")
    op.drop_table("editorial_block_versions")
    op.drop_index("ix_editorial_blocks_subject", table_name="editorial_blocks")
    op.drop_index("ix_editorial_blocks_subject_code", table_name="editorial_blocks")
    op.drop_index("ix_editorial_blocks_team_id", table_name="editorial_blocks")
    op.drop_table("editorial_blocks")
