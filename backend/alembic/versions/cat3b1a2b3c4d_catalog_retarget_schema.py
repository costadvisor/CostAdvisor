"""Scrum 74 / 3b — schema for the catalog retarget

Four changes, each forced by the 2026-07 data:

1. `formula_region_coverage.variant`. The drop contains combo pairs that differ
   only by variant (bentonite activated/natural, talc treated/untreated), which
   `uq_frc_template_region` rejects — the second of each pair simply cannot
   load. The column is NOT NULL DEFAULT '' rather than nullable, because
   Postgres treats every NULL as distinct in a unique constraint, so a nullable
   variant would let two rows share (template, region, NULL) and defeat the
   very uniqueness it is meant to extend. (Same reasoning as
   `supplier_trust_scores.grain_key`.)

2. `formula_region_coverage.proxy_density_tier`. `coverage_tier` means three
   different things across the shipped code, the drop and the mockup. Two of
   them are useful and they are not the same measurement: the shipped column is
   the *worst retrieval tier* among a combo's inputs, the drop's is *proxy
   density* (P1/P2/P3). Two columns, not one with a bigger vocabulary.

3. `formula_template_components.line_proxy_status`. The recipe line and the
   type-code registry disagree about proxy status on a material slice of the
   library, and neither is authoritative — see services/drop/authority.py. The
   registry's reading lives on `type_codes.proxy_status`; this is where the
   line's own reading lives, so both survive. The existing boolean `is_proxy`
   cannot hold it: the source has three values (direct / proxy / unclassified)
   and a boolean silently folds `unclassified` into "not a proxy".

4. Relax `ck_ftc_target_coherence`. It predates the type-code layer and
   requires every `index` line to carry a `commodity_id`. But a line naming an
   `ambiguous` type code resolves to nothing, so it has no commodity to record
   — and the drop has 25 such lines across three parent-feed codes
   (`natural-gas`, `crude-oil`, `acrylic-acid`). Under the old constraint those
   lines cannot be stored at all, so a load would have to drop them and
   misreport every recipe containing them. An `index` line may now be
   identified by either a commodity or a type code.

Revision ID: cat3b1a2b3c4d
Revises: dl1a2b3c4d5e
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cat3b1a2b3c4d"
down_revision: Union[str, None] = "dl1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The original, from wc1a2b3c4d5e — restored on downgrade.
_OLD_COHERENCE = (
    "(component_type = 'index' AND commodity_id IS NOT NULL AND input_template_id IS NULL)"
    " OR (component_type = 'formula' AND input_template_id IS NOT NULL AND commodity_id IS NULL)"
    " OR (component_type = 'fixed' AND commodity_id IS NULL AND input_template_id IS NULL)"
)

# An index line is now satisfied by a commodity OR a type code. Everything else
# holds exactly as before: a formula line still points at a template and only a
# template, and a fixed line still points at nothing.
_NEW_COHERENCE = (
    "(component_type = 'index' AND (commodity_id IS NOT NULL OR type_code_id IS NOT NULL)"
    " AND input_template_id IS NULL)"
    " OR (component_type = 'formula' AND input_template_id IS NOT NULL AND commodity_id IS NULL)"
    " OR (component_type = 'fixed' AND commodity_id IS NULL AND input_template_id IS NULL)"
)


def upgrade() -> None:
    # ── 1 + 2. Coverage gains variant and the second tier metric ─────────────
    op.add_column(
        "formula_region_coverage",
        sa.Column("variant", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "formula_region_coverage",
        sa.Column("proxy_density_tier", sa.String(length=8), nullable=True),
    )
    op.drop_constraint("uq_frc_template_region", "formula_region_coverage", type_="unique")
    op.create_unique_constraint(
        "uq_frc_template_region_variant",
        "formula_region_coverage",
        ["template_id", "region", "variant"],
    )

    # ── 3. The line's own proxy reading ──────────────────────────────────────
    op.add_column(
        "formula_template_components",
        sa.Column("line_proxy_status", sa.String(length=16), nullable=True),
    )

    # ── 4. An index line may be identified by a type code ────────────────────
    op.drop_constraint("ck_ftc_target_coherence", "formula_template_components", type_="check")
    op.create_check_constraint(
        "ck_ftc_target_coherence", "formula_template_components", _NEW_COHERENCE
    )


def downgrade() -> None:
    # An index line identified only by a type code cannot satisfy the old
    # constraint, so those rows have to go before it is restored. Reverting the
    # type-code dimension means losing the rows that only it could express.
    op.execute(
        "DELETE FROM formula_template_components "
        "WHERE component_type = 'index' AND commodity_id IS NULL"
    )
    op.drop_constraint("ck_ftc_target_coherence", "formula_template_components", type_="check")
    op.create_check_constraint(
        "ck_ftc_target_coherence", "formula_template_components", _OLD_COHERENCE
    )
    op.drop_column("formula_template_components", "line_proxy_status")

    # Same for coverage: reverting to a (template, region) key means at most
    # one row per pair, so the extra variants cannot survive. Keeps the
    # lowest-id row of each group — arbitrary but deterministic, and the only
    # way the old constraint can be restored at all.
    op.execute(
        """
        DELETE FROM formula_region_coverage frc
        USING formula_region_coverage keep
        WHERE frc.template_id = keep.template_id
          AND frc.region = keep.region
          AND frc.id > keep.id
        """
    )
    op.drop_constraint(
        "uq_frc_template_region_variant", "formula_region_coverage", type_="unique"
    )
    op.create_unique_constraint(
        "uq_frc_template_region", "formula_region_coverage", ["template_id", "region"]
    )
    op.drop_column("formula_region_coverage", "proxy_density_tier")
    op.drop_column("formula_region_coverage", "variant")
