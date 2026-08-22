import { useState, useEffect, useMemo, Fragment } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import exportCsv from '../../utils/exportCsv';
import { GroupHeader } from './wsCharts';
import ProductFormModal from '../../components/ProductFormModal';
import { useConfirm, useAlert } from '../../components/ConfirmDialog';

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

/* Money formatting must never be ambiguous on this page — the number here is the
 * one a buyer puts in front of a supplier.
 *
 * The previous version was `v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3)`,
 * which had two compounding faults: under 100 it emitted three decimals, so a
 * $3/kg product rendered "$3.000"; and above 100 it called `toLocaleString()` with
 * no locale, so it used the BROWSER's — rendering 1234.56 as "1.235" for a de-AT
 * user (StaminaChem is Vienna-based). "$3.000" and "1.235" are indistinguishable
 * from three thousand and one point two three five respectively.
 *
 * Fixed locale + magnitude-based decimals: 3 → "3.00", 89.5 → "89.50",
 * 1234.56 → "1,235", 0.42 → "0.4200".
 */
const MONEY_LOCALE = 'en-US';
const fmtMoney = (v) => {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const abs = Math.abs(n);
  const decimals = abs >= 100 ? 0 : abs >= 1 ? 2 : 4;
  return n.toLocaleString(MONEY_LOCALE, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
};

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
  const confirm = useConfirm();
  const showAlert = useAlert();

  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [templates, setTemplates] = useState([]);   // catalog formula templates, for the add-product modal
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sc, setSc] = useState({});           // { [costModelId]: { status: 'loading'|'ok'|'err', value, currency, unit } }
  const [loadingExample, setLoadingExample] = useState(false);

  // Scrum 16 — self-serve onboarding: seed a runnable demo for a brand-new team.
  const handleLoadExampleData = async () => {
    const ok = await confirm({
      title: 'Load example data?',
      message: 'This adds 5 sample products, suppliers, cost models and 3 years of prices/volumes to your team so you can explore CostAdvisor immediately. Safe to run more than once.',
      confirmLabel: 'Load example data',
    });
    if (!ok) return;
    setLoadingExample(true);
    try {
      await api.post(`/api/teams/${activeTeamId}/load-example-data`);
      fetchData();
    } catch (err) {
      showAlert({ title: 'Could not load example data', message: formatApiError(err) });
    } finally {
      setLoadingExample(false);
    }
  };

  const [search, setSearch] = useState('');
  const [familyFilter, setFamilyFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [groupBy, setGroupBy] = useState('family');
  const [closed, setClosed] = useState(() => new Set());   // groups are open unless closed
  // Add-product modal — embedded here so a new product shows up in this page's own
  // table immediately, with no navigation to /products and back.
  const [showAddProduct, setShowAddProduct] = useState(false);

  // Named (not an anonymous effect body) so the add-product modal can call it
  // after a save to refresh the table in place, mirroring Products.jsx's own
  // fetchData/onSaved pattern.
  const fetchData = () => {
    if (!activeTeamId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/chemical-families'),
      api.get('/api/formulas/', { params: { team_id: activeTeamId } }),
    ])
      .then(([cmRes, pRes, fRes, tRes]) => {
        setCostModels(cmRes.data);
        setProducts(pRes.data);
        setFamilies(fRes.data);
        setTemplates(tRes.data);
      })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  };

  useEffect(fetchData, [activeTeamId]);

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

  const NO_GROUP_LABEL = { family: 'No family', supplier: 'No supplier', region: 'No region' };

  const groups = useMemo(() => {
    const keyOf = (r) => groupBy === 'supplier' ? (r.supplier || 'No supplier')
      : groupBy === 'region' ? (r.shipFrom || 'No region')
        : (r.familyLabel || 'No family');
    const map = new Map();
    filtered.forEach(r => { const k = keyOf(r); if (!map.has(k)) map.set(k, []); map.get(k).push(r); });
    // The "no X" bucket (products with nothing assigned) sorts last regardless of
    // where its label would otherwise fall alphabetically — it's a catch-all, not
    // a real category, so it shouldn't interrupt the alphabetical list of real ones.
    const noLabel = NO_GROUP_LABEL[groupBy];
    return [...map.entries()]
      .sort((a, b) => {
        if (a[0] === noLabel) return 1;
        if (b[0] === noLabel) return -1;
        return a[0].localeCompare(b[0]);
      })
      .map(([label, rs]) => ({ key: `${groupBy}:${label}`, label, rows: rs }));
  }, [filtered, groupBy]);

  const toggleGroup = (key) => setClosed(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });

  // Only families the team actually owns products in, with counts. Derived from
  // `rows` rather than /api/chemical-families so the list can't be 22 entries long
  // for a 3-product portfolio.
  const presentFamilies = useMemo(() => {
    const counts = new Map();
    rows.forEach(r => {
      const k = r.familyId ?? null;
      if (!counts.has(k)) counts.set(k, { id: k, name: r.familyLabel, count: 0 });
      counts.get(k).count += 1;
    });
    // "No family" is a catch-all, not a real family — keep it last rather than
    // wherever "N" happens to fall among real family names.
    return [...counts.values()].sort((a, b) => {
      if (a.name === 'No family') return 1;
      if (b.name === 'No family') return -1;
      return a.name.localeCompare(b.name);
    });
  }, [rows]);

  const filtersActive = familyFilter !== 'all' || statusFilter !== 'all' || !!search;
  const clearFilters = () => { setFamilyFilter('all'); setStatusFilter('all'); setSearch(''); };

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
    // Right-aligned to match the column header, and each state says which it is —
    // "loading", "not modelled" and "failed" all used to render as a bare dash or
    // ellipsis distinguished only by a title attribute.
    const wrap = (node) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>{node}</div>
    );
    if (r.kind !== 'cm') return wrap(<span style={{ fontSize: 12, color: 'var(--muted)' }} title="No formula yet">—</span>);
    const s = sc[r.costModelId];
    if (!s || s.status === 'loading') {
      return wrap(<span className="ca-skeleton" style={{ display: 'inline-block', width: 68, height: 11 }} role="status" aria-label="Calculating should-cost" />);
    }
    if (s.status === 'err' || s.value == null) {
      return wrap(<span style={{ fontSize: 12, color: 'var(--accent3)' }} title="Should-cost could not be computed — check the formula's indices have data">Unavailable</span>);
    }
    return wrap(
      <>
        <span style={{ fontWeight: 500, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{curSym(s.currency)}{fmtMoney(s.value)}/{s.unit}</span>
        <span className="ca-badge" style={{ background: 'var(--success-bg)', color: 'var(--accent)' }}>live</span>
      </>,
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
        s && s.status === 'ok' ? (s.currency || '') : '',
        r.fv ? r.fv.base_price : '',
        r.fv ? `Q${r.fv.base_quarter}-${r.fv.base_year}` : '',
      ];
    })
  );

  return (
    <div className="ca-page ca-fade-in">
      {/* Heading and actions share one row; the subtitle moves to its own
          full-width line below. Title+subtitle together were wide enough that
          the button block wouldn't fit beside them at normal desktop widths and
          wrapped below — putting only the (much narrower) heading on that row
          instead is what actually keeps the buttons beside it. */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div className="ca-h1" style={{ marginBottom: 0 }}>Product portfolio</div>
        {/* marginLeft: 'auto' hugs the right edge even if this ever wraps onto its
            own line — same fix already used for "Group by" in this page's own
            filter bar below. */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
          {filtered.length > 0 && <button className="ca-btn ca-btn-ghost" onClick={exportRows}>Export CSV</button>}
          {/* /products is still where editing full details or deleting a product
              lives — this is its one visible link from Portfolio. */}
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate('/products')}>Manage products</button>
          <span style={{ width: 1, height: 24, background: 'var(--border)' }} />
          {/* Product-centric primary action (mockup): the product is the thing you
              add first. Opens in place — this used to navigate to /products and
              force a second click there just to reveal the create form. */}
          <button className="ca-btn ca-btn-primary" onClick={() => setShowAddProduct(true)}>+ Add product</button>
        </div>
      </div>
      <p className="ca-subtitle" style={{ marginTop: 4 }}>Every product you buy, its supplier and route, and its live should-cost — evolved to Q{CUR_Q} {CUR_Y} by your linked indices.</p>

      <ProductFormModal
        isOpen={showAddProduct}
        editing={null}
        families={families}
        templates={templates}
        activeTeamId={activeTeamId}
        onClose={() => setShowAddProduct(false)}
        onSaved={() => { setShowAddProduct(false); fetchData(); }}
      />

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : products.length === 0 && costModels.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No products yet — add one to start building should-cost models.</div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
            <button className="ca-btn ca-btn-primary" onClick={() => setShowAddProduct(true)}>Add your first product</button>
            <button className="ca-btn ca-btn-ghost" onClick={handleLoadExampleData} disabled={loadingExample}>
              {loadingExample ? 'Loading…' : 'Load example data'}
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* Filter bar — a select for families, chips only where the option set is
              small. This used to render one chip per platform family: 23 chips
              across 3 rows to filter 3 products, 21 of them matching nothing,
              because the list came from /api/chemical-families rather than from
              the products actually present. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 0', flexWrap: 'wrap' }}>
            <input
              className="ca-input" style={{ width: 220 }} type="search"
              aria-label="Search products by name, reference or supplier"
              placeholder="Search products…"
              value={search} onChange={e => setSearch(e.target.value)}
            />
            <label className="ca-label" htmlFor="pf-family" style={{ margin: 0 }}>Family</label>
            <select id="pf-family" className="ca-select" style={{ width: 'auto' }} value={familyFilter} onChange={e => setFamilyFilter(e.target.value)}>
              <option value="all">All families ({rows.length})</option>
              {presentFamilies.map(f => (
                <option key={f.id ?? 'none'} value={String(f.id)}>{f.name} ({f.count})</option>
              ))}
            </select>
            <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 2px' }} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }} role="group" aria-label="Filter by formula status">
              {STATUS_FILTERS.map(s => (
                <button key={s.key} className={filterBtn(statusFilter === s.key)} aria-pressed={statusFilter === s.key} onClick={() => setStatusFilter(s.key)}>{s.label}</button>
              ))}
            </div>
            {filtersActive && (
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={clearFilters}>Clear filters</button>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }} role="group" aria-label="Group rows by">
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Group by</span>
              {GROUP_BY.map(g => (
                <button key={g.key} className={filterBtn(groupBy === g.key)} aria-pressed={groupBy === g.key} onClick={() => setGroupBy(g.key)}>{g.label}</button>
              ))}
            </div>
          </div>

          {/* Stat tiles. `repeat(4, 1fr)` was locked to four columns at any width;
              auto-fit lets them reflow. Value-before-label and mono numerals match
              every other page and DESIGN.md's rule that data values use the mono. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 14 }}>
            {stats.map(s => (
              <div key={s.lbl} className="ca-metric">
                <div className="ca-metric-val" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{s.val}</div>
                <div className="ca-metric-lbl">{s.lbl}</div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Grouped table */}
          <div className="ca-card" style={{ padding: 0 }}>
            {/* `ca-scroll-x` capped this at 440px — a porthole over a table that
                doesn't even fill it — and as a scroll container it would also stop
                the sticky header reaching the viewport. Page scrolls instead. */}
            <div className="ca-grid-scroll">
              <table className="ca-table ca-table-sticky" style={{ width: '100%' }}>
                <caption className="ca-sr-only">
                  Products grouped by {groupBy}, each with its supplier, route, formula status and
                  live should-cost for the current quarter.
                </caption>
                <thead>
                  <tr>
                    <th style={{ width: 20 }}><span className="ca-sr-only">Expand</span></th>
                    <th scope="col">Ref</th>
                    <th scope="col">Product</th>
                    <th scope="col">Supplier</th>
                    <th scope="col">Ship-from</th>
                    <th scope="col">Ship-to</th>
                    <th scope="col">Formula</th>
                    <th scope="col" className="right" style={{ whiteSpace: 'nowrap' }}>Should-cost today</th>
                    <th scope="col" style={{ width: 180 }}><span className="ca-sr-only">Actions</span></th>
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
                      <Fragment key={group.key}>
                        {/* Shared GroupHeader — carries role/aria-expanded and an
                            Enter/Space toggle. The hand-rolled <tr onClick> this
                            replaces had no keyboard path, so a collapsed group
                            could not be reopened without a mouse. */}
                        <tr>
                          <td colSpan={9} style={{ background: 'var(--surface2)', padding: '0 14px' }}>
                            <GroupHeader
                              label={group.label}
                              count={group.rows.length}
                              open={open}
                              onToggle={() => toggleGroup(group.key)}
                              /* Pluralise on the total, not the completed count —
                                 otherwise "0 of 1 formulas complete". */
                              meta={`${completeCount} of ${group.rows.length} ${group.rows.length === 1 ? 'formula' : 'formulas'} complete`}
                            />
                          </td>
                        </tr>
                        {open && group.rows.map(r => {
                          // Complete rows (kind === 'cm') open the cost model on a
                          // row click instead of a dedicated "Open" button — same
                          // pattern as the Index Library grid: the row is the single
                          // tab stop, Enter/Space activates it, and the remaining
                          // action buttons (Evolution/Brief) stop propagation so
                          // clicking them doesn't also fire the row's own navigate.
                          // Draft rows have no cost model to open, so they're left as
                          // plain rows with just their "Complete formula" button.
                          const openable = r.kind === 'cm';
                          const openRow = () => navigate(`/portfolio/${r.costModelId}`);
                          return (
                          <tr
                            key={r.key}
                            {...(openable ? {
                              tabIndex: 0,
                              role: 'button',
                              'aria-label': `Open ${r.name}`,
                              onClick: openRow,
                              onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRow(); } },
                              style: { cursor: 'pointer' },
                            } : {})}
                          >
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
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={(e) => { e.stopPropagation(); navigate(`/cost-models/${r.costModelId}/evolution`); }}>Evolution</button>
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={(e) => { e.stopPropagation(); navigate(`/cost-models/${r.costModelId}/brief`); }}>Brief</button>
                                  </>
                                )}
                              </div>
                            </td>
                          </tr>
                          );
                        })}
                      </Fragment>
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
