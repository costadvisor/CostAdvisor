import { useEffect, useState } from 'react';
import api from '../api';

// Session-level cache: regions are global reference data that rarely change,
// so fetch once and share across every picker instance (CostModelBuilder alone
// renders two). invalidateRegions() lets a future admin UI force a refresh.
let _cache = null;
let _inflight = null;

export function loadRegions() {
  if (_cache) return Promise.resolve(_cache);
  if (!_inflight) {
    _inflight = api.get('/api/regions')
      .then(res => { _cache = res.data; _inflight = null; return _cache; })
      .catch(err => { _inflight = null; throw err; });
  }
  return _inflight;
}

export function invalidateRegions() { _cache = null; }

// Order a flat region list parent-first: each top-level region immediately
// followed by its subregions, so a native <select> reads as a shallow tree.
function orderRegions(regions) {
  const byParent = new Map();
  for (const r of regions) {
    const key = r.parent_id ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(r);
  }
  for (const list of byParent.values()) list.sort((a, b) => a.name.localeCompare(b.name));

  const out = [];
  for (const top of byParent.get(null) || []) {
    out.push({ ...top, depth: 0 });
    for (const child of byParent.get(top.id) || []) out.push({ ...child, depth: 1 });
  }
  // Append any orphan (parent not in the list) so nothing is ever hidden.
  const seen = new Set(out.map(r => r.id));
  for (const r of regions) if (!seen.has(r.id)) out.push({ ...r, depth: 0 });
  return out;
}

/**
 * Region dropdown backed by /api/regions. Submits the region `code` (the value
 * the backend stores), shows the human name, and indents subregions under their
 * parent. `onChange` receives the selected code string.
 */
export default function RegionSelect({
  value,
  onChange,
  includeEmpty = false,
  emptyLabel = '—',
  className = 'ca-select',
  id,
  disabled = false,
}) {
  const [regions, setRegions] = useState(_cache || []);

  useEffect(() => {
    let alive = true;
    loadRegions().then(data => { if (alive) setRegions(data); }).catch(() => { /* leave empty */ });
    return () => { alive = false; };
  }, []);

  const ordered = orderRegions(regions);
  // Keep the current value selectable even if it's a legacy/odd code the list
  // doesn't include (backfilled data may hold values not yet tidied by an admin).
  const valueKnown = !value || ordered.some(r => r.code === value);

  return (
    <select
      className={className}
      id={id}
      value={value ?? ''}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
    >
      {includeEmpty && <option value="">{emptyLabel}</option>}
      {!valueKnown && <option value={value}>{value} (unknown)</option>}
      {ordered.map(r => (
        <option key={r.id} value={r.code}>
          {r.depth ? '  ↳ ' : ''}{r.name}
        </option>
      ))}
    </select>
  );
}
