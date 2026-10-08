"""The demand axis of the content drop, and the line-key vocabulary both axes share.

**Supply axis** (family › sub-family › product line › product) lives in its own
modules: `chemical_family.py`, `subfamily.py`, `product_line.py`.

**Demand axis** — industry › category › product line › product, as ratified in
`tree/category_tree.json`. A category is one function in one industry.
Authored rows are kept as authored (`CategoryMember`, `CategoryRef`,
`CategorySharedMember`, `CategoryBuildItem`, `IndustryOut`); `CategoryPlacement`
is the derived projection — one row per (category, product) — rebuilt on every
load.

All platform-only: no `team_id`, no RLS — the `commodity_indexes` precedent.
Links to the supply side are by `line_key` / `pid` strings with nullable
resolved ids beside them, the same rule as `producer_formulas.subject_code`: a
hard FK would drop the rows whose target is not loaded yet.
"""
import uuid
from datetime import date

from sqlalchemy import (
    Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.product_line import LINE_KEY_SEP  # noqa: F401  (re-exported)

CATEGORY_STATUSES = ("servable", "partial", "build")


# ── Demand axis ──────────────────────────────────────────────────────────────

class Industry(Base):
    """One of the 50 industries, with its reference buyer.

    `status`/`ratified_on` are the tree's ratification state; `scope_status`
    is `industries_v2.json`'s own status (existing / new …) — two different
    facts that share a word in the source.
    """

    __tablename__ = "industries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    anchor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ratified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    scope_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    buyer_one_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_scope: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    out_of_scope: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    boundaries: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Remaining authored keys (from, inherits, ratification_note, revision,
    # fixup, ratification, recut, removed_categories, out_removed …).
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    categories = relationship(
        "Category", back_populates="industry", cascade="all, delete-orphan",
        order_by="Category.sort_order",
    )


class Category(Base):
    """A ratified demand category, e.g. `MW-01`. One function in one industry."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("status IN ('servable', 'partial', 'build')",
                        name="ck_category_status"),
        Index("ix_categories_industry_id", "industry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("industries.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    fn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    src: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The other authored keys (former_name, merged_from, split_reason, dual,
    # lines, ratification_edits, build_note, moves …) as given.
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    industry = relationship("Industry", back_populates="categories")


class CategoryShared(Base):
    """A shared object (e.g. `FEED-AROMATICS`) that several categories
    reference instead of repeating its members."""

    __tablename__ = "category_shared"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    fn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # build, proposedBy, owner_before_ruling_39, edits … as given.
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CategorySharedMember(Base):
    """One authored member row of a shared object. Keyed by position, not by
    line: the same line can legitimately appear twice with different pids."""

    __tablename__ = "category_shared_members"
    __table_args__ = (
        UniqueConstraint("shared_id", "sort_order", name="uq_category_shared_member_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shared_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("category_shared.id", ondelete="CASCADE"), nullable=False)
    line_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # The authored pid list; NULL when the source says "*" (whole line).
    pids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_whole_line: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CategoryRef(Base):
    """A category's reference to a shared object (the category's `ref` list)."""

    __tablename__ = "category_refs"
    __table_args__ = (
        UniqueConstraint("category_id", "shared_id", name="uq_category_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    shared_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("category_shared.id", ondelete="CASCADE"), nullable=False)
    # NULL = the whole shared object; a list narrows it to those pids.
    pids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CategoryMember(Base):
    """One authored member row of a category (`line` + `pids`). Keyed by
    position: PC-01 lists the same sorbitan line three times, once per pid."""

    __tablename__ = "category_members"
    __table_args__ = (
        UniqueConstraint("category_id", "sort_order", name="uq_category_member_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    line_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # The authored pid list; NULL when the source says "*" (whole line).
    pids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_whole_line: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    # moves_to / moves_note as given.
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CategoryBuildItem(Base):
    """One line of a category's `build` list — what is missing to serve it."""

    __tablename__ = "category_build_items"
    __table_args__ = (
        UniqueConstraint("category_id", "seq", name="uq_category_build_item_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class IndustryOut(Base):
    """A product the industry explicitly does not buy, and where it goes
    instead (the industry's `out` rows).

    `to_industry` is the raw `to` text. `to_industries` is that text resolved
    to industry names: the whole text when it is one industry name (a name may
    contain a comma), else the known names matched longest first; a part that
    matches no name is kept as text."""

    __tablename__ = "industry_out"
    __table_args__ = (
        UniqueConstraint("industry_id", "pid", name="uq_industry_out_pid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("industries.id", ondelete="CASCADE"), nullable=False)
    pid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    to_industries: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    to_was: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # code_note, reviewed, was, closed, also_in_via_ref, pending_note as given.
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class CategoryPlacement(Base):
    """Derived: product `pid` is bought in `category` (directly, or through
    the shared object `via_shared_id`). Rebuilt on every load."""

    __tablename__ = "category_placements"
    __table_args__ = (
        UniqueConstraint("category_id", "pid", name="uq_category_placement_pid"),
        Index("ix_category_placements_pid", "pid"),
        Index("ix_category_placements_line_key", "line_key"),
        Index("ix_category_placements_industry_id", "industry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("industries.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    line_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    pid: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"),
        nullable=True)
    # The category's function and name as FUNCTIONALITY states them.
    fn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    via_shared_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("category_shared.id", ondelete="SET NULL"), nullable=True)

    category = relationship("Category")
    industry = relationship("Industry")
