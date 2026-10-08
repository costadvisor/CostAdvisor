import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { parseStatusParam, statusParamOf, statusQuery, isDefaultStatus } from '../../../components/intel';

/* The catalogue's filters live in the URL query string, so a filtered view is
 * shareable and the back button steps through it. URL keys are the API's own
 * parameter names (GET /api/intel/products, docs/api/intel_catalogue.md §3).
 *
 * The API ignores a parameter it does not know (a stale `family=`, `tier=` or
 * `demo=` URL would get the unfiltered list), so this list is the contract.
 *
 * `status` is special: absent in the URL means the default view (Verified
 * makers + Concentrated supply), and the request always names the statuses
 * explicitly, so /facets and /products count the same cards. */
export const FILTER_KEYS = [
  'q', 'family_id', 'subfamily_id', 'line_id', 'industry', 'fn', 'supplier', 'region', 'trend', 'has_report',
];
const ID_KEYS = new Set(['family_id', 'subfamily_id', 'line_id']);
const TRENDS = new Set(['up', 'flat', 'down']);

export default function useProductFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const statusRaw = searchParams.get('status');
  const statuses = useMemo(() => parseStatusParam(statusRaw), [statusRaw]);

  const filters = useMemo(() => {
    const out = {};
    FILTER_KEYS.forEach((k) => {
      const v = searchParams.get(k);
      out[k] = v == null || v === '' ? null : v;
    });
    // Ids are positive integers; anything else would be a 422.
    ID_KEYS.forEach((k) => { if (out[k] != null && !/^\d+$/.test(out[k])) out[k] = null; });
    // `has_report` is on or absent: has_report=false would ask for the lines
    // WITHOUT a report, which is not what an unticked toggle means.
    out.has_report = out.has_report === '1' || out.has_report === 'true' ? true : null;
    if (out.trend && !TRENDS.has(out.trend)) out.trend = null;
    return out;
  }, [searchParams]);

  // What the API gets: every filter plus the explicit status list. No limit:
  // the grid needs every card to group them (T7).
  const params = useMemo(() => ({ ...filters, status: statusQuery(statuses) }), [filters, statuses]);

  const statusIsDefault = isDefaultStatus(statuses);
  const activeCount = FILTER_KEYS.filter((k) => filters[k] != null).length + (statusIsDefault ? 0 : 1);

  /* Patch one or more keys. null / '' removes a key. Clicks push a history
   * entry; typing passes { replace: true }. */
  const update = useCallback((patch, { replace = false } = {}) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(patch).forEach(([k, v]) => {
        if (v == null || v === '' || v === false) next.delete(k);
        else next.set(k, v === true ? '1' : String(v));
      });
      return next;
    }, { replace });
  }, [setSearchParams]);

  const setStatuses = useCallback((codes) => update({ status: statusParamOf(codes) }), [update]);

  /* Clears the filters and returns to the default status view. */
  const clearAll = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      [...FILTER_KEYS, 'status'].forEach((k) => next.delete(k));
      return next;
    });
  }, [setSearchParams]);

  return {
    filters, statuses, statusIsDefault, params, activeCount, update, setStatuses, clearAll,
  };
}
