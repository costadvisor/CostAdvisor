import { useState, useEffect } from 'react';
import api, { formatApiError } from '../api';
import VariableMapEditor, { normalizeVarMap } from './VariableMapEditor';
import RegionSelect from './RegionSelect';

/* Derived-index management (super-admin), relocated out of the Admin page into the
 * Index Library. Two kinds of "derived" index:
 *   - Composite / calculated: value computed live from other indexes via a formula.
 *   - Proxy: paywalled index approximated from a free base (metadata for the FD-1 estimator).
 * Vocab mirrors backend app/constants/index_metadata.py (source of truth). */
const PROXY_OPERATIONS = ['passthrough', 'ratio', 'multiply', 'add', 'spread', 'regression'];
const RECALIBRATION_OPTS = ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Annual'];
const RETRIEVAL_STATUSES = ['free', 'good_proxy', 'weak_proxy', 'blocked'];

export default function DerivedIndexesModal({ onClose }) {
  const [indexes, setIndexes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editProxy, setEditProxy] = useState(undefined);
  const [editComposite, setEditComposite] = useState(undefined);

  const load = () => {
    setLoading(true); setError(null);
    api.get('/api/indexes/')
      .then(({ data }) => setIndexes(data || []))
      .catch(e => setError(formatApiError(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const composites = indexes.filter(i => i.composite_expression).sort((a, b) => a.name.localeCompare(b.name));
  const proxies = indexes
    .filter(i => ['good_proxy', 'weak_proxy', 'blocked'].includes(i.retrieval_status) || i.proxy_logic)
    .sort((a, b) => a.name.localeCompare(b.name));

  const opSummary = (pl) => {
    if (!pl || !pl.operation) return '—';
    const s = pl.spread != null ? ` ${pl.spread > 0 ? '+' : ''}${pl.spread}${pl.spread_unit === 'pct' ? '%' : ''}` : '';
    return `${pl.base_index || '?'} · ${pl.operation}${s}`;
  };
  const compSummary = (i) => {
    const n = Object.keys(i.composite_variables || {}).length;
    return `${i.composite_expression}  ·  ${n} var${n === 1 ? '' : 's'}`;
  };

  return (
    <>
      <div className="ca-modal-backdrop" onClick={onClose}>
        <div className="ca-modal" style={{ width: 760, maxWidth: '95vw' }} onClick={e => e.stopPropagation()}>
          <div className="ca-modal-header">
            <div className="ca-modal-title">Derived indexes</div>
            <button className="ca-modal-close" onClick={onClose}>×</button>
          </div>
          <div className="ca-modal-body" style={{ maxHeight: '72vh', overflow: 'auto' }}>
            {loading ? <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
              : error ? <div style={{ color: 'var(--accent2)' }}>{error}</div>
              : (
              <>
                {/* Composite / calculated */}
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
                  <div className="ca-card-title" style={{ margin: 0 }}>Composite (calculated) indexes</div>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
                    Computed live from other indexes. Create via “+ Add Index → Composite”.
                  </span>
                </div>
                <div className="ca-card" style={{ marginBottom: 18 }}>
                  <div className="ca-scroll-x">
                    <table className="ca-table" style={{ width: '100%' }}>
                      <thead><tr><th>Index</th><th>Formula</th><th className="center">Actions</th></tr></thead>
                      <tbody>
                        {composites.length === 0 && (
                          <tr><td colSpan={3} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No composite indexes yet.</td></tr>
                        )}
                        {composites.map(i => (
                          <tr key={i.id}>
                            <td style={{ fontWeight: 600 }}>{i.name}</td>
                            <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: 'var(--text-secondary)' }}>{compSummary(i)}</td>
                            <td className="center"><button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditComposite(i)}>Edit</button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Proxy */}
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
                  <div className="ca-card-title" style={{ margin: 0 }}>Proxy / approximated indexes</div>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
                    How each paywalled index is derived from free data (FD-1 estimator input).
                  </span>
                </div>
                <div className="ca-card">
                  <div className="ca-scroll-x">
                    <table className="ca-table" style={{ width: '100%' }}>
                      <thead><tr><th>Index</th><th>Status</th><th>Derivation</th><th className="center">Actions</th></tr></thead>
                      <tbody>
                        {proxies.length === 0 && (
                          <tr><td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No proxy indexes.</td></tr>
                        )}
                        {proxies.map(i => (
                          <tr key={i.id}>
                            <td style={{ fontWeight: 600 }}>{i.name}</td>
                            <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: i.retrieval_status === 'blocked' ? 'var(--accent2)' : 'var(--muted)' }}>{i.retrieval_status || '—'}</td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{opSummary(i.proxy_logic)}</td>
                            <td className="center"><button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditProxy(i)}>Edit</button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {editComposite !== undefined && (
        <CompositeFormModal index={editComposite} commodities={indexes}
          onClose={() => setEditComposite(undefined)} onSaved={() => { setEditComposite(undefined); load(); }} />
      )}
      {editProxy !== undefined && (
        <ProxyLogicFormModal index={editProxy}
          onClose={() => setEditProxy(undefined)} onSaved={() => { setEditProxy(undefined); load(); }} />
      )}
    </>
  );
}

function CompositeFormModal({ index, commodities, onClose, onSaved }) {
  const [expression, setExpression] = useState(index.composite_expression || '');
  const [vars, setVars] = useState(index.composite_variables || {});
  const [compositeRegion, setCompositeRegion] = useState(index.composite_region || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const save = async () => {
    setSaving(true); setError(null);
    try {
      await api.put(`/api/indexes/${index.id}/composite`, {
        composite_expression: expression.trim() || null,
        composite_variables: normalizeVarMap(vars),
        composite_region: compositeRegion || null,
      });
      onSaved();
    } catch (e) {
      setError(formatApiError(e));
      setSaving(false);
    }
  };

  const pickable = commodities.filter(c => c.id !== index.id);  // can't reference itself

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 520, maxWidth: '95vw' }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">Composite formula — {index.name}</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">
          <VariableMapEditor expression={expression} setExpression={setExpression}
            vars={vars} setVars={setVars} commodities={pickable} />
          <div style={{ marginTop: 12 }}>
            <label className="ca-label">Region</label>
            <RegionSelect
              value={compositeRegion} onChange={setCompositeRegion}
              includeEmpty emptyLabel="Any region (computed on request)"
            />
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              {compositeRegion
                ? `Labels this index as ${compositeRegion} and reports it under that region. It does not change where the formula reads from — pin a region on a variable to control that, otherwise it uses the global series.`
                : 'Any region — reported under GLOBAL, and unpinned variables follow whatever region is requested.'}
            </div>
          </div>
          <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>
            Clear the formula to turn this back into a normal index.
          </p>
          {error && <div style={{ padding: '8px 12px', borderRadius: 6, fontSize: 11, background: 'var(--accent2-dim)', color: 'var(--accent2)' }}>{error}</div>}
        </div>
        <div className="ca-modal-footer">
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose}>Cancel</button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save formula'}</button>
        </div>
      </div>
    </div>
  );
}

function ProxyLogicFormModal({ index, onClose, onSaved }) {
  const pl = index.proxy_logic || {};
  const [baseIndex, setBaseIndex] = useState(pl.base_index || '');
  const [operation, setOperation] = useState(pl.operation || '');
  const [spread, setSpread] = useState(pl.spread ?? '');
  const [spreadUnit, setSpreadUnit] = useState(pl.spread_unit || 'pct');
  const [recalibration, setRecalibration] = useState(pl.recalibration || '');
  const [note, setNote] = useState(pl.note || '');
  const [retrievalStatus, setRetrievalStatus] = useState(index.retrieval_status || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const save = async () => {
    setSaving(true); setError(null);
    const proxy_logic = {
      base_index: baseIndex.trim() || null,
      operation: operation || null,
      spread: spread === '' ? null : Number(spread),
      spread_unit: spread === '' ? null : spreadUnit,
      recalibration: recalibration || null,
      note: note.trim() || null,
    };
    try {
      await api.put(`/api/indexes/${index.id}/proxy-logic`, { proxy_logic, retrieval_status: retrievalStatus || null });
      onSaved();
    } catch (e) {
      setError(formatApiError(e));
      setSaving(false);
    }
  };

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 460 }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">Proxy logic — {index.name}</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">
          <div style={{ marginBottom: 12 }}>
            <label className="ca-label">Base index <span style={{ fontWeight: 400, color: 'var(--muted)' }}>(the free feed to derive from)</span></label>
            <input className="ca-input" value={baseIndex} onChange={e => setBaseIndex(e.target.value)} placeholder="e.g. Brent Crude" />
          </div>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="ca-label">Operation</label>
              <select className="ca-select" value={operation} onChange={e => setOperation(e.target.value)}>
                <option value="">—</option>
                {PROXY_OPERATIONS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div style={{ width: 110 }}>
              <label className="ca-label">Spread</label>
              <input className="ca-input" type="number" value={spread} onChange={e => setSpread(e.target.value)} placeholder="0" />
            </div>
            <div style={{ width: 90 }}>
              <label className="ca-label">Unit</label>
              <select className="ca-select" value={spreadUnit} onChange={e => setSpreadUnit(e.target.value)}>
                <option value="pct">%</option>
                <option value="abs">abs</option>
              </select>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label className="ca-label">Recalibration</label>
              <select className="ca-select" value={recalibration} onChange={e => setRecalibration(e.target.value)}>
                <option value="">—</option>
                {RECALIBRATION_OPTS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label className="ca-label">Retrieval status</label>
              <select className="ca-select" value={retrievalStatus} onChange={e => setRetrievalStatus(e.target.value)}>
                <option value="">—</option>
                {RETRIEVAL_STATUSES.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          </div>
          <div style={{ marginBottom: 12 }}>
            <label className="ca-label">Note <span style={{ fontWeight: 400, color: 'var(--muted)' }}>(analyst reasoning)</span></label>
            <textarea className="ca-input" rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="How this estimate is worked out" />
          </div>
          {error && <div style={{ padding: '8px 12px', borderRadius: 6, fontSize: 11, background: 'var(--accent2-dim)', color: 'var(--accent2)' }}>{error}</div>}
        </div>
        <div className="ca-modal-footer">
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose}>Cancel</button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save proxy logic'}</button>
        </div>
      </div>
    </div>
  );
}
