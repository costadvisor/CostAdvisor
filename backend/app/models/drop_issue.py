"""The delivered defect register, persisted (Wave 3, SCRUM-74 Loader v2).

`tables/_issues.csv` is the data team's own list of what is wrong with the
drop. The loader **carries it through rather than recomputing it** — re-deriving
these findings in loader code would produce a second, drifting opinion of the
same facts, and the point of the register is that a defect travels with the
data it describes instead of being rediscovered by whoever hits it next.

Platform-level, no RLS — this describes shared reference data, like the index
layers it annotates.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DropIssueRecord(Base):
    """One finding from the drop's own defect list.

    Mirrors the source file exactly, so a load replaces the set rather than
    accumulating across runs — the register is a snapshot of the current drop,
    not a history.
    """

    __tablename__ = "drop_issues"
    __table_args__ = (
        UniqueConstraint(
            "source_table", "source_key", "source_column", "problem",
            name="uq_drop_issue_finding",
        ),
        Index("ix_drop_issues_table_key", "source_table", "source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    # Polymorphic by design: a combo_id, a `{combo_id}#{seq}`, a commodity_key,
    # a `{series}|{region}` feed key, a type_code, a formula_id — and for a
    # handful of rows a bare region name. Stored as text, never FK'd, because
    # no single table could satisfy it.
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_column: Mapped[str] = mapped_column(String(64), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)

    # Classification derived from the problem text at load time (see
    # services/drop/issues.py). Stored so the register is filterable in SQL
    # without re-parsing prose.
    #
    # awaiting_decision — waiting on a human filling in one of the two forms
    #   in decisions/; blocking only if the column is declared NOT NULL.
    # blocking — a genuine NOT NULL / FK failure if ignored.
    # Neither true — provenance: a note, or a resolution already applied.
    awaiting_decision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
