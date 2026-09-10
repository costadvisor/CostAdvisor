"""Scrum 74 / DB-5 + DB-6 — three-layer index model + monthly series grain

Splits the index into the three layers the 2026-07 data actually has:

    type_codes           what a cost line names        (many codes -> one series)
        | resolves_to
    commodity_indexes    the price series              (gains the series fields)
        |
        +-- index_cards           how it is displayed  (several cards may share
        |                         one series)
        +-- index_monthly_values  the numbers, monthly (quarterly derives)

Additive by design. `commodity_indexes` keeps its existing role and columns,
`index_values` (quarterly, region-keyed) is untouched, and
`formula_template_components.commodity_id` still resolves exactly as before —
the new `type_code_id` sits beside it. Nothing that works today is repointed
here, per DROP_2026-07_ANALYSIS.md section 1.

Also widens two shipped constraints that reject valid drop data outright:
`provider` (agency strings reach 72 chars) and `frequency` (compound cadences
reach 45).

No RLS: these are platform-level reference tables, following
`commodity_indexes`, which has no team_id and no policy. Team-specific values
continue to live in index_overrides / team_index_sources.

Revision ID: db5a1b2c3d4e
Revises: es1a2b3c4d5e
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "db5a1b2c3d4e"
down_revision: Union[str, None] = "es1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Widen the two constraints that reject valid data ─────────────────────
    op.alter_column(
        "commodity_indexes", "provider",
        existing_type=sa.String(length=64), type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "commodity_indexes", "frequency",
        existing_type=sa.String(length=16), type_=sa.String(length=64),
        existing_nullable=True,
    )

    # ── commodity_indexes becomes the price-series layer ─────────────────────
    op.add_column("commodity_indexes", sa.Column("commodity_key", sa.String(length=64), nullable=True))
    op.add_column("commodity_indexes", sa.Column("value_kind", sa.String(length=24), nullable=True))
    op.add_column("commodity_indexes", sa.Column("base_period", sa.String(length=16), nullable=True))
    op.add_column("commodity_indexes", sa.Column("source_region", sa.String(length=20), nullable=True))
    op.create_unique_constraint(
        "uq_commodity_indexes_commodity_key", "commodity_indexes", ["commodity_key"]
    )

    # ── The resolution join ──────────────────────────────────────────────────
    op.create_table(
        "type_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("resolves_to_id", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("proxy_status", sa.String(length=16), nullable=True),
        sa.Column("swap_priority", sa.String(length=1), nullable=True),
        sa.Column("ideal_index", sa.Text(), nullable=True),
        sa.Column("registry_note", sa.Text(), nullable=True),
        sa.Column("source_n_formulas", sa.Integer(), nullable=True),
        sa.Column("source_n_lines", sa.Integer(), nullable=True),
        sa.Column("source_total_weight", sa.Numeric(14, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_type_codes_code"),
        sa.ForeignKeyConstraint(["resolves_to_id"], ["commodity_indexes.id"]),
        sa.CheckConstraint(
            "resolution IN ('resolved', 'no_series', 'ambiguous')",
            name="ck_type_code_resolution",
        ),
        # Only `ambiguous` may lack a target: `no_series` codes all name a real
        # series (it means that series has no numbers), so a NULL there would
        # let a genuine load failure pass as a known state.
        sa.CheckConstraint(
            "resolves_to_id IS NOT NULL OR resolution = 'ambiguous'",
            name="ck_type_code_target_required",
        ),
    )
    op.create_index("ix_type_codes_resolves_to", "type_codes", ["resolves_to_id"])

    # ── The display layer ────────────────────────────────────────────────────
    op.create_table(
        "index_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_key", sa.String(length=64), nullable=False),
        sa.Column("feed_slug", sa.String(length=64), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        # Not an FK to regions.code — the drop's vocabulary is its own, and
        # `multi`/`Global` are not regions at all. Mapping is a decision-form
        # dependency, not something to guess at load time.
        sa.Column("region", sa.String(length=20), nullable=True),
        sa.Column("region_label", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("incoterm", sa.String(length=8), nullable=True),
        sa.Column("named_place", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("access", sa.String(length=32), nullable=True),
        sa.Column("frequency", sa.String(length=64), nullable=True),
        # Deliberately not unique per slug — 18 slugs carry several defaults.
        sa.Column("is_default_region", sa.Boolean(), nullable=True),
        sa.Column("agency", sa.String(length=255), nullable=True),
        sa.Column("source_freq", sa.String(length=64), nullable=True),
        sa.Column("sourcing_note", sa.Text(), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column("used_in_formulas", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_key", name="uq_index_cards_feed_key"),
        sa.ForeignKeyConstraint(["commodity_id"], ["commodity_indexes.id"]),
    )
    op.create_index("ix_index_cards_commodity", "index_cards", ["commodity_id"])
    op.create_index("ix_index_cards_slug", "index_cards", ["feed_slug"])

    # ── The numbers, at the grain the source publishes ───────────────────────
    op.create_table(
        "index_monthly_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Numeric(14, 4), nullable=False),
        # NOT NULL: the source's own README is explicit that actual and
        # forecast must never be averaged together, so every aggregate has to
        # be able to filter.
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commodity_id", "year", "month", name="uq_imv_commodity_period"),
        sa.ForeignKeyConstraint(["commodity_id"], ["commodity_indexes.id"]),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_imv_month"),
        sa.CheckConstraint("kind IN ('actual', 'forecast')", name="ck_imv_kind"),
    )
    op.create_index("idx_imv_lookup", "index_monthly_values", ["commodity_id", "year", "month"])

    # ── A cost line can name a type code ─────────────────────────────────────
    op.add_column(
        "formula_template_components", sa.Column("type_code_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ftc_type_code", "formula_template_components", "type_codes",
        ["type_code_id"], ["id"],
    )
    op.create_index(
        "ix_ftc_type_code_id", "formula_template_components", ["type_code_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ftc_type_code_id", table_name="formula_template_components")
    op.drop_constraint("fk_ftc_type_code", "formula_template_components", type_="foreignkey")
    op.drop_column("formula_template_components", "type_code_id")

    op.drop_index("idx_imv_lookup", table_name="index_monthly_values")
    op.drop_table("index_monthly_values")

    op.drop_index("ix_index_cards_slug", table_name="index_cards")
    op.drop_index("ix_index_cards_commodity", table_name="index_cards")
    op.drop_table("index_cards")

    op.drop_index("ix_type_codes_resolves_to", table_name="type_codes")
    op.drop_table("type_codes")

    op.drop_constraint("uq_commodity_indexes_commodity_key", "commodity_indexes", type_="unique")
    op.drop_column("commodity_indexes", "source_region")
    op.drop_column("commodity_indexes", "base_period")
    op.drop_column("commodity_indexes", "value_kind")
    op.drop_column("commodity_indexes", "commodity_key")

    op.alter_column(
        "commodity_indexes", "frequency",
        existing_type=sa.String(length=64), type_=sa.String(length=16),
        existing_nullable=True,
    )
    op.alter_column(
        "commodity_indexes", "provider",
        existing_type=sa.String(length=255), type_=sa.String(length=64),
        existing_nullable=True,
    )
