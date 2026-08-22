"""Add composite_region to commodity_indexes

A composite index is computed live from other indexes, and until now it had no
region of its own: `resolve_index_values` emitted its grid row at
`region or "GLOBAL"`, and `compute_composite_value` resolved every unpinned
variable at whatever region the caller asked for. So there was no way to create,
say, a Europe composite — the Add Index modal simply hid the region field and
called composites "region-agnostic".

This adds an optional region to the index itself. NULL keeps exactly the old
behaviour (follow the requested region, default GLOBAL), so existing composites
are unaffected.

Deliberately NOT a FK to `regions.code`: `commodity_indexes` is platform
reference data with no other region column (region lives on `index_values` by
design — Scrum 57), and the write paths validate the code at the API layer the
same way the Scrum 58 coverage writes do. Kept as a plain VARCHAR to avoid
implying this table is region-scoped.

Revision ID: cr1a2b3c4d5e
Revises: ff1a2b3c4d5e
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cr1a2b3c4d5e"
down_revision: Union[str, None] = "ff1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "commodity_indexes",
        sa.Column("composite_region", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("commodity_indexes", "composite_region")
