import uuid

from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ChemicalFamily(Base):
    __tablename__ = "chemical_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # team_id NULL = platform row (shipped by us, shared read-only with every team);
    # set = a team's private fork. See sample_idea/scrum55/02-platform-vs-team.md.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    # Back-link a fork to the platform row it was copied from. This is what lets a
    # team rename their fork without breaking platform formula/index resolution:
    # the app can always answer "this team row is really a copy of that platform row".
    origin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True
    )
    # Catalog code, e.g. "F01". NOT contiguous (codes skip numbers) and NOT globally
    # unique (a fork shares its origin's code), so never assume either.
    code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Not globally unique any more: uniqueness is scoped platform-vs-team via partial
    # indexes (see the taxonomy-spine migration) so an un-renamed fork can't collide.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    custom_attribute_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Schema example: [{"name": "concentration", "type": "number"}, {"name": "charge", "type": "string"}]

    products = relationship("Product", back_populates="chemical_family")
    subfamilies = relationship(
        "Subfamily", back_populates="family", cascade="all, delete-orphan"
    )
    # Self-referential fork back-link (a fork -> the platform family it copied).
    origin = relationship("ChemicalFamily", remote_side=[id])
