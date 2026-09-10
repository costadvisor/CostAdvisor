"""Contract + clause model (Wave 3, SCRUM-79 / MON-1).

The only genuinely absent data in the trigger-radar story: nothing in
`app/models` modelled a contract, and `CostModel.supplier_id` was as close as
it got.

**Grain.** A contract is one team-to-supplier agreement with a term, and it
usually covers several cost models — while one cost model is covered by
successive contracts over time. So it is a contract row owning clause rows,
with a join table to the cost models it covers, rather than parallel FKs on one
table.

**The notice deadline is first-class.** It is the only hard future date the
radar has today, so it is stored on the row and recomputed on every write
(`compute_notice_deadline`) rather than derived ad hoc inside a rule — a rule
that recomputes it cannot be queried for "which contracts are approaching
notice" without scanning everything.

Strict tenant, `h8i9j0k1l2m3` policy shape: contract prices and notice dates
are the most sensitive rows in the product.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# What a clause is about. `notice` and `price_review` are the two the radar
# reads; the rest are recorded so a contract is a faithful record rather than
# only the fields one feature needs.
CLAUSE_TYPES = (
    "notice", "price_review", "indexation", "renewal", "volume", "penalty", "other",
)

PRICE_REVIEW_CADENCES = (
    "none", "monthly", "quarterly", "semiannual", "annual", "on_request",
)


def compute_notice_deadline(term_end: date | None, notice_days: int | None) -> date | None:
    """The last day notice can be given: the term end minus the notice period.

    Returns None when either input is missing — an absent deadline is a real
    state (a contract with no notice clause), not a zero to fill in.
    """
    if term_end is None or notice_days is None:
        return None
    return term_end - timedelta(days=int(notice_days))


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    # Nullable so deleting a supplier does not take the contract history with
    # it — the agreement happened even if the counterparty row is gone.
    supplier_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)

    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    term_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    term_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notice_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Stored, not derived on read — see the module docstring.
    notice_deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    price_review_cadence: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The indexation clause IS the formula the team already keeps, so this
    # points at it rather than restating a formula inside the contract.
    indexation_formula_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("formula_versions.id", ondelete="SET NULL"), nullable=True)

    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    clauses: Mapped[list["ContractClause"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
        order_by="ContractClause.sort_order",
    )
    covered: Mapped[list["ContractCostModel"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    supplier = relationship("Supplier", lazy="joined")

    def refresh_notice_deadline(self) -> None:
        self.notice_deadline = compute_notice_deadline(self.term_end, self.notice_days)


class ContractClause(Base):
    __tablename__ = "contract_clauses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)

    clause_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # A clause may carry its own hard date (a price-review window opening, a
    # volume commitment falling due) independent of the contract's notice date.
    deadline_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    contract: Mapped[Contract] = relationship(back_populates="clauses")


class ContractCostModel(Base):
    """Which cost models a contract covers.

    A join rather than a column on either side: one contract covers several
    products, and one product is covered by successive contracts over time.
    """
    __tablename__ = "contract_cost_models"
    __table_args__ = (
        UniqueConstraint("contract_id", "cost_model_id", name="uq_contract_cost_model"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    cost_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_models.id", ondelete="CASCADE"), nullable=False, index=True)
    # Optional share of the contract's value this product represents; used only
    # for display ordering, never in a calculation.
    share_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    contract: Mapped[Contract] = relationship(back_populates="covered")
