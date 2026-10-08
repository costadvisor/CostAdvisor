from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# A line key is `Family|||Line`: three pipes, no spaces.
LINE_KEY_SEP = "|||"


class ProductLine(Base):
    """A product line: family › sub-family › **product line** › product.

    Identity is the manufacturing platform handle (`PLAT-*`), which stays the
    same when a line is renamed. `line_key` (`Family|||Line`) and `name` are
    attributes that follow the current name; every earlier key is kept in
    `former_keys`, so report, scope and tree joins and old URLs still resolve.
    The loader matches a line by `platform`, then `line_key`, then a key in
    `former_keys`.

    Platform-only (no team forks, no row-level security). The drop's record
    field called `subfamily` holds this tier, not the sub-family.
    """

    __tablename__ = "product_lines"
    __table_args__ = (
        UniqueConstraint("platform", name="uq_product_lines_platform"),
        UniqueConstraint("line_key", name="uq_product_lines_line_key"),
        # A line's sub-family must belong to the line's own family. No ON
        # DELETE: a sub-family that still has lines cannot be deleted.
        ForeignKeyConstraint(
            ["subfamily_id", "family_id"], ["subfamilies.id", "subfamilies.family_id"],
            name="fk_product_lines_subfamily_family",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="CASCADE"),
        nullable=False, index=True)
    subfamily_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # NULL is allowed for safety; every loaded line has one.
    platform: Mapped[str | None] = mapped_column(String(128), nullable=True)
    line_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Every earlier `Family|||Line` key of this line.
    former_keys: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # The line is in the v1 scope list. Stored, not used as a filter.
    in_v1_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # Soft key to market_reports.slug, and the earlier line name the report
    # was written against.
    report_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_old_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Structured flags: status, route_flag, validation_question, retracted,
    # do_not_publish.
    flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Authoring provenance. Stored, never displayed.
    confidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The other authored keys of the axis line, without its product list
    # (that list is not membership; a product's line is its record key).
    axis_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Set when the line's platform leaves the axis. Retired lines are hidden;
    # the row stays because team data may point at it.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")

    family = relationship("ChemicalFamily", back_populates="product_lines")
    # Joined on subfamily_id alone: family_id is written through `family`.
    # The composite foreign key keeps the two coherent in the database.
    subfamily = relationship(
        "Subfamily",
        primaryjoin="foreign(ProductLine.subfamily_id) == Subfamily.id",
        back_populates="lines",
    )
