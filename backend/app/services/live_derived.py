"""Recompute `derived_from` for every text that can carry live numbers
(Jira 210 / LIVE-3, S5).

New text records it as it is written (report and lever loads, every block
version). This fills the rows written before that, and is safe to run again:
it only changes a row whose record differs, and reports every broken token.

    report sections   html
    report panels     html + the text inside `data`
    platform levers   title, guidance, default notes
    platform blocks   the current version's body (text or JSON)

Team blocks and custom levers are a team's own writing and are not read here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.editorial import EditorialBlock, EditorialBlockVersion
from app.models.market_report import MarketReportPanel, MarketReportSection
from app.models.strategy import PlaybookLever
from app.services.live_values import derived_from, scan_html, scan_text


@dataclass
class DerivedReport:
    checked: dict[str, int] = field(default_factory=dict)
    changed: dict[str, int] = field(default_factory=dict)
    with_tokens: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _apply(report: DerivedReport, table: str, row, tokens, problems, label: str) -> None:
    report.checked[table] = report.checked.get(table, 0) + 1
    record = derived_from(tokens)
    if record is not None:
        report.with_tokens[table] = report.with_tokens.get(table, 0) + 1
    report.problems += [f"{table} {label}: {p}" for p in problems]
    if row.derived_from != record:
        row.derived_from = record
        report.changed[table] = report.changed.get(table, 0) + 1


def recompute(db: Session) -> DerivedReport:
    """Set `derived_from` on every row from its current text. Flushes; the
    caller commits or rolls back."""
    report = DerivedReport()
    for row in db.query(MarketReportSection):
        tokens, problems = scan_html(row.html)
        _apply(report, "market_report_sections", row, tokens, problems,
               f"{row.slug}/{row.section_id}")
    for row in db.query(MarketReportPanel):
        tokens, problems = scan_html(row.html)
        data_tokens, data_problems = scan_text(row.data)
        _apply(report, "market_report_panels", row, tokens + data_tokens,
               problems + data_problems, f"{row.slug}/{row.panel}")
    for row in db.query(PlaybookLever):
        tokens, problems = scan_text(row.title, row.guidance, row.default_notes)
        _apply(report, "playbook_levers", row, tokens, problems, row.lever_code)
    for block, version in (
        db.query(EditorialBlock, EditorialBlockVersion)
        .join(EditorialBlockVersion, EditorialBlockVersion.id == EditorialBlock.current_version_id)
        .filter(EditorialBlock.team_id.is_(None))
    ):
        body = version.body_json if version.body_format == "json" else version.body_text
        tokens, problems = scan_text(body)
        _apply(report, "editorial_blocks", block, tokens, problems,
               f"{block.subject_code}/{block.block_type}")
    db.flush()
    return report
