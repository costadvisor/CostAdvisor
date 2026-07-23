import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import exportCsv from '../../utils/exportCsv';

/* ──────────────────────────────────────────────────────────────────────
 * Portfolio — the product as the central object. REAL data: every product
 * a category manager owns, each cost model carrying its own live should-cost
 * (index-evolved to the current quarter via POST /api/costing/should-cost).
 * One row per cost model; products with no cost model appear as Draft rows so
 * every product is visible. Priority-matrix / trigger-radar depth is Wave 2/3.
 * ──────────────────────────────────────────────────────────────────── */

// Current calendar quarter — the "today" we evolve should-cost to.
const NOW = new Date();
const CUR_Y = NOW.getFullYear();
const CUR_Q = Math.ceil((NOW.getMonth() + 1) / 3);

const STATUS_FILTERS = [
  { key: 'all', label: 'All statuses' },
  { key: 'complete', label: '● Complete' },
  { key: 'draft', label: '◯ Draft' },
];

const GROUP_BY = [
  { key: 'family', label: 'Family' },
  { key: 'supplier', label: 'Supplier' },
  { key: 'region', label: 'Region' },
];

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');
const fmtMoney = (v) => (v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3));

function StatusBadge({ status }) {
  const complete = status === 'complete';
  return (
    <span className="ca-badge" style={{ background: complete ? 'var(--success-bg)' : 'var(--warn-bg)', color: complete ? 'var(--accent)' : 'var(--accent3)' }}>
      {complete ? '● Complete' : '◯ Draft'}
    </span>
  );
}

export default function PortfolioArea() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();

  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sc, setSc] = useState({});           // { [costModelId]: { status: 'loading'|'ok'|'err', value, currency, unit } }

  const [search, setSearch] = useState('');
  const [familyFilter, setFamilyFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [groupBy, setGroupBy] = useState('family');
  const [closed, setClosed] = useState(() => new Set());   // groups are open unless closed

  useEffect(() => {
    if (!activeTeamId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/chemical-families'),
    ])
      .then(([cmRes, pRes, fRes]) => {
        setCostModels(cmRes.data);
        setProducts(pRes.data);
        setFamilies(fRes.data);
      })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [activeTeamId]);

  const familyName = (fid) => families.find(f => f.id === fid)?.name || null;
  const productById = useMemo(() => Object.fromEntries(products.map(p => [p.id, p])), [products]);

  // Rows: one per cost model, plus a Draft row per product with no cost model.
  const rows = useMemo(() => {
    const cmRows = costModels.map(cm => {
      const fid = productById[cm.product_id]?.chemical_family_id ?? null;
      return {
        kind: 'cm',
        key: cm.id,
        costModelId: cm.id,
        productId: cm.product_id,
        ref: cm.product_reference || '—',
        name: cm.product_name || 'Unnamed product',
        supplier: cm.supplier_name || null,
        shipFrom: cm.region || null,
        shipTo: cm.destination_country || cm.destination_region || null,
        familyId: fid,
        familyLabel: familyName(fid) || 'No family',
        status: 'complete',
        fv: cm.formula_versions?.[0] || null,
      };
    });
    const withCm = new Set(costModels.map(cm => cm.product_id));
    const draftRows = products.filter(p => !withCm.has(p.id)).map(p => ({
      kind: 'draft',
      key: `p-${p.id}`,
      productId: p.id,
      ref: p.formula || '—',
      name: p.name,
      supplier: null,
      shipFrom: null,
      shipTo: null,
      familyId: p.chemical_family_id ?? null,
      familyLabel: familyName(p.chemical_family_id) || 'No family',
      status: 'draft',
      fv: null,
    }));
    return [...cmRows, ...draftRows];
  }, [costModels, products, families, productById]);

  // Fire live should-cost per cost model (progressive fill).
  const cmIdsKey = costModels.map(cm => cm.id).join(',');
  useEffect(() => {
    if (!costModels.length) return;
    let cancelled = false;
    setSc(Object.fromEntries(costModels.map(cm => [cm.id, { status: 'loading' }])));
    costModels.forEach(cm => {
      api.post('/api/costing/should-cost', { cost_model_id: cm.id, target_year: CUR_Y, target_quarter: CUR_Q })
        .then(({ data }) => {
          if (cancelled) return;
          setSc(prev => ({ ...prev, [cm.id]: { status: 'ok', value: data.should_cost, currency: data.currency, unit: data.unit } }));
        })
        .catch(() => { if (!cancelled) setSc(prev => ({ ...prev, [cm.id]: { status: 'err' } })); });
    });
    return () => { cancelled = true; };
  }, [cmIdsKey]);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter(r => {
    if (familyFilter !== 'all' && String(r.familyId) !== familyFilter) return false;
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (q && !`${r.name} ${r.ref} ${r.supplier || ''}`.toLowerCase().includes(q)) return false;
    return true;
  });

  const groups = useMemo(() => {
    const keyOf = (r) => groupBy === 'supplier' ? (r.supplier || 'No supplier')
      : groupBy === 'region' ? (r.shipFrom || 'No region')
        : (r.familyLabel || 'No family');
    const map = new Map();
    filtered.forEach(r => { const k = keyOf(r); if (!map.has(k)) map.set(k, []); map.get(k).push(r); });
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([label, rs]) => ({ key: `${groupBy}:${label}`, label, rows: rs }));
  }, [filtered, groupBy]);

  const toggleGroup = (key) => setClosed(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });

  const supplierCount = new Set(costModels.map(cm => cm.supplier_name).filter(Boolean)).size;
  const regionCount = new Set(costModels.map(cm => cm.region).filter(Boolean)).size;
  const draftCount = rows.filter(r => r.status === 'draft').length;
  // Mockup g4: Total products / Formulas complete / Draft formulas / Suppliers.
  const stats = [
    { val: products.length, lbl: 'Total products', sub: `Across ${new Set(rows.map(r => r.familyLabel)).size} families` },
    { val: costModels.length, lbl: 'Formulas complete', sub: 'Should-cost live' },
    { val: draftCount, lbl: 'Draft formulas', sub: draftCount ? 'Action needed' : 'All modelled' },
    { val: supplierCount, lbl: 'Suppliers', sub: `Across ${regionCount || 0} region${regionCount === 1 ? '' : 's'}` },
  ];

  const filterBtn = (active) => (active ? 'ca-btn ca-btn-primary ca-btn-sm' : 'ca-btn ca-btn-ghost ca-btn-sm');

  const scCell = (r) => {
    if (r.kind !== 'cm') return <span style={{ fontSize: 12, color: 'var(--muted)' }}>—</span>;
    const s = sc[r.costModelId];
    if (!s || s.status === 'loading') return <span style={{ fontSize: 12, color: 'var(--muted)' }}>…</span>;
    if (s.status === 'err' || s.value == null) return <span style={{ fontSize: 12, color: 'var(--muted)' }} title="Could not compute">—</span>;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontWeight: 500, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{curSym(s.currency)}{fmtMoney(s.value)}/{s.unit}</span>
        <span className="ca-badge" style={{ background: 'var(--success-bg)', color: 'var(--accent)' }}>live</span>
      </div>
    );
  };

  const exportRows = () => exportCsv(
    'portfolio.csv',
    ['Ref', 'Product', 'Supplier', 'Ship-from', 'Ship-to', 'Family', 'Formula', 'Should-cost today', 'Currency', 'Starting price', 'Starting quarter'],
    filtered.map(r => {
      const s = sc[r.costModelId];
      return [
        r.ref === '—' ? '' : r.ref, r.name, r.supplier || '', r.shipFrom || '', r.shipTo || '', r.familyLabel,
        r.status === 'complete' ? 'Complete' : 'Draft',
        s && s.status === 'ok' && s.value != null ? s.value : '',
        s && s.status === 'ok' ? s.currency : (r.fv ? '' : ''),
        r.fv ? r.fv.base_price : '',
        r.fv ? `Q${r.fv.base_quarter}-${r.fv.base_year}` : '',
      ];
    })
  );

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div className="ca-h1">Product portfolio</div>
          <p className="ca-subtitle">Every product you buy, its supplier and route, and its live should-cost — evolved to Q{CUR_Q} {CUR_Y} by your linked indices.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {filtered.length > 0 && <button className="ca-btn ca-btn-ghost" onClick={exportRows}>Export CSV</button>}
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate('/cost-models/new')}>+ New cost model</button>
          {/* Product-centric primary action (mockup): the product is the thing you add first. */}
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/products')}>+ Add product</button>
        </div>
      </div>

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : products.length === 0 && costModels.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No products yet — add one to start building should-cost models.</div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/products')}>Add your first product</button>
        </div>
      ) : (
        <>
          {/* Filter bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 0', flexWrap: 'wrap' }}>
            <input className="ca-input" style={{ width: 200 }} placeholder="Search product, ref or supplier…" value={search} onChange={e => setSearch(e.target.value)} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              <button className={filterBtn(familyFilter === 'all')} onClick={() => setFamilyFilter('all')}>All families</button>
              {families.map(f => (
                <button key={f.id} className={filterBtn(familyFilter === String(f.id))} onClick={() => setFamilyFilter(String(f.id))}>{f.name}</button>
              ))}
            </div>
            <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 2px' }} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {STATUS_FILTERS.map(s => (
                <button key={s.key} className={filterBtn(statusFilter === s.key)} onClick={() => setStatusFilter(s.key)}>{s.label}</button>
              ))}
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Group by</span>
              {GROUP_BY.map(g => (
                <button key={g.key} className={filterBtn(groupBy === g.key)} onClick={() => setGroupBy(g.key)}>{g.label}</button>
              ))}
            </div>
          </div>

          {/* Stat tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
            {stats.map(s => (
              <div key={s.lbl} className="ca-metric">
                <div className="ca-metric-lbl">{s.lbl}</div>
                <div className="ca-metric-val">{s.val}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Grouped table */}
          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="ca-scroll-x">
              <table className="ca-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ width: 20 }} />
                    <th>Ref</th>
                    <th>Product</th>
                    <th>Supplier</th>
                    <th>Ship-from</th>
                    <th>Ship-to</th>
                    <th>Formula</th>
                    <th>Should-cost today</th>
                    <th style={{ width: 180 }} />
                  </tr>
                </thead>
                <tbody>
                  {groups.length === 0 && (
                    <tr>
                      <td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No products match these filters.</td>
                    </tr>
                  )}
                  {groups.map(group => {
                    const open = !closed.has(group.key);
                    const completeCount = group.rows.filter(r => r.status === 'complete').length;
                    return (
                      <FragmentGroup key={group.key}>
                        <tr style={{ cursor: 'pointer' }} onClick={() => toggleGroup(group.key)}>
                          <td colSpan={9} style={{ background: 'var(--surface2)', padding: '7px 14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)' }}>
                              <span style={{ fontSize: 11, display: 'inline-block', transition: 'transform .15s', transform: open ? 'none' : 'rotate(-90deg)' }}>▾</span>
                              {group.label}
                              <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>
                                {group.rows.length} {group.rows.length === 1 ? 'product' : 'products'} · {completeCount} {completeCount === 1 ? 'formula' : 'formulas'} complete
                              </span>
                            </div>
                          </td>
                        </tr>
                        {open && group.rows.map(r => (
                          <tr key={r.key}>
                            <td />
                            <td style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: 'var(--muted)' }}>{r.ref}</td>
                            <td><div style={{ fontWeight: 500 }}>{r.name}</div></td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.supplier || '—'}</td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.shipFrom || '—'}</td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.shipTo || '—'}</td>
                            <td><StatusBadge status={r.status} /></td>
                            <td>{scCell(r)}</td>
                            <td>
                              <div style={{ display: 'flex', gap: 4 }}>
                                {r.kind === 'draft' ? (
                                  <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => navigate('/cost-models/new', { state: { productId: r.productId } })}>Complete formula</button>
                                ) : (
                                  <>
                                    <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => navigate(`/portfolio/${r.costModelId}`)}>Open</button>
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${r.costModelId}/evolution`)}>Evolution</button>
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${r.costModelId}/brief`)}>Brief</button>
                                  </>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </FragmentGroup>
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

/* Thin wrapper so a group's header + rows share one keyed parent without
 * inserting an invalid element inside <tbody>. */
function FragmentGroup({ children }) {
  return <>{children}</>;
}
