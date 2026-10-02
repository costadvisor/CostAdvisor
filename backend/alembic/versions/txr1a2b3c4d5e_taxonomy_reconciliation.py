"""Taxonomy reconciliation decision-file store (Phase 2 item 1, post-Wave-3
roadmap) — the analyst-decided mapping of 2026-07-drop family/subfamily names
onto the existing platform taxonomy, on the Scrum 27b sheet-roundtrip
mechanism (see dimension_decision.py's own migration dim1a2b3c4d5e for the
precedent this copies).

Revision ID: txr1a2b3c4d5e
Revises: idxfam1a2b3c4d5e
Create Date: 2026-09-24

Platform-level, no RLS — same reasoning as dimension_terms/producers: this
reconciles the shared catalog's naming, not tenant data.
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "txr1a2b3c4d5e"
down_revision: Union[str, None] = "idxfam1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "taxonomy_reconciliations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("drop_family", sa.String(length=200), nullable=False),
        sa.Column("drop_subfamily", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("family_id", sa.Integer(),
                  sa.ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subfamily_id", sa.Integer(),
                  sa.ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("level", "drop_family", "drop_subfamily",
                             name="uq_taxonomy_reconciliation_key"),
        sa.CheckConstraint("level IN ('family', 'subfamily')", name="ck_taxonomy_reconciliation_level"),
    )


def downgrade() -> None:
    op.drop_table("taxonomy_reconciliations")
