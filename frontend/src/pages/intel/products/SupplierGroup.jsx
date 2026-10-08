import { useEffect, useId, useMemo, useState } from 'react';
import { useApi } from '../../../components/intel';

/* Supplier filter: the facets' top producers (by card count), plus a search
 * that also reaches the others through GET /api/intel/suppliers. Same markup
 * and classes as the shared FilterGroup, single-select, because FilterGroup's
 * search only filters the options it is given.
 *
 * Only makers that count toward the supplier floor are here, on listed cards
 * (the API's rule). Counts: the top producers' counts are product cards in the
 * current status view. Producers found by search show no count. */
const COUNT_HINT = 'Products in this view where this producer is a counted maker.';

export default function SupplierGroup({ options = [], value, onPick, limit = 8 }) {
  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState('');
  const [needle, setNeedle] = useState('');
  const bodyId = useId();

  // Debounce the directory lookup; the local list filters as you type.
  useEffect(() => {
    const id = setTimeout(() => setNeedle(text.trim()), 250);
    return () => clearTimeout(id);
  }, [text]);
  const lookup = useApi(needle.length >= 2 ? '/api/intel/suppliers' : null, { q: needle, limit: 12 });

  const known = useMemo(() => new Set(options.map((o) => o.value)), [options]);
  const local = useMemo(() => {
    const t = text.trim().toLowerCase();
    if (t) return options.filter((o) => o.value.toLowerCase().includes(t));
    if (expanded || options.length <= limit) return options;
    const head = options.slice(0, limit);
    const sel = options.find((o) => o.value === value);
    return sel && !head.includes(sel) ? [...head, sel] : head;
  }, [options, text, expanded, limit, value]);

  const others = useMemo(() => {
    if (!text.trim() || needle !== text.trim()) return [];
    return (lookup.data?.items || [])
      .filter((s) => !known.has(s.name))
      .map((s) => ({ value: s.name }));
  }, [lookup.data, known, needle, text]);

  // A supplier picked through the URL or the search, outside the top 60.
  const pinned = value && !known.has(value) && !others.some((o) => o.value === value) ? [{ value }] : [];

  const toggle = (v) => onPick(value === v ? null : v);
  const row = (o) => {
    const checked = value === o.value;
    return (
      <label key={o.value} className={`ix-fitem${checked ? ' checked' : ''}`}>
        <input type="checkbox" checked={checked} onChange={() => toggle(o.value)} />
        <span className="ix-fitem-label">{o.value}</span>
        {o.count != null && <span className="ix-count" title={COUNT_HINT}>{o.count}</span>}
      </label>
    );
  };

  const searching = !!text.trim();
  const waiting = searching && (needle !== text.trim() || lookup.loading);

  return (
    <div className="ix-fgroup" role="group" aria-label="Supplier">
      <button type="button" className="ix-fgroup-head" aria-expanded={open} aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}>
        <span>Supplier</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {value && <span className="ix-fgroup-active">1 selected</span>}
          <span className="ix-chev" aria-hidden>▾</span>
        </span>
      </button>
      {open && (
        <div className="ix-fgroup-body" id={bodyId}>
          <div className="ix-fgroup-search">
            <input type="search" className="ix-search" value={text} onChange={(e) => setText(e.target.value)}
              placeholder="Search producers…"
              aria-label="Search producers" />
          </div>
          {!searching && pinned.map(row)}
          {local.map(row)}
          {searching && others.length > 0 && (
            <>
              <div className="ixp-fsub">Other producers</div>
              {others.map(row)}
            </>
          )}
          {searching && !waiting && local.length === 0 && others.length === 0 && (
            <div className="ix-fitem ix-muted" style={{ cursor: 'default' }}>No producer matches</div>
          )}
          {waiting && local.length === 0 && (
            <div className="ix-fitem ix-muted" style={{ cursor: 'default' }}>Searching…</div>
          )}
          {!searching && options.length > limit && (
            <button type="button" className="ix-fgroup-more" onClick={() => setExpanded((e) => !e)}>
              {expanded ? 'Show fewer' : `Show all ${options.length}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
