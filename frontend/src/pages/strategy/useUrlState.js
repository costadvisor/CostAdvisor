import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

/* Filters, sort and view kept in the URL, as ?tab= already is: they survive a
 * tab switch and Back, and a filtered view can be shared. `defaults` names the
 * fields and their unset value (left out of the URL); `prefix` keeps two tabs'
 * fields apart on one URL (Opportunities and Actions both filter by status).
 * Changes replace the history entry, like ?tab=.
 *
 *   const NO_FILTERS = { status: '', gem: '' };          // module level
 *   const [filters, setFilters] = useUrlState(NO_FILTERS, 'act_');
 *   setFilters((f) => ({ ...f, status: 'Done' }));     // or setFilters(NO_FILTERS)
 */
export default function useUrlState(defaults, prefix = '') {
  const [params, setParams] = useSearchParams();

  const read = useCallback((p) => Object.fromEntries(
    Object.entries(defaults).map(([k, d]) => [k, p.get(prefix + k) ?? d]),
  ), [defaults, prefix]);

  const value = useMemo(() => read(params), [read, params]);

  const set = useCallback((next) => {
    setParams((prev) => {
      const v = typeof next === 'function' ? next(read(prev)) : next;
      const out = new URLSearchParams(prev);
      Object.entries(defaults).forEach(([k, d]) => {
        if (v[k] == null || v[k] === d) out.delete(prefix + k);
        else out.set(prefix + k, v[k]);
      });
      return out;
    }, { replace: true });
  }, [setParams, read, defaults, prefix]);

  return [value, set];
}
