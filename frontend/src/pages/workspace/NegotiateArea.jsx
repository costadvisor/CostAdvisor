import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { DriftBar, GAP_STATUS as STATUS, gapStatus } from './wsCharts';
import DemoDataNote from '../../components/DemoDataNote';
import { qLabel } from '../../utils/quarters';
import { dominantCurrency, fmtMoney, fmtSignedMoney, EXPOSURE_NOTE, rowExposure } from '../../utils/currency';

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

export default function NegotiateArea() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();

  const [summary, setSummary] = useState(null);
  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [reportCur, setReportCur] = useState('USD');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // The search lives in the URL so Back from a brief returns to the same list.
  const [params, setParams] = useSearchParams();
  const search = params.get('q') || '';
  const setSearch = (v) => setParams(prev => {
    const next = new URLSearchParams(prev);
    if (v) next.set('q', v); else next.delete('q');
    return next;
  }, { replace: true });

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
        const dominant = dominantCurrency(cmRes.data) || 'USD';
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
      const status = gapStatus(s);
      const exposure = rowExposure(s);
      return {
        kind: 'cm', costModelId: cm.id, productId: cm.product_id,
        name: cm.product_name || 'Unnamed product',
        supplier: cm.supplier_name || null,
        region: cm.region || null,
        // Two cost models for one product and supplier differ by destination
        // (and Incoterm), which the row has to show to tell them apart.
        destination: cm.destination_country || null,
        incoterm: cm.incoterm || null,
        currency: cm.currency || reportCur,
        status,
        shouldCost: s ? s.current_should_cost : null,
        // Priced at the latest quarter with data, the quarter its brief ends at.
        asOf: s?.as_of_year ? qLabel(s.as_of_year, s.as_of_quarter) : null,
        actual: s ? s.latest_actual_price : null,
        actualPeriod: s?.latest_actual_year ? qLabel(s.latest_actual_year, s.latest_actual_quarter) : null,
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
  const liveCount = negotiable.filter(r => r.status !== 'draft').length;
  // The server's total converts each model's exposure into the reporting
  // currency (the same figure Monitor shows); the row sum is the fallback.
  const totalExposure = summary?.kpis?.total_exposure ?? rows.reduce((s, r) => s + (r.exposure || 0), 0);
  const biggest = negotiable[0] || null;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div className="ca-h1">Negotiate</div>
        <DemoDataNote />
      </div>
      <p className="ca-subtitle">Products sorted by status, then by money at stake. Open one to see its live should-cost, the gap to the last price paid, and a negotiation brief.</p>

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
            {/* Counted as Monitor and Forecast count: products, then cost models. */}
            <div className="ca-metric">
              <div className="ca-metric-lbl">Products in portfolio</div>
              <div className="ca-metric-val">{products.length}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{liveCount} of {negotiable.length} cost models ready to negotiate</div>
            </div>
            <div className="ca-metric" title={EXPOSURE_NOTE}>
              <div className="ca-metric-lbl">Total exposure</div>
              <div className="ca-metric-val" style={{ color: totalExposure > 0 ? 'var(--accent2)' : undefined }}>
                {fmtMoney(totalExposure, reportCur, { decimals: 0 })}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>Sum of today's gap × all volume on record, in {reportCur}</div>
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
            <div className="ca-scroll-x ca-scroll-x--full">
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
                    return (
                      <tr key={r.kind === 'cm' ? r.costModelId : `p-${r.productId}`}
                        style={{ cursor: r.kind === 'cm' ? 'pointer' : 'default' }}
                        onClick={() => r.kind === 'cm' && navigate(`/negotiate/${r.costModelId}`)}>
                        <td style={{ width: 4, padding: 0, background: st.color }} />
                        <td>
                          <div style={{ fontWeight: 600, color: r.kind === 'draft' ? 'var(--text-secondary)' : 'var(--text)' }}>{r.name}</div>
                          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                            {r.supplier || 'No supplier'}{r.destination ? ` → ${r.destination}` : r.region ? ` · ${r.region}` : ''}{r.incoterm ? ` · ${r.incoterm}` : ''}
                          </div>
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.shouldCost != null ? 'var(--accent)' : 'var(--muted)' }}>
                          {r.shouldCost != null ? fmtMoney(r.shouldCost, r.currency) : '—'}
                          {r.asOf && <div style={{ fontSize: 10, color: 'var(--muted)' }}>as of {r.asOf}</div>}
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.actual != null ? 'var(--accent4)' : 'var(--muted)' }}>
                          {r.actual != null ? fmtMoney(r.actual, r.currency) : '—'}
                          {r.actualPeriod && <div style={{ fontSize: 10, color: 'var(--muted)' }}>{r.actualPeriod}</div>}
                        </td>
                        <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.gap > 0 ? 'var(--accent2)' : r.gap < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                          {r.gap != null ? fmtSignedMoney(r.gap, r.currency, { scale: r.shouldCost }) : '—'}
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
