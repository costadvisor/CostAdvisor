import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import exportCsv from '../../utils/exportCsv';
import { DriftBar } from './wsCharts';
import RadarView from '../../components/RadarView';

/* Monitor — the standing question of the job: across everything I buy, where am
 * I overpaying right now, before I walk into a negotiation. Rebuilt to the new IA
 * (sample_idea/costadvisor_mockup.html): the whole book on one screen, product by
 * product, grouped by family, ranked by the money at stake.
 *
 * This is a re-platform, not new brains — it wires the EXISTING gap /
 * should-cost-vs-actual outputs (GET /api/portfolio/summary, joined client-side
 * with cost-models/products/families for family grouping + draft products, the
 * same shape PortfolioArea uses). No new backend engine. The trigger radar /
 * priority matrix / alerts that turn "here's a gap" into "fix this one first"
 * are deliberately Wave 3, not built here. */

// Status is derived on the frontend from the endpoint's existing flags + whether
// a product has a completed formula — no new backend logic.
const STATUS = {
  alert: { label: 'Alert', color: 'var(--accent2)', bg: 'var(--danger-bg)', rank: 0 },
  watch: { label: 'Watch', color: 'var(--accent3)', bg: 'var(--warn-bg)', rank: 1 },
  ok: { label: 'On track', color: 'var(--accent)', bg: 'var(--success-bg)', rank: 2 },
  draft: { label: 'Formula draft', color: 'var(--muted)', bg: 'var(--neutral-bg)', rank: 3 },
};

const STATUS_FILTERS = [
  { key: 'all', label: 'All products' },
  { key: 'alert', label: 'Alerts only' },
  { key: 'watch', label: 'Watch' },
  { key: 'ok', label: 'On track' },
  { key: 'draft', label: 'Formula draft' },
];

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');
const fmtMoney = (v) => (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3));

function Badge({ color, bg, children, title }) {
  return (
    <span className="ca-badge" title={title} style={{ background: bg, color }}>{children}</span>
  );
}

export default function MonitorArea() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();

  const [summary, setSummary] = useState(null);   // /api/portfolio/summary
  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [reportCur, setReportCur] = useState('USD');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [familyFilter, setFamilyFilter] = useState('all');
  const [closed, setClosed] = useState(() => new Set());   // groups open unless closed
  // 'drift' = the shipped should-cost-vs-actual table; 'radar' = the SCRUM-79
  // trigger radar. Two different questions — where am I overpaying now, and
  // what has a deadline on it — so they are views, not merged columns.
  const [view, setView] = useState('drift');

  useEffect(() => {
    if (!activeTeamId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Fetch the joinable context first, then the gap summary in the team's
    // dominant currency so the aggregate KPI is denominated sensibly.
    Promise.all([
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/chemical-families'),
    ])
      .then(([cmRes, pRes, fRes]) => {
        if (cancelled) return null;
        setCostModels(cmRes.data);
        setProducts(pRes.data);
        setFamilies(fRes.data);
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

  const familyName = (fid) => families.find(f => f.id === fid)?.name || null;
  const productById = useMemo(() => Object.fromEntries(products.map(p => [p.id, p])), [products]);
  const summaryByModel = useMemo(
    () => Object.fromEntries((summary?.models || []).map(m => [m.cost_model_id, m])),
    [summary],
  );

  // One row per cost model (with its gap data), plus a Draft row per product
  // that has no cost model yet — so the whole book is visible, per the mockup.
  const rows = useMemo(() => {
    const cmRows = costModels.map(cm => {
      const s = summaryByModel[cm.id];
      const fid = productById[cm.product_id]?.chemical_family_id ?? null;
      // A cost model with no completed formula still reads as a draft.
      const status = !s ? 'draft'
        : s.flag_price_drift ? 'alert'
          : s.flag_index_moved ? 'watch' : 'ok';
      const exposure = s ? Math.abs(s.cumulative_impact ?? s.gap ?? 0) : 0;
      return {
        kind: 'cm', key: cm.id, costModelId: cm.id, productId: cm.product_id,
        ref: cm.product_reference || null,
        name: cm.product_name || 'Unnamed product',
        supplier: cm.supplier_name || null,
        region: cm.region || null,
        currency: cm.currency || reportCur,
        familyLabel: familyName(fid) || 'No family',
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
      kind: 'draft', key: `p-${p.id}`, productId: p.id,
      ref: p.formula || null, name: p.name, supplier: null, region: null,
      currency: reportCur,
      familyLabel: familyName(p.chemical_family_id) || 'No family',
      status: 'draft', shouldCost: null, actual: null, gap: null, gapPct: null, exposure: 0,
    }));
    return [...cmRows, ...draftRows];
  }, [costModels, products, families, summaryByModel, productById, reportCur]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter(r => {
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (familyFilter !== 'all' && r.familyLabel !== familyFilter) return false;
    if (q && !`${r.name} ${r.ref || ''} ${r.supplier || ''}`.toLowerCase().includes(q)) return false;
    return true;
  });

  // Shared severity scale for drift bars; floor so small gaps don't render full.
  const maxAbsGap = Math.max(25, ...rows.map(r => Math.abs(r.gapPct || 0)));

  // Group by family. Families holding an alert float up (money-at-stake triage);
  // within a family, worst status first, then biggest exposure.
  const groups = useMemo(() => {
    const map = new Map();
    filtered.forEach(r => { if (!map.has(r.familyLabel)) map.set(r.familyLabel, []); map.get(r.familyLabel).push(r); });
    return [...map.entries()]
      .map(([label, rs]) => {
        rs.sort((a, b) => (STATUS[a.status].rank - STATUS[b.status].rank) || (b.exposure - a.exposure));
        const counts = rs.reduce((acc, r) => { acc[r.status] = (acc[r.status] || 0) + 1; return acc; }, {});
        const severity = Math.min(...rs.map(r => STATUS[r.status].rank));
        return { key: label, label, rows: rs, counts, severity };
      })
      .sort((a, b) => (a.severity - b.severity) || a.label.localeCompare(b.label));
  }, [filtered]);

  const toggleGroup = (key) => setClosed(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });

  // Stats — mockup's g4, all from existing outputs.
  const kpis = summary?.kpis;
  const completeCount = costModels.filter(cm => summaryByModel[cm.id]).length;
  const draftCount = rows.filter(r => r.status === 'draft').length;
  // "Awaiting invoice" = a live should-cost exists but no actual price landed yet.
  const awaitingInvoice = rows.filter(r => r.shouldCost != null && r.actual == null).length;
  const totalProducts = products.length;
  const stats = [
    { lbl: 'Products in portfolio', val: totalProducts, sub: `${completeCount} active · ${draftCount} formula incomplete` },
    { lbl: 'Should-costs live', val: `${completeCount} / ${totalProducts}`, color: 'var(--accent)', sub: draftCount ? `${draftCount} awaiting formula completion` : 'all products modelled' },
    { lbl: 'Estimated drift', val: `${curSym(reportCur)}${Math.round(kpis?.total_exposure || 0).toLocaleString()}`, color: (kpis?.total_exposure || 0) > 0 ? 'var(--accent2)' : undefined, sub: 'money at stake vs should-cost' },
    { lbl: 'Awaiting invoice', val: awaitingInvoice, sub: awaitingInvoice ? 'price not yet received this period' : 'all invoices in' },
  ];

  const filterBtn = (active) => (active ? 'ca-btn ca-btn-primary ca-btn-sm' : 'ca-btn ca-btn-ghost ca-btn-sm');
  const groupBadges = (counts) => ['alert', 'watch', 'ok', 'draft']
    .filter(k => counts[k])
    .map(k => <Badge key={k} color={STATUS[k].color} bg={STATUS[k].bg}>{counts[k]} {STATUS[k].label.toLowerCase()}</Badge>);

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div className="ca-h1">Monitor</div>
          <p className="ca-subtitle">Should-cost is always live — driven by your linked indices, not invoices. Every product ranked by the money at stake where actuals drift away from it.</p>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 4 }}>
            {[['drift', 'Drift'], ['radar', 'Radar']].map(([k, l]) => (
              <button key={k} onClick={() => setView(k)}
                className={`ca-btn ca-btn-sm ${view === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                title={k === 'drift'
                  ? 'Every product ranked by the money at stake right now'
                  : 'Time-bounded negotiation windows: what has a deadline on it, and what the radar cannot see'}>
                {l}
              </button>
            ))}
          </div>
          {view === 'drift' && filtered.length > 0 && (
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => exportCsv(
              'monitor.csv',
              ['Family', 'Ref', 'Product', 'Supplier', 'Region', 'Currency', 'Should-cost', 'Actual', 'Gap', 'Gap %', 'Exposure', 'Status', 'Invoice'],
              filtered.map(r => [
                r.familyLabel, r.ref || '', r.name, r.supplier || '', r.region || '', r.currency,
                r.shouldCost != null ? r.shouldCost : '', r.actual != null ? r.actual : '',
                r.gap != null ? r.gap : '', r.gapPct != null ? r.gapPct : '', r.exposure || '',
                STATUS[r.status].label, r.kind === 'cm' ? (r.actual != null ? 'Received' : 'Awaited') : '',
              ])
            )}>Export CSV</button>
          )}
        </div>
      </div>

      {view === 'radar' && <RadarView />}

      {view === 'drift' && (loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading&hellip;</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : rows.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
            No products yet &mdash; add one and build a should-cost to see where you're overpaying.
          </div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/products')}>Add your first product</button>
        </div>
      ) : (
        <>
          {/* Inline filter bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 0', flexWrap: 'wrap' }}>
            <input className="ca-input" style={{ width: 200 }} placeholder="Search products or ref&hellip;" value={search} onChange={e => setSearch(e.target.value)} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {STATUS_FILTERS.map(f => (
                <button key={f.key} className={filterBtn(statusFilter === f.key)} onClick={() => setStatusFilter(f.key)}>{f.label}</button>
              ))}
            </div>
            {families.length > 0 && <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 2px' }} />}
            {families.length > 0 && (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                <button className={filterBtn(familyFilter === 'all')} onClick={() => setFamilyFilter('all')}>All families</button>
                {[...new Set(rows.map(r => r.familyLabel))].sort().map(fl => (
                  <button key={fl} className={filterBtn(familyFilter === fl)} onClick={() => setFamilyFilter(fl)}>{fl}</button>
                ))}
              </div>
            )}
          </div>

          {/* Stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
            {stats.map(s => (
              <div key={s.lbl} className="ca-metric">
                <div className="ca-metric-lbl">{s.lbl}</div>
                <div className="ca-metric-val" style={{ color: s.color }}>{s.val}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Grouped monitor table */}
          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="ca-scroll-x">
              <table className="ca-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ width: 4, padding: 0 }} />
                    <th>Ref</th>
                    <th>Product</th>
                    <th className="center">Should-cost today</th>
                    <th className="center">Last actual</th>
                    <th className="center">Movement gap</th>
                    <th>Drift trend</th>
                    <th className="center">Invoice</th>
                    <th className="center">Status</th>
                    <th className="center">Exposure</th>
                    <th className="center">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.length === 0 && (
                    <tr><td colSpan={11} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No products match these filters.</td></tr>
                  )}
                  {groups.map(group => {
                    const open = !closed.has(group.key);
                    return (
                      <FragmentGroup key={group.key}>
                        <tr style={{ cursor: 'pointer' }} onClick={() => toggleGroup(group.key)}>
                          <td colSpan={11} style={{ background: 'var(--surface2)', padding: '7px 14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)' }}>
                              <span style={{ fontSize: 11, display: 'inline-block', transition: 'transform .15s', transform: open ? 'none' : 'rotate(-90deg)' }}>▾</span>
                              {group.label}
                              <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>{group.rows.length} {group.rows.length === 1 ? 'product' : 'products'}</span>
                              <span style={{ display: 'inline-flex', gap: 4 }}>{groupBadges(group.counts)}</span>
                            </div>
                          </td>
                        </tr>
                        {open && group.rows.map(r => {
                          const st = STATUS[r.status];
                          const cs = curSym(r.currency);
                          return (
                            <tr key={r.key}>
                              <td style={{ width: 4, padding: 0, background: st.color }} />
                              <td style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: 'var(--muted)' }}>{r.ref || '—'}</td>
                              <td>
                                <div style={{ fontWeight: 600, color: r.kind === 'draft' ? 'var(--text-secondary)' : 'var(--text)' }}>{r.name}</div>
                                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.supplier || 'No supplier'}{r.region ? ` · ${r.region}` : ''}</div>
                              </td>
                              <td className="center">
                                {r.shouldCost != null ? (
                                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                    <span style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)' }}>{cs}{fmtMoney(r.shouldCost)}</span>
                                    <Badge color="var(--accent)" bg="var(--success-bg)">live</Badge>
                                  </div>
                                ) : <span style={{ color: 'var(--muted)' }}>—</span>}
                              </td>
                              <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.actual != null ? 'var(--accent4)' : 'var(--muted)' }}>
                                {r.actual != null ? `${cs}${fmtMoney(r.actual)}` : '—'}
                              </td>
                              <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: r.gap > 0 ? 'var(--accent2)' : r.gap < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                                {r.gap != null ? `${r.gap > 0 ? '+' : ''}${cs}${fmtMoney(Math.abs(r.gap))}` : '—'}
                                {r.gapPct != null && <div style={{ fontSize: 10, color: 'var(--muted)' }}>{r.gapPct > 0 ? '+' : ''}{r.gapPct.toFixed(1)}%</div>}
                              </td>
                              <td>{r.gapPct != null ? <DriftBar value={Math.abs(r.gapPct)} max={maxAbsGap} color={st.color} /> : null}</td>
                              <td className="center">
                                {r.shouldCost != null
                                  ? (r.actual != null
                                    ? <Badge color="var(--accent)" bg="var(--success-bg)">Received</Badge>
                                    : <Badge color="var(--accent3)" bg="var(--warn-bg)">Awaited</Badge>)
                                  : <span style={{ color: 'var(--muted)' }}>—</span>}
                              </td>
                              <td className="center"><Badge color={st.color} bg={st.bg}>{st.label}</Badge></td>
                              <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>
                                {r.exposure ? `${cs}${Math.round(r.exposure).toLocaleString()}` : '—'}
                              </td>
                              <td className="center">
                                <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                                  {r.status === 'draft' ? (
                                    <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => navigate('/cost-models/new', { state: { productId: r.productId } })}>Complete formula</button>
                                  ) : (
                                    <>
                                      <button className={`ca-btn ca-btn-sm ${r.status === 'alert' || r.status === 'watch' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => navigate(`/cost-models/${r.costModelId}/brief`)}>Brief</button>
                                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${r.costModelId}/evolution`)}>Evolution</button>
                                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${r.costModelId}`)}>View</button>
                                    </>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </FragmentGroup>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ))}
    </div>
  );
}

/* Thin wrapper so a group's header + rows share one keyed parent without
 * inserting an invalid element inside <tbody>. */
function FragmentGroup({ children }) {
  return <>{children}</>;
}
