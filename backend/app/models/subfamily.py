from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Subfamily(Base):
    """The sub-family tier: family › **sub-family** › product line › product.

    A reading layer of the supply axis that groups product lines inside one
    family. Platform-only (no team forks, no row-level security).

    It has no stable handle, so its identity is `(family_id, name)`; a rename
    is matched through `former_names`. `name` may be NULL: the axis can leave a
    node deliberately unnamed, and the partial unique index allows exactly one
    such node per family.

    `UNIQUE(id, family_id)` exists only so `product_lines` can reference
    `(subfamily_id, family_id)` together: a line can never sit under a
    sub-family of another family.
    """

    __tablename__ = "subfamilies"
    __table_args__ = (
        UniqueConstraint("family_id", "name", name="uq_subfamilies_family_name"),
        UniqueConstraint("id", "family_id", name="uq_subfamilies_id_family"),
        Index("uq_subfamilies_family_unnamed", "family_id", unique=True,
              postgresql_where=text("name IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"),
        nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Earlier names of this node, from the axis and from renames seen across
    # loads. The loader matches a renamed node through these.
    former_names: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    # The other authored keys of the node, as given.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    family = relationship("ChemicalFamily", back_populates="subfamilies")
    lines = relationship(
        "ProductLine",
        primaryjoin="Subfamily.id == foreign(ProductLine.subfamily_id)",
        back_populates="subfamily",
        order_by="ProductLine.sort_order",
    )
