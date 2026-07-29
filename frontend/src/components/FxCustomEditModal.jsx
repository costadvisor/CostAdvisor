import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useToast } from './Toast';
import api from '../api';

/**
 * Team FX custom-override editor with the three value-type modes:
 *   fixed       — a static rate
 *   live        — always the latest daily scraped rate for the pair
 *   quarter_ref — references a platform quarterly rate
 * Extracted from FxRates.jsx so the Index Library drill-in can manage FX
 * overrides directly. Writes to /api/fx-rates/custom (team-scoped).
 *
 * Props: pair {from,to}, period {year,quarter,label}, current (CustomFxRateOut|null),
 *        liveRate (number|null), availableQuarters [{year,quarter,rate}], teamId, onSaved, onClose
 */
export default function FxCustomEditModal({ pair, period, current, liveRate, availableQuarters, teamId, onSaved, onClose }) {
  const { addToast } = useToast();
  const [mode, setMode] = useState(current?.value_type || 'fixed');
  const [fixedValue, setFixedValue] = useState(
    current?.value_type === 'fixed' && current.rate != null ? String(current.rate) : ''
  );
  const [refPeriod, setRefPeriod] = useState(
    current?.value_type === 'quarter_ref'
      ? `${current.ref_year}-${current.ref_quarter}`
      : (availableQuarters[0] ? `${availableQuarters[0].year}-${availableQuarters[0].quarter}` : '')
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const payload = {
      team_id: teamId,
      from_currency: pair.from,
      to_currency: pair.to,
      year: period.year,
      quarter: period.quarter,
      value_type: mode,
      rate: null, ref_year: null, ref_quarter: null,
    };
    if (mode === 'fixed') {
      const v = parseFloat(fixedValue);
      if (isNaN(v) || v <= 0) { addToast('Enter a valid positive rate', 'error'); return; }
      payload.rate = v;
    } else if (mode === 'quarter_ref') {
      if (!refPeriod) { addToast('Select a platform quarter', 'error'); return; }
      const [ry, rq] = refPeriod.split('-').map(Number);
      payload.ref_year = ry;
      payload.ref_quarter = rq;
    }
    setSaving(true);
    try {
      await api.put('/api/fx-rates/custom', payload);
      addToast('Rate saved', 'success');
      onSaved();
      onClose();
    } catch {
      addToast('Failed to save rate', 'error');
    } finally { setSaving(false); }
  };

  const deleteRate = async () => {
    if (!current) return;
    setSaving(true);
    try {
      await api.delete('/api/fx-rates/custom-by-key', {
        params: { team_id: teamId, from_currency: pair.from, to_currency: pair.to, year: period.year, quarter: period.quarter },
      });
      addToast('Override removed', 'success');
      onSaved();
      onClose();
    } catch {
      addToast('Failed to remove override', 'error');
    } finally { setSaving(false); }
  };

  const modeStyle = (m) => ({
    padding: '8px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
    fontWeight: mode === m ? 600 : 400, border: `1px solid ${mode === m ? 'var(--accent)' : 'var(--border)'}`,
    background: mode === m ? 'var(--accent-dim)' : 'transparent',
    color: mode === m ? 'var(--accent)' : 'var(--text-secondary)',
  });

  return createPortal(
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 440 }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">{pair.from}/{pair.to} · {period.label}</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">
          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            <button style={modeStyle('fixed')} onClick={() => setMode('fixed')}>Custom rate</button>
            <button style={modeStyle('live')} onClick={() => setMode('live')}>Latest daily rate</button>
            <button style={modeStyle('quarter_ref')} onClick={() => setMode('quarter_ref')}>Platform Quarter</button>
          </div>

          {mode === 'fixed' && (
            <div>
              <label className="ca-label">Rate ({pair.from} → {pair.to})</label>
              <input className="ca-input" type="number" step="0.000001" placeholder="1.000000"
                value={fixedValue} onChange={e => setFixedValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') save(); }} autoFocus />
            </div>
          )}

          {mode === 'live' && (
            <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Always resolves to the latest daily scraped rate for this pair (refreshed once per day).
              </div>
              {liveRate != null
                ? <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 18, fontWeight: 700, color: 'var(--accent2)' }}>
                    Latest daily: {Number(liveRate).toFixed(4)}
                    <span style={{ fontSize: 10, color: 'var(--text-secondary)', marginLeft: 8 }}>refreshed daily</span>
                  </div>
                : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No daily rate available yet. Scrape live first.</div>}
            </div>
          )}

          {mode === 'quarter_ref' && (
            <div>
              <label className="ca-label">Use platform rate from quarter</label>
              {availableQuarters.length === 0
                ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>No platform quarterly rates available.</div>
                : <select className="ca-select" value={refPeriod} onChange={e => setRefPeriod(e.target.value)}>
                    {availableQuarters.map(q => (
                      <option key={`${q.year}-${q.quarter}`} value={`${q.year}-${q.quarter}`}>
                        Q{q.quarter}-{q.year} — {q.rate != null ? Number(q.rate).toFixed(4) : 'no data'}
                      </option>
                    ))}
                  </select>}
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>
                Looks up the platform quarterly rate dynamically each time costing runs.
              </div>
            </div>
          )}
        </div>
        <div className="ca-modal-footer">
          {current && (
            <button className="ca-btn ca-btn-danger" onClick={deleteRate} disabled={saving} style={{ marginRight: 'auto' }}>
              Remove Override
            </button>
          )}
          <button className="ca-btn ca-btn-ghost" onClick={onClose}>Cancel</button>
          <button className="ca-btn ca-btn-primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
