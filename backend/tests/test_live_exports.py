"""Exports get plain values, never tokens (Jira 210 S8).

Every export the app has today is built from data the API has already filled
in: printing a report (and Brief's "Export PDF", which is the browser's print)
prints what the page shows, and the CSVs are made in the browser from API
responses. So the guarantee to test is the text those exports are made from:

* acceptance 4 — a report's text, as a reader prints or copies it, holds a
  plain number and its month, no token and no markup around the number;
* the Strategy tab's structured panel text (what its tables and cards show)
  is plain too.

The browser's CSV writer (`frontend/src/utils/exportCsv.js`) also turns any
raw token that ever reaches it into its fallback, as a last line of defence.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from app.models.strategy import Playbook
from app.services.strategy import analysis
from tests.test_live_readers import report  # noqa: F401 — fixture
from tests.test_live_resolver import made  # noqa: F401 — fixture


def _printed(html: str) -> str:
    """The text a reader prints or copies from a section."""
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())


def test_acceptance_4_a_printed_report_holds_a_plain_number(report, tenant_a, client_as):
    body = client_as(tenant_a).get(f"/api/intel/reports/{report['slug']}").json()
    printed = " ".join(_printed(s["html"]) for s in body["sections"])
    assert "Crude stood at 129.9 · Jun 2026 , +29.9% · Jun 2026 on the year." in printed
    assert "{{" not in printed and "}}" not in printed


def test_strategy_panel_text_is_plain(db, report):
    out = analysis(db, Playbook(slug=report["slug"], name="Export test", report_slug=report["slug"]))
    card = out["panels"]["market_drivers"]["cards"][0]["text"]
    assert card == "up +29.9% · Jun 2026"
    assert "<" not in card and "{{" not in card
