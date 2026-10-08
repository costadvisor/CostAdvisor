"""Delivered market reports.

One HTML file per report, seven `<section id>` fragments apiece
(overview, how, applications, tech, process, supply, strategic). Stored twice
over, on purpose:

* `MarketReportSection` — the seven sections, sanitised, in order. What the
  Product lines › Report tab renders.
* `MarketReportPanel` — the same content cut the way the Strategy tab reads
  it: the six sections as panels, plus section 7 split by its `<h3>` into
  `pestel`, `porter`, `market_drivers` and `kraljic`.

Report names are earlier line names. `MarketReportLine` joins a report to the
current lines it serves (`v1_scope.report_map` or `supply_axis.key_map`); one
report can serve several lines, so this is a table rather than a column on the
report.

Platform-only: no `team_id`, no RLS — the `commodity_indexes` precedent.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, SmallInteger,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

SECTION_IDS = ("overview", "how", "applications", "tech", "process", "supply", "strategic")
PANELS = ("overview", "how", "applications", "tech", "process", "supply",
          "pestel", "porter", "market_drivers", "kraljic")
LINE_SOURCES = ("report_map", "key_map")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MarketReport(Base):
    __tablename__ = "market_reports"

    # MANIFEST slug, e.g. "coagulants" — also the playbook's `category.id`.
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_file: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    old_line_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_line_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    in_v1_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # Hash of the delivered file — the idempotency check for a reload.
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kraljic: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)

    sections = relationship(
        "MarketReportSection", back_populates="report", cascade="all, delete-orphan",
        order_by="MarketReportSection.ordinal",
    )
    panels = relationship(
        "MarketReportPanel", back_populates="report", cascade="all, delete-orphan",
        order_by="MarketReportPanel.ordinal",
    )
    lines = relationship(
        "MarketReportLine", back_populates="report", cascade="all, delete-orphan",
    )


class MarketReportSection(Base):
    __tablename__ = "market_report_sections"
    __table_args__ = (
        UniqueConstraint("slug", "section_id", name="uq_market_report_section"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("market_reports.slug", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Sanitised (allowlist), entity-normalised; `.src` attribution blocks kept.
    html: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    src_block_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    report = relationship("MarketReport", back_populates="sections")


class MarketReportLine(Base):
    __tablename__ = "market_report_lines"
    __table_args__ = (
        CheckConstraint("source IN ('report_map', 'key_map')", name="ck_market_report_line_source"),
    )

    slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("market_reports.slug", ondelete="CASCADE"), primary_key=True)
    line_key: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    # The current line the key resolves to (through `line_key`, then
    # `former_keys`). NULL when it resolves to no current line.
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    report = relationship("MarketReport", back_populates="lines")


class MarketReportPanel(Base):
    __tablename__ = "market_report_panels"
    __table_args__ = (
        UniqueConstraint("slug", "panel", name="uq_market_report_panel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(64), ForeignKey("market_reports.slug", ondelete="CASCADE"), nullable=False)
    panel: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured reading where one exists (e.g. the Kraljic position).
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    report = relationship("MarketReport", back_populates="panels")
