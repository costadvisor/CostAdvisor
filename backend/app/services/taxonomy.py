"""The supply taxonomy as screens read it: family › sub-family › product line
(design §2.2, §4.2).

    GET /api/taxonomy             the whole tree with card counts
    GET /api/taxonomy/families    the families, for filters and dropdowns
    GET /api/taxonomy/lines       the product-line picker (custom products)

Platform reference, read-only, the same for every user. Everything here is a
view over the Intelligence reference snapshot (`intel_reference.get_snapshot`),
so it shares that snapshot's load-version key and its definitions:

* a line is hidden when it is retired or flagged `do_not_publish`; hidden lines
  appear nowhere here;
* counts are counts of cards: listed templates (`catalog_visibility`) on a line
  that is not hidden. `default_view` counts the cards the Products grid opens
  on (verified makers or concentrated supply);
* `unplaced` counts the cards with no product line ("Product line not yet
  published": the off-axis products and the card-only keys).

Every sub-family node is in the tree, including those with no line (the
page folds them away) and the one per family that may be unnamed
(`name: null`). Order is the authored axis order (`sort_order`) at every
level.

A team product's line is not resolved here: that is
`app.services.effective_lines.effective_lines()`, which the team-side APIs
call (they hold the product ids and the team context).
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.product_line import LINE_KEY_SEP
from app.services import intel_reference as ref

PICKER_MAX = 1000


def _counts(snap: ref.Snapshot, line_ids) -> dict:
    listed = default_view = 0
    for lid in line_ids:
        for code in snap.line_cards.get(lid, ()):
            listed += 1
            default_view += snap.templates[code].default_view
    return {"listed": listed, "default_view": default_view}


def _visible_lines_by_subfamily(snap: ref.Snapshot) -> dict[int, dict[int | None, list]]:
    """family id → sub-family id (None: a line with no sub-family) → lines."""
    out: dict[int, dict[int | None, list]] = defaultdict(lambda: defaultdict(list))
    for ln in snap.lines.values():
        if not ln.hidden:
            out[ln.family_id][ln.subfamily_id].append(ln)
    for subs in out.values():
        for lines in subs.values():
            lines.sort(key=lambda ln: (ln.sort_order, ln.name.lower(), ln.id))
    return out


def _line_node(snap: ref.Snapshot, ln: ref.LineRow) -> dict:
    return {
        "id": ln.id,
        "name": ln.name,
        "platform": ln.platform,
        "in_v1_scope": ln.in_v1_scope,
        "has_report": bool(snap.reports_by_line.get(ln.id)),
        "flags": ref._flags(ln),
        "counts": _counts(snap, [ln.id]),
    }


def _build_tree(snap: ref.Snapshot) -> dict:
    by_family = _visible_lines_by_subfamily(snap)
    subs_by_family: dict[int, list[ref.SubfamilyRow]] = defaultdict(list)
    for sf in snap.subfamilies.values():
        subs_by_family[sf.family_id].append(sf)

    families = []
    totals = {"families": 0, "subfamilies": 0, "lines": 0, "listed": 0, "default_view": 0}
    for fam in sorted(snap.families.values(), key=lambda f: (f["sort_order"], f["name"].lower())):
        lines_by_sub = by_family.get(fam["id"], {})
        nodes = []
        for sf in sorted(subs_by_family.get(fam["id"], []),
                         key=lambda s: (s.sort_order, s.name is None, (s.name or "").lower())):
            lines = lines_by_sub.get(sf.id, [])
            c = _counts(snap, [ln.id for ln in lines])
            nodes.append({"id": sf.id, "name": sf.name,
                          "counts": {"lines": len(lines), **c},
                          "lines": [_line_node(snap, ln) for ln in lines]})
        loose = lines_by_sub.get(None, [])
        if loose:  # none in today's data; the schema allows it
            c = _counts(snap, [ln.id for ln in loose])
            nodes.append({"id": None, "name": None, "counts": {"lines": len(loose), **c},
                          "lines": [_line_node(snap, ln) for ln in loose]})
        all_lines = [ln.id for subs in lines_by_sub.values() for ln in subs]
        c = _counts(snap, all_lines)
        families.append({
            "id": fam["id"], "name": fam["name"],
            "counts": {"subfamilies": sum(1 for n in nodes if n["id"] is not None),
                       "lines": len(all_lines), **c},
            "subfamilies": nodes,
        })
        totals["families"] += 1
        totals["subfamilies"] += families[-1]["counts"]["subfamilies"]
        totals["lines"] += len(all_lines)
        totals["listed"] += c["listed"]
        totals["default_view"] += c["default_view"]

    unplaced = [t for t in snap.templates.values()
                if t.visible and t.product_line_id not in snap.lines]
    unplaced_counts = {"listed": len(unplaced),
                       "default_view": sum(1 for t in unplaced if t.default_view)}
    totals["listed"] += unplaced_counts["listed"]
    totals["default_view"] += unplaced_counts["default_view"]
    return {"counts": totals, "families": families, "unplaced": unplaced_counts}


def get_tree(db: Session) -> dict:
    snap = ref.get_snapshot(db)
    return ref.derived(snap, "taxonomy_tree", _build_tree)


def _families(snap: ref.Snapshot) -> list[dict]:
    tree = ref.derived(snap, "taxonomy_tree", _build_tree)
    return sorted(({"id": f["id"], "name": f["name"], "counts": f["counts"]}
                   for f in tree["families"]), key=lambda f: f["name"].lower())


def list_families(db: Session) -> dict:
    snap = ref.get_snapshot(db)
    items = ref.derived(snap, "taxonomy_families", _families)
    return {"total": len(items), "items": items}


def _picker_items(snap: ref.Snapshot) -> list[dict]:
    items = []
    for ln in snap.lines.values():
        if ln.hidden:
            continue
        former = sorted({k.split(LINE_KEY_SEP, 1)[-1] for k in ln.former_keys} - {ln.name})
        items.append({
            "id": ln.id, "name": ln.name, "line_key": ln.line_key,
            "family": {"id": ln.family_id, "name": ln.family},
            "subfamily": ref._subfamily_ref(ln),
            "platform": ln.platform,
            "former_names": former,
            "counts": _counts(snap, [ln.id]),
            "_search": " ".join([ln.name, ln.family, ln.subfamily or "", ln.platform or "",
                                 *former]).lower(),
            "_sort": (ln.family.lower(), ln.subfamily is None, (ln.subfamily or "").lower(),
                      ln.name.lower()),
        })
    items.sort(key=lambda it: it["_sort"])
    return items


def search_lines(db: Session, *, q: str | None = None, family_id: int | None = None,
                 subfamily_id: int | None = None, limit: int | None = None) -> dict:
    """The product-line picker: every visible line (a custom product may sit
    on a line with no card yet), filtered by a substring of the line, family,
    sub-family, platform or a former name, and by ids."""
    snap = ref.get_snapshot(db)
    needle = q.strip().lower() if q and q.strip() else None
    out = []
    for it in ref.derived(snap, "taxonomy_picker", _picker_items):
        if family_id is not None and it["family"]["id"] != family_id:
            continue
        if subfamily_id is not None and (it["subfamily"] or {}).get("id") != subfamily_id:
            continue
        if needle and needle not in it["_search"]:
            continue
        out.append(it)
    total = len(out)
    if limit is not None:
        out = out[:limit]
    return {"total": total,
            "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in out]}


__all__ = ["PICKER_MAX", "get_tree", "list_families", "search_lines"]
