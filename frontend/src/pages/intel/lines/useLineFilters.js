import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ssfValue, famValue, listedOf, UNPLACED_LABEL,
} from './lineUtil';

/* Product-lines filters, kept in the URL:
 *   ?q=…&family_id=4&family_id=9&subfamily_id=31&industry=…&fn=…&report=yes|no&empty=1
 *
 * The page fetches /api/intel/lines once per search term (the search also
 * matches product codes and names, which only the server knows) and applies
 * the other filters here — every row carries its family / sub-family /
 * industries / functions, so each facet can count "what you would get if you
 * picked this" (OR within a group, AND across groups).
 *
 * Lines with no tracked product (no listed card) are folded away unless
 * `empty=1` ("Show lines with no tracked product"); the facets count the lines
 * the page can show. */

const LIST_KEYS = ['family_id', 'subfamily_id', 'industry', 'fn'];
const FLAG_KEYS = ['report'];

const PRED = {
  family_id: (it, sel) => sel.includes(famValue(it)),
  subfamily_id: (it, sel) => sel.includes(ssfValue(it)),
  industry: (it, sel) => (it.industries || []).some((x) => sel.includes(x)),
  fn: (it, sel) => (it.functions || []).some((x) => sel.includes(x)),
  report: (it, v) => (v === 'yes' ? !!it.has_report : !it.has_report),
};

function isActive(v) {
  return Array.isArray(v) ? v.length > 0 : !!v;
}

function passes(it, filters, skip) {
  for (const key of Object.keys(PRED)) {
    if (key === skip) continue;
    const v = filters[key];
    if (isActive(v) && !PRED[key](it, v)) return false;
  }
  return true;
}

export function useLineFilters() {
  const [params, setParams] = useSearchParams();

  const filters = useMemo(() => {
    const f = { q: params.get('q') || '' };
    LIST_KEYS.forEach((k) => { f[k] = params.getAll(k); });
    FLAG_KEYS.forEach((k) => {
      const v = params.get(k);
      f[k] = v === 'yes' || v === 'no' ? v : '';
    });
    f.empty = params.get('empty') === '1';
    return f;
  }, [params]);

  const update = useCallback((patch) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(patch).forEach(([k, v]) => {
        next.delete(k);
        if (Array.isArray(v)) v.forEach((x) => next.append(k, x));
        else if (v === true) next.set(k, '1');
        else if (v) next.set(k, v);
      });
      return next;
    }, { replace: true });
  }, [setParams]);

  const clear = useCallback(() => setParams(new URLSearchParams(), { replace: true }), [setParams]);

  const activeCount = [...LIST_KEYS, ...FLAG_KEYS].reduce((n, k) => {
    const v = filters[k];
    return n + (Array.isArray(v) ? v.length : v ? 1 : 0);
  }, 0);

  return { filters, update, clear, activeCount };
}

function countBy(list, filters, dim, keysOf) {
  const counts = new Map();
  list.forEach((it) => {
    if (!passes(it, filters, dim)) return;
    keysOf(it).forEach((k) => counts.set(k, (counts.get(k) || 0) + 1));
  });
  return counts;
}

const byCountThenName = (a, b) => (b.count - a.count) || String(a.label).localeCompare(String(b.label));

/* The filtered rows plus a facet list per sidebar group. */
export function useFilteredLines(items, filters) {
  return useMemo(() => {
    const all = items || [];
    const emptyCount = all.filter((it) => listedOf(it) === 0).length;
    // Fold away the lines with no tracked product unless asked.
    const list = filters.empty ? all : all.filter((it) => listedOf(it) > 0);
    const rows = list.filter((it) => passes(it, filters));

    // Family: every family in the (searched) list, alphabetical, even at 0.
    const famCounts = countBy(list, filters, 'family_id', (it) => [famValue(it)]);
    const famNames = new Map();
    list.forEach((it) => { if (!famNames.has(famValue(it))) famNames.set(famValue(it), it.family?.name || '—'); });
    const families = Array.from(famNames.entries())
      .map(([value, label]) => ({ value, label, count: famCounts.get(value) || 0 }))
      .sort((a, b) => a.label.localeCompare(b.label));

    // Sub-family: the ones reachable under the other filters.
    const ssfCounts = countBy(list, filters, 'subfamily_id', (it) => [ssfValue(it)]);
    const ssfLabels = new Map();
    list.forEach((it) => {
      const v = ssfValue(it);
      if (!ssfLabels.has(v)) ssfLabels.set(v, it.subfamily?.name || UNPLACED_LABEL);
    });
    const subfamilies = Array.from(ssfLabels.entries())
      .map(([value, label]) => ({ value, label, count: ssfCounts.get(value) || 0 }))
      .filter((o) => o.count > 0 || filters.subfamily_id.includes(o.value))
      .sort(byCountThenName);

    const listFacet = (dim, field) => {
      const counts = countBy(list, filters, dim, (it) => it[field] || []);
      const names = new Set([...counts.keys(), ...filters[dim]]);
      return Array.from(names)
        .map((name) => ({ value: name, label: name, count: counts.get(name) || 0 }))
        .sort(byCountThenName);
    };
    const industries = listFacet('industry', 'industries');
    const functions = listFacet('fn', 'functions');

    const flagFacet = (dim, field) => {
      let yes = 0; let no = 0;
      list.forEach((it) => {
        if (!passes(it, filters, dim)) return;
        if (it[field]) yes += 1; else no += 1;
      });
      return { yes, no };
    };

    return {
      rows,
      shownTotal: list.length,
      emptyCount,
      facets: {
        families,
        subfamilies,
        industries,
        functions,
        report: flagFacet('report', 'has_report'),
      },
    };
  }, [items, filters]);
}

/* Family → sub-family → lines, alphabetical, unplaced last. */
export function groupLines(rows) {
  const fams = new Map();
  rows.forEach((it) => {
    const fk = famValue(it);
    if (!fams.has(fk)) fams.set(fk, { id: it.family?.id ?? null, name: it.family?.name || '—', subs: new Map() });
    const { subs } = fams.get(fk);
    const key = ssfValue(it);
    if (!subs.has(key)) {
      subs.set(key, {
        key,
        id: it.subfamily?.id ?? null,
        name: it.subfamily?.name || UNPLACED_LABEL,
        unplaced: !it.subfamily?.name,
        items: [],
      });
    }
    subs.get(key).items.push(it);
  });
  return Array.from(fams.values())
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((f) => {
      const clusters = Array.from(f.subs.values()).sort((a, b) => {
        if (a.unplaced !== b.unplaced) return a.unplaced ? 1 : -1;
        return a.name.localeCompare(b.name);
      });
      clusters.forEach((c) => c.items.sort((a, b) => a.name.localeCompare(b.name)));
      return {
        id: f.id, family: f.name, clusters, count: clusters.reduce((n, c) => n + c.items.length, 0),
      };
    });
}
