"""Load the Scrum 57 index metadata + proxy mapping from the reference workbook.

Reads sample_idea/scrum57/seed-data-reference.xlsx (the 158 feeds) and loads them
against the region-agnostic (commodity, region) shape:

- Feed names bake region in ("Iron scrap · China"); we reconcile that to a base
  commodity ("Iron scrap") — region is NOT written onto the index (it lives on
  index_values). So the 158 feeds collapse to ~59 CommodityIndex rows.
- Metadata (access_tier / frequency / role / retrieval_status / free source /
  proxy_logic) is index-level per the spec. Where a commodity's regional feeds
  disagree (20 of 32 multi-region commodities do), we take a representative feed
  by a fixed region priority (documented below). Per-region proxy fidelity is a
  follow-up for FD-1 (SCRUM-80).
- The 2 hard-blocked feeds (ilmenite, rutile) are marked retrieval_status=blocked,
  never dropped.

Idempotent: upserts by (case-insensitive) name. Run:  python -m seed_index_metadata
Seeded reference commodities are identifiable by `retrieval_status IS NOT NULL`.
"""
import re
import sys
from pathlib import Path

import openpyxl
from sqlalchemy import func

from app.constants.index_metadata import (
    ACCESS_TIERS, FREQUENCIES, ROLES, RETRIEVAL_STATUSES, validate_proxy_logic,
)
from app.database import SessionLocal, bypass_rls_var
from app.models.index_data import CommodityIndex

DEFAULT_XLSX = Path(__file__).resolve().parents[1] / "sample_idea" / "scrum57" / "seed-data-reference.xlsx"

# Which regional feed's metadata represents the (region-agnostic) index when
# regions disagree. Broadest signal first, then the major markets.
REGION_PRIORITY = ["Global", "EU", "NA", "APAC", "CN", "IN", "LA", "MEA"]

# ── New-workbook (2026-07 reference) support ──────────────────────────────────
# The refreshed workbook (scrum59/scrum60 copies) replaced the Scrum-57 "Indexes"
# sheet with a region-specific proxy model: codes like "LCI-NA", "ELEC-EU",
# "BZ-CN"; region baked into the code as a trailing token; Access/Use columns
# dropped; Direct/Proxy + Swap priority stand in for retrieval status. We detect
# the format by header and remap onto the existing feed-dict shape so the rest of
# the pipeline (build_commodities + the loaders) is unchanged. See jvpdocs.

# Trailing code tokens that mean "region", not part of the commodity name.
NEW_REGION_TOKENS = {
    "NA", "EU", "CN", "APAC", "IN", "MEA", "LA", "US", "NWE", "ASIA",
    "LME", "GLB", "GLOBAL", "WB", "SG", "MB", "ROW", "JP", "KR", "PPI",
}

# Free-text frequencies in the new sheet → the canonical FREQUENCIES vocabulary.
# Anything compound/unknown becomes "Irregular" rather than crashing the enum
# assert; a blank stays None (loaders preserve the existing value).
_FREQ_MAP = {
    "daily": "Daily", "weekly": "Weekly", "monthly": "Monthly",
    "quarterly": "Quarterly", "annual": "Annual",
}


def _normalize_frequency(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "unknown"):
        return None
    return _FREQ_MAP.get(s.lower(), "Irregular")


def _new_base_and_region(code: str) -> tuple[str, str]:
    """'LCI-NA' -> ('LCI', 'NA'); 'BRENT' -> ('BRENT', 'GLOBAL').

    Region is the trailing token when it's a known region marker; the remaining
    prefix is the region-agnostic commodity key (region lives on index_values,
    not the index, exactly as in the Scrum-57 reconciliation).

    SUPERSEDED by the three-layer model (Scrum 74 / DB-5). Splitting a code on
    its trailing token mis-files feeds, because that token is often a data
    source rather than a region — `-ppi`, `-wb` and `-mb` are Producer Price
    Index, World Bank and Metal Bulletin. In the new model the region lives on
    IndexCard (where the source states it) and the series key stays opaque;
    see app/models/index_layer.py.

    Kept because the pre-drop seed path still runs through it. Whether
    seed_index_metadata / seed_catalog / seed_combos are retargeted at the
    drop or retired is the Phase-0 call that Loader v2 has to close first —
    deleting this before then would break a loader that currently works.
    """
    parts = str(code).strip().split("-")
    if len(parts) > 1 and parts[-1].upper() in NEW_REGION_TOKENS:
        region = parts[-1].upper()
        base = "-".join(parts[:-1])
        # Emit "Global" (title case) to match REGION_PRIORITY + the old-format
        # convention, so the broadest-signal-first representative tie-break in
        # pick_representative() actually matches GLOBAL feeds.
        return base, ("Global" if region in ("GLB", "GLOBAL") else region)
    return str(code).strip(), "Global"


def base_name(name: str) -> str:
    """Strip the ' · Region' label a feed name carries → the base commodity name."""
    return re.split(r"\s*·\s*", str(name))[0].strip()


def to_proxy_logic(retrieval: str, prose) -> dict | None:
    """Build the structured proxy_logic spec. The reference only gives analyst
    prose, so we store it in `note` and leave the executable params for SCRUM-67.
    Pure 'free' feeds need no proxy → None."""
    prose = (str(prose).strip() if prose else "")
    if retrieval not in ("good_proxy", "weak_proxy", "blocked") or not prose:
        return None
    return validate_proxy_logic({
        "base_index": None, "operation": None, "spread": None,
        "spread_unit": None, "recalibration": None, "note": prose,
    })


def pick_representative(feeds: list[dict]) -> dict:
    """Choose the feed whose metadata represents the index, by region priority."""
    order = {r: i for i, r in enumerate(REGION_PRIORITY)}
    return sorted(feeds, key=lambda f: order.get(f["region"], len(order)))[0]


def _read_feeds(xlsx_path: Path) -> list[dict]:
    """Read the Indexes sheet into normalized feed dicts, auto-detecting the
    old Scrum-57 layout ('Index ID' …) vs the 2026-07 refreshed layout
    ('Index (type-code)' …). Both produce the same dict shape."""
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    rows = list(wb["Indexes"].iter_rows(values_only=True))
    hdr = list(rows[0])
    data = [r for r in rows[1:] if any(v is not None for v in r)]
    return (_read_feeds_new(hdr, data) if "Index (type-code)" in hdr
            else _read_feeds_old(hdr, data))


def _read_feeds_old(hdr: list, data: list) -> list[dict]:
    idx = {name: i for i, name in enumerate(hdr)}
    feeds = []
    for r in data:
        def g(col):
            v = r[idx[col]]
            return None if v is None else (v.strip() if isinstance(v, str) else v)
        feeds.append({
            "index_id": g("Index ID"),
            "name": g("Name"),
            "base": base_name(g("Name")),
            "category": g("Category"),
            "region": str(g("Region")) if g("Region") is not None else "",
            "access": g("Access"),
            "frequency": g("Frequency"),
            "role": g("Use"),
            "retrieval": g("Retrieval"),
            "free_source": g("Free source"),
            "free_source_url": g("Free source URL"),
            "proxy": g("Proxy logic"),
        })
    return feeds


# The 2026-07 sheet dropped a free source/retrieval column and instead flags each
# feed direct-vs-proxy with a swap-priority rank. Map that onto our retrieval
# vocabulary: direct feed = the real number is free; an A-ranked proxy is a solid
# approximation, B/C ranks are rougher.
#
# SUPERSEDED, and this literal is why. That workbook has no column expressing
# blocked-ness, so the two ore feedstocks were named in code — and "exactly two
# feeds have no source" then got repeated as a fact about the data when it was
# only ever a fact about this set. The drop states resolution per type code, so
# `services.proxy_derivation.blocked_series()` derives the real answer from the
# data and moves when the data moves. Kept unchanged only because this seeder
# still runs against the pre-drop workbook, where there is nothing to derive
# from; do not extend it.
_NEW_BLOCKED_CODES = {"ILM-MB", "RUT-MB"}


def _new_retrieval(code: str, direct_proxy, swap) -> str:
    if str(code).strip().upper() in _NEW_BLOCKED_CODES:
        return "blocked"
    if str(direct_proxy).strip().lower() == "direct":
        return "free"
    return "good_proxy" if str(swap).strip().upper() == "A" else "weak_proxy"


def _read_feeds_new(hdr: list, data: list) -> list[dict]:
    idx = {name: i for i, name in enumerate(hdr) if name is not None}
    feeds = []
    for r in data:
        def g(col):
            v = r[idx[col]] if col in idx else None
            return None if v is None else (v.strip() if isinstance(v, str) else v)
        code = g("Index (type-code)")
        base, region = _new_base_and_region(code)
        retrieval = _new_retrieval(code, g("Direct/Proxy"), g("Swap priority"))
        # `How we source it` carries the analyst prose explaining a proxy; keep it
        # only for proxied feeds (it becomes proxy_logic.note downstream).
        proxy_note = g("How we source it") if retrieval in ("good_proxy", "weak_proxy", "blocked") else None
        # New `Category` is a free-text descriptor ("Benzene · … · 7 regions");
        # take the leading token as the coarse category, matching the old enum's
        # spirit without forcing it (category is nullable and preserved on load).
        cat = g("Category")
        category = str(cat).split("·")[0].strip() if cat else None
        feeds.append({
            "index_id": code,
            "name": g("What it is") or base,
            "base": base,
            "category": category if category and category.lower() != "none" else None,
            "region": region,
            "access": None,          # no Access column in the new sheet
            "frequency": _normalize_frequency(g("Frequency")),
            "role": None,            # no Use column in the new sheet
            "retrieval": retrieval,
            "free_source": g("Source"),
            "free_source_url": None,  # no URL column in the new sheet
            "proxy": proxy_note,
        })
    return feeds


def build_commodities(feeds: list[dict]) -> dict[str, dict]:
    """Collapse feeds → one metadata dict per base commodity (representative feed)."""
    groups: dict[str, list[dict]] = {}
    for f in feeds:
        groups.setdefault(f["base"], []).append(f)
    out = {}
    for base, group in groups.items():
        rep = pick_representative(group)
        out[base] = {
            "category": rep["category"],
            "access_tier": rep["access"],
            "frequency": rep["frequency"],
            "role": rep["role"],
            "retrieval_status": rep["retrieval"],
            "free_source_name": rep["free_source"],
            "free_source_url": rep["free_source_url"],
            "proxy_logic": to_proxy_logic(rep["retrieval"], rep["proxy"]),
            "regions": sorted({f["region"] for f in group}),
        }
    return out


def load(db, xlsx_path: Path = DEFAULT_XLSX) -> dict:
    feeds = _read_feeds(xlsx_path)
    commodities = build_commodities(feeds)

    created = updated = 0
    for name, meta in commodities.items():
        # Sanity-check vocab so bad reference data fails loudly rather than silently.
        # None is tolerated: the refreshed workbook carries no Access/Use columns,
        # so access_tier/role legitimately arrive empty and the existing value
        # (if any) is preserved rather than clobbered.
        assert meta["access_tier"] is None or meta["access_tier"] in ACCESS_TIERS, meta["access_tier"]
        assert meta["frequency"] is None or meta["frequency"] in FREQUENCIES, meta["frequency"]
        assert meta["role"] is None or meta["role"] in ROLES, meta["role"]
        assert meta["retrieval_status"] in RETRIEVAL_STATUSES, meta["retrieval_status"]

        ci = db.query(CommodityIndex).filter(func.lower(CommodityIndex.name) == name.lower()).first()
        if ci is None:
            ci = CommodityIndex(name=name)
            db.add(ci)
            created += 1
        else:
            updated += 1
        ci.category = meta["category"] or ci.category
        ci.access_tier = meta["access_tier"] or ci.access_tier
        ci.frequency = meta["frequency"] or ci.frequency
        ci.role = meta["role"] or ci.role
        ci.retrieval_status = meta["retrieval_status"]
        ci.free_source_name = meta["free_source_name"]
        ci.free_source_url = meta["free_source_url"]
        ci.proxy_logic = meta["proxy_logic"]

    blocked = [n for n, m in commodities.items() if m["retrieval_status"] == "blocked"]
    region_combos = sum(len(m["regions"]) for m in commodities.values())
    return {
        "feeds": len(feeds),
        "commodities": len(commodities),
        "created": created,
        "updated": updated,
        "blocked": sorted(blocked),
        "region_combos": region_combos,
    }


def main():
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    if not xlsx.exists():
        raise SystemExit(f"Reference workbook not found: {xlsx}")
    bypass_rls_var.set(True)  # platform reference data — no user context
    db = SessionLocal()
    try:
        report = load(db, xlsx)
        db.commit()
    finally:
        db.close()
    print(f"Loaded {report['feeds']} feeds → {report['commodities']} commodities "
          f"({report['created']} created, {report['updated']} updated) across "
          f"{report['region_combos']} region combos.")
    print(f"Blocked (marked, not dropped): {report['blocked']}")


if __name__ == "__main__":
    main()
