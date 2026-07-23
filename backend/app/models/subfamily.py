import uuid

from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Subfamily(Base):
    """The middle tier of the taxonomy spine: family -> subfamily -> product.

    Same platform/team fork model as ChemicalFamily: team_id NULL = platform,
    set = a team's private fork; origin_id back-links a fork to its platform
    original so resolution survives a rename.
    """

    __tablename__ = "subfamilies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = platform row (shared read-only); set = a team's private fork.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    # Back-link to the platform subfamily this was forked from (see ChemicalFamily.origin_id).
    origin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subfamilies.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    family = relationship("ChemicalFamily", back_populates="subfamilies")
    products = relationship("Product", back_populates="subfamily")
    origin = relationship("Subfamily", remote_side=[id])
