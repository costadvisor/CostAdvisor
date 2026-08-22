import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { qLabel } from '../../utils/quarters';
import { MultiLineChart } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * Intelligence detail — modelled on sample_idea/intelligence_mockup.html.
 * ONE /api/costing/brief call feeds Tab 1 "Market & Pricing": should-cost
 * index history (rebased to base 100) + a STUB forecast band, an index-driver
 * decomposition, the stored (cached) AI narrative read-only, and derived
 * cycle-position / snapshot cards. Tab 2 "Product Intelligence" is a flagged
 * placeholder — its expert-authored reference content needs a backend
 * persistence + review layer that doesn't exist yet. No new engine.
 * ──────────────────────────────────────────────────────────────────── */

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');
const FORECAST_STEPS = 2;          // stub: quarters projected past the last real period
const FORECAST_BAND = 0.015;       // stub: ±1.5% illustrative range

function nextQuarters(year, quarter, n) {
  const out = [];
  let y = year, q = quarter;
  for (let i = 0; i < n; i++) { q += 1; if (q > 4) { q = 1; y += 1; } out.push({ year: y, quarter: q, label: qLabel(y, q) }); }
  return out;
}

const dirIcon = (d) => (d === 'up' ? '↑' : d === 'down' ? '↓' : '→');

// Tab 2 reference sections — rendered as flagged placeholders (no data model yet).
const PENDING_SECTIONS = [
  'Functionalities & applications', 'Synthesis & feedstock chain', 'Commercial variants',
  'Supply & demand', 'Key suppliers', 'Compliance & quality flags', 'Index sources', 'Negotiation pointers',
];

export default function IntelligenceDetailArea() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState('market');

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.post('/api/costing/brief', { cost_model_id: costModelId })
      .then(({ data }) => setData(data))
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [costModelId]);

  if (loading) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;
  if (error) return <div className="ca-page ca-fade-in"><button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/intelligence')}>← Intelligence</button><div className="ca-card" style={{ color: 'var(--accent2)', marginTop: 12 }}>Error: {error}</div></div>;
  if (!data) return null;

  const sym = curSym(data.currency);
  const unit = data.unit || '';
  const periods = data.evolution || [];
  const theo = periods.map(p => p.theoretical);
  const base = theo.find(v => v != null) || null;              // base-100 anchor = first real should-cost
  const N = periods.length;

  // Rebase should-cost + actual to an index (base 100) for the mockup's framing.
  const idxTheo = base ? theo.map(v => (v != null ? (v / base) * 100 : null)) : theo;
  const idxActual = base ? periods.map(p => (p.actual != null ? (p.actual / base) * 100 : null)) : periods.map(p => p.actual);

  // Stub forecast: flat continuation of the last real should-cost index + a ±band.
  const lastIdx = idxTheo.length ? idxTheo[idxTheo.length - 1] : null;
  const last = N ? periods[N - 1] : null;
  const fLabels = last ? nextQuarters(last.year, last.quarter, FORECAST_STEPS).map(o => o.label) : [];
  const pad = Array(FORECAST_STEPS).fill(null);
  const at = (i, v) => { const a = Array(N + FORECAST_STEPS).fill(null); a[i] = v; return a; };
  const fLine = last != null ? (() => { const a = at(N - 1, lastIdx); for (let i = 0; i < FORECAST_STEPS; i++) a[N + i] = lastIdx; return a; })() : [];
  const fUp = last != null ? (() => { const a = at(N - 1, lastIdx); for (let i = 0; i < FORECAST_STEPS; i++) a[N + i] = lastIdx * (1 + FORECAST_BAND); return a; })() : [];
  const fDn = last != null ? (() => { const a = at(N - 1, lastIdx); for (let i = 0; i < FORECAST_STEPS; i++) a[N + i] = lastIdx * (1 - FORECAST_BAND); return a; })() : [];

  const xLabels = [...periods.map(p => p.period), ...fLabels];
  const series = [
    { name: 'Should-cost index', color: 'var(--accent)', values: [...idxTheo, ...pad] },
    { name: 'Actual', color: 'var(--accent4)', values: [...idxActual, ...pad] },
    ...(last != null ? [
      { name: 'Forecast (stub)', color: 'var(--accent3)', values: fLine, dashed: true },
      { color: 'var(--muted)', values: fUp, dashed: true },
      { color: 'var(--muted)', values: fDn, dashed: true },
    ] : []),
  ];

  // Cycle position from the should-cost history (min / max / latest).
  const realTheo = theo.filter(v => v != null);
  const lo = realTheo.length ? Math.min(...realTheo) : null;
  const hi = realTheo.length ? Math.max(...realTheo) : null;
  const cur = realTheo.length ? realTheo[realTheo.length - 1] : null;
  const cyclePct = (lo != null && hi != null && hi > lo) ? ((cur - lo) / (hi - lo)) * 100 : null;
  const cycleVerdict = cyclePct == null ? 'Not enough history to place the cycle.'
    : cyclePct >= 70 ? 'Near the 24-month high — unfavourable for locking in long contracts.'
      : cyclePct <= 30 ? 'Near the 24-month low — favourable window to lock in.'
        : 'Mid-range — no strong cycle signal.';

  // Snapshot
  const latestIdx = idxTheo.length ? idxTheo[idxTheo.length - 1] : null;
  const changePct = latestIdx != null ? latestIdx - 100 : null;
  const mean = realTheo.length ? realTheo.reduce((s, v) => s + v, 0) / realTheo.length : null;
  const vol = (realTheo.length > 1 && mean) ? Math.sqrt(realTheo.reduce((s, v) => s + (v - mean) ** 2, 0) / realTheo.length) / mean * 100 : null;

  const drivers = data.drivers || [];

  return (
    <div className="ca-page ca-fade-in">
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/intelligence')}>← Intelligence</button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginTop: 10 }}>
        <div>
          <div className="ca-h1">{data.product_name}</div>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            {data.supplier_name || 'No supplier'}{data.destination_country ? ` · → ${data.destination_country}` : ''} · {data.period_label} · should-cost index base 100
          </p>
        </div>
        <button className="ca-btn ca-btn-primary" onClick={() => navigate(`/cost-models/${costModelId}`)}>Edit formula</button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button className={`ca-btn ca-btn-sm ${tab === 'market' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setTab('market')}>Market &amp; Pricing</button>
        <button className={`ca-btn ca-btn-sm ${tab === 'intel' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setTab('intel')}>Product Intelligence</button>
      </div>

      {tab === 'market' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 16, alignItems: 'start' }}>
          {/* Left column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
            {/* Index chart */}
            <div className="ca-card">
              <div className="ca-card-title">Should-cost index — history + forecast</div>
              {N >= 2 ? (
                <MultiLineChart series={series} xLabels={xLabels} height={240} refValue={base ? 100 : null} refLabel="base 100" splitIndex={last != null ? N : null} splitLabel="Forecast" />
              ) : (
                <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16 }}>Not enough history to chart. Record actuals / index data across quarters.</div>
              )}
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
                Formula-weighted should-cost, rebased to 100 at {periods[0]?.period || 'start'}. Dashed segment past “Forecast” is an <strong>illustrative stub</strong> (±1.5% range) — no forecast engine yet.
              </div>
            </div>

            {/* Index components */}
            <div className="ca-card">
              <div className="ca-card-title">Index components</div>
              {drivers.length ? (
                <table className="ca-table">
                  <thead><tr><th>Component</th><th>Reference index</th><th className="center">Weight</th><th className="center">Δ vs base</th><th className="center">Weighted impact</th></tr></thead>
                  <tbody>
                    {drivers.map((d, i) => {
                      const weight = data.current_should_cost ? (d.component_cost / data.current_should_cost) * 100 : null;
                      return (
                        <tr key={i}>
                          <td style={{ fontWeight: 500 }}>{d.component_label}</td>
                          <td style={{ color: 'var(--text-secondary)' }}>{d.index_name || <span style={{ color: 'var(--muted)' }}>fixed / none</span>}</td>
                          <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{weight != null ? `${weight.toFixed(0)}%` : '—'}</td>
                          <td className="center" style={{ color: d.index_change_pct > 0 ? 'var(--accent2)' : d.index_change_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                            {d.index_change_pct != null ? `${d.index_change_pct > 0 ? '+' : ''}${d.index_change_pct.toFixed(1)}%` : '—'}
                          </td>
                          <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{sym}{(d.contribution_to_gap ?? 0).toFixed(2)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No component drivers for this model.</div>}
            </div>

            {/* Market dynamics: stored narrative + forward signals */}
            <div className="ca-card">
              <div className="ca-card-title">Market dynamics</div>
              <div style={{ fontSize: 13, lineHeight: 1.9, color: 'var(--text-secondary)', whiteSpace: 'pre-line' }}>
                {data.narrative || <span style={{ color: 'var(--muted)', fontStyle: 'italic' }}>AI narrative unavailable.</span>}
              </div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8, fontStyle: 'italic' }}>Cached AI narrative — read-only, not expert-reviewed.</div>
              {drivers.length > 0 && (
                <>
                  <div className="ca-card-title" style={{ marginTop: 18 }}>Forward signals</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {drivers.slice(0, 5).map((d, i) => (
                      <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 12 }}>
                        <span style={{ color: d.direction === 'up' ? 'var(--accent2)' : d.direction === 'down' ? 'var(--accent)' : 'var(--muted)', fontWeight: 700 }}>{dirIcon(d.direction)}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>
                          <strong style={{ color: 'var(--text)' }}>{d.index_name || d.component_label}</strong> {d.index_change_pct != null ? `moved ${d.index_change_pct > 0 ? '+' : ''}${d.index_change_pct.toFixed(1)}% since base` : 'flat'}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Right column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="ca-card">
              <div className="ca-card-title">Market snapshot</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <Stat label="Index today" value={latestIdx != null ? latestIdx.toFixed(1) : '—'} />
                <Stat label="vs base" value={changePct != null ? `${changePct > 0 ? '+' : ''}${changePct.toFixed(1)}%` : '—'} color={changePct > 0 ? 'var(--accent2)' : changePct < 0 ? 'var(--accent)' : undefined} />
                <Stat label="Volatility" value={vol != null ? `${vol.toFixed(1)}%` : '—'} />
                <Stat label="Gap vs actual" value={data.gap_pct != null ? `${data.gap_pct > 0 ? '+' : ''}${data.gap_pct.toFixed(1)}%` : '—'} color={data.gap_pct > 0 ? 'var(--accent2)' : data.gap_pct < 0 ? 'var(--accent)' : undefined} />
              </div>
            </div>

            <div className="ca-card">
              <div className="ca-card-title">Cycle position</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginBottom: 6 }}>
                <span>range low</span>{cyclePct != null && <span style={{ fontWeight: 700, color: 'var(--text)' }}>{cyclePct.toFixed(0)}%</span>}<span>range high</span>
              </div>
              <div style={{ position: 'relative', height: 10, background: 'linear-gradient(90deg, var(--accent), var(--accent3), var(--accent2))', borderRadius: 5, marginBottom: 8 }}>
                {cyclePct != null && <div style={{ position: 'absolute', top: -3, left: `${Math.max(0, Math.min(100, cyclePct))}%`, width: 4, height: 16, background: 'var(--text)', borderRadius: 2, transform: 'translateX(-50%)' }} />}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '8px 10px', background: 'var(--surface2)', borderRadius: 'var(--radius)' }}>{cycleVerdict}</div>
            </div>
          </div>
        </div>
      ) : (
        /* Tab 2 — flagged dependency */
        <div className="ca-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <span className="ca-badge" style={{ background: 'var(--warn-bg)', color: 'var(--accent3)' }}>Persistence pending</span>
            <div className="ca-card-title" style={{ marginBottom: 0 }}>Product Intelligence</div>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, maxWidth: 720 }}>
            Product-level reference intelligence — functionalities, synthesis chain, suppliers, compliance flags, index sources and negotiation pointers — is <strong>expert-authored</strong> content. It needs a backend <strong>persistence + review</strong> layer that isn’t built yet: AI narratives today are cached in-memory (Redis) only, never stored or expert-reviewed. This tab is intentionally a placeholder until that dependency lands.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10, marginTop: 14 }}>
            {PENDING_SECTIONS.map(s => (
              <div key={s} style={{ border: '1px dashed var(--border)', borderRadius: 'var(--radius)', padding: '14px 16px', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 12 }}>{s}</span>
                <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: 'var(--muted)' }}>pending</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div className="ca-metric">
      <div className="ca-metric-val" style={{ color, fontSize: 18 }}>{value}</div>
      <div className="ca-metric-lbl">{label}</div>
    </div>
  );
}
