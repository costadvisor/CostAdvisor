import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import api, { formatApiError } from '../../api';

/* A GET that follows its URL and params.
 *
 *   const { data, error, status, loading, reload, setData } =
 *     useApi('/api/intel/products', { family, q, limit: 60 });
 *
 * - A falsy `url` (or `enabled: false`) means "not yet" — nothing is fetched and
 *   `loading` is false. Use it for dependent requests.
 * - Params are compared by value, so an inline object literal does not refetch
 *   on every render. null / undefined / '' params are dropped; arrays go out as
 *   repeated keys (`fn=a&fn=b`), which is what FastAPI list params expect.
 * - A superseded request is aborted, and its late answer can never overwrite a
 *   newer one.
 * - `keepPrevious: true` keeps the last data on screen while the next request
 *   is in flight (catalogue filters); the default clears it, so a detail page
 *   never shows product A's body under product B's header.
 * - NB the shared axios interceptor turns a 403 into a redirect to /dashboard
 *   and a 401 into /login; only other failures reach `error`.
 */

function cleanParams(params) {
  if (!params) return undefined;
  const out = {};
  Object.entries(params).forEach(([k, v]) => {
    if (v === null || v === undefined || v === '') return;
    if (Array.isArray(v) && v.length === 0) return;
    out[k] = v;
  });
  return out;
}

function stableKey(params) {
  const p = cleanParams(params);
  if (!p) return '';
  return JSON.stringify(Object.keys(p).sort().map((k) => [k, p[k]]));
}

export default function useApi(url, params, { enabled = true, keepPrevious = false } = {}) {
  const key = url && enabled ? `${url}|${stableKey(params)}` : null;
  const [state, setState] = useState({ data: null, error: null, status: null, loading: !!key });
  const [nonce, setNonce] = useState(0);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  useEffect(() => {
    if (!key) {
      setState((s) => (s.loading ? { ...s, loading: false } : s));
      return undefined;
    }
    const ctrl = new AbortController();
    setState((s) => ({ data: keepPrevious ? s.data : null, error: null, status: null, loading: true }));
    api.get(url, {
      params: cleanParams(paramsRef.current),
      signal: ctrl.signal,
      paramsSerializer: { indexes: null },
    })
      .then((res) => {
        if (!ctrl.signal.aborted) setState({ data: res.data, error: null, status: res.status, loading: false });
      })
      .catch((err) => {
        if (ctrl.signal.aborted || axios.isCancel(err)) return;
        setState((s) => ({
          data: keepPrevious ? s.data : null,
          error: formatApiError(err),
          status: err?.response?.status ?? null,
          loading: false,
        }));
      });
    return () => ctrl.abort();
    // `url` is inside `key`; params are read from the ref so a new-but-equal
    // object does not refire the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const setData = useCallback((updater) => {
    setState((s) => ({ ...s, data: typeof updater === 'function' ? updater(s.data) : updater }));
  }, []);

  return { ...state, reload, setData };
}
