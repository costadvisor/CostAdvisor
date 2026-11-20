import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '../../../api';

/* Helpers shared by the supplier directory and the supplier detail page. */

export const SUPPLIERS_URL = '/api/intel/suppliers';
export const PAGE_SIZE = 48;
export const MIN_PRODUCT_STEPS = [2, 3, 5, 10, 20];

export { supplierHref as supplierPath, productHref as productPath, lineHref as linePath } from '../../../components/intel';

/* The directory lists a producer only when it is a counted maker (it counts
   toward the supplier floor) on at least one product in the grid, and every
   number here counts those products. Labels say "products tracked here",
   never a bare total (design §4.2). */
export const PRODUCT_COUNT_HINT = 'Products in the grid on which this producer is a counted maker.';

/* ── Filters, synced to the URL ──────────────────────────────────────
 * ?q= &family_id=4&family_id=9 &industry=…&industry=… &integrated=true|false &min=N
 * Family and industry take several values (a repeated key): a maker of any of
 * them matches, as on the Lines and Products pages. Updates replace the
 * history entry, so "Back" from a supplier page lands on the list exactly as
 * it was filtered. */
export const LIST_FILTERS = ['family_id', 'industry'];

export function useSupplierFilters() {
  const [params, setParams] = useSearchParams();

  const filters = useMemo(() => {
    const integrated = params.get('integrated');
    const min = parseInt(params.get('min'), 10);
    return {
      q: params.get('q') || '',
      family_id: [...new Set(params.getAll('family_id').filter((v) => /^\d+$/.test(v)))],
      industry: [...new Set(params.getAll('industry').filter(Boolean))],
      integrated: integrated === 'true' || integrated === 'false' ? integrated : '',
      min: Number.isFinite(min) && min > 1 ? min : 1,
    };
  }, [params]);

  // An array writes one entry per value; '' / null / [] removes the key.
  const setFilter = useCallback((key, value) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete(key);
      if (Array.isArray(value)) value.forEach((v) => next.append(key, String(v)));
      else if (!(value === '' || value == null || (key === 'min' && Number(value) <= 1))) next.set(key, String(value));
      return next;
    }, { replace: true });
  }, [setParams]);

  const clearAll = useCallback(() => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      ['q', 'family_id', 'industry', 'integrated', 'min'].forEach((k) => next.delete(k));
      return next;
    }, { replace: true });
  }, [setParams]);

  const activeCount = ['q', 'integrated'].filter((k) => filters[k]).length
    + filters.family_id.length + filters.industry.length + (filters.min > 1 ? 1 : 0);

  return { filters, setFilter, clearAll, activeCount };
}

/* Query params for GET /api/intel/suppliers. `omit` drops one filter, which is
 * how the sidebar gets a group's counts: every other filter applied, its own
 * left out, so a count is what ticking that option gives. */
export function apiParams(filters, { omit, ...extra } = {}) {
  const p = {
    q: filters.q.trim() || undefined,
    family_id: filters.family_id.length ? filters.family_id : undefined,
    industry: filters.industry.length ? filters.industry : undefined,
    integrated: filters.integrated || undefined,
    min_products: filters.min > 1 ? filters.min : undefined,
  };
  (Array.isArray(omit) ? omit : [omit]).forEach((k) => { if (k) delete p[k]; });
  return { ...p, ...extra };
}

/* ── Family colours ──────────────────────────────────────────────────
 * One colour per family, the same on every card and on the detail page.
 * The palette is twelve theme-aware variables (styles/intel-suppliers.css);
 * families are ranked by how many suppliers make them, so the common ones
 * never share a colour. The ranking comes from one small request, cached for
 * the session. */
export const FAMILY_COLOR_COUNT = 12;

export function rankFamilies(facetFamilies = []) {
  return [...facetFamilies]
    .sort((a, b) => (b.count || 0) - (a.count || 0) || a.name.localeCompare(b.name))
    .map((f) => f.name);
}

let familyOrderPromise = null;
function loadFamilyOrder() {
  if (!familyOrderPromise) {
    familyOrderPromise = api.get(SUPPLIERS_URL, { params: { limit: 1 } })
      .then((res) => rankFamilies(res.data?.families || []))
      .catch(() => {
        familyOrderPromise = null;
        return [];
      });
  }
  return familyOrderPromise;
}

export function familyColorFrom(order) {
  const index = new Map(order.map((name, i) => [name, i]));
  return (name) => {
    const i = index.get(name);
    return i == null ? 'var(--muted)' : `var(--ixs-fam-${i % FAMILY_COLOR_COUNT})`;
  };
}

/* `known` — the ranked family list when the caller already has it (the
 * directory's unfiltered facets); without it the hook fetches it once. */
export function useFamilyColors(known) {
  const knownKey = known && known.length ? known.join('\u0001') : '';
  const [fetched, setFetched] = useState(null);
  useEffect(() => {
    if (knownKey) return undefined;
    let alive = true;
    loadFamilyOrder().then((list) => { if (alive) setFetched(list); });
    return () => { alive = false; };
  }, [knownKey]);
  return useMemo(
    () => familyColorFrom(knownKey ? knownKey.split('\u0001') : (fetched || [])),
    [knownKey, fetched],
  );
}

export const ROLE_LABEL = { producer: 'Producer', distributor: 'Distributor' };

/* `total` for several filter variants at once — the sidebar counts for the
 * integration options (the list endpoint has no facet for those).
 * requests: { key: params }. Returns { key: total } once all have answered;
 * failures leave that count out rather than showing a wrong number. */
export function useTotals(requests) {
  const sig = JSON.stringify(requests);
  const [totals, setTotals] = useState({});
  useEffect(() => {
    const ctrl = new AbortController();
    const entries = Object.entries(JSON.parse(sig));
    Promise.all(entries.map(([key, params]) => api
      .get(SUPPLIERS_URL, { params: { ...params, limit: 1 }, signal: ctrl.signal, paramsSerializer: { indexes: null } })
      .then((res) => [key, res.data?.total])
      .catch(() => [key, undefined])))
      .then((pairs) => {
        if (ctrl.signal.aborted) return;
        const out = {};
        pairs.forEach(([k, v]) => { if (v != null) out[k] = v; });
        setTotals(out);
      });
    return () => ctrl.abort();
  }, [sig]);
  return totals;
}
