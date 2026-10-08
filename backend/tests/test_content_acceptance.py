"""The design §8 acceptance table, on the loaded database (WP-12).

Every expected number is derived from the drop files at run time by
`tests/content_drop_expect.py`; none is written here. The loader packages test
their own tables in depth; this file is the one place that holds the whole
§8 table against one build, so the integration gate reads it in one run.

Needs a database holding a full content load of the current drop
(`content_loaded`); it fails, never skips, without one. Read-only.
"""
from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select, text

from app.database import SessionLocal, bypass_rls_var
from app.models.formula_template import FormulaTemplate
from app.services.catalog_visibility import default_view_clause, listed_clause
from tests import content_drop_expect as expect


@pytest.fixture
def s(content_loaded):
    prior = bypass_rls_var.get()
    bypass_rls_var.set(True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        bypass_rls_var.set(prior)


def _n(s, sql: str, **params) -> int:
    return int(s.execute(text(sql), params).scalar())


def _platform_codes(s, clause) -> set[str]:
    return set(s.execute(select(FormulaTemplate.code).where(
        FormulaTemplate.team_id.is_(None), clause)).scalars())


def test_supply_tree(s):
    assert _n(s, "SELECT count(*) FROM chemical_families") == len(expect.families())
    assert _n(s, "SELECT count(*) FROM subfamilies") == len(expect.subfamilies())
    assert _n(s, "SELECT count(*) FROM product_lines WHERE retired_at IS NULL") \
        == len(expect.product_lines())
    lines_with_listed = _n(s, """
        SELECT count(DISTINCT t.product_line_id) FROM formula_templates t
        WHERE t.team_id IS NULL AND t.card_kind IN ('product', 'group')
          AND EXISTS (SELECT 1 FROM formula_region_coverage c
                      WHERE c.template_id = t.id AND c.withdrawn_at IS NULL)""")
    assert lines_with_listed == len(expect.lines_with_listed())


def test_templates_by_kind_and_status(s):
    rows = s.execute(text(
        "SELECT card_kind, supply_status FROM formula_templates WHERE team_id IS NULL")).all()
    assert len(rows) == len(expect.template_codes())
    assert Counter(k for k, _ in rows) == expect.kind_counts()
    assert Counter(st for _, st in rows if st) == expect.status_counts()
    assert _n(s, "SELECT count(*) FROM formula_templates "
                 "WHERE team_id IS NULL AND product_line_id IS NULL") \
        == len(expect.templates_without_line())


def test_listed_and_default_view(s):
    assert _platform_codes(s, listed_clause()) == set(expect.listed_codes())
    assert _platform_codes(s, default_view_clause()) == set(expect.default_view_codes())


def test_redirects(s):
    stored = dict(s.execute(text(
        "SELECT code, redirect_to FROM formula_templates "
        "WHERE team_id IS NULL AND card_kind IN ('pointer', 'duplicate')")).all())
    assert stored == expect.redirects()


def test_coverage_and_cost_lines(s):
    assert _n(s, """SELECT count(*) FROM formula_region_coverage c
                    JOIN formula_templates t ON t.id = c.template_id
                    WHERE t.team_id IS NULL""") == expect.coverage_count()
    assert _n(s, """SELECT count(*) FROM formula_template_components l
                    JOIN formula_templates t ON t.id = l.template_id
                    WHERE t.team_id IS NULL""") == expect.cost_line_count()


def test_demand_axis(s):
    assert _n(s, "SELECT count(*) FROM industries") == len(expect.industries())
    assert _n(s, "SELECT count(*) FROM categories") == len(expect.categories())
    assert _n(s, "SELECT count(*) FROM industry_out") == len(expect.out_rows())
    assert _n(s, "SELECT count(*) FROM category_placements") == len(expect.placements())


def test_indexes(s):
    idx = expect.index_counts()
    assert _n(s, "SELECT count(*) FROM commodity_indexes WHERE commodity_key IS NOT NULL") \
        == idx["series"]
    assert _n(s, "SELECT count(*) FROM index_monthly_values") == idx["monthly_points"]
    assert _n(s, "SELECT count(*) FROM type_codes") == idx["type_codes"]


def test_editorial_blocks(s):
    stored = Counter(dict(s.execute(text(
        "SELECT block_type, count(*) FROM editorial_blocks WHERE team_id IS NULL "
        "GROUP BY 1")).all()))
    assert stored == expect.expected_block_counts()


def test_makers_store_no_share(s):
    """Maker evidence is stored per link; no share is (design §2.4)."""
    assert _n(s, "SELECT count(*) FROM producer_formulas") > 0
    assert _n(s, "SELECT count(*) FROM producer_formulas "
                 "WHERE share_pct IS NOT NULL OR share_disclosed") == 0
    assert _n(s, "SELECT count(*) FROM producer_formulas WHERE counts_toward_floor") > 0


def test_reports_and_playbooks(s):
    rep = expect.report_counts()
    assert _n(s, "SELECT count(*) FROM market_reports") == rep["reports"]
    assert _n(s, "SELECT count(*) FROM market_report_sections") == rep["sections"]
    assert _n(s, "SELECT count(*) FROM market_report_panels") == rep["panels"]
    assert _n(s, "SELECT count(*) FROM market_report_lines") == rep["joins"]
    assert _n(s, "SELECT count(*) FROM market_report_lines WHERE product_line_id IS NULL") \
        == rep["joins_not_current_line"]
    pb = expect.playbook_counts()
    assert _n(s, "SELECT count(*) FROM playbooks") == pb["playbooks"]
    assert _n(s, "SELECT count(*) FROM playbook_levers") == pb["levers"]
    assert _n(s, "SELECT count(*) FROM playbook_objectives") == pb["objectives"]


def test_the_load_names_the_drop(s, content_loaded):
    assert content_loaded["source_commit"] == expect.source_commit()
    row = s.execute(text("SELECT dry_run, finished_at FROM content_loads "
                         "WHERE id = :i"), {"i": content_loaded["id"]}).one()
    assert row.dry_run is False and row.finished_at is not None
