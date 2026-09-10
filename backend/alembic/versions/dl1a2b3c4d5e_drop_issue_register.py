"""Scrum 74 / Loader v2 — persist the delivered defect register

`tables/_issues.csv` is the data team's own list of what is wrong with the
drop. The loader carries it through rather than recomputing it, and it has to
outlive the load run to be worth anything — SCRUM-34's whole premise is that
these findings become queryable instead of rediscovered.

Platform-level, no RLS: this annotates shared reference data, like the index
layers it describes.

Revision ID: dl1a2b3c4d5e
Revises: db5a1b2c3d4e
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "dl1a2b3c4d5e"
down_revision: Union[str, None] = "db5a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drop_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        # Polymorphic by design — a combo_id, a `{combo_id}#{seq}`, a
        # commodity_key, a `{series}|{region}` feed key, a type_code, a
        # formula_id, and for a handful of rows a bare region name. Text, never
        # FK'd, because no single table could satisfy it.
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("source_column", sa.String(length=64), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        # Derived from the problem text at load time, stored so the register is
        # filterable in SQL without re-parsing prose.
        sa.Column("awaiting_decision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_table", "source_key", "source_column", "problem",
            name="uq_drop_issue_finding",
        ),
    )
    op.create_index("ix_drop_issues_table_key", "drop_issues", ["source_table", "source_key"])


def downgrade() -> None:
    op.drop_index("ix_drop_issues_table_key", table_name="drop_issues")
    op.drop_table("drop_issues")
