import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useToast } from './Toast';
import api from '../api';

/**
 * Add / edit an FX pair (currency pair + scrape source config). Extracted from
 * FxRates.jsx so the Index Library can manage pairs directly. Self-contained:
 * POSTs a new pair or PUTs an existing one, then calls onSaved().
 *
 * Props: pair (FxPairOut|null for create), onSaved, onClose
 */
export default function FxPairModal({ pair, onSaved, onClose }) {
  const { addToast } = useToast();
  const [form, setForm] = useState(pair || {
    from_currency: '', to_currency: '', name: '', source_type: 'frankfurter', scrape_url: '', scrape_enabled: true,
  });
  const [saving, setSaving] = useState(false);
  const isEdit = !!pair;

  const updateForm = field => e => setForm(f => ({ ...f, [field]: e.target.value }));

  const save = async () => {
    if (!form.from_currency || !form.to_currency || !form.name) {
      addToast('Fill in From, To, and Name', 'error'); return;
    }
    setSaving(true);
    try {
      if (isEdit) await api.put(`/api/fx-rates/pairs/${pair.id}`, form);
      else await api.post('/api/fx-rates/pairs', form);
      addToast(isEdit ? 'Pair updated' : 'Pair added', 'success');
      onSaved();
      onClose();
    } catch (err) {
      addToast(err?.response?.data?.detail || 'Failed to save pair', 'error');
    } finally { setSaving(false); }
  };

  return createPortal(
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 460 }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">{isEdit ? 'Edit FX Pair' : 'Add FX Pair'}</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <label className="ca-label">From Currency</label>
            <input className="ca-input" maxLength={3} placeholder="EUR" value={form.from_currency}
              onChange={e => setForm(f => ({ ...f, from_currency: e.target.value.toUpperCase() }))} />
          </div>
          <div>
            <label className="ca-label">To Currency</label>
            <input className="ca-input" maxLength={3} placeholder="USD" value={form.to_currency}
              onChange={e => setForm(f => ({ ...f, to_currency: e.target.value.toUpperCase() }))} />
          </div>
          <div>
            <label className="ca-label">Name</label>
            <input className="ca-input" placeholder="EUR/USD" value={form.name} onChange={updateForm('name')} />
          </div>
          <div>
            <label className="ca-label">Source Type</label>
            <select className="ca-select" value={form.source_type} onChange={updateForm('source_type')}>
              <option value="frankfurter">Frankfurter (JSON API, recommended)</option>
              <option value="ecb">ECB (SDMX quarterly)</option>
              <option value="generic">Generic URL</option>
              <option value="manual">Manual only</option>
            </select>
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label">Scrape URL</label>
            <input className="ca-input"
              placeholder={form.source_type === 'frankfurter'
                ? 'https://api.frankfurter.app/latest?from=CNY&to=EUR'
                : 'https://data-api.ecb.europa.eu/service/data/EXR/Q....'}
              value={form.scrape_url || ''} onChange={updateForm('scrape_url')} />
          </div>
          <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" id="fxpair-scrape-enabled" checked={form.scrape_enabled}
              onChange={e => setForm(f => ({ ...f, scrape_enabled: e.target.checked }))} />
            <label htmlFor="fxpair-scrape-enabled" style={{ fontSize: 13, cursor: 'pointer' }}>Scraping enabled</label>
          </div>
        </div>
        <div className="ca-modal-footer">
          <button className="ca-btn ca-btn-ghost" onClick={onClose}>Cancel</button>
          <button className="ca-btn ca-btn-primary" disabled={saving} onClick={save}>
            {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Add Pair'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
