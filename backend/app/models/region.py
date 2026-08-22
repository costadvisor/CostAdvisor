from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Region(Base):
    """First-class region reference entity (Scrum 56).

    Region used to be free-text on cost models, index values/overrides, team
    index sources, and freight lanes. It is now a managed row: those columns are
    FKs to `regions.code`. `code` is the stable natural key the rest of the app
    matches on (e.g. "Europe", "GLOBAL", "NWE"), so promoting to this table did
    not require rewriting the string-based resolver/costing/scraper logic.

    Subregions are modelled with a self-referential `parent_id` (e.g. "NWE" is a
    child of "Europe"), so a finer grain can be added as a child row with no
    migration.
    """

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable natural key the region FK columns reference. Case-sensitive.
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )

    parent = relationship("Region", remote_side=[id], back_populates="children")
    children = relationship("Region", back_populates="parent")
