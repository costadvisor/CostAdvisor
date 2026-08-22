import { useState, useEffect, useRef } from 'react';
import Modal from './Modal';
import api, { formatApiError } from '../api';

export default function EditCellModal({ isOpen, onClose, cell, teamId, teamSource, periods, onSaved }) {
  const [newValue, setNewValue] = useState('');
  const [applyMode, setApplyMode] = useState('single'); // 'single' | 'all' | 'range'
  const [rangeStart, setRangeStart] = useState(0);
  const [rangeEnd, setRangeEnd] = useState(0);
  const [saving, setSaving] = useState(false);
  const [scraping, setScraping] = useState(false);
  const [message, setMessage] = useState(null); // { type: 'success' | 'error', text }
  const inputRef = useRef(null);

  // Reset state when cell changes
  useEffect(() => {
    if (cell) {
      setNewValue(cell.value !== null && cell.value !== undefined ? String(cell.value) : '');
      setApplyMode('single');
      setMessage(null);
      setSaving(false);
      setScraping(false);
      // Focus input after render
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [cell]);

  if (!cell) return null;

  const periodLabel = `Q${cell.quarter}-${String(cell.year).slice(2)}`;
  const isOverridden = cell.source === 'team_override';

  const handleSave = async () => {
    const val = parseFloat(newValue);
    if (isNaN(val)) {
      setMessage({ type: 'error', text: 'Please enter a valid number.' });
      return;
    }

    setSaving(true);
    setMessage(null);
    try {
      if (applyMode === 'single') {
        const res = await api.put('/api/indexes/overrides/cell', {
          team_id: teamId,
          commodity_id: cell.commodity_id,
          region: cell.region,
          year: cell.year,
          quarter: cell.quarter,
          value: val,
        });
        onSaved(res.data);
      } else {
        // Build periods list
        let targetPeriods;
        if (applyMode === 'all') {
          targetPeriods = periods.map(p => ({ year: p.year, quarter: p.quarter }));
        } else {
          // range
          targetPeriods = periods.slice(rangeStart, rangeEnd + 1).map(p => ({ year: p.year, quarter: p.quarter }));
        }
        await api.put('/api/indexes/overrides/bulk', {
          team_id: teamId,
          commodity_id: cell.commodity_id,
          region: cell.region,
          value: val,
          periods: targetPeriods,
        });
        onSaved(null); // trigger full refetch for bulk
      }
      setMessage({ type: 'success', text: 'Saved.' });
      setTimeout(() => onClose(), 600);
    } catch (err) {
      setMessage({ type: 'error', text: formatApiError(err) });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await api.delete('/api/indexes/overrides/bulk', {
        params: {
          team_id: teamId,
          commodity_id: cell.commodity_id,
          region: cell.region,
          year: cell.year,
          quarter: cell.quarter,
        },
      });
      onSaved(null); // trigger full refetch
      setMessage({ type: 'success', text: 'Reset to default.' });
      setTimeout(() => onClose(), 600);
    } catch (err) {
      setMessage({ type: 'error', text: formatApiError(err) });
    } finally {
      setSaving(false);
    }
  };

  const handleScrape = async () => {
    if (!teamSource) return;
    setScraping(true);
    setMessage(null);
    try {
      const res = await api.post(`/api/indexes/sources/${teamSource.id}/scrape-now`);
      if (res.data.status === 'ok') {
        setMessage({ type: 'success', text: `Scraped: ${res.data.value}` });
        setNewValue(String(res.data.value));
      } else {
        setMessage({ type: 'error', text: `Scrape error: ${res.data.error}` });
      }
    } catch (err) {
      setMessage({ type: 'error', text: 'Scrape request failed.' });
    } finally {
      setScraping(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !saving) handleSave();
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`${cell.commodity_name} / ${cell.region} / ${periodLabel}`}>
      <div className="ca-modal-body">
        {/* Info row */}
        <div style={{ marginBottom: 16, fontSize: 12, color: 'var(--text-secondary)' }}>
          {cell.scraped_value !== null && cell.scraped_value !== undefined ? (
            <span>Global value: <strong style={{ color: 'var(--text)' }}>{cell.scraped_value.toLocaleString(undefined, { maximumFractionDigits: 3 })}</strong></span>
          ) : (
            <span style={{ color: 'var(--muted)' }}>No base data</span>
          )}
          {isOverridden && (
            <span className="ca-badge" style={{
              marginLeft: 10, background: 'var(--accent4-dim)',
              color: 'var(--accent4)', fontSize: 9,
            }}>
              OVERRIDE {cell.override_by && `by ${cell.override_by}`}
              {cell.override_at && ` on ${new Date(cell.override_at).toLocaleDateString()}`}
            </span>
          )}
        </div>

        {/* Value input */}
        <div style={{ marginBottom: 16 }}>
          <label className="ca-label">Value</label>
          <input
            ref={inputRef}
            className="ca-input"
            type="number"
            step="any"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>

        {/* Apply mode */}
        <div style={{ marginBottom: 16 }}>
          <label className="ca-label">Apply to</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input type="radio" name="applyMode" checked={applyMode === 'single'} onChange={() => setApplyMode('single')} />
              This period only
            </label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input type="radio" name="applyMode" checked={applyMode === 'all'} onChange={() => setApplyMode('all')} />
              All periods
            </label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input type="radio" name="applyMode" checked={applyMode === 'range'} onChange={() => setApplyMode('range')} />
              Custom range
            </label>
          </div>
          {applyMode === 'range' && periods && (
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <div style={{ flex: 1 }}>
                <label className="ca-label">From</label>
                <select className="ca-select" value={rangeStart} onChange={e => setRangeStart(+e.target.value)}>
                  {periods.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label className="ca-label">To</label>
                <select className="ca-select" value={rangeEnd} onChange={e => setRangeEnd(+e.target.value)}>
                  {periods.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
                </select>
              </div>
            </div>
          )}
        </div>

        {/* Feedback */}
        {message && (
          <div style={{
            padding: '8px 12px', borderRadius: 6, fontSize: 11, marginBottom: 14,
            background: message.type === 'success' ? 'var(--accent-dim)' : 'var(--accent2-dim)',
            color: message.type === 'success' ? 'var(--accent)' : 'var(--accent2)',
          }}>
            {message.text}
          </div>
        )}
      </div>

      <div className="ca-modal-footer">
        <button
          className="ca-btn ca-btn-danger"
          onClick={handleReset}
          disabled={!isOverridden || saving}
          style={{ marginRight: 'auto', opacity: isOverridden ? 1 : 0.4 }}
        >
          Reset to Default
        </button>
        {teamSource?.source_type === 'scrape_url' && (
          <button
            className="ca-btn ca-btn-ghost ca-btn-sm"
            onClick={handleScrape}
            disabled={scraping}
          >
            {scraping ? 'Scraping...' : 'Re-scrape Now'}
          </button>
        )}
        <button
          className="ca-btn ca-btn-primary ca-btn-sm"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </Modal>
  );
}
