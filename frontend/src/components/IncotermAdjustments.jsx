import { useState } from 'react';
import api, { formatApiError } from '../api';
import { useAlert } from './ConfirmDialog';

// Mirror of backend/app/constants/incoterms.py COST_BUCKETS + labels
export const COST_BUCKETS = [
  { key: 'export_clear', label: 'Export clearance' },
  { key: 'main_freight', label: 'Main carriage' },
  { key: 'insurance',    label: 'Transit insurance' },
  { key: 'import_clear', label: 'Import clearance' },
  { key: 'duty',         label: 'Import duties' },
  { key: 'last_mile',    label: 'Last-mile delivery' },
];

/**
 * Editable per-bucket adjustments. Each bucket is either flat ($/unit) or pct
 * (% of price). A "Use lane defaults" button fills empty buckets from the
 * region's default lane.
 *
 * Props:
 *   value: { [bucket]: { type: 'flat'|'pct', value: number } } | null
 *   onChange(next): called with the updated dict (or null when empty)
 *   editing: bool
 *   originRegion / destinationRegion: used for the lane defaults lookup
 *   currencySym: '$' or '€' for the flat-input prefix
 */
export default function IncotermAdjustments({
  value, onChange, editing,
  originRegion, destinationRegion, currencySym = '$',
}) {
  const [loadingDefaults, setLoadingDefaults] = useState(false);
  const showAlert = useAlert();
  const adj = value || {};

  const setBucket = (key, next) => {
    const out = { ...adj };
    if (next == null) delete out[key];
    else out[key] = next;
    onChange(Object.keys(out).length ? out : null);
  };

  const setType = (key, type) => {
    const cur = adj[key] || { value: 0 };
    setBucket(key, { type, value: cur.value || 0 });
  };

  const setValue = (key, raw) => {
    const num = raw === '' ? 0 : Number(raw);
    if (Number.isNaN(num)) return;
    const cur = adj[key] || { type: 'flat' };
    if (num === 0 && !adj[key]) return; // don't auto-create empties
    setBucket(key, { type: cur.type || 'flat', value: num });
  };

  const useLaneDefaults = async () => {
    if (!originRegion || !destinationRegion) {
      showAlert({ title: 'Regions required', message: 'Set producing region and destination region first.' });
      return;
    }
    setLoadingDefaults(true);
    try {
      const { data } = await api.get('/api/freight-lanes/lookup', {
        params: { origin_region: originRegion, destination_region: destinationRegion, mode: 'sea' },
      });
      if (!data) {
        showAlert({ title: 'No defaults found', message: 'No lane default for this region pair.' });
        return;
      }
      // Fill buckets we don't already have set.
      const next = { ...adj };
      Object.entries(data.adjustments || {}).forEach(([k, v]) => {
        if (!next[k]) next[k] = v;
      });
      onChange(Object.keys(next).length ? next : null);
    } catch (err) {
      showAlert({ title: 'Lane lookup failed', message: formatApiError(err) });
    } finally {
      setLoadingDefaults(false);
    }
  };

  return (
    <div>
      {editing && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>
            Per-bucket landed-cost adjustments. Empty buckets fall back to lane defaults.
          </span>
          <button
            type="button"
            className="ca-btn ca-btn-ghost ca-btn-sm"
            onClick={useLaneDefaults}
            disabled={loadingDefaults}
          >
            {loadingDefaults ? 'Loading…' : 'Use lane defaults'}
          </button>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 90px 100px 30px', gap: 8, alignItems: 'center', fontSize: 11 }}>
        <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Bucket</span>
        <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Type</span>
        <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase', textAlign: 'right' }}>Value</span>
        <span></span>
        {COST_BUCKETS.map(b => {
          const cur = adj[b.key];
          const type = cur?.type || 'flat';
          const val = cur?.value ?? '';
          return (
            <ContentsRow key={b.key}>
              <span>{b.label}</span>
              {editing ? (
                <select
                  className="ca-select"
                  style={{ fontSize: 11, padding: '4px 6px' }}
                  value={type}
                  onChange={e => setType(b.key, e.target.value)}
                  disabled={!cur}
                >
                  <option value="flat">Flat</option>
                  <option value="pct">%</option>
                </select>
              ) : (
                <span style={{ color: 'var(--muted)' }}>{cur ? (type === 'pct' ? '%' : 'Flat') : '—'}</span>
              )}
              {editing ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                  <span style={{ color: 'var(--muted)', fontSize: 11 }}>{type === 'pct' ? '' : currencySym}</span>
                  <input
                    type="number"
                    className="ca-input"
                    value={val}
                    placeholder="—"
                    style={{ textAlign: 'right', padding: '4px 6px', fontSize: 11, width: 70 }}
                    onChange={e => setValue(b.key, e.target.value)}
                  />
                  <span style={{ color: 'var(--muted)', fontSize: 11 }}>{type === 'pct' ? '%' : ''}</span>
                </div>
              ) : (
                <span style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                  {cur
                    ? (type === 'pct'
                        ? `${cur.value}%`
                        : `${currencySym}${cur.value}`)
                    : '—'}
                </span>
              )}
              {editing && cur ? (
                <button className="ca-btn-danger" onClick={() => setBucket(b.key, null)} title="Clear">x</button>
              ) : <span />}
            </ContentsRow>
          );
        })}
      </div>
    </div>
  );
}

// Helper so each bucket is a fragment row in the parent grid.
function ContentsRow({ children }) {
  return <>{children}</>;
}
