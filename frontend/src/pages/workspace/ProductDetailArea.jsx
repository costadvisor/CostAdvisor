import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAlert } from '../../components/ConfirmDialog';
import { QUARTER_OPTS, qLabel } from '../../utils/quarters';
import { PIE_COLORS } from '../../utils/constants';
import { StackedBar } from './wsCharts';
import { BuySignalBadge } from '../../components/BuyWindows';
import NotesPanel from '../../components/NotesPanel';

// Scrum 25 — negotiation flag states
const NEG_STATES = [
  { key: 'none', label: 'No flag', color: 'var(--muted)' },
  { key: 'in_negotiation', label: 'In negotiation', color: 'var(--accent3)' },
  { key: 'under_review', label: 'Under review', color: 'var(--accent4)' },
  { key: 'agreed', label: 'Agreed', color: 'var(--accent)' },
];

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
  const [buySignal, setBuySignal] = useState(null);       // buy-window signal
  const [flagSaving, setFlagSaving] = useState(false);    // negotiation flag save
  const [showBreakdown, setShowBreakdown] = useState(false);  // Scrum 17 — inspectable numbers
  const [breakdown, setBreakdown] = useState(null);
  const [breakdownErr, setBreakdownErr] = useState(null);

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

  // Buy-window signal (Scrum 22) — best-effort; absent for models without history.
  const loadBuy = () => {
    api.get(`/api/portfolio/buy-windows/${costModelId}`)
      .then(({ data }) => setBuySignal(data))
      .catch(() => setBuySignal(null));
  };

  useEffect(() => { loadCm(); loadSc(); loadBuy(); /* eslint-disable-next-line */ }, [costModelId]);

  // Scrum 17 — should-cost breakdown, fetched lazily on first expand.
  const toggleBreakdown = () => {
    const next = !showBreakdown;
    setShowBreakdown(next);
    if (next && !breakdown && !breakdownErr) {
      api.post('/api/costing/should-cost/breakdown', { cost_model_id: costModelId, target_year: CUR_Y, target_quarter: CUR_Q })
        .then(({ data }) => setBreakdown(data))
        .catch(err => setBreakdownErr(formatApiError(err)));
    }
  };

  // Scrum 25 — set the negotiation flag (needs costing.edit; backend enforces).
  const setNegotiation = async (state) => {
    setFlagSaving(true);
    try {
      await api.put(`/api/cost-models/${costModelId}/flag`, { negotiation_state: state });
      setCm(prev => ({ ...prev, negotiation_state: state }));
    } catch (e) {
      showAlert({ title: 'Could not update flag', message: formatApiError(e) });
    } finally {
      setFlagSaving(false);
    }
  };

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

      {/* Negotiation flag (Scrum 25) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>Status</span>
        {NEG_STATES.map(s => {
          const active = (cm.negotiation_state || 'none') === s.key;
          return (
            <button key={s.key} disabled={flagSaving} onClick={() => setNegotiation(s.key)}
              className="ca-btn ca-btn-sm"
              style={{
                borderColor: active ? s.color : 'var(--border)',
                color: active ? s.color : 'var(--text-secondary)',
                background: active ? 'var(--surface2)' : 'transparent',
                fontWeight: active ? 700 : 400,
              }}>
              {s.label}
            </button>
          );
        })}
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
              {buySignal && buySignal.signal !== 'insufficient' && (
                <div style={{ marginTop: 8 }} title="Current should-cost vs the trailing 4-quarter average">
                  <BuySignalBadge signal={buySignal.signal} deviationPct={buySignal.deviation_pct} />
                </div>
              )}
              <hr className="ca-sep" />
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                <Metric label="Indexed cost" value={`${sym}${fmtMoney(sc.cost_before_margin)}`} color="var(--accent3)" />
                <Metric label="Margin" value={`${sym}${fmtMoney(sc.margin_amount)}`} color="var(--accent2)" />
                <Metric label={`Per active ${unit}`} value={sc.per_active_unit != null ? `${sym}${fmtMoney(sc.per_active_unit)}` : '—'} color="var(--accent4)" />
              </div>
              <button className="ca-btn-link" style={{ fontSize: 11, marginTop: 10 }} onClick={toggleBreakdown}>
                {showBreakdown ? '▾ Hide breakdown' : '▸ Show breakdown'}
              </button>
              {showBreakdown && (
                <ShouldCostBreakdownTable breakdown={breakdown} error={breakdownErr} sym={sym} />
              )}
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

      {/* Team notes (Scrum 25) */}
      <div style={{ marginTop: 16 }}>
        <NotesPanel costModelId={costModelId} />
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

const SOURCE_LABEL = {
  composite: 'composite', fixed: 'fixed', team_override: 'override',
  scraped_region: 'scraped', scraped_global: 'scraped (global)',
  scraped_any_region: 'scraped (other region)', scraped_temporal_carry_forward: 'scraped (carried)',
};

// Scrum 17 — itemized should-cost: index name, weight, base/current value, ratio,
// contribution, and source, with a footer that sums to exactly the should-cost shown
// above (mirrors FormulaDetailModal's resolved-recipe table pattern).
function ShouldCostBreakdownTable({ breakdown, error, sym }) {
  if (error) return <div className="ca-card" style={{ marginTop: 10, color: 'var(--accent2)', fontSize: 12 }}>Error: {error}</div>;
  if (!breakdown) return <div style={{ marginTop: 10, color: 'var(--muted)', fontSize: 12 }}>Loading…</div>;

  const mono = { fontFamily: "'JetBrains Mono', monospace" };
  const fmt = (v) => (v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 3 }));

  return (
    <div style={{ marginTop: 10, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
      <div className="ca-scroll-x">
        <table className="ca-table" style={{ margin: 0 }}>
          <thead>
            <tr>
              <th>Component</th>
              <th style={{ textAlign: 'right' }}>Weight</th>
              <th style={{ textAlign: 'right' }}>Base value</th>
              <th style={{ textAlign: 'right' }}>Current value</th>
              <th style={{ textAlign: 'right' }}>× Ratio</th>
              <th style={{ textAlign: 'right' }}>Contribution</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {breakdown.components.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 16, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
                Advanced (expression) formula — no discrete weighted components to break down.
              </td></tr>
            )}
            {breakdown.components.map((c, i) => (
              <tr key={i}>
                <td style={{ fontSize: 12 }}>{c.label}{c.commodity_name ? <span style={{ color: 'var(--muted)' }}> · {c.commodity_name}</span> : null}</td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>{c.weight_pct.toFixed(1)}%</td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right', color: 'var(--muted)' }}>{fmt(c.base_value)}</td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right', color: 'var(--muted)' }}>{fmt(c.current_value)}</td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>
                  {c.has_data ? (
                    <span style={{ color: c.ratio > 1 ? 'var(--accent2)' : c.ratio < 1 ? 'var(--accent)' : 'var(--text-secondary)' }}>
                      {c.ratio.toFixed(3)}
                    </span>
                  ) : (
                    <span title="No index data — line rides flat" style={{ color: 'var(--accent3)' }}>flat</span>
                  )}
                </td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right', fontWeight: 600 }}>{fmt(c.contribution)}</td>
                <td style={{ fontSize: 10, color: 'var(--muted)' }}>{c.source ? (SOURCE_LABEL[c.source] || c.source) : '—'}</td>
              </tr>
            ))}
            <tr>
              <td colSpan={5} style={{ fontSize: 11, fontWeight: 600, textAlign: 'right', color: 'var(--text-secondary)' }}>Indexed cost (Σ components)</td>
              <td style={{ ...mono, fontSize: 12, fontWeight: 700, textAlign: 'right' }}>{sym}{fmt(breakdown.cost_before_margin)}</td>
              <td />
            </tr>
            <tr>
              <td colSpan={5} style={{ fontSize: 11, textAlign: 'right', color: 'var(--text-secondary)' }}>+ Margin ({breakdown.margin_type})</td>
              <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>{sym}{fmt(breakdown.margin_amount)}</td>
              <td />
            </tr>
            {breakdown.incoterm_adjustment != null && (
              <tr>
                <td colSpan={5} style={{ fontSize: 11, textAlign: 'right', color: 'var(--text-secondary)' }}>
                  + Incoterm adjustment{breakdown.normalized_to_incoterm ? ` (→ ${breakdown.normalized_to_incoterm})` : ''}
                </td>
                <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>{sym}{fmt(breakdown.incoterm_adjustment)}</td>
                <td />
              </tr>
            )}
            <tr>
              <td colSpan={5} style={{ fontSize: 12, fontWeight: 700, textAlign: 'right' }}>= Should-cost</td>
              <td style={{ ...mono, fontSize: 13, fontWeight: 700, textAlign: 'right', color: 'var(--accent)' }}>{sym}{fmt(breakdown.should_cost)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      {breakdown.data_gaps.length > 0 && (
        <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--accent3)', borderTop: '1px solid var(--border)' }}>
          {breakdown.data_gaps.length} component{breakdown.data_gaps.length > 1 ? 's' : ''} missing index data and rode flat: {breakdown.data_gaps.map(g => g.component_label).join(', ')}.
        </div>
      )}
    </div>
  );
}
