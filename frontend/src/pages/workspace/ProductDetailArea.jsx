import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAlert } from '../../components/ConfirmDialog';
import { QUARTER_OPTS, qLabel } from '../../utils/quarters';
import { PIE_COLORS } from '../../utils/constants';
import { StackedBar } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * Product detail — a product opens to its formula, its starting point, and
 * its live should-cost (index-evolved to the current quarter). The starting
 * point is first-class here: edit base price / base quarter inline, saved via
 * the existing renegotiate endpoint. No new engine — reads should-cost outputs.
 * ──────────────────────────────────────────────────────────────────── */

const NOW = new Date();
const CUR_Y = NOW.getFullYear();
const CUR_Q = Math.ceil((NOW.getMonth() + 1) / 3);

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');
const fmtMoney = (v) => (v == null ? '—' : v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3));

export default function ProductDetailArea() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const showAlert = useAlert();

  const [cm, setCm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sc, setSc] = useState({ status: 'loading' });   // live should-cost

  // Starting-point editor
  const [editing, setEditing] = useState(false);
  const [spPrice, setSpPrice] = useState('');
  const [spPeriod, setSpPeriod] = useState('');          // "year-quarter"
  const [saving, setSaving] = useState(false);

  const loadSc = () => {
    setSc({ status: 'loading' });
    api.post('/api/costing/should-cost', { cost_model_id: costModelId, target_year: CUR_Y, target_quarter: CUR_Q })
      .then(({ data }) => setSc({ status: 'ok', ...data }))
      .catch(err => setSc({ status: 'err', msg: formatApiError(err) }));
  };

  const loadCm = () => {
    setLoading(true);
    setError(null);
    api.get(`/api/cost-models/${costModelId}`)
      .then(({ data }) => setCm(data))
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadCm(); loadSc(); /* eslint-disable-next-line */ }, [costModelId]);

  const fv = cm?.formula_versions?.[0] || null;

  const startEdit = () => {
    if (!fv) return;
    setSpPrice(String(fv.base_price));
    setSpPeriod(`${fv.base_year}-${fv.base_quarter}`);
    setEditing(true);
  };

  const saveStartingPoint = async () => {
    const price = Number(spPrice);
    if (!price || price <= 0) { showAlert({ title: 'Invalid price', message: 'Base price must be a positive number.' }); return; }
    const [yy, qq] = spPeriod.split('-').map(Number);
    // Reconstruct the FULL formula version, changing only the starting point.
    // renegotiate upserts on (base_year, base_quarter): same quarter updates in
    // place; a new quarter records a new formula version (which becomes current).
    const payload = {
      formula_type: fv.formula_type || 'simple',
      base_price: price,
      base_year: yy,
      base_quarter: qq,
      margin_type: fv.margin_type || 'pct',
      margin_value: fv.margin_value,
      incoterm: fv.incoterm || null,
      named_place: fv.named_place || null,
      landed_cost_adjustments: fv.landed_cost_adjustments || null,
      notes: fv.notes || null,
      ...(fv.formula_type === 'advanced'
        ? { expression: fv.expression, variables: fv.variables }
        : { components: (fv.components || []).map(c => ({ label: c.label, commodity_name: c.commodity_name || null, weight: c.weight })) }),
    };
    setSaving(true);
    try {
      await api.post(`/api/cost-models/${costModelId}/renegotiate`, payload);
      setEditing(false);
      loadCm();
      loadSc();
    } catch (err) {
      showAlert({ title: 'Could not save', message: formatApiError(err) });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;
  if (error) return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div></div>;
  if (!cm) return null;
  if (!fv) {
    return (
      <div className="ca-page ca-fade-in">
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/portfolio')}>← Portfolio</button>
        <div className="ca-card" style={{ textAlign: 'center', padding: 48, marginTop: 12 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>This cost model has no formula yet.</div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate(`/cost-models/${costModelId}`)}>Build the formula</button>
        </div>
      </div>
    );
  }

  const sym = curSym(cm.currency);
  const unit = cm.product_unit || (sc.status === 'ok' ? sc.unit : '');
  const basePrice = Number(fv.base_price);
  const liveVal = sc.status === 'ok' ? sc.should_cost : null;
  const deltaPct = liveVal != null && basePrice ? ((liveVal - basePrice) / basePrice) * 100 : null;

  // Weight breakdown for the simple-mode formula (component weights + margin remainder).
  const compWeightSum = (fv.components || []).reduce((s, c) => s + (Number(c.weight) || 0), 0);
  const marginRemainder = Math.max(0, 1 - compWeightSum);
  const segments = [
    ...(fv.components || []).map((c, i) => ({ label: c.label, pct: (Number(c.weight) || 0) * 100, color: PIE_COLORS[i % PIE_COLORS.length] })),
    ...(marginRemainder > 0.001 ? [{ label: 'Margin / other', pct: marginRemainder * 100, color: 'var(--muted)' }] : []),
  ];

  return (
    <div className="ca-page ca-fade-in">
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/portfolio')}>← Portfolio</button>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginTop: 10 }}>
        <div>
          <div className="ca-h1">{cm.product_name}</div>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            {cm.product_reference ? <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{cm.product_reference} · </span> : null}
            {cm.supplier_name || 'No supplier'} · {cm.region}{cm.destination_country ? ` → ${cm.destination_country}` : ''}
            {cm.product_unit ? ` · per ${cm.product_unit}` : ''}
            {cm.product_active_content != null ? ` · ${(cm.product_active_content * 100).toFixed(0)}% active` : ''}
          </p>
        </div>
        <button className="ca-btn ca-btn-primary" onClick={() => navigate(`/cost-models/${costModelId}`)}>Edit formula</button>
      </div>

      {/* Live should-cost + starting point */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, margin: '16px 0' }}>
        <div className="ca-result">
          <div className="ca-result-label">Live should-cost · as of Q{CUR_Q} {CUR_Y}</div>
          {sc.status === 'loading' ? (
            <div className="ca-result-big" style={{ color: 'var(--muted)' }}>…</div>
          ) : sc.status === 'err' ? (
            <div style={{ color: 'var(--accent2)', fontSize: 13, marginTop: 8 }}>{sc.msg || 'Could not compute should-cost.'}</div>
          ) : (
            <>
              <div className="ca-result-big">{sym}{fmtMoney(liveVal)}<span style={{ fontSize: 14, color: 'var(--muted)', fontWeight: 400 }}> /{unit}</span></div>
              {deltaPct != null && (
                <div style={{ marginTop: 4, fontSize: 12, color: deltaPct > 0 ? 'var(--accent2)' : deltaPct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                  {deltaPct > 0 ? '+' : ''}{deltaPct.toFixed(1)}% since starting point
                </div>
              )}
              <hr className="ca-sep" />
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                <Metric label="Indexed cost" value={`${sym}${fmtMoney(sc.cost_before_margin)}`} color="var(--accent3)" />
                <Metric label="Margin" value={`${sym}${fmtMoney(sc.margin_amount)}`} color="var(--accent2)" />
                <Metric label={`Per active ${unit}`} value={sc.per_active_unit != null ? `${sym}${fmtMoney(sc.per_active_unit)}` : '—'} color="var(--accent4)" />
              </div>
            </>
          )}
        </div>

        <div className="ca-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div className="ca-card-title" style={{ marginBottom: 0 }}>Starting point</div>
            {!editing && <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={startEdit}>Edit</button>}
          </div>
          {editing ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label className="ca-label">Base price ({cm.currency}{unit ? ` / ${unit}` : ''})</label>
                  <input className="ca-input" type="number" step="any" min={0} value={spPrice} onChange={e => setSpPrice(e.target.value)} />
                </div>
                <div>
                  <label className="ca-label">Base quarter</label>
                  <select className="ca-select" value={spPeriod} onChange={e => setSpPeriod(e.target.value)}>
                    {QUARTER_OPTS.map(o => <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>)}
                  </select>
                </div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', margin: '10px 0' }}>
                Changing the quarter records a new starting point (a new formula version); keeping the same quarter updates it in place.
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={saveStartingPoint} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
              </div>
            </>
          ) : (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 26, fontWeight: 700 }}>{sym}{fmtMoney(basePrice)}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>Base price{unit ? ` / ${unit}` : ''}</div>
              </div>
              <div>
                <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 26, fontWeight: 700 }}>{qLabel(fv.base_year, fv.base_quarter)}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase' }}>Base quarter</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Formula */}
      <div className="ca-card">
        <div className="ca-card-title">Formula · {fv.formula_type === 'advanced' ? 'Advanced expression' : 'Simple parts + weights'}</div>
        {fv.formula_type === 'advanced' ? (
          <>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13, background: 'var(--surface2)', padding: 12, borderRadius: 'var(--radius)', overflowX: 'auto' }}>
              {fv.expression || <span style={{ color: 'var(--muted)' }}>No expression.</span>}
            </div>
            {fv.variables && Object.keys(fv.variables).length > 0 && (
              <table className="ca-table" style={{ marginTop: 12 }}>
                <thead><tr><th>Variable</th><th>Type</th><th>Index / value</th></tr></thead>
                <tbody>
                  {Object.entries(fv.variables).map(([name, def]) => (
                    <tr key={name}>
                      <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>{name}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{def?.type || 'fixed'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{def?.type === 'index' ? (def.commodity_name || `index #${def.commodity_id ?? '—'}`) : (def?.value ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : (
          <>
            {segments.length > 0 && <div style={{ marginBottom: 12 }}><StackedBar segments={segments} height={16} /></div>}
            <table className="ca-table">
              <thead><tr><th>Component</th><th>Reference index</th><th className="center">Weight</th></tr></thead>
              <tbody>
                {(fv.components || []).map((c, i) => (
                  <tr key={c.id ?? i}>
                    <td style={{ fontWeight: 500 }}>
                      <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length], marginRight: 8 }} />
                      {c.label}
                    </td>
                    <td style={{ color: 'var(--text-secondary)' }}>{c.commodity_name || <span style={{ color: 'var(--muted)' }}>fixed / none</span>}</td>
                    <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{((Number(c.weight) || 0) * 100).toFixed(1)}%</td>
                  </tr>
                ))}
                {(fv.components || []).length === 0 && (
                  <tr><td colSpan={3} style={{ color: 'var(--muted)', padding: 16 }}>No components.</td></tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
        <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>Evolution</button>
        <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/brief`)}>Brief</button>
        <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/squeeze`)}>Squeeze</button>
        <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/negotiate/${costModelId}`)}>Negotiate</button>
      </div>
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 18, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>{label}</div>
    </div>
  );
}
