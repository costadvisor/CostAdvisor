"""Mention audit (Jira 208): do reports and intelligence point at indexes?

The matching rules as tests: whole words only, longest term first, every series
a term can mean kept (never an auto-picked region), figures anchored only when
they share a sentence with a commodity, product codes counted only when they
are real catalogue codes, and a vocabulary built from the library's own names.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.database import bypass_rls_var
from app.models.index_data import CommodityIndex
from app.models.index_layer import IndexCard
from app.services.mention_audit import (
    EXCLUDED_TERMS, _matcher, audit_mentions, audit_text, build_vocabulary,
)

VOCAB = {
    "propylene": {1},
    "caustic soda": {2, 3},     # one term, two regional series
    "soda": {9},
    "methanol": {4},
}
CODES = {"F27-PEA-EU"}


def _audit(body: str):
    return audit_text(body, source="report", ref="r", vocab=VOCAB,
                      matcher=_matcher(VOCAB), codes=CODES)


# ── Matching ────────────────────────────────────────────────────────────────

def test_whole_words_only_and_case_insensitive():
    assert _audit("Polypropylene film demand.").commodity_terms == []
    assert _audit("Propylene tightened.").commodity_terms == ["propylene"]


def test_the_longest_term_wins_where_two_overlap():
    r = _audit("Caustic soda was flat.")
    assert r.commodity_terms == ["caustic soda"]


def test_a_term_naming_several_series_keeps_them_all():
    assert _audit("Caustic soda eased.").series_ids == [2, 3]


def test_html_is_stripped_and_entities_decoded():
    r = _audit("<p>Methanol&nbsp;rose <b>12%</b> to &euro;480/t.</p>")
    assert r.commodity_terms == ["methanol"]
    assert r.typed_figures == 2
    assert r.anchored_figures == 1


# ── Figures ──────────────────────────────────────────────────────────────────

def test_price_and_percent_forms_are_counted():
    r = _audit("Prices: USD 1,200 then 480 EUR/t, and €95, up 3.5%.")
    assert r.typed_figures == 4


def test_a_figure_is_anchored_only_in_the_same_sentence():
    r = _audit("Methanol was quiet. Freight rose 8%.")
    assert r.typed_figures == 1
    assert r.anchored_figures == 0


# ── Product codes (the indirect link) ────────────────────────────────────────

def test_only_real_catalogue_codes_count():
    r = _audit("See F27-PEA-EU and AB-NOT-REAL.")
    assert r.product_codes == ["F27-PEA-EU"]


# ── Vocabulary from the library's own names ──────────────────────────────────

def test_vocabulary_comes_from_series_and_card_names(db):
    key = f"zz-{uuid.uuid4().hex[:8]}"
    content = CommodityIndex(name=f"Widgetine Zq{key[-4:]} — NA", commodity_key=key,
                             scrape_enabled=False)
    ppi = CommodityIndex(name=f"Widget Zq{key[-4:]} PPI", commodity_key=f"{key}-ppi",
                         scrape_enabled=False)
    reference = CommodityIndex(name=f"Gizmo Zq{key[-4:]}", scrape_enabled=False)  # no key
    db.add_all([content, ppi, reference])
    db.commit()
    card = IndexCard(feed_key=f"{key}-card", feed_slug=key, commodity_id=content.id,
                     name=f"Sprocket Zq{key[-4:]} (proxy) / Cog Zq{key[-4:]}")
    db.add(card)
    db.commit()
    try:
        vocab = build_vocabulary(db)
        tail = key[-4:].lower()
        assert vocab[f"widgetine zq{tail}"] == {content.id}      # " — NA" dropped
        assert vocab[f"sprocket zq{tail}"] == {content.id}       # "(proxy)" dropped, split on " / "
        assert vocab[f"cog zq{tail}"] == {content.id}
        assert f"widget zq{tail} ppi" not in vocab               # PPI names left out
        assert f"gizmo zq{tail}" not in vocab                    # not a content series
        assert not EXCLUDED_TERMS & set(vocab)
    finally:
        db.rollback()
        bypass_rls_var.set(True)
        db.execute(text("DELETE FROM index_cards WHERE id = :i"), {"i": card.id})
        for s in (content, ppi, reference):
            db.execute(text("DELETE FROM commodity_indexes WHERE id = :i"), {"i": s.id})
        db.commit()


def test_the_full_audit_runs_and_reports_both_sources(db):
    rows, summary = audit_mentions(db)
    for block in (summary.reports, summary.editorial):
        assert {"units", "with_commodity_mention", "with_product_code", "with_neither",
                "with_typed_figures", "anchored_figures", "already_linked"} <= set(block)
    assert summary.reports["units"] + summary.editorial["units"] == len(rows)
