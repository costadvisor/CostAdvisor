from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChemicalFamily(Base):
    """Top tier of the supply taxonomy: family › sub-family › product line › product.

    Platform-only since the taxonomy spine rework (`tax2a1b2c3d4e`): the content
    loader writes these rows, every team reads them, no team forks them. So no
    `team_id`, no `origin_id` and no row-level security.
    """

    __tablename__ = "chemical_families"
    __table_args__ = (
        UniqueConstraint("name", name="uq_chemical_families_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Legacy catalog code (e.g. "F01"). Not contiguous and not unique; the
    # content drop does not carry one, so it is NULL on loaded rows.
    code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The identity the loader matches on.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    # Other authored keys of the family node, as given.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    subfamilies = relationship(
        "Subfamily", back_populates="family", passive_deletes=True,
        order_by="Subfamily.sort_order",
    )
    product_lines = relationship(
        "ProductLine", back_populates="family", passive_deletes=True,
        order_by="ProductLine.sort_order",
    )
