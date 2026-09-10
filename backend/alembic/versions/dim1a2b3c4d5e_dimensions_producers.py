"""SCRUM-77 / INT-3 — dimension terms + aliases + assertions, the unresolved
register, and the producer entity with its alias layer.

Revision ID: dim1a2b3c4d5e
Revises: edb1a2b3c4d5e
Create Date: 2026-08-28

**No permission migration here.** The `dimensions.*` keys were created by
SCRUM-76's single revision (`edb1a2b3c4d5e`) carrying `content.*` and
`dimensions.*` together; this story consumes them and deliberately does not add
a second one.

Tenancy, per the ticket and for a concrete reason: dimension terms, aliases and
assertions use the **platform-readable with team forks** policy
(`USING (bypass OR team_id IS NULL OR member_of)`), never strict tenant. Under
strict tenant every platform term is invisible to every team, so the facet is
empty for everyone on day one and the bug looks like a loader failure.

`producers` / `producer_aliases` / `producer_formulas` are platform reference
tables with **no `team_id` and no RLS**, following `commodity_indexes`. A team
does not fork "BASF exists"; what a team overrides is an assertion, and that
lives on `dimension_assertions.team_id`.

`dimension_unresolved` is also platform-level — it is the load's own report,
the same reasoning as `index_projection_runs` and `drop_issues`.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dim1a2b3c4d5e"
down_revision: Union[str, None] = "edb1a2b3c4d5e"
branch_labels = None
depends_on = None

_UID = "(NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid"
_BYPASS = "current_setting('app.bypass_rls'::text, true) = 'on'::text"
_MEMBER_OF = (
    "team_id IN (SELECT team_memberships.team_id FROM team_memberships "
    f"WHERE team_memberships.user_id = {_UID})"
)

_KINDS = (
    "'functionality','functionality_family','industry',"
    "'compliance_flag','supply_region','substitution_risk'"
)


def upgrade() -> None:
    # ── Terms ────────────────────────────────────────────────────────────────
    op.create_table(
        "dimension_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name="ck_dimension_term_kind"),
    )
    op.create_index("ix_dimension_terms_team_id", "dimension_terms", ["team_id"])
    op.create_index("ix_dimension_terms_kind", "dimension_terms", ["kind"])
    op.create_foreign_key("fk_dimension_terms_origin", "dimension_terms",
                          "dimension_terms", ["origin_id"], ["id"], ondelete="SET NULL")
    # A fork legitimately shares its origin's (kind, code), so uniqueness is
    # re-scoped by partial index — the uq_chem_fam_platform_name convention.
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_term_platform
        ON dimension_terms (kind, code) WHERE team_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_term_team
        ON dimension_terms (team_id, kind, code) WHERE team_id IS NOT NULL
    """)

    # ── Aliases ──────────────────────────────────────────────────────────────
    op.create_table(
        "dimension_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("term_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dimension_terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("raw_value", sa.String(length=400), nullable=False),
        sa.Column("normalized", sa.String(length=400), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_dimension_aliases_team_id", "dimension_aliases", ["team_id"])
    op.create_index("ix_dimension_aliases_term_id", "dimension_aliases", ["term_id"])
    # The resolution lookup: (kind, normalized) within a scope.
    op.create_index("ix_dimension_aliases_lookup", "dimension_aliases",
                    ["kind", "normalized"])
    # One meaning per raw value per facet — otherwise the same string resolves
    # two ways depending on row order, which is how a reordered rule list
    # quietly reclassifies a library.
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_alias_platform
        ON dimension_aliases (kind, normalized) WHERE team_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_alias_team
        ON dimension_aliases (team_id, kind, normalized) WHERE team_id IS NOT NULL
    """)

    # ── Assertions ───────────────────────────────────────────────────────────
    op.create_table(
        "dimension_assertions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
        sa.Column("origin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("term_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dimension_terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_code", sa.String(length=160), nullable=False),
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("commodity_id", sa.Integer(),
                  sa.ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subfamily_id", sa.Integer(),
                  sa.ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_value", sa.String(length=400), nullable=True),
        sa.Column("matched_alias_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dimension_aliases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('formula','index','subfamily','family','producer')",
            name="ck_dimension_assertion_subject_type"),
    )
    op.create_index("ix_dimension_assertions_team_id", "dimension_assertions", ["team_id"])
    op.create_index("ix_dimension_assertions_term_id", "dimension_assertions", ["term_id"])
    op.create_index("ix_dimension_assertions_subject", "dimension_assertions",
                    ["subject_type", "subject_code"])
    op.create_foreign_key("fk_dimension_assertions_origin", "dimension_assertions",
                          "dimension_assertions", ["origin_id"], ["id"], ondelete="SET NULL")
    # `region` is nullable and Postgres treats every NULL as distinct in a
    # unique index, so the "all regions" case is folded to a literal — otherwise
    # the same claim could be inserted twice and the load would stop being
    # idempotent.
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_assertion_platform
        ON dimension_assertions (term_id, subject_type, subject_code,
                                 COALESCE(region, '*'))
        WHERE team_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_dimension_assertion_team
        ON dimension_assertions (team_id, term_id, subject_type, subject_code,
                                 COALESCE(region, '*'))
        WHERE team_id IS NOT NULL
    """)

    # ── The unresolved register (the analyst's work queue) ───────────────────
    op.create_table(
        "dimension_unresolved",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("raw_value", sa.String(length=400), nullable=False),
        sa.Column("normalized", sa.String(length=400), nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sample_subjects", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("kind", "normalized", name="uq_dimension_unresolved"),
    )
    op.create_index("ix_dimension_unresolved_kind", "dimension_unresolved", ["kind"])

    # ── Producers ────────────────────────────────────────────────────────────
    op.create_table(
        "producers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("hq_country", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_producer_normalized_name"),
    )
    op.create_index("ix_producers_normalized_name", "producers", ["normalized_name"])

    op.create_table(
        "producer_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("producer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("producers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_value", sa.String(length=400), nullable=False),
        sa.Column("normalized", sa.String(length=400), nullable=False),
        # Separate lookup key: the same string with a trailing parenthetical
        # dropped. Two columns because one would have to choose between
        # preserving "BASF (Uvinul line)" and matching it onto BASF.
        sa.Column("match_key", sa.String(length=400), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # NOT unique on `normalized` alone: 40 raw names name several companies
        # ("Sinopec / PetroChina"), so one raw string legitimately maps to N
        # producers and a one-to-one index would reject the real data.
        sa.UniqueConstraint("normalized", "producer_id", name="uq_producer_alias"),
    )
    op.create_index("ix_producer_aliases_producer_id", "producer_aliases", ["producer_id"])
    op.create_index("ix_producer_aliases_normalized", "producer_aliases", ["normalized"])
    op.create_index("ix_producer_aliases_match_key", "producer_aliases", ["match_key"])

    op.create_table(
        "producer_formulas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("producer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("producers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_code", sa.String(length=160), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("share_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("share_disclosed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("hq_country", sa.String(length=80), nullable=True),
        sa.Column("regions_raw", postgresql.JSONB(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("raw_name", sa.String(length=400), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="loader", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("producer_id", "subject_code", "region",
                            name="uq_producer_formula"),
    )
    op.create_index("ix_producer_formulas_producer_id", "producer_formulas", ["producer_id"])
    op.create_index("ix_producer_formulas_subject_code", "producer_formulas", ["subject_code"])

    # ── RLS: platform-readable with team forks ──────────────────────────────
    for table in ("dimension_terms", "dimension_aliases", "dimension_assertions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL
            USING ({_BYPASS} OR team_id IS NULL OR {_MEMBER_OF})
        """)


def downgrade() -> None:
    for table in ("dimension_assertions", "dimension_aliases", "dimension_terms"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_index("ix_producer_formulas_subject_code", table_name="producer_formulas")
    op.drop_index("ix_producer_formulas_producer_id", table_name="producer_formulas")
    op.drop_table("producer_formulas")
    # IF EXISTS: this index was added after the revision had already been
    # applied to a dev database, so a downgrade there must not fail on it.
    op.execute("DROP INDEX IF EXISTS ix_producer_aliases_match_key")
    op.drop_index("ix_producer_aliases_normalized", table_name="producer_aliases")
    op.drop_index("ix_producer_aliases_producer_id", table_name="producer_aliases")
    op.drop_table("producer_aliases")
    op.drop_index("ix_producers_normalized_name", table_name="producers")
    op.drop_table("producers")

    op.drop_index("ix_dimension_unresolved_kind", table_name="dimension_unresolved")
    op.drop_table("dimension_unresolved")

    op.execute("DROP INDEX IF EXISTS uq_dimension_assertion_team")
    op.execute("DROP INDEX IF EXISTS uq_dimension_assertion_platform")
    op.drop_constraint("fk_dimension_assertions_origin", "dimension_assertions",
                       type_="foreignkey")
    op.drop_index("ix_dimension_assertions_subject", table_name="dimension_assertions")
    op.drop_index("ix_dimension_assertions_term_id", table_name="dimension_assertions")
    op.drop_index("ix_dimension_assertions_team_id", table_name="dimension_assertions")
    op.drop_table("dimension_assertions")

    op.execute("DROP INDEX IF EXISTS uq_dimension_alias_team")
    op.execute("DROP INDEX IF EXISTS uq_dimension_alias_platform")
    op.drop_index("ix_dimension_aliases_lookup", table_name="dimension_aliases")
    op.drop_index("ix_dimension_aliases_term_id", table_name="dimension_aliases")
    op.drop_index("ix_dimension_aliases_team_id", table_name="dimension_aliases")
    op.drop_table("dimension_aliases")

    op.execute("DROP INDEX IF EXISTS uq_dimension_term_team")
    op.execute("DROP INDEX IF EXISTS uq_dimension_term_platform")
    op.drop_constraint("fk_dimension_terms_origin", "dimension_terms", type_="foreignkey")
    op.drop_index("ix_dimension_terms_kind", table_name="dimension_terms")
    op.drop_index("ix_dimension_terms_team_id", table_name="dimension_terms")
    op.drop_table("dimension_terms")
