import { useState, useEffect } from 'react';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { qLabel } from '../../utils/quarters';
import exportCsv from '../../utils/exportCsv';
import { MultiLineChart } from './wsCharts';

/* Forecast area.
 * REAL data, no fabricated forward numbers:
 *  - chart: a composite commodity index (headline feeds from /api/indexes/public-quarterly,
 *    each rebased to base 100 and averaged) as real history — no invented trajectory.
 *  - table + KPIs: present-day should-cost / gap from /api/portfolio/summary (the Monitor source).
 *  - assumption cards: each headline index's REAL latest QoQ %.
 * Scrum 70 shipped a real per-series projection engine (Index Library →
 * commodity detail) and a per-cost-model lock/hold verdict (buy-windows
 * verdict endpoint) — this page composites several headline indices into one
 * synthetic average, and those projections are per single (commodity,
 * region) series, so there's no 1:1 substitute here without a page redesign.
 * A flat illustrative band used to fill that gap; removed rather than kept
 * around as a placeholder that looks like a forecast but isn't one. */

export default function ForecastArea() {
  const { activeTeamId } = useAuth();
  const [models, setModels] = useState([]);
  const [indices, setIndices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!activeTeamId) return;
    setLoading(true); setError(null);
    Promise.all([
      api.get('/api/portfolio/summary', { params: { team_id: activeTeamId } }),
      api.get('/api/indexes/public-quarterly', { params: { limit: 12 } }),
    ])
      .then(([sum, idx]) => { setModels(sum.data.models || []); setIndices(idx.data || []); })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [activeTeamId]);

  // ── Composite commodity index (real history only — no forward band) ──
  const keyed = new Map();
  indices.forEach(ix => (ix.points || []).forEach(p => keyed.set(`${p.year}-${p.quarter}`, { year: p.year, quarter: p.quarter })));
  const timeline = [...keyed.values()].sort((a, b) => a.year - b.year || a.quarter - b.quarter).slice(-12);
  const rebased = indices.map(ix => {
    const first = (ix.points || []).find(p => p.value != null)?.value;
    const m = new Map();
    if (first) (ix.points || []).forEach(p => { if (p.value != null) m.set(`${p.year}-${p.quarter}`, (p.value / first) * 100); });
    return m;
  });
  const composite = timeline.map(t => {
    const vals = rebased.map(m => m.get(`${t.year}-${t.quarter}`)).filter(v => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const N = timeline.length;

  const xLabels = timeline.map(t => qLabel(t.year, t.quarter));
  const series = [
    { name: 'Commodity index (base 100)', color: 'var(--accent)', values: composite },
  ];

  // ── Real present-day KPIs from the portfolio ──
  const flagged = models.filter(m => m.flag_price_drift || m.flag_index_moved).length;
  const gaps = models.map(m => m.gap_pct).filter(v => v != null);
  const avgGap = gaps.length ? gaps.reduce((a, b) => a + Math.abs(b), 0) / gaps.length : null;
  const totalExposure = models.reduce((a, m) => a + Math.abs(m.cumulative_impact || 0), 0);
  const stats = [
    { lbl: 'Products tracked', val: String(models.length), color: 'var(--text)', bg: 'var(--neutral-bg)' },
    { lbl: 'Flagged (drift / index)', val: String(flagged), color: flagged ? 'var(--accent3)' : 'var(--text)', bg: flagged ? 'var(--warn-bg)' : 'var(--neutral-bg)' },
    { lbl: 'Avg |gap|', val: avgGap != null ? `${avgGap.toFixed(1)}%` : '—', color: 'var(--accent)', bg: 'var(--neutral-bg)' },
    { lbl: 'Total exposure', val: totalExposure ? Math.round(totalExposure).toLocaleString() : '—', color: 'var(--accent2)', bg: 'var(--neutral-bg)' },
  ];

  const doExport = () => exportCsv(
    'forecast-portfolio.csv',
    ['Reference', 'Product', 'Supplier', 'Region', 'Should-cost', 'Actual price', 'Gap %'],
    models.map(m => [
      m.product_reference || '', m.product_name, m.supplier_name || '', m.region,
      m.current_should_cost, m.latest_actual_price ?? '', m.gap_pct ?? '',
    ]),
  );

  if (loading) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;
  if (error) return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div></div>;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="ca-h1">Cost forecast</div>
          <p className="ca-subtitle">Real commodity-index history and current portfolio should-cost. For a real forward projection of a specific index, or a lock/hold verdict on a specific product, see the Index Library and the product's buy-window verdict.</p>
        </div>
        <button className="ca-btn ca-btn-ghost" onClick={doExport} disabled={!models.length}>↓ Export</button>
      </div>

      <div style={{ display: 'flex', gap: 16, margin: '16px 0', flexWrap: 'wrap' }}>
        {stats.map(s => (
          <div key={s.lbl} className="ca-card ca-metric" style={{ flex: '1 1 180px', background: s.bg }}>
            <div className="ca-metric-val" style={{ color: s.color }}>{s.val}</div>
            <div className="ca-metric-lbl">{s.lbl}</div>
          </div>
        ))}
      </div>

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div className="ca-card-title">Commodity index — history</div>
        {N >= 2
          ? <MultiLineChart series={series} xLabels={xLabels} refValue={100} refLabel="base 100" height={220} />
          : <div style={{ padding: 20, color: 'var(--muted)' }}>Not enough index history to chart yet.</div>}
      </div>

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div className="ca-card-title">Portfolio — current should-cost vs price</div>
        {models.length ? (
          <div className="ca-scroll-x">
            <table className="ca-table">
              <thead>
                <tr>
                  <th>Ref</th><th>Product</th><th>Supplier</th><th>Region</th>
                  <th className="right">Should-cost</th><th className="right">Actual</th><th className="right">Gap %</th>
                </tr>
              </thead>
              <tbody>
                {models.map(m => (
                  <tr key={m.cost_model_id}>
                    <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{m.product_reference}</td>
                    <td style={{ fontWeight: 600 }}>{m.product_name}</td>
                    <td>{m.supplier_name || '—'}</td>
                    <td>{m.region}</td>
                    <td className="right" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{m.current_should_cost?.toLocaleString()}</td>
                    <td className="right" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{m.latest_actual_price != null ? m.latest_actual_price.toLocaleString() : '—'}</td>
                    <td className="right" style={{ fontFamily: "'JetBrains Mono', monospace", color: m.gap_pct == null ? 'var(--muted)' : m.gap_pct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>{m.gap_pct != null ? `${m.gap_pct > 0 ? '+' : ''}${m.gap_pct}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ padding: 20, color: 'var(--muted)' }}>No cost models yet — build one to see forecast inputs.</div>}
      </div>

      {indices.length > 0 && <>
        <div className="ca-h2">Index movement <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 400 }}>(latest quarter-over-quarter, real)</span></div>
        <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
          {indices.map(a => {
            const up = (a.qoq_pct ?? 0) >= 0;
            return (
              <div key={a.commodity_name} className="ca-card" style={{ flex: '1 1 160px' }}>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{a.commodity_name}</div>
                <div style={{ fontSize: 22, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", color: a.qoq_pct == null ? 'var(--muted)' : up ? 'var(--accent3)' : 'var(--accent)', margin: '4px 0' }}>
                  {a.qoq_pct != null ? `${up ? '+' : ''}${a.qoq_pct.toFixed(1)}%` : '—'}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{a.latest != null ? `${a.latest.toLocaleString()} ${a.unit || ''}` : ''} · {a.region}</div>
              </div>
            );
          })}
        </div>
      </>}
    </div>
  );
}
