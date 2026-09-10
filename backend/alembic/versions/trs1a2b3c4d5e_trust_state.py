"""SCRUM-78 / INT-4 — expert sign-off + derived trust state.

Revision ID: trs1a2b3c4d5e
Revises: ssn1a2b3c4d5e
Create Date: 2026-08-28

Four changes to `formula_region_coverage`, all extending the review state that
already shipped rather than adding a second one — the UI, the review endpoint
and the seed preservation logic all write this row, and a parallel model would
give two answers to "has an expert vouched for this".

1. **`reviewed_by_id` becomes a real users FK**, following
   `access_request.reviewed_by_id`. The column it replaces held
   `current_user.email`, so the record decayed the moment somebody changed their
   address. Existing values are emails, so they are backfilled by matching
   `users.email`; the legacy column is **kept**, not dropped, so a sign-off by
   somebody who has since left is still explicable.
2. **`trust_grade` + `trust_inputs` + `trust_computed_at`** — the derived grade
   that replaces `data_confidence` as the driver of `needs_review`, in its own
   field. Neither `coverage_tier` nor `proxy_density_tier` is touched: they
   answer "how well covered is this", the grade answers "is this worth a
   human's time", and coverage is an input to the grade.
3. **`review_fingerprint`** — a digest of the reviewed line set, so a sign-off
   returns to the queue when the weights or index inputs underneath it move
   instead of showing a stale green tick.
4. **`review_derived_from`** — the descriptor of what was signed off, using
   CON-5's field name and JSONB shape rather than a second format for the same
   idea.

Plus the gotcha the ticket names: **`audit_logs.team_id` becomes nullable**, so
a platform-grain sign-off is auditable without borrowing a tenant. It was NOT
NULL with an FK to teams, which left callers on platform data choosing between
attributing the event to whichever team the actor happened to belong to and
using a nil-UUID sentinel that violates the FK and loses the row at commit.

The RLS policy is re-cut to match: NULL rows stay **invisible** to every tenant
(only the bypass context reads them, which is how the super-admin platform audit
log already works), while `WITH CHECK` admits them so a platform action can
write one. An append-only log that records its actor is exactly the thing that
should be writable and not tenant-readable.

No permission keys here: the review endpoint moves onto `content.approve`,
created by SCRUM-76's single revision.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "trs1a2b3c4d5e"
down_revision: Union[str, None] = "ssn1a2b3c4d5e"
branch_labels = None
depends_on = None


_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
_MEMBERSHIP = (
    "audit_logs.team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)


def upgrade() -> None:
    # ── Platform-grain audit ────────────────────────────────────────────────
    op.alter_column("audit_logs", "team_id", existing_type=postgresql.UUID(),
                    nullable=True)
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON audit_logs")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON audit_logs
        USING ({_BYPASS} OR {_MEMBERSHIP})
        WITH CHECK ({_BYPASS} OR {_MEMBERSHIP} OR audit_logs.team_id IS NULL)
    """)

    op.add_column("formula_region_coverage", sa.Column(
        "reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_frc_reviewed_by", "formula_region_coverage", "users",
        ["reviewed_by_id"], ["id"], ondelete="SET NULL",
    )
    # Backfill by matching the email the old column stored. A reviewer whose
    # address no longer exists simply keeps the legacy string and gets no FK,
    # which is the honest outcome — inventing a user id would be worse.
    op.execute("""
        UPDATE formula_region_coverage AS c
        SET reviewed_by_id = u.id
        FROM users AS u
        WHERE c.reviewed_by IS NOT NULL
          AND lower(c.reviewed_by) = lower(u.email)
    """)

    op.add_column("formula_region_coverage", sa.Column(
        "trust_grade", sa.String(length=16), nullable=True))
    op.add_column("formula_region_coverage", sa.Column(
        "trust_inputs", postgresql.JSONB(), nullable=True))
    op.add_column("formula_region_coverage", sa.Column(
        "trust_computed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("formula_region_coverage", sa.Column(
        "review_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("formula_region_coverage", sa.Column(
        "review_derived_from", postgresql.JSONB(), nullable=True))

    # The queue reads by grade and review state across the whole library, so the
    # pair is worth an index — it was only ever listed per template before.
    op.create_index("ix_frc_trust_queue", "formula_region_coverage",
                    ["needs_review", "trust_grade"])


def downgrade() -> None:
    # Platform rows have no team to fall back to, so they are removed rather
    # than reassigned to an arbitrary tenant on the way down.
    op.execute("DELETE FROM audit_logs WHERE team_id IS NULL")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON audit_logs")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON audit_logs
        USING ({_BYPASS} OR {_MEMBERSHIP})
        WITH CHECK ({_BYPASS} OR {_MEMBERSHIP})
    """)
    op.alter_column("audit_logs", "team_id", existing_type=postgresql.UUID(),
                    nullable=False)

    op.drop_index("ix_frc_trust_queue", table_name="formula_region_coverage")
    op.drop_column("formula_region_coverage", "review_derived_from")
    op.drop_column("formula_region_coverage", "review_fingerprint")
    op.drop_column("formula_region_coverage", "trust_computed_at")
    op.drop_column("formula_region_coverage", "trust_inputs")
    op.drop_column("formula_region_coverage", "trust_grade")
    op.drop_constraint("fk_frc_reviewed_by", "formula_region_coverage",
                       type_="foreignkey")
    op.drop_column("formula_region_coverage", "reviewed_by_id")
