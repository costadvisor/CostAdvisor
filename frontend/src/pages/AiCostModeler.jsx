import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';

// AI cost modeler (Scrum 32).
//
// The product decision this page encodes: an LLM-suggested breakdown is a
// DRAFT, never a cost model. Nothing prices anything until a human has
// corrected it and pressed promote, and the promoted formula carries
// `ai_draft` provenance so the estimate caveat survives the save.
//
// The two gates are the server's, shown here rather than discovered at save:
// a recipe must close at 100%, and every index line must bind to a tracked
// index. Both failures are silent otherwise — one prices wrong without looking
// wrong, the other saves a broken link that rides flat.

const CONFIDENCE = {
  high: { label: 'HIGH', color: 'var(--accent)', bg: 'var(--accent-dim)' },
  medium: { label: 'MEDIUM', color: 'var(--accent3)', bg: 'var(--accent3-dim)' },
  low: { label: 'LOW — CHECK THIS', color: 'var(--accent2)', bg: 'var(--accent2-dim)' },
};

const SECTORS = ['Surfactants', 'Polymers', 'Agrochemicals', 'Paper & pulp',
  'Water treatment', 'Lubricants', 'Coatings', 'Other'];

export default function AiCostModeler() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    product_name: '', sector: 'Surfactants', rough_price: '',
    currency: 'EUR', unit: 't', region: 'Europe', product_id: '',
  });
  const [products, setProducts] = useState([]);
  const [commodities, setCommodities] = useState([]);
  const [draft, setDraft] = useState(null);
  const [lines, setLines] = useState([]);
  const [recent, setRecent] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [promoteFor, setPromoteFor] = useState(null);

  const loadRecent = useCallback(() => {
    if (!activeTeamId) return;
    api.get('/api/ai-cost-modeler/drafts', { params: { team_id: activeTeamId, limit: 10 } })
      .then(({ data }) => setRecent(data))
      .catch(() => setRecent([]));
  }, [activeTeamId]);

  useEffect(() => {
    if (!activeTeamId) return;
    api.get('/api/products', { params: { team_id: activeTeamId } })
      .then(({ data }) => setProducts(data)).catch(() => setProducts([]));
    api.get('/api/indexes').then(({ data }) => setCommodities(data)).catch(() => setCommodities([]));
    loadRecent();
  }, [activeTeamId, loadRecent]);

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));

  const applyDraft = (d) => {
    setDraft(d);
    setLines(d.lines.map(l => ({ ...l })));
  };

  const suggest = async () => {
    if (!form.product_name.trim()) return;
    setBusy(true); setErr(null);
    try {
      const { data } = await api.post('/api/ai-cost-modeler/drafts', {
        product_name: form.product_name.trim(),
        sector: form.sector || null,
        rough_price: form.rough_price === '' ? null : Number(form.rough_price),
        currency: form.currency || null,
        unit: form.unit || null,
        region: form.region || null,
        product_id: form.product_id || null,
      }, { params: { team_id: activeTeamId } });
      applyDraft(data);
      loadRecent();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not get a suggestion.');
    } finally {
      setBusy(false);
    }
  };

  const updateLine = (i, patch) =>
    setLines(ls => ls.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const removeLine = (i) => setLines(ls => ls.filter((_, idx) => idx !== i));
  const addLine = () => setLines(ls => [...ls, {
    label: '', weight_pct: 0, component_type: 'fixed',
    commodity_id: null, index_resolved: false,
  }]);

  const saveLines = async () => {
    setBusy(true); setErr(null);
    try {
      const { data } = await api.put(`/api/ai-cost-modeler/drafts/${draft.id}/lines`, {
        lines: lines.map(l => ({
          label: l.label,
          weight_pct: Number(l.weight_pct) || 0,
          component_type: l.component_type,
          commodity_id: l.component_type === 'index' ? (l.commodity_id ?? null) : null,
          suggested_index: l.suggested_index ?? null,
          confidence: l.confidence ?? null,
          rationale: l.rationale ?? null,
        })),
      });
      applyDraft(data);
      addToast(data.blockers.length ? 'Saved — still some things to fix.' : 'Saved. Ready to promote.',
        data.blockers.length ? 'info' : 'success');
    } catch (e) {
      setErr(formatApiError(e) || 'Could not save those changes.');
    } finally {
      setBusy(false);
    }
  };

  const promote = async () => {
    setBusy(true); setErr(null);
    try {
      const { data } = await api.post(`/api/ai-cost-modeler/drafts/${draft.id}/promote`, {
        product_id: promoteFor.product_id || null,
        base_price: Number(promoteFor.base_price),
        base_year: Number(promoteFor.base_year),
        base_quarter: Number(promoteFor.base_quarter),
        region: form.region || null,
        currency: form.currency || null,
      });
      addToast('Saved as a cost model — tagged as an AI estimate.', 'success');
      navigate(`/cost-models/${data.cost_model_id}`);
    } catch (e) {
      setErr(formatApiError(e) || 'Could not promote this draft.');
    } finally {
      setBusy(false);
    }
  };

  const total = lines.reduce((s, l) => s + (Number(l.weight_pct) || 0), 0);
  const dirty = draft && JSON.stringify(lines.map(l => [l.label, Number(l.weight_pct), l.component_type, l.commodity_id]))
    !== JSON.stringify(draft.lines.map(l => [l.label, Number(l.weight_pct), l.component_type, l.commodity_id]));

  return (
    <div className="ca-page ca-fade-in">
      <h1 className="ca-h1">AI cost modeler</h1>
      <p className="ca-subtitle">
        A first-draft cost structure for a product nobody has decomposed — to be argued with, then saved.
      </p>

      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 14 }}>{err}</div>}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div className="ca-card" style={{ flex: '1 1 320px', minWidth: 300 }}>
          <div className="ca-card-title" style={{ marginBottom: 12 }}>What are you buying?</div>

          <label className="ca-label" htmlFor="ai-name">Product</label>
          <input id="ai-name" className="ca-input" value={form.product_name} onChange={set('product_name')}
                 placeholder="e.g. Sodium lauryl ether sulfate (SLES 70%)"
                 style={{ width: '100%', marginBottom: 10 }} />

          <label className="ca-label" htmlFor="ai-sector">Sector</label>
          <select id="ai-sector" className="ca-select" value={form.sector} onChange={set('sector')}
                  style={{ width: '100%', marginBottom: 10 }}>
            {SECTORS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
            <div style={{ flex: 2 }}>
              <label className="ca-label" htmlFor="ai-price">Rough price</label>
              <input id="ai-price" className="ca-input" type="number" value={form.rough_price}
                     onChange={set('rough_price')} style={{ width: '100%' }} />
            </div>
            <div style={{ flex: 1 }}>
              <label className="ca-label" htmlFor="ai-cur">Cur</label>
              <input id="ai-cur" className="ca-input" value={form.currency} onChange={set('currency')}
                     style={{ width: '100%' }} />
            </div>
            <div style={{ flex: 1 }}>
              <label className="ca-label" htmlFor="ai-unit">Unit</label>
              <input id="ai-unit" className="ca-input" value={form.unit} onChange={set('unit')}
                     style={{ width: '100%' }} />
            </div>
          </div>

          <label className="ca-label" htmlFor="ai-product">Existing product (optional)</label>
          <select id="ai-product" className="ca-select" value={form.product_id} onChange={set('product_id')}
                  style={{ width: '100%', marginBottom: 14 }}>
            <option value="">Not one of mine yet</option>
            {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>

          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={suggest}
                  disabled={busy || !form.product_name.trim()}>
            {busy ? 'Estimating…' : 'Suggest a cost structure'}
          </button>
          <p style={{ fontSize: 10, color: 'var(--muted)', marginTop: 10 }}>
            If the model is unreachable you will be told so rather than handed an empty draft — an estimate
            that found nothing and an estimate that never ran are different things.
          </p>
        </div>

        <div style={{ flex: '2 1 480px', minWidth: 320 }}>
          {!draft ? (
            <div className="ca-card" style={{ textAlign: 'center', padding: '44px 20px' }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>No estimate yet</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                Fill in what you know and ask for a draft. You will get a breakdown to correct, not an answer.
              </div>
            </div>
          ) : (
            <>
              <div className="ca-card" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
                  <span className="ca-badge" style={{ background: 'var(--accent2-dim)', color: 'var(--accent2)', fontWeight: 600 }}>
                    AI estimate — not measured
                  </span>
                  {draft.model && <span className="ca-tag">{draft.model}</span>}
                  {draft.status !== 'ai_draft' && (
                    <span className="ca-badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                      {draft.status}
                    </span>
                  )}
                </div>
                {draft.rationale && (
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{draft.rationale}</p>
                )}
              </div>

              <div className="ca-card" style={{ marginBottom: 16 }}>
                <div className="ca-card-title" style={{ marginBottom: 12 }}>
                  Suggested breakdown — correct it before saving
                </div>
                <div className="ca-scroll-x">
                  <table className="ca-table">
                    <caption className="ca-sr-only">
                      Suggested components with editable weights and index bindings.
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Component</th>
                        <th scope="col">Index</th>
                        <th scope="col" style={{ textAlign: 'right' }}>Weight</th>
                        <th scope="col">Confidence</th>
                        <th scope="col" />
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((l, i) => {
                        const conf = CONFIDENCE[l.confidence];
                        const unbound = l.component_type === 'index' && !l.commodity_id;
                        return (
                          <tr key={i}>
                            <td>
                              <input className="ca-input" value={l.label} aria-label={`Label for line ${i + 1}`}
                                     onChange={e => updateLine(i, { label: e.target.value })}
                                     style={{ width: '100%', minWidth: 120 }} />
                              {l.rationale && (
                                <div style={{ fontSize: 10, color: 'var(--muted)', maxWidth: 320, marginTop: 2 }}>
                                  {l.rationale}
                                </div>
                              )}
                            </td>
                            <td>
                              <select
                                className="ca-select"
                                aria-label={`Index for line ${i + 1}`}
                                value={l.component_type === 'index' ? (l.commodity_id || '') : 'fixed'}
                                onChange={e => {
                                  const v = e.target.value;
                                  updateLine(i, v === 'fixed'
                                    ? { component_type: 'fixed', commodity_id: null }
                                    : { component_type: 'index', commodity_id: Number(v) });
                                }}
                                style={{ width: '100%', minWidth: 160,
                                         borderColor: unbound ? 'var(--accent2)' : undefined }}
                              >
                                <option value="fixed">Fixed line (no index)</option>
                                <option value="" disabled>— pick an index —</option>
                                {commodities.map(ci => (
                                  <option key={ci.id} value={ci.id}>{ci.name}</option>
                                ))}
                              </select>
                              {unbound && (
                                <div style={{ fontSize: 10, color: 'var(--accent2)', marginTop: 2 }}>
                                  {l.suggested_index
                                    ? `Suggested "${l.suggested_index}" — not one we track`
                                    : 'Pick an index or make it a fixed line'}
                                </div>
                              )}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              <input className="ca-input" type="number" value={l.weight_pct}
                                     aria-label={`Weight for line ${i + 1}`}
                                     onChange={e => updateLine(i, { weight_pct: e.target.value })}
                                     style={{ width: 72, textAlign: 'right' }} />
                            </td>
                            <td>
                              {conf && (
                                <span className="ca-badge" style={{ background: conf.bg, color: conf.color, fontWeight: 600 }}>
                                  {conf.label}
                                </span>
                              )}
                            </td>
                            <td>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm"
                                      aria-label={`Remove line ${i + 1}`}
                                      onClick={() => removeLine(i)}>×</button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colSpan={2} style={{ fontWeight: 600 }}>Total</td>
                        <td style={{ textAlign: 'right', fontWeight: 700,
                                     color: Math.abs(total - 100) < 0.05 ? 'var(--text)' : 'var(--accent2)' }}>
                          {total.toFixed(1)}%
                        </td>
                        <td colSpan={2} style={{ fontSize: 10, color: 'var(--muted)' }}>
                          must close at 100%
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                  <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={addLine}>+ Component</button>
                  <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={saveLines}
                          disabled={busy || !dirty}>
                    Save changes
                  </button>
                </div>
              </div>

              <div className="ca-card">
                <div className="ca-card-title" style={{ marginBottom: 8 }}>Save as a real formula</div>
                {draft.blockers.length > 0 ? (
                  <ul style={{ margin: '0 0 12px 16px', fontSize: 11, color: 'var(--accent2)' }}>
                    {draft.blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                ) : (
                  <p style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12 }}>
                    The formula is saved tagged as an AI estimate, so every should-cost built on it keeps the
                    caveat until somebody signs it off. Margin is a line in this recipe, so no separate margin
                    is applied on top.
                  </p>
                )}
                {!promoteFor ? (
                  <button className="ca-btn ca-btn-primary ca-btn-sm"
                          disabled={draft.blockers.length > 0 || dirty || draft.status !== 'ai_draft'}
                          onClick={() => setPromoteFor({
                            product_id: form.product_id || draft.product_id || '',
                            base_price: form.rough_price || '',
                            base_year: new Date().getFullYear(),
                            base_quarter: Math.floor(new Date().getMonth() / 3) + 1,
                          })}>
                    Promote to a cost model
                  </button>
                ) : (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                    <div style={{ flex: '2 1 180px' }}>
                      <label className="ca-label" htmlFor="pr-product">Product</label>
                      <select id="pr-product" className="ca-select" value={promoteFor.product_id}
                              onChange={e => setPromoteFor(p => ({ ...p, product_id: e.target.value }))}
                              style={{ width: '100%' }}>
                        <option value="">Choose a product…</option>
                        {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>
                    </div>
                    <div style={{ flex: '1 1 110px' }}>
                      <label className="ca-label" htmlFor="pr-price">Base price</label>
                      <input id="pr-price" className="ca-input" type="number" value={promoteFor.base_price}
                             onChange={e => setPromoteFor(p => ({ ...p, base_price: e.target.value }))}
                             style={{ width: '100%' }} />
                    </div>
                    <div style={{ flex: '1 1 90px' }}>
                      <label className="ca-label" htmlFor="pr-year">Year</label>
                      <input id="pr-year" className="ca-input" type="number" value={promoteFor.base_year}
                             onChange={e => setPromoteFor(p => ({ ...p, base_year: e.target.value }))}
                             style={{ width: '100%' }} />
                    </div>
                    <div style={{ flex: '1 1 70px' }}>
                      <label className="ca-label" htmlFor="pr-q">Q</label>
                      <input id="pr-q" className="ca-input" type="number" min={1} max={4}
                             value={promoteFor.base_quarter}
                             onChange={e => setPromoteFor(p => ({ ...p, base_quarter: e.target.value }))}
                             style={{ width: '100%' }} />
                    </div>
                    <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={promote}
                            disabled={busy || !promoteFor.product_id || !promoteFor.base_price}>
                      {busy ? 'Saving…' : 'Create it'}
                    </button>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setPromoteFor(null)}>
                      Cancel
                    </button>
                  </div>
                )}
                {dirty && (
                  <div style={{ fontSize: 11, color: 'var(--accent3)', marginTop: 10 }}>
                    Save your changes first — the draft on the server is what gets promoted.
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {recent.length > 0 && (
        <div className="ca-card" style={{ marginTop: 16 }}>
          <div className="ca-card-title" style={{ marginBottom: 12 }}>Recent drafts</div>
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">Previously generated cost-structure drafts.</caption>
              <thead>
                <tr>
                  <th scope="col">Product</th>
                  <th scope="col">Created</th>
                  <th scope="col">Status</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Lines</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {recent.map(d => (
                  <tr key={d.id}>
                    <td>{d.product_name}</td>
                    <td style={{ color: 'var(--muted)' }}>{new Date(d.created_at).toLocaleDateString()}</td>
                    <td>
                      <span className="ca-badge" style={
                        d.status === 'approved'
                          ? { background: 'var(--accent-dim)', color: 'var(--accent)' }
                          : { background: 'var(--accent2-dim)', color: 'var(--accent2)' }
                      }>
                        {d.status === 'approved' ? 'promoted' : 'estimate'}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>{d.lines.length}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => applyDraft(d)}>
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
