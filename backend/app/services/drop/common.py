"""Small helpers the content loaders share, kept from the retired July loaders.

The 2026-07 loaders (`catalog_loader`, `index_loader`, `dimension_loader`) were
removed with the July seed path. The content-drop loaders and a few services
still need these pieces, so they live here now:

* `REGION_MAP` — drop region code -> app region name.
* `BASE_PERIOD` — the index levels' fixed anchor month.
* `_apply` / `_clean` — idempotent field writes.
* `_slug` — the slug rule editorial and dimension keys use.
"""
from __future__ import annotations

from app.models.dimension import normalize_value
from app.services.drop.normalize import is_blank

# Drop region code -> app region name. This is the mapping the existing coverage
# rows use, so the catalogue stays on one region vocabulary. `GL` -> `GLOBAL` is
# a mapping decision: the drop itself never writes the string "GLOBAL".
REGION_MAP = {
    "EU": "Europe",
    "NA": "NA",
    "CN": "China",
    "IN": "India",
    "APAC": "APAC",
    "MEA": "MEA",
    "LA": "Latam",
    "GL": "GLOBAL",
}

# The drop states index levels against a fixed anchor: January 2023 = 100. It is
# not a column in the source, so it is recorded here rather than inferred per row.
# Forecast-only series have no 2023 row, so their base stays NULL.
BASE_PERIOD = "2023-01"

_UNSET = object()


def _apply(obj, field_name: str, value, changes: list) -> None:
    """Set a field only when it actually differs, recording that it did.

    Numeric columns come back from Postgres as Decimal, so an unchanged row
    would otherwise read as an update on every run and idempotency would
    never hold.
    """
    current = getattr(obj, field_name, _UNSET)
    if current is _UNSET:
        return
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and current is not None
        and not isinstance(current, bool)
    ):
        try:
            if float(current) == float(value):
                return
        except (TypeError, ValueError):
            pass
    elif current == value:
        return
    setattr(obj, field_name, value)
    changes.append(field_name)


def _clean(value):
    return None if is_blank(value) else value


def _slug(text: str) -> str:
    out = []
    for ch in normalize_value(text):
        out.append(ch if (ch.isalnum() or ch in "-_") else "-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:64]
