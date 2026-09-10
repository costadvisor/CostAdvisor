"""In-app support: threads (person/team-scoped), messages, a platform-wide
canned-response library, and an admin-managed FAQ/knowledge base — plus the
one permission revision it needs.

Revision ID: sup1a2b3c4d5e
Revises: trs1a2b3c4d5e
Create Date: 2026-09-09

Tenancy: support_threads/support_messages are strict-tenant (the al1a2b3c4d5e
shape) — every thread belongs to exactly one team. "Person" vs "team" scope
is NOT a second RLS predicate: this codebase has no per-user RLS anywhere
(AlertSubscription is the precedent — its RLS is plain team-membership, and
per-user narrowing is an application-layer filter/ownership-check in the
router). support_canned_responses/support_faqs are platform reference data,
no team_id, no RLS — mirrors commodity_indexes.

⚠️ Permission revision, same rule as edb1a2b3c4d5e: `has_permission` applies
the plan ceiling before roles, so support.* must be plan-granted or every
non-super-admin is denied regardless of role; a member with any custom role
skips the membership fallback, so the seeded per-team Owner/Admin/Member
roles need the grants too. A platform "Support Agent" role is seeded
alongside Content Editor/Chemist/FX Manager, since platform-wide reply/manage
access needs a role that is not "be a super admin".
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "sup1a2b3c4d5e"
down_revision: Union[str, None] = "trs1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = (
    "team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)

SUPPORT_PERMS = {
    "support.reply":         ("Reply to Support Threads", "support", "reply"),
    "support.manage_content": ("Manage Support Content",  "support", "manage_content"),
}
# Every plan/role already sees their own threads via plain team membership +
# the router's own-thread check — support.* only gates the STAFF side
# (seeing/replying to every team's threads, managing canned responses/FAQ),
# so it is deliberately not granted to Free/Member at all.
SUPPORT_AGENT_ROLE = "Support Agent"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
        USING ({_BYPASS} OR {_MEMBER_OF})
    """)


def upgrade() -> None:
    op.create_table(
        "support_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=10), server_default="person", nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("assigned_admin_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("scope IN ('person', 'team')", name="ck_support_thread_scope"),
        sa.CheckConstraint(
            "status IN ('open', 'pending', 'resolved', 'closed')", name="ck_support_thread_status",
        ),
    )
    op.create_index("ix_support_threads_team_id", "support_threads", ["team_id"])
    op.create_index("ix_support_threads_user_id", "support_threads", ["user_id"])

    op.create_table(
        "support_canned_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "support_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("support_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_staff", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("canned_response_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("support_canned_responses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_support_messages_team_id", "support_messages", ["team_id"])
    op.create_index("ix_support_messages_thread_id", "support_messages", ["thread_id"])

    op.create_table(
        "support_faqs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("question", sa.String(length=300), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_support_faqs_category", "support_faqs", ["category"])

    _enable_rls("support_threads")
    _enable_rls("support_messages")

    # ── The one permission revision ──────────────────────────────────────────
    conn = op.get_bind()
    import uuid as _uuid

    perm_ids = {}
    for key, (label, category, action) in SUPPORT_PERMS.items():
        existing = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}).scalar()
        if existing:
            perm_ids[key] = str(existing)
            continue
        pid = str(_uuid.uuid4())
        perm_ids[key] = pid
        conn.execute(sa.text("""
            INSERT INTO permissions (id, key, label, category, action)
            VALUES (:id, :key, :label, :category, :action)
        """), {"id": pid, "key": key, "label": label, "category": category, "action": action})

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
        grant_role_ids([superadmin], list(SUPPORT_PERMS))

    agent = conn.execute(sa.text(
        "SELECT id FROM roles WHERE team_id IS NULL AND name = :n"
    ), {"n": SUPPORT_AGENT_ROLE}).scalar()
    if not agent:
        agent = str(_uuid.uuid4())
        conn.execute(sa.text("""
            INSERT INTO roles (id, team_id, name, description)
            VALUES (:id, NULL, :n, 'Reply to support threads and manage canned responses/FAQ')
        """), {"id": agent, "n": SUPPORT_AGENT_ROLE})
    grant_role_ids([agent], list(SUPPORT_PERMS))

    # Deliberately NOT granted to Free/Dream plans or seeded team roles —
    # support.* is staff-only, not a team-plan feature. A team's own members
    # reach their own threads via plain team membership + the router's
    # own-thread check, with no permission key involved at all.


def downgrade() -> None:
    conn = op.get_bind()
    for key in SUPPORT_PERMS:
        pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": key}).scalar()
        if not pid:
            continue
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :p"), {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM plan_permissions WHERE permission_id = :p"), {"p": str(pid)})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :p"), {"p": str(pid)})
    conn.execute(sa.text("DELETE FROM roles WHERE team_id IS NULL AND name = :n"), {"n": SUPPORT_AGENT_ROLE})

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON support_messages")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON support_threads")
    op.drop_index("ix_support_faqs_category", table_name="support_faqs")
    op.drop_table("support_faqs")
    op.drop_index("ix_support_messages_thread_id", table_name="support_messages")
    op.drop_index("ix_support_messages_team_id", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_table("support_canned_responses")
    op.drop_index("ix_support_threads_user_id", table_name="support_threads")
    op.drop_index("ix_support_threads_team_id", table_name="support_threads")
    op.drop_table("support_threads")
