import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { DriftBar } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * Negotiate — landing lets a buyer pick which product to prep a call for,
 * ranked by money at stake; the detail view (NegotiateDetailArea) renders
 * the existing end-to-end product → gap → exportable brief flow (the same
 * POST /api/costing/brief Brief.jsx and Intelligence already use) in the
 * new IA. No new backend engine — reuses GET /api/portfolio/summary (same
 * source Monitor/Dashboard use) to rank products by exposure.
 *
 * The previous version of this file was a hardcoded demo (fixed product,
 * fabricated price ladder / tornado sensitivity / phased playbook). That
 * "new negotiation smarts" depth is explicitly Wave 3 — removed in favour
 * of the real flow.
 * ──────────────────────────────────────────────────────────────────── */

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');
const fmtMoney = (v) => (v == null ? '—' : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3));

const STATUS = {
  alert: { label: 'Above should-cost', color: 'var(--accent2)', bg: 'var(--danger-bg)', rank: 0 },
  watch: { label: 'Index moved', color: 'var(--accent3)', bg: 'var(--warn-bg)', rank: 1 },
  ok: { label: 'On track', color: 'var(--accent)', bg: 'var(--success-bg)', rank: 2 },
  draft: { label: 'Formula draft', color: 'var(--muted)', bg: 'var(--neutral-bg)', rank: 3 },
};

export default function NegotiateArea() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();

  const [summary, setSummary] = useState(null);
  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [reportCur, setReportCur] = useState('USD');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!activeTeamId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      api.get('/api/products', { params: { team_id: activeTeamId } }),
    ])
      .then(([cmRes, pRes]) => {
        if (cancelled) return null;
        setCostModels(cmRes.data);
        setProducts(pRes.data);
        const counts = {};
        cmRes.data.forEach(cm => { if (cm.currency) counts[cm.currency] = (counts[cm.currency] || 0) + 1; });
        const dominant = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || 'USD';
        setReportCur(dominant);
        return api.get('/api/portfolio/summary', { params: { team_id: activeTeamId, reporting_currency: dominant } });
      })
      .then(res => { if (res && !cancelled) setSummary(res.data); })
      .catch(err => { if (!cancelled) setError(formatApiError(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [activeTeamId]);

  const summaryByModel = useMemo(
    () => Object.fromEntries((summary?.models || []).map(m => [m.cost_model_id, m])),
    [summary],
  );

  // One row per cost model (with its gap data), plus a draft row per product
  // that has no cost model yet, so every product has a next action.
  const rows = useMemo(() => {
    const cmRows = costModels.map(cm => {
      const s = summaryByModel[cm.id];
      const status = !s ? 'draft'
        : s.flag_price_drift ? 'alert'
          : s.flag_index_moved ? 'watch' : 'ok';
      const exposure = s ? Math.abs(s.cumulative_impact ?? s.gap ?? 0) : 0;
      return {
        kind: 'cm', costModelId: cm.id, productId: cm.product_id,
        name: cm.product_name || 'Unnamed product',
        supplier: cm.supplier_name || null,
        region: cm.region || null,
        currency: cm.currency || reportCur,
        status,
        shouldCost: s ? s.current_should_cost : null,
        actual: s ? s.latest_actual_price : null,
        gap: s ? s.gap : null,
        gapPct: s ? s.gap_pct : null,
        exposure,
      };
    });
    const withCm = new Set(costModels.map(cm => cm.product_id));
    const draftRows = products.filter(p => !withCm.has(p.id)).map(p => ({
      kind: 'draft', costModelId: null, productId: p.id,
      name: p.name, supplier: null, region: null, currency: reportCur,
      status: 'draft', shouldCost: null, actual: null, gap: null, gapPct: null, exposure: 0,
    }));
    return [...cmRows, ...draftRows]
      .sort((a, b) => (STATUS[a.status].rank - STATUS[b.status].rank) || (b.exposure - a.exposure));
  }, [costModels, products, summaryByModel, reportCur]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter(r => !q || `${r.name} ${r.supplier || ''}`.toLowerCase().includes(q));

  const maxAbsGap = Math.max(25, ...rows.map(r => Math.abs(r.gapPct || 0)));
  const negotiable = rows.filter(r => r.kind === 'cm');
  const totalExposure = rows.reduce((s, r) => s + (r.exposure || 0), 0);
  const biggest = negotiable[0] || null;

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Negotiate</div>
      <p className="ca-subtitle">Every step before this one exists so you can walk into a supplier call with evidence, not a gut feeling. Pick a product — its live should-cost, gap and brief are ready to argue from.</p>

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : rows.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No products yet — build a should-cost before preparing a negotiation.</div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/cost-models/new')}>New cost model</button>
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, margin: '14px 0' }}>
            <div className="ca-metric">
              <div className="ca-metric-lbl">Ready to negotiate</div>
              <div className="ca-metric-val">{negotiable.length} / {rows.length}</div>
            </div>
            <div className="ca-metric">
              <div className="ca-metric-lbl">Total exposure</div>
              <div className="ca-metric-val" style={{ color: totalExposure > 0 ? 'var(--accent2)' : undefined }}>
                {curSym(reportCur)}{Math.round(totalExposure).toLocaleString()}
              </div>
            </div>
            <div className="ca-metric">
              <div className="ca-metric-lbl">Biggest opportunity</div>
              <div className="ca-metric-val" style={{ color: biggest?.gapPct ? 'var(--accent2)' : undefined }}>
                {biggest?.gapPct != null ? `${biggest.gapPct > 0 ? '+' : ''}${biggest.gapPct.toFixed(1)}%` : '—'}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{biggest?.name || 'None flagged yet'}</div>
            </div>
          </div>

          <input className="ca-input" style={{ width: 240, marginBottom: 12 }} placeholder="Search product or supplier…" value={search} onChange={e => setSearch(e.target.value)} />

          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="ca-scroll-x">
              <table className="ca-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ width: 4, padding: 0 }} />
                    <th>Product</th>
                    <th className="center">Should-cost</th>
                    <th className="center">Last actual</th>
                    <th className="center">Gap</th>
                    <th>Severity</th>
                    <th className="center">Status</th>
                    <th className="center">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 && (
                    <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No products match this search.</td></tr>
                  )}
                  {filtered.map(r => {
                    const st = STATUS[r.status];
                    const cs = curSym(r.currency);
                    return (
                      <tr key={r.kind === 'cm' ? r.costModelId : `p-${r.productId}`}
                        style={{ cursor: r.kind === 'cm' ? 'pointer' : 'default' }}
                        onClick={() => r.kind === 'cm' && navigate(`/negotiate/${r.costModelId}`)}>
                        <td style={{ width: 4, padding: 0, background: st.color }} />
                        <td>
                          <div style={{ fontWeight: 600, color: r.kind === 'draft' ? 'var(--text-secondary)' : 'var(--text)' }}>{r.name}</div>
                          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.supplier || 'No supplier'}{r.region ? ` · ${r.region}` : ''}</div>
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.shouldCost != null ? 'var(--accent)' : 'var(--muted)' }}>
                          {r.shouldCost != null ? `${cs}${fmtMoney(r.shouldCost)}` : '—'}
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.actual != null ? 'var(--accent4)' : 'var(--muted)' }}>
                          {r.actual != null ? `${cs}${fmtMoney(r.actual)}` : '—'}
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.gap > 0 ? 'var(--accent2)' : r.gap < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                          {r.gap != null ? `${r.gap > 0 ? '+' : ''}${cs}${fmtMoney(Math.abs(r.gap))}` : '—'}
                          {r.gapPct != null && <div style={{ fontSize: 10, color: 'var(--muted)' }}>{r.gapPct > 0 ? '+' : ''}{r.gapPct.toFixed(1)}%</div>}
                        </td>
                        <td>{r.gapPct != null ? <DriftBar value={Math.abs(r.gapPct)} max={maxAbsGap} color={st.color} /> : null}</td>
                        <td className="center"><span className="ca-badge" style={{ background: st.bg, color: st.color }}>{st.label}</span></td>
                        <td className="center">
                          {r.kind === 'draft' ? (
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={(e) => { e.stopPropagation(); navigate('/cost-models/new', { state: { productId: r.productId } }); }}>Complete formula</button>
                          ) : (
                            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={(e) => { e.stopPropagation(); navigate(`/negotiate/${r.costModelId}`); }}>Prepare →</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
