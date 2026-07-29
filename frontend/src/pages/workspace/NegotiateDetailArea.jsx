import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import EvoChart from '../../components/EvoChart';
import api, { formatApiError } from '../../api';

/* ──────────────────────────────────────────────────────────────────────
 * Negotiate detail — the moment the journey pays off: product → gap →
 * exportable brief, in the new IA. Wired to the SAME endpoint and export
 * mechanism as the existing Brief page (POST /api/costing/brief, then
 * window.print() with a title-swap trick) — no new backend engine, no new
 * negotiation smarts (cheat-sheet / tornado / phased playbook were Wave-3
 * depth, not this scrum). Print styling (.ca-print-page / .ca-no-print /
 * .ca-print-only) is the same global CSS Brief.jsx already relies on.
 * ──────────────────────────────────────────────────────────────────── */

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');

/* Money on this page is the number a buyer reads out in a negotiation, so it must
 * not be ambiguous. `toFixed(3)` rendered a $3/kg should-cost as "$3.000", which in
 * any locale using `.` as a thousands separator reads as three thousand dollars.
 * Fixed locale + decimals from magnitude: 3 → "3.00", 1234.56 → "1,235".
 * (Same rule as PortfolioArea — worth extracting to a shared util next time one of
 * these pages is touched.) */
const MONEY_LOCALE = 'en-US';
const decimalsFor = (magnitude) => {
  const m = Math.abs(magnitude ?? 0);
  if (m >= 100) return 0;
  if (m >= 1) return 2;
  return 4;
};
/* `decimals` lets a caller pin every figure in one comparison to the same scale.
 * Deriving decimals per value gave "$1.94" next to "$0.3600" inside a single
 * chart, which reads as two different kinds of number. */
const fmtMoney = (v, { signed = false, decimals } = {}) => {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const dp = decimals != null ? decimals : decimalsFor(n);
  const body = Math.abs(n).toLocaleString(MONEY_LOCALE, { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const sign = n < 0 ? '−' : signed && n > 0 ? '+' : '';
  return `${sign}${body}`;
};
// Product name is nullable on the model; without a fallback the verdict card
// rendered a blank line and the subtitle read " · Supplier" with a dangling dot.
const PRODUCT_FALLBACK = 'Unnamed product';

export default function NegotiateDetailArea() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!costModelId) return;
    setLoading(true);
    setError(null);
    api.post('/api/costing/brief', { cost_model_id: costModelId })
      .then(({ data }) => setData(data))
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [costModelId]);

  if (loading) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;
  if (error) return (
    <div className="ca-page ca-fade-in">
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/negotiate')}>← Negotiate</button>
      <div className="ca-card" style={{ color: 'var(--accent2)', marginTop: 12 }}>Error: {error}</div>
    </div>
  );
  if (!data) return null;

  const {
    product_name, supplier_name, destination_country, currency, unit,
    current_should_cost, current_actual_price, gap, gap_pct,
    total_impact, volumes_missing, period_label, evolution, narrative, drivers,
  } = data;
  const sym = curSym(currency);

  // calculate_brief returns an empty evolution only when the cost model has
  // no formula version yet — there is nothing to negotiate from until then.
  if (!evolution || evolution.length === 0) {
    return (
      <div className="ca-page ca-fade-in">
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/negotiate')}>← Negotiate</button>
        <div className="ca-card" style={{ textAlign: 'center', padding: 48, marginTop: 12 }}>
          <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 16, marginBottom: 6 }}>{product_name}</div>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No formula yet — build a should-cost before preparing a negotiation brief.</div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate(`/cost-models/${costModelId}`)}>Build the formula</button>
        </div>
      </div>
    );
  }

  const displayName = product_name || PRODUCT_FALLBACK;
  const routeLabel = [supplier_name || 'No supplier', destination_country].filter(Boolean).join(' → ');

  /* Readiness. A brief needs a supplier price to be a brief at all — without one
   * the gap, the total impact and every "contribution to gap" are undefined, and
   * the page used to render all of them as zeros anyway: five driver rows of
   * $0.000, a flat one-line chart, and a narrative describing 0.0% moves as
   * reductions. The should-cost build-up IS still meaningful, so we keep that and
   * say plainly what's missing instead of faking the rest. */
  const hasActual = current_actual_price !== null && current_actual_price !== undefined;
  const indicesFlat = drivers.length > 0 && drivers.every(d => !d.index_change_pct);
  // One decimal scale for every per-unit figure on the page, taken from the
  // should-cost, so components and the total read as the same kind of number.
  const dp = decimalsFor(current_should_cost);

  const verdictColor = !hasActual ? 'var(--muted)' : gap > 0 ? 'var(--accent2)' : 'var(--accent)';
  const verdictLabel = !hasActual ? 'Not comparable yet' : gap > 0 ? 'Above should-cost' : 'Below should-cost';

  const handleExportPDF = () => {
    const slug = (s) => (s || '').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
    const prev = document.title;
    document.title = `brief-${slug(product_name)}-${slug(supplier_name)}-${period_label || ''}`;
    window.print();
    setTimeout(() => { document.title = prev; }, 500);
  };

  const theoretical = evolution.map(p => p.theoretical);
  const actual = evolution.map(p => p.actual);
  const periodLabels = evolution.map(p => p.period);

  return (
    <div className="ca-page ca-fade-in ca-print-page">
      <nav className="ca-no-print" style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
        <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => navigate('/negotiate')}>Negotiate</button>
        <span aria-hidden>›</span>
        <span>{displayName}</span>
      </nav>

      <div className="ca-no-print" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="ca-h1">Negotiation Brief</div>
          {/* Built from parts rather than interpolated, so a missing product name
              can't leave a dangling " · " at the front of the line. */}
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>{[displayName, routeLabel].filter(Boolean).join(' · ')}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}`)}>View Model</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>Evolution</button>
          <button className="ca-btn ca-btn-primary" onClick={handleExportPDF}>Export PDF</button>
        </div>
      </div>

      {/* Print-only masthead */}
      <div className="ca-print-only" style={{ marginBottom: 24, borderBottom: '2px solid #111', paddingBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase', color: '#111' }}>CostAdvisor</div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 20, fontWeight: 800, color: '#111', lineHeight: 1.2, marginTop: 2 }}>Negotiation Brief</div>
          </div>
          <div style={{ textAlign: 'right', fontSize: 11, color: '#444', lineHeight: 1.8 }}>
            <div><strong>{product_name}</strong></div>
            {supplier_name && <div>Supplier: {supplier_name}</div>}
            {destination_country && <div>Destination: {destination_country}</div>}
            <div>Period: {period_label}</div>
            <div>Generated: {new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</div>
          </div>
        </div>
      </div>

      {/* Readiness callout. Leads with what's missing rather than letting the
          reader work it out from a page of zeros. */}
      {!hasActual && (
        <div className="ca-card ca-no-print" style={{ marginBottom: 16, borderColor: 'var(--accent3)', background: 'var(--warn-bg)' }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 320px', minWidth: 0 }}>
              <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 14, marginBottom: 4 }}>
                No supplier price recorded for {period_label}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                The should-cost below is complete, but without a supplier price there is no gap to
                negotiate against — so the assessment, the total impact and each driver's
                contribution can't be calculated yet.
                {indicesFlat && ' The linked indices have also not moved over this period.'}
              </div>
            </div>
            <button className="ca-btn ca-btn-primary" style={{ flexShrink: 0 }}
              onClick={() => navigate(`/cost-models/${costModelId}/pricing`)}>
              Add supplier prices
            </button>
          </div>
        </div>
      )}

      {/* Verdict card. The 3px coloured left stripe is gone — the assessment text
          already carries the colour, and a side-stripe accent is a house-style ban. */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Product</div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 15 }}>{displayName}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>{routeLabel}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Should-Cost</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 18, fontWeight: 700, color: 'var(--accent)' }}>
              {sym}{fmtMoney(current_should_cost, { decimals: dp })}
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>per {unit} at {period_label}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Actual Price</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 18, fontWeight: 700, color: hasActual ? 'var(--accent4)' : 'var(--muted)' }}>
              {hasActual ? `${sym}${fmtMoney(current_actual_price, { decimals: dp })}` : 'Not recorded'}
            </div>
            {!hasActual && <div style={{ fontSize: 11, color: 'var(--muted)' }}>from your supplier invoices</div>}
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Assessment</div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 18, fontWeight: 700, color: verdictColor }}>
              {verdictLabel}
            </div>
            {hasActual && (
              <div style={{ fontSize: 11, color: verdictColor, fontFamily: "'JetBrains Mono', monospace" }}>
                {sym}{fmtMoney(gap, { signed: true, decimals: dp })} ({gap_pct > 0 ? '+' : ''}{gap_pct.toFixed(1)}%)
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Total impact — only meaningful once there IS a gap to multiply by volume. */}
      {hasActual && (
        <div className="ca-metric" style={{ marginBottom: 16 }}>
          <div className="ca-metric-lbl">Total Financial Impact (Gap × Volume)</div>
          {volumes_missing ? (
            <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>
              Upload volumes in the{' '}
              <button className="ca-btn-link" onClick={() => navigate(`/cost-models/${costModelId}/pricing`)}>
                Pricing tab
              </button>{' '}
              to calculate your total financial exposure.
            </div>
          ) : (
            <div className="ca-metric-val" style={{ fontFamily: "'JetBrains Mono', monospace", color: total_impact > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
              {sym}{fmtMoney(total_impact, { signed: true })}
            </div>
          )}
        </div>
      )}

      {/* Chart. No `.ca-scroll-x` — its 440px cap made a porthole over a chart that
          already fits, and as a scroll container it clipped the SVG when printing. */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title" style={{ marginBottom: 8 }}>
          Price Evolution
          <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: 'var(--muted)', fontSize: 11 }}>
            {' · '}{periodLabels[0]} → {periodLabels[periodLabels.length - 1]}
          </span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <EvoChart
            periods={periodLabels} theoretical={theoretical} actual={actual}
            refCost={current_should_cost}
            /* One flat should-cost line doesn't need the full canvas. */
            height={hasActual ? 230 : 150}
          />
        </div>
        <div style={{ display: 'flex', gap: 20, marginTop: 14, fontSize: 11, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: hasActual ? 'var(--text-secondary)' : 'var(--muted)' }}>
            <div style={{ width: 20, height: 2, background: hasActual ? 'var(--accent4)' : 'var(--muted)' }} />
            Actual Price{!hasActual && ' — none recorded'}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)' }}>
            <div style={{ width: 20, height: 0, borderTop: '2px dashed var(--accent)' }} /> Should-Cost
          </div>
        </div>
      </div>

      {/* Decomposition Waterfall */}
      {drivers.length > 0 && (
        <div className="ca-card" style={{ marginBottom: 16 }}>
          <div className="ca-card-title">Cost Decomposition</div>
          <div style={{ padding: '16px 0' }}>
            {(() => {
              /* Bar heights are PIXELS, not percentages.
               *
               * Every bar here used to render at exactly 2px — its `minHeight` —
               * whichever value it represented. The columns are flex items in a
               * row with `align-items: flex-end`, so each shrank to its content
               * height (31px); a percentage height on the bar then resolved
               * against an indefinite parent and collapsed. The chart has never
               * shown a difference between $1.94 and $0.16. */
              const PLOT_H = 96;
              // Scale against the total, not the largest component, so bar heights
              // are read as shares of the should-cost the total bar represents.
              const scaleMax = Math.max(Math.abs(current_should_cost) || 0, ...drivers.map(d => Math.abs(d.component_cost)), 0.001);
              const barPx = (v) => Math.max(2, Math.round((Math.abs(v) / scaleMax) * PLOT_H));
              const column = (key, { value, color, opacity, label, bold, divider }) => (
                <div key={key} style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end',
                  flex: 1, minWidth: 44, height: '100%',
                  ...(divider ? { borderLeft: '1px solid var(--border)', paddingLeft: 6, marginLeft: 2 } : {}),
                }}>
                  <div style={{ fontSize: 9, color: bold ? color : 'var(--muted)', fontWeight: bold ? 700 : 400, marginBottom: 3, whiteSpace: 'nowrap', fontFamily: "'JetBrains Mono', monospace" }}>
                    {sym}{fmtMoney(value, { decimals: dp })}
                  </div>
                  <div
                    style={{ width: '58%', height: barPx(value), background: color, borderRadius: '3px 3px 0 0', opacity }}
                    title={`${label} — ${sym}${fmtMoney(value, { decimals: dp })}`}
                  />
                  <div style={{ fontSize: 8, fontWeight: bold ? 700 : 400, color: 'var(--text-secondary)', marginTop: 5, textAlign: 'center', lineHeight: 1.25, minHeight: 20 }}>
                    {label}
                  </div>
                </div>
              );
              return (
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: PLOT_H + 46 }}>
                  {drivers.map((d, i) => column(i, {
                    value: d.component_cost, color: 'var(--accent4)', opacity: 0.85, label: d.component_label,
                  }))}
                  {column('total', {
                    value: current_should_cost, color: 'var(--accent)', opacity: 0.75,
                    label: 'SHOULD-COST', bold: true, divider: true,
                  })}
                </div>
              );
            })()}
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>
            Each bar shows a component's contribution to the current should-cost estimate.
          </div>
        </div>
      )}

      {/* Cost Drivers. "Contribution to gap" is undefined without a supplier price,
          so that column is dropped rather than filled with a column of $0.000 — the
          component's share of the should-cost is shown instead, which is real. */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title" style={{ marginBottom: 8 }}>
          {hasActual ? 'Top Cost Drivers' : 'Cost Components'}
        </div>
        {drivers.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16 }}>No component drivers for this model.</div>
        ) : (
          <>
            <table className="ca-table">
              <thead>
                <tr>
                  <th scope="col">Component</th>
                  <th scope="col">Index</th>
                  <th scope="col" className="right">Index Change</th>
                  <th scope="col" className="right">{hasActual ? 'Contribution to Gap' : 'Share of Should-Cost'}</th>
                  <th scope="col" className="right">Direction</th>
                </tr>
              </thead>
              <tbody>
                {drivers.map((d, i) => {
                  const flat = !d.index_change_pct;
                  const share = current_should_cost ? (d.component_cost / current_should_cost) * 100 : null;
                  return (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{d.component_label}</td>
                      <td style={{ color: 'var(--muted)' }}>{d.index_name || '—'}</td>
                      <td className="right" style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        color: flat ? 'var(--muted)' : d.index_change_pct > 0 ? 'var(--accent2)' : 'var(--accent)',
                      }}>
                        {flat ? 'flat' : `${d.index_change_pct > 0 ? '+' : ''}${d.index_change_pct.toFixed(1)}%`}
                      </td>
                      <td className="right" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                        {hasActual
                          ? `${sym}${fmtMoney(d.contribution_to_gap, { signed: true, decimals: dp })}`
                          : <>{sym}{fmtMoney(d.component_cost, { decimals: dp })}{share != null && <span style={{ color: 'var(--muted)' }}> · {share.toFixed(0)}%</span>}</>}
                      </td>
                      <td className="right">
                        {/* Full border, not a left stripe. The arrow glyph carries
                            direction without relying on colour, which is what keeps
                            this legible in the black-and-white printed brief. */}
                        <span style={{
                          display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                          background: d.direction === 'up' ? 'var(--danger-bg)' : d.direction === 'down' ? 'var(--success-bg)' : 'var(--neutral-bg)',
                          color: d.direction === 'up' ? 'var(--accent2)' : d.direction === 'down' ? 'var(--accent)' : 'var(--muted)',
                          border: '1px solid currentColor',
                        }}>
                          {d.direction === 'up' ? '↑ Up' : d.direction === 'down' ? '↓ Down' : '↔ Flat'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!hasActual && (
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>
                Each row shows what the component contributes to the should-cost. Once a supplier
                price is recorded, this becomes each component's contribution to the gap.
              </div>
            )}
          </>
        )}
      </div>

      {/* Narrative */}
      <div className="ca-card">
        <div className="ca-card-title">Narrative Summary</div>
        <div style={{ fontSize: 13, lineHeight: 1.9, color: 'var(--text-secondary)', whiteSpace: 'pre-line' }}>
          {narrative ?? <span style={{ color: 'var(--muted)', fontStyle: 'italic' }}>AI narrative unavailable.</span>}
        </div>
      </div>
    </div>
  );
}
