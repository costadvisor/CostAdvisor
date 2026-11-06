import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { parseStatusParam, statusParamOf, statusQuery, isDefaultStatus } from '../../../components/intel';

/* The catalogue's filters live in the URL query string, so a filtered view is
 * shareable and the back button steps through it. URL keys are the API's own
 * parameter names (GET /api/intel/products, docs/api/intel_catalogue.md §3).
 *
 * The sidebar groups (LIST_KEYS) take several values as a repeated key
 * (`industry=A&industry=B`): the API ORs the values of one key and ANDs the
 * keys, as the Lines page does. Their filter values are arrays, empty when off.
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
export const LIST_KEYS = new Set(['family_id', 'industry', 'fn', 'supplier', 'region']);
const ID_KEYS = new Set(['family_id', 'subfamily_id', 'line_id']);
const TRENDS = new Set(['up', 'flat', 'down']);
const isId = (v) => /^\d+$/.test(v);

export const isOn = (v) => (Array.isArray(v) ? v.length > 0 : v != null);

export default function useProductFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const statusRaw = searchParams.get('status');
  const statuses = useMemo(() => parseStatusParam(statusRaw), [statusRaw]);

  const filters = useMemo(() => {
    const out = {};
    FILTER_KEYS.forEach((k) => {
      if (LIST_KEYS.has(k)) {
        // Ids are positive integers; anything else would be a 422.
        out[k] = [...new Set(searchParams.getAll(k).filter((v) => v !== '' && (!ID_KEYS.has(k) || isId(v))))];
        return;
      }
      const v = searchParams.get(k);
      out[k] = v == null || v === '' ? null : v;
      if (ID_KEYS.has(k) && out[k] != null && !isId(out[k])) out[k] = null;
    });
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
  // One per ticked value, as on the Lines page.
  const activeCount = FILTER_KEYS.reduce((n, k) => {
    const v = filters[k];
    return n + (Array.isArray(v) ? v.length : v != null ? 1 : 0);
  }, 0) + (statusIsDefault ? 0 : 1);

  /* Patch one or more keys. null / '' / [] removes a key; an array writes
   * one entry per value. Clicks push a history entry; typing passes
   * { replace: true }. */
  const update = useCallback((patch, { replace = false } = {}) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(patch).forEach(([k, v]) => {
        next.delete(k);
        if (Array.isArray(v)) v.forEach((x) => next.append(k, String(x)));
        else if (v != null && v !== '' && v !== false) next.set(k, v === true ? '1' : String(v));
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
