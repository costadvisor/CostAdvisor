import { useNavigate } from 'react-router-dom';
import exportCsv from '../utils/exportCsv';

/* Scrum 22 — Opportunistic buy windows.
 * Per-product "cheap now / expensive now" signal: current should-cost vs the
 * trailing-4-quarter average. Data from GET /api/portfolio/buy-windows. */

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');

export const BUY_SIGNAL = {
  cheap:        { label: 'Buy now',      color: 'var(--accent)',  bg: 'var(--success-bg)' },
  expensive:    { label: 'Hold / wait',  color: 'var(--accent2)', bg: 'var(--danger-bg)' },
  neutral:      { label: 'Neutral',      color: 'var(--muted)',   bg: 'var(--neutral-bg)' },
  insufficient: { label: 'Not enough history', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
};

/** Small inline badge — reused in the cost-model / product view. */
export function BuySignalBadge({ signal, deviationPct }) {
  const s = BUY_SIGNAL[signal] || BUY_SIGNAL.neutral;
  return (
    <span className="ca-badge" style={{ background: s.bg, color: s.color }}>
      {s.label}
      {deviationPct != null && signal !== 'insufficient' ? ` (${deviationPct > 0 ? '+' : ''}${deviationPct.toFixed(1)}%)` : ''}
    </span>
  );
}

/* Scrum 70 (Part 2) — forward lock/hold verdict, from
 * GET /api/portfolio/buy-windows/{id}/verdict. A should-cost forecast
 * horizon_quarters ahead vs today's should-cost, built from the projection
 * service's stored index forecasts — separate signal from BUY_SIGNAL above,
 * which only looks backward at the trailing 4-quarter average. */
export const LOCK_HOLD_SIGNAL = {
  lock:         { label: 'Lock now',    color: 'var(--accent2)', bg: 'var(--danger-bg)' },
  hold:         { label: 'Hold / wait', color: 'var(--accent)',  bg: 'var(--success-bg)' },
  neutral:      { label: 'Neutral',     color: 'var(--muted)',   bg: 'var(--neutral-bg)' },
  insufficient: { label: 'No forecast yet', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
};

/** Small inline badge for the forward verdict — reused in the product view. */
export function LockHoldBadge({ verdict, deviationPct, horizonQuarters }) {
  const s = LOCK_HOLD_SIGNAL[verdict] || LOCK_HOLD_SIGNAL.neutral;
  const title = horizonQuarters != null ? `Should-cost forecast ${horizonQuarters}Q ahead vs today` : undefined;
  return (
    <span className="ca-badge" style={{ background: s.bg, color: s.color }} title={title}>
      {s.label}
      {deviationPct != null && verdict !== 'insufficient' ? ` (${deviationPct > 0 ? '+' : ''}${deviationPct.toFixed(1)}%)` : ''}
    </span>
  );
}

export default function BuyWindows({ data, loading, error }) {
  const navigate = useNavigate();

  if (loading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Computing buy windows…</div>;
  if (error) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>;
  if (!data) return null;

  const rows = data.filter(w => w.signal !== 'insufficient');
  const insufficient = data.filter(w => w.signal === 'insufficient');
  if (data.length === 0) {
    return (
      <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
        <div style={{ color: 'var(--text-secondary)' }}>
          No buy-window signals yet — build a should-cost with index history to see whether each product is cheap or expensive right now.
        </div>
      </div>
    );
  }

  const handleCsv = () => exportCsv(
    'buy-windows.csv',
    ['Product', 'Supplier', 'Region', 'Should-Cost Now', '4Q Average', 'Deviation %', 'Signal'],
    data.map(w => [w.product_name, w.supplier_name || '', w.region, w.current_should_cost, w.avg_4q, w.deviation_pct, BUY_SIGNAL[w.signal]?.label || w.signal]),
  );

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          Current should-cost vs the trailing 4-quarter average. "Buy now" = ≥3% below recent; "Hold / wait" = ≥3% above.
        </div>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handleCsv}>Export CSV</button>
      </div>
      <div className="ca-scroll-x">
        <table className="ca-table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th>Product</th>
              <th>Supplier</th>
              <th className="center">Should-cost now</th>
              <th className="center">4Q avg</th>
              <th className="center">Deviation</th>
              <th className="center">Signal</th>
              <th className="center">Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(w => {
              const cs = curSym(w.currency);
              return (
                <tr key={w.cost_model_id}>
                  <td style={{ fontWeight: 600 }}>{w.product_name}</td>
                  <td style={{ color: 'var(--muted)' }}>{w.supplier_name || '—'}</td>
                  <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)' }}>{cs}{w.current_should_cost.toFixed(2)}</td>
                  <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--muted)' }}>{w.avg_4q != null ? `${cs}${w.avg_4q.toFixed(2)}` : '—'}</td>
                  <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: w.deviation_pct > 0 ? 'var(--accent2)' : w.deviation_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                    {w.deviation_pct != null ? `${w.deviation_pct > 0 ? '+' : ''}${w.deviation_pct.toFixed(2)}%` : '—'}
                  </td>
                  <td className="center"><BuySignalBadge signal={w.signal} /></td>
                  <td className="center">
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${w.cost_model_id}/evolution`)}>Evolution</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {insufficient.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: 'var(--muted)' }}>
          {insufficient.length} product{insufficient.length > 1 ? 's' : ''} without enough index history for a signal yet.
        </div>
      )}
    </div>
  );
}
