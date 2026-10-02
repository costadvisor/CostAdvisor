import { useState, useEffect, useMemo } from 'react';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { useToast } from '../../components/Toast';
import { qLabel } from '../../utils/quarters';
import exportCsv from '../../utils/exportCsv';
import { MultiLineChart } from './wsCharts';

/* Forecast area — real history AND a real forward projection.
 *
 * This page used to chart a synthetic composite: several headline indices each
 * rebased to 100 and averaged. That composite could not be forecast, because
 * the projection engine works per (commodity, region) series, and so the page
 * carried no forward line at all — honest, but half a page.
 *
 * Fixed by dropping the composite rather than bolting a forecast onto it: each
 * headline commodity is now charted as itself, with its own stored projection
 * vintage, its own residual-based interval, and its own status. Averaging them
 * was the thing standing in the way.
 *
 * Nothing forward is invented. A series whose projection came back `hold` says
 * so; one that has never been projected says that instead of drawing a flat
 * line. `/api/indexes/{id}/projections/latest` returns null in that case and
 * null is rendered, not smoothed over. */

const STATUS = {
  fitted: { label: 'Fitted trend', color: 'var(--accent)', bg: 'var(--accent-dim)',
    help: 'A least-squares trend over real history, with an interval from its own residuals.' },
  hold: { label: 'Flat hold', color: 'var(--accent3)', bg: 'var(--accent3-dim)',
    help: 'Recent history is flat, or there are too few points to trust a line. No trend is claimed.' },
  no_history: { label: 'No history', color: 'var(--muted)', bg: 'var(--neutral-bg)',
    help: 'Nothing observed to anchor a projection to.' },
  none: { label: 'Not projected', color: 'var(--muted)', bg: 'var(--neutral-bg)',
    help: 'This series has never been projected. A super admin can run projections.' },
  superseded: { label: 'Superseded', color: 'var(--muted)', bg: 'var(--neutral-bg)',
    help: 'Every quarter this run projected has since been observed. Re-run projections for a forward view.' },
};

const num = (v, unit) => (v == null ? '—'
  : `${v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 2 : 0 })}${unit ? ` ${unit}` : ''}`);

const daysSince = (iso) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400000) : null);

const qOrd = (y, q) => y * 4 + q;

/* A stored vintage is a snapshot: it was fitted against the history that
 * existed when it ran, and quarters observed since then can sit inside its
 * horizon. Two of the five headline series are in exactly that state right now
 * — fitted to data ending 2026 Q2 while 2026 Q3 has since landed.
 *
 * So a projected point at or before the newest observation is dropped rather
 * than charted: it is not a forecast any more, the observed value supersedes
 * it, and plotting both would put two different numbers on one quarter. The
 * `stale` flag says so out loud instead of quietly shortening the horizon. */
function forwardOf(ix, run) {
  if (!run || run.status !== 'fitted') return { points: [], stale: false };
  const hist = ix.points || [];
  if (!hist.length) return { points: [], stale: false };
  const lastObserved = qOrd(hist[hist.length - 1].year, hist[hist.length - 1].quarter);
  const points = (run.points || []).filter(p => qOrd(p.year, p.quarter) > lastObserved);
  const fittedTo = run.history_to_year != null
    ? qOrd(run.history_to_year, run.history_to_quarter) : null;
  return { points, stale: fittedTo != null && fittedTo < lastObserved };
}

export default function ForecastArea() {
  const { activeTeamId, user } = useAuth();
  const { addToast } = useToast();
  const [models, setModels] = useState([]);
  const [indices, setIndices] = useState([]);
  const [projections, setProjections] = useState({}); // commodity_name -> run | null
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [projecting, setProjecting] = useState(false);
  const [error, setError] = useState(null);

  const load = () => {
    if (!activeTeamId) return;
    setLoading(true); setError(null);
    Promise.all([
      api.get('/api/portfolio/summary', { params: { team_id: activeTeamId } }),
      api.get('/api/indexes/public-quarterly', { params: { limit: 16 } }),
      // public-quarterly is a no-auth landing-page endpoint and deliberately
      // carries no commodity_id, so the id has to come from the authenticated
      // list. That id is what the projection endpoint is keyed on.
      api.get('/api/indexes'),
    ])
      .then(async ([sum, pub, all]) => {
        setModels(sum.data.models || []);
        const series = pub.data || [];
        setIndices(series);
        setSelected(s => s || series[0]?.commodity_name || null);

        const byName = new Map((all.data || []).map(c => [c.name, c.id]));
        const results = await Promise.all(series.map(async ix => {
          const id = byName.get(ix.commodity_name);
          if (!id) return [ix.commodity_name, null];
          try {
            const { data } = await api.get(`/api/indexes/${id}/projections/latest`,
              { params: { region: ix.region } });
            return [ix.commodity_name, data || null];
          } catch {
            // One series failing to load a projection must not blank the page.
            return [ix.commodity_name, null];
          }
        }));
        setProjections(Object.fromEntries(results));
      })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [activeTeamId]); // eslint-disable-line react-hooks/exhaustive-deps

  const runProjections = async () => {
    setProjecting(true);
    try {
      const { data } = await api.post('/api/indexes/project-all');
      // Shape is {series_projected, fitted, hold, no_history} — the split
      // matters, because "projected 143 series" reads as 143 forecasts when
      // some of them are a refusal to claim a trend.
      addToast(
        `${data.series_projected} series projected — ${data.fitted} fitted, `
        + `${data.hold} held flat, ${data.no_history} with no history.`,
        'success',
      );
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not run projections.', 'error');
    } finally {
      setProjecting(false);
    }
  };

  const current = indices.find(ix => ix.commodity_name === selected) || null;
  const run = current ? projections[current.commodity_name] : null;

  // History + projection on one axis, in the series' own units. Rebasing would
  // make the interval meaningless, so a single commodity is charted at a time.
  const chart = useMemo(() => {
    if (!current) return null;
    const hist = (current.points || []);
    if (hist.length < 2) return null;
    const { points: fwd } = forwardOf(current, run);
    const xLabels = [...hist.map(p => qLabel(p.year, p.quarter)), ...fwd.map(p => qLabel(p.year, p.quarter))];
    const nH = hist.length;

    const history = [...hist.map(p => p.value), ...fwd.map(() => null)];
    // Projection starts at the last observed point so the two lines join
    // instead of floating apart with a visual gap at the boundary.
    const forecast = [...hist.map((p, i) => (i === nH - 1 ? p.value : null)), ...fwd.map(p => p.value)];
    const lo = [...hist.map((p, i) => (i === nH - 1 ? p.value : null)), ...fwd.map(p => p.ci_lo)];
    const hi = [...hist.map((p, i) => (i === nH - 1 ? p.value : null)), ...fwd.map(p => p.ci_hi)];
    const hasBand = fwd.some(p => p.ci_lo != null && p.ci_hi != null);

    return {
      xLabels, splitIndex: fwd.length ? nH - 1 : null,
      band: hasBand ? { lo, hi, color: 'var(--chart-fill)' } : null,
      series: [
        { name: `${current.commodity_name} — observed`, color: 'var(--accent)', values: history },
        ...(fwd.length ? [{ name: 'Projected', color: 'var(--accent4)', values: forecast, dashed: true }] : []),
      ],
    };
  }, [current, run]);

  const flagged = models.filter(m => m.flag_price_drift || m.flag_index_moved).length;
  const gaps = models.map(m => m.gap_pct).filter(v => v != null);
  const avgGap = gaps.length ? gaps.reduce((a, b) => a + Math.abs(b), 0) / gaps.length : null;
  const totalExposure = models.reduce((a, m) => a + Math.abs(m.cumulative_impact || 0), 0);
  const stats = [
    { lbl: 'Products tracked', val: String(models.length), color: 'var(--text)' },
    { lbl: 'Flagged (drift / index)', val: String(flagged), color: flagged ? 'var(--accent3)' : 'var(--text)' },
    { lbl: 'Avg |gap|', val: avgGap != null ? `${avgGap.toFixed(1)}%` : '—', color: 'var(--accent)' },
    { lbl: 'Total exposure', val: totalExposure ? Math.round(totalExposure).toLocaleString() : '—', color: 'var(--accent2)' },
  ];

  const doExport = () => exportCsv(
    'forecast-portfolio.csv',
    ['Reference', 'Product', 'Supplier', 'Region', 'Should-cost', 'Actual price', 'Gap %'],
    models.map(m => [
      m.product_reference || '', m.product_name, m.supplier_name || '', m.region,
      m.current_should_cost, m.latest_actual_price ?? '', m.gap_pct ?? '',
    ]),
  );

  const exportForecasts = () => exportCsv(
    'index-forecasts.csv',
    ['Commodity', 'Region', 'Unit', 'Status', 'Method', 'Vintage', 'Latest', 'Period', 'Projected', 'Projected period', 'CI low', 'CI high', 'Change %'],
    indices.map(ix => {
      const r = projections[ix.commodity_name];
      const fwd = forwardOf(ix, r).points;
      const last = fwd.length ? fwd[fwd.length - 1] : null;
      const hp = (ix.points || []).slice(-1)[0];
      return [
        ix.commodity_name, ix.region, ix.unit || '', r ? r.status : 'none', r?.method || '',
        r?.vintage_at || '', ix.latest, hp ? qLabel(hp.year, hp.quarter) : '',
        last?.value ?? '', last ? qLabel(last.year, last.quarter) : '',
        last?.ci_lo ?? '', last?.ci_hi ?? '',
        last && ix.latest ? (((last.value - ix.latest) / ix.latest) * 100).toFixed(1) : '',
      ];
    }),
  );

  if (loading) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;
  if (error) return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div></div>;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div className="ca-h1">Cost forecast</div>
          <p className="ca-subtitle">
            Where the indices under your portfolio are heading, from stored projection vintages — and where your
            should-cost stands today.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {user?.is_super_admin && (
            <button className="ca-btn ca-btn-ghost" onClick={runProjections} disabled={projecting}>
              {projecting ? 'Projecting…' : '⟳ Project forecasts'}
            </button>
          )}
          <button className="ca-btn ca-btn-ghost" onClick={doExport} disabled={!models.length}>↓ Export</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, margin: '16px 0', flexWrap: 'wrap' }}>
        {stats.map(s => (
          <div key={s.lbl} className="ca-card ca-metric" style={{ flex: '1 1 180px' }}>
            <div className="ca-metric-val" style={{ color: s.color }}>{s.val}</div>
            <div className="ca-metric-lbl">{s.lbl}</div>
          </div>
        ))}
      </div>

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div className="ca-card-title" style={{ marginBottom: 0 }}>Index outlook</div>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
                  onClick={exportForecasts} disabled={!indices.length}>
            ↓ Forecasts CSV
          </button>
        </div>

        {indices.length === 0 ? (
          <div style={{ padding: 20, color: 'var(--muted)' }}>No headline index data available yet.</div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
              {indices.map(ix => {
                const r = projections[ix.commodity_name];
                const { points: fwd, stale } = forwardOf(ix, r);
                // A fitted run whose whole horizon has since been observed has
                // nothing forward left to say, so it reads as superseded rather
                // than as a forecast.
                const st = (r && r.status === 'fitted' && fwd.length === 0)
                  ? STATUS.superseded
                  : (STATUS[r ? r.status : 'none'] || STATUS.none);
                const last = fwd.length ? fwd[fwd.length - 1] : null;
                const delta = last && ix.latest ? ((last.value - ix.latest) / ix.latest) * 100 : null;
                const on = selected === ix.commodity_name;
                return (
                  <button
                    key={ix.commodity_name}
                    onClick={() => setSelected(ix.commodity_name)}
                    aria-pressed={on}
                    className="ca-card"
                    style={{
                      flex: '1 1 190px', minWidth: 180, textAlign: 'left', cursor: 'pointer',
                      padding: 14, background: 'var(--bg)',
                      borderColor: on ? 'var(--accent)' : 'var(--border)',
                      color: 'inherit', font: 'inherit',
                    }}
                  >
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 2 }}>
                      {ix.commodity_name}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>{ix.region}</div>
                    <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
                      {num(ix.latest, ix.unit)}
                    </div>
                    <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span className="ca-badge" style={{ background: st.bg, color: st.color }} title={st.help}>
                        {st.label}
                      </span>
                      {delta != null && (
                        <span style={{
                          fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 600,
                          color: delta > 0 ? 'var(--accent2)' : delta < 0 ? 'var(--accent)' : 'var(--muted)',
                        }}>
                          {delta > 0 ? '+' : ''}{delta.toFixed(1)}%
                        </span>
                      )}
                    </div>
                    {last && (
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
                        by {qLabel(last.year, last.quarter)}
                        {last.ci_lo != null && last.ci_hi != null
                          && ` · ${num(last.ci_lo)}–${num(last.ci_hi)}`}
                      </div>
                    )}
                    {stale && (
                      <div style={{ fontSize: 10, color: 'var(--accent3)', marginTop: 4 }}>
                        fitted before the latest observation
                      </div>
                    )}
                  </button>
                );
              })}
            </div>

            {chart ? (
              <>
                <MultiLineChart
                  series={chart.series}
                  xLabels={chart.xLabels}
                  band={chart.band}
                  splitIndex={chart.splitIndex}
                  splitLabel="projected →"
                  height={240}
                />
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 10 }}>
                  {chart.splitIndex != null ? (
                    <>
                      {run.method} over {run.history_points_used} quarters
                      {run.history_from_year != null && ` (${qLabel(run.history_from_year, run.history_from_quarter)}–${qLabel(run.history_to_year, run.history_to_quarter)})`}
                      {run.residual_std != null && `, residual σ ${run.residual_std.toFixed(2)}`}
                      . Vintage {new Date(run.vintage_at).toLocaleDateString()}
                      {daysSince(run.vintage_at) > 30 && (
                        <span style={{ color: 'var(--accent3)' }}> — {daysSince(run.vintage_at)} days old, worth re-running</span>
                      )}
                      . The shaded band is the prediction interval, not a scenario range.
                      {forwardOf(current, run).stale && (
                        <span style={{ color: 'var(--accent3)' }}>
                          {' '}It was fitted to data ending {qLabel(run.history_to_year, run.history_to_quarter)},
                          and quarters observed since then are shown as observed, not as forecast.
                        </span>
                      )}
                    </>
                  ) : (
                    <>
                      {(run && run.status === 'fitted'
                        ? STATUS.superseded
                        : (STATUS[run ? run.status : 'none'] || STATUS.none)).help}
                      {' '}Observed history is charted; no forward line is drawn.
                    </>
                  )}
                </div>
              </>
            ) : (
              <div style={{ padding: 20, color: 'var(--muted)' }}>
                Not enough history on this series to chart.
              </div>
            )}
          </>
        )}
      </div>

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div className="ca-card-title">Portfolio — current should-cost vs price</div>
        {models.length ? (
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">Current should-cost against the latest actual price, per product.</caption>
              <thead>
                <tr>
                  <th scope="col">Ref</th><th scope="col">Product</th><th scope="col">Supplier</th><th scope="col">Region</th>
                  <th scope="col" className="right">Should-cost</th><th scope="col" className="right">Actual</th><th scope="col" className="right">Gap %</th>
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

      <p style={{ fontSize: 11, color: 'var(--muted)' }}>
        A projection is an estimate with a stated method and vintage, not a guarantee. For a lock-or-hold call on
        one product rather than one index, see that product&apos;s buy-window verdict.
      </p>
    </div>
  );
}
