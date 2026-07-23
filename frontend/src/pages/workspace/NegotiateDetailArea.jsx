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

  const verdictColor = gap === null ? 'var(--muted)' : gap > 0 ? 'var(--accent2)' : 'var(--accent)';
  const verdictLabel = gap === null ? 'No actual price data' : gap > 0 ? 'Above should-cost' : 'Below should-cost';

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
        <span>›</span>
        <span>{product_name}</span>
      </nav>

      <div className="ca-no-print" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="ca-h1">Negotiation Brief</div>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>{product_name} · {supplier_name || 'No supplier'}{destination_country ? ` → ${destination_country}` : ''}</p>
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

      {/* Verdict card */}
      <div className="ca-card" style={{ marginBottom: 16, borderLeft: `3px solid ${verdictColor}` }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Product</div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 15 }}>{product_name}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
              {supplier_name || 'No supplier'}{destination_country ? ` → ${destination_country}` : ''}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Should-Cost</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 18, fontWeight: 700, color: 'var(--accent)' }}>
              {sym}{current_should_cost.toFixed(3)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>per {unit} at {period_label}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Actual Price</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 18, fontWeight: 700, color: 'var(--accent4)' }}>
              {current_actual_price !== null ? `${sym}${current_actual_price.toFixed(3)}` : '—'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>Assessment</div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 18, fontWeight: 700, color: verdictColor }}>
              {verdictLabel}
            </div>
            <div style={{ fontSize: 11, color: verdictColor }}>
              {gap !== null ? `${gap > 0 ? '+' : ''}${sym}${gap.toFixed(3)} (${gap_pct > 0 ? '+' : ''}${gap_pct.toFixed(1)}%)` : ''}
            </div>
          </div>
        </div>
      </div>

      {/* Total impact — always shown; prompts to add volumes when missing */}
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
          <div className="ca-metric-val" style={{ color: total_impact > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
            {total_impact > 0 ? '+' : ''}{sym}{total_impact?.toFixed(0) ?? '0'}
          </div>
        )}
      </div>

      {/* Chart */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title">Price Evolution</div>
        <div className="ca-scroll-x">
          <EvoChart periods={periodLabels} theoretical={theoretical} actual={actual} refCost={current_should_cost} />
        </div>
        <div style={{ display: 'flex', gap: 20, marginTop: 14, fontSize: 11 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 20, height: 2, background: 'var(--accent4)' }} /> Actual Price
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
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
              const maxCost = Math.max(...drivers.map(d => Math.abs(d.component_cost)), 0.001);
              return (
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 140 }}>
                  {drivers.map((d, i) => {
                    const pct = Math.abs(d.component_cost) / maxCost * 100;
                    return (
                      <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, minWidth: 40 }}>
                        <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, whiteSpace: 'nowrap' }}>
                          {sym}{d.component_cost.toFixed(2)}
                        </div>
                        <div style={{
                          width: '60%', height: `${Math.max(pct, 2)}%`, minHeight: 2,
                          background: 'var(--accent4)', borderRadius: '3px 3px 0 0', opacity: 0.85,
                        }} />
                        <div style={{ fontSize: 8, color: 'var(--text-secondary)', marginTop: 4, textAlign: 'center', lineHeight: 1.2 }}>
                          {d.component_label}
                        </div>
                      </div>
                    );
                  })}
                  {/* Total bar */}
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, minWidth: 40, borderLeft: '1px solid var(--border)', paddingLeft: 4 }}>
                    <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--accent)', marginBottom: 2 }}>
                      {sym}{current_should_cost.toFixed(2)}
                    </div>
                    <div style={{
                      width: '60%', height: '100%', minHeight: 2,
                      background: 'var(--accent)', borderRadius: '3px 3px 0 0', opacity: 0.7,
                    }} />
                    <div style={{ fontSize: 8, fontWeight: 700, color: 'var(--text-secondary)', marginTop: 4 }}>
                      SHOULD-COST
                    </div>
                  </div>
                </div>
              );
            })()}
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>
            Each bar shows a component's contribution to the current should-cost estimate.
          </div>
        </div>
      )}

      {/* Cost Drivers */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title">Top Cost Drivers</div>
        {drivers.length === 0 ? (
          <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16 }}>No component drivers for this model.</div>
        ) : (
          <table className="ca-table">
            <thead>
              <tr>
                <th>Component</th>
                <th>Index</th>
                <th className="center">Index Change</th>
                <th className="center">Contribution to Gap</th>
                <th className="center">Direction</th>
              </tr>
            </thead>
            <tbody>
              {drivers.map((d, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{d.component_label}</td>
                  <td style={{ color: 'var(--muted)' }}>{d.index_name || '—'}</td>
                  <td className="center" style={{ color: d.index_change_pct > 0 ? 'var(--accent2)' : d.index_change_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                    {d.index_change_pct > 0 ? '+' : ''}{d.index_change_pct.toFixed(1)}%
                  </td>
                  <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    {sym}{d.contribution_to_gap.toFixed(3)}
                  </td>
                  <td className="center">
                    <span style={{
                      display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                      background: d.direction === 'up' ? 'var(--danger-bg)' : d.direction === 'down' ? 'var(--success-bg)' : 'var(--neutral-bg)',
                      color: d.direction === 'up' ? 'var(--accent2)' : d.direction === 'down' ? 'var(--accent)' : 'var(--muted)',
                      borderLeft: '3px solid currentColor',
                    }}>
                      {d.direction === 'up' ? '↑ Up' : d.direction === 'down' ? '↓ Down' : '↔ Flat'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
