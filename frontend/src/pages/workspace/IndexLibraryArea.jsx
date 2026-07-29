import { useState, useEffect, useMemo, Fragment } from 'react';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { useToast } from '../../components/Toast';
import IndexPopupModal from '../../components/IndexPopupModal';
import AddIndexModal from '../../components/AddIndexModal';
import DerivedIndexesModal from '../../components/DerivedIndexesModal';
import EditCellModal from '../../components/EditCellModal';
import FxCustomEditModal from '../../components/FxCustomEditModal';
import exportCsv from '../../utils/exportCsv';
import { Sparkline, GroupHeader, useOpenSet } from './wsCharts';

/* Index Library — mockup layout, REAL data. Mirrors the fetch/reshape of
 * pages/Indexes.jsx and opens IndexPopupModal (trend graph + AI + portfolio
 * impact + source) on row click. Theme-safe: colours via var(--…) only. */

/* ── Display vocabulary ─────────────────────────────────────────────────────
 * `commodity_indexes.category` is free text seeded from the reference workbook.
 * The 2026-07 drop produced 43 distinct values with near-duplicates ("Base metal"
 * / "Base metals", three labour variants, six overlapping energy buckets) — and
 * since only 7 names had a colour token, 36 of them fell back to the brand accent,
 * i.e. colour that looked like a signal while encoding nothing.
 *
 * So we fold the raw values onto the 7 canonical families the design system
 * actually has colours for. DISPLAY ONLY — nothing here writes to the DB, and the
 * raw category stays on the row title so no detail is lost. The real fix is a
 * `commodity_indexes.family_id` FK onto the Scrum 55 taxonomy spine, which indexes
 * have no link to today.
 */
const CAT_COLOR = {
  Metal: 'var(--cat-metal)', Energy: 'var(--cat-energy)', Chemical: 'var(--cat-chemical)',
  Labor: 'var(--cat-labor)', PPI: 'var(--cat-ppi)', Freight: 'var(--cat-freight)',
  FX: 'var(--cat-fx)', Composite: 'var(--accent4)', Other: 'var(--muted)',
};
const CANONICAL_CATEGORIES = ['Metal', 'Energy', 'Chemical', 'Labor', 'PPI', 'Freight', 'FX', 'Composite', 'Other'];

// Exact matches for every category the seeders have actually produced. Checked
// before the patterns below so the known vocabulary can't be mis-grouped by a
// greedy regex, and so each judgement call is reviewable in one place.
const CATEGORY_OVERRIDES = {
  composite: 'Composite',
  chemical: 'Chemical',
  'agricultural commodities': 'Chemical',   // grain/oilseed feedstocks, not food retail
  'agricultural commodity': 'Chemical',
  'crude palm oil': 'Chemical',             // oleochemical feedstock, NOT an energy oil
  'palm kernel oil': 'Chemical',
  oleochemicals: 'Chemical',
  'fermentation feedstock proxy': 'Chemical',
  fertiliser: 'Chemical',
  'fluorochemicals & refrigerants': 'Chemical',
  'guar gum': 'Chemical',
  'inorganic acids & alkalis': 'Chemical',
  'inorganic chemicals proxy': 'Chemical',
  'linear alkylbenzene': 'Chemical',
  methanol: 'Chemical',
  'monomers — aromatics': 'Chemical',
  'monomers — olefins': 'Chemical',
  'monomers — oxygenated & specialty': 'Chemical',
  'oxo alcohol proxy': 'Chemical',
  'packaging proxies': 'Chemical',          // polymer / board resin proxies
  'specialty intermediates': 'Chemical',
  'sulfuric acid': 'Chemical',
  urea: 'Chemical',
  energy: 'Energy',
  'crude oil': 'Energy',
  'crude oil & base oils': 'Energy',
  'energy — electricity': 'Energy',
  'energy — natural gas': 'Energy',
  'industrial electricity': 'Energy',
  'natural gas': 'Energy',
  utilities: 'Energy',
  metal: 'Metal',
  'base metal': 'Metal',
  'base metals': 'Metal',
  'iron scrap': 'Metal',
  // Mined feedstocks (ilmenite/rutile ore) track like metals on cost, so they
  // group with Metal even though the pigment they end up in is a chemical.
  'pigment & mineral feedstocks': 'Metal',
  labor: 'Labor',
  'labour cost index': 'Labor',
  'labour & fixed cost escalators': 'Labor',
  ppi: 'PPI',
  freight: 'Freight',
  fx: 'FX',
  custom: 'Other',                          // team-created; no catalog family
  other: 'Other',
};

// Fallback patterns for categories a future workbook drop invents. Ordered —
// first match wins — so narrow rules precede greedy ones: vegetable oils before
// /oil/, "natural gas" before /gas/, "labour cost index" before /price index/.
const CATEGORY_RULES = [
  [/\bfx\b|currency|exchange[ -]?rate/, 'FX'],
  [/freight|shipping|container|bunker|charter|haulage/, 'Freight'],
  [/labou?r|wage|salary|escalator/, 'Labor'],
  [/palm|kernel|rapeseed|soy|sunflower|coconut|tallow|oleo/, 'Chemical'],
  [/crude|petroleum|\boil\b|\bgas\b|electric|power|utilit|energy|coal|diesel|naphtha|fuel|lng/, 'Energy'],
  [/metal|steel|iron|alumin|copper|nickel|zinc|\bscrap\b|\bore\b|mineral|smelt/, 'Metal'],
  [/producer price|\bppi\b|price index|\bcpi\b/, 'PPI'],
  [/chemical|acid|alkali|caustic|monomer|polymer|resin|solvent|methanol|urea|fertili[sz]|olefin|aromatic|alcohol|amine|glycol|benzene|refrigerant|fluoro|agricultur|grain|corn|wheat|sugar|starch|\bgum\b|pigment|intermediate|specialty|feedstock|surfactant/, 'Chemical'],
];

// Unknown values return 'Other' rather than inventing a group — an honest
// "we don't know" beats a confident mis-grouping.
function canonicalCategory(raw) {
  if (!raw) return 'Other';
  const key = String(raw).trim().toLowerCase();
  if (CATEGORY_OVERRIDES[key]) return CATEGORY_OVERRIDES[key];
  for (const [pattern, family] of CATEGORY_RULES) if (pattern.test(key)) return family;
  return 'Other';
}

// `fx_pairs.source_type` is an internal enum. It used to render raw in the
// Provider column, so a CPO saw the lowercase string "frankfurter".
const PROVIDER_LABEL = {
  frankfurter: 'Frankfurter (ECB)',
  google_finance: 'Google Finance',
  ecb_url: 'ECB',
  ecb_live: 'ECB (daily)',
  manual: 'Manual entry',
};

/* ── Data trust status ──────────────────────────────────────────────────────
 * Two thirds of these rows are legitimately value-less: the catalog seeds every
 * tracked commodity but only a minority have a wired feed. An unexplained
 * em-dash reads as a broken tool, so every row says which case it is.
 * `retrieval_status` is the Scrum 57 metadata, already on CommodityIndexOut.
 */
const STATUS = {
  live:    { key: 'live',    label: 'Live',    color: 'var(--accent)',  bg: 'var(--success-bg)', hint: 'Wired to a free public feed.' },
  proxy:   { key: 'proxy',   label: 'Proxy',   color: 'var(--accent4)', bg: 'var(--info-bg)',    hint: 'Estimated from a related free index — an approximation, not a quote.' },
  weak:    { key: 'weak',    label: 'Weak',    color: 'var(--accent3)', bg: 'var(--warn-bg)',    hint: 'Loose proxy. Directionally useful; do not put this in front of a supplier as fact.' },
  blocked: { key: 'blocked', label: 'Blocked', color: 'var(--accent2)', bg: 'var(--danger-bg)',  hint: 'Only published behind a paywall — no free source exists for this feed.' },
  stale:   { key: 'stale',   label: 'Stale',   color: 'var(--accent3)', bg: 'var(--warn-bg)',    hint: 'Has history, but nothing in the last four quarters.' },
  nodata:  { key: 'nodata',  label: 'No data', color: 'var(--muted)',   bg: 'var(--neutral-bg)', hint: 'Tracked, but no source is configured and no values have loaded yet.' },
};

// Feed provenance wins when it is the more important caveat (blocked), otherwise
// data presence does, then proxy fidelity, then staleness.
function dataStatus({ retrievalStatus, hasValues, staleQuarters = 0 }) {
  if (retrievalStatus === 'blocked') return STATUS.blocked;
  if (!hasValues) return STATUS.nodata;
  if (retrievalStatus === 'weak_proxy') return STATUS.weak;
  if (retrievalStatus === 'good_proxy') return STATUS.proxy;
  if (staleQuarters >= 4) return STATUS.stale;
  return STATUS.live;
}

/* ── Number formatting ─────────────────────────────────────────────────────
 * Decimals are chosen per ROW from that row's own magnitude, not per cell. A
 * flat 2dp used to erase the very movement this page exists to show: a series
 * around 0.13 EUR/kWh rendered "0.13" in every quarter while its own vs-base
 * column reported −10.8%. Per-row decimals also keep the mono column aligned.
 */
function decimalsFor(magnitude) {
  const m = Math.abs(magnitude ?? 0);
  if (m >= 1000) return 0;
  if (m >= 100) return 1;
  if (m >= 10) return 2;
  if (m >= 1) return 3;
  if (m >= 0.01) return 4;
  return 6;
}

// Unit is NOT appended here. FX rows used to pass the pair name as their unit,
// producing "0.61/AUD/EUR" in every cell — the unit belongs in the header once.
function fmtVal(v, decimals = 2) {
  if (v == null) return '—';
  return v.toFixed(decimals);
}

// Movement under half a basis point is noise, not a rise. Treating `>= 0` as "up"
// painted pegged pairs (+0.0%) in danger red, inverting the Signal Color Rule.
const FLAT_PCT = 0.05;
function pctTone(delta) {
  if (delta == null) return { color: 'var(--muted)', sign: '', flat: true };
  if (Math.abs(delta) < FLAT_PCT) return { color: 'var(--text-secondary)', sign: '', flat: true };
  return { color: delta > 0 ? 'var(--accent2)' : 'var(--accent)', sign: delta > 0 ? '+' : '', flat: false };
}

/**
 * An editable grid cell.
 *
 * Deliberately NOT a <button> and NOT in the tab order. With 28 period columns
 * over 201 rows, one tab stop per cell would put ~5,800 stops between the toolbar
 * and anything after the table — technically "keyboard accessible" and completely
 * unusable. The row is the single tab stop (Enter opens the detail view), and the
 * keyboard path to editing a specific period is the Historical Data list inside
 * that view, which is ~28 stops the user opted into.
 *
 * `role`/`tabIndex={-1}` keep the cell programmatically focusable and announced
 * as an editable control for screen readers driving the table directly.
 */
function EditableCell({ onActivate, title, style, children }) {
  return (
    <td
      className="right ca-cell-edit"
      role="button"
      tabIndex={-1}
      aria-label={title}
      title={title}
      // The row is itself a click target; editing a cell must not also open it.
      onClick={(e) => { e.stopPropagation(); onActivate(e); }}
      style={style}
    >
      {children}
    </td>
  );
}

/** Skeleton rows — a 201-row grid used to announce itself as the text "Loading…". */
function SkeletonGrid({ periods = 8, rows = 8 }) {
  return (
    <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }} aria-busy="true" aria-label="Loading indexes">
      <div style={{ overflowX: 'hidden' }}>
        <table className="ca-table">
          <tbody>
            {Array.from({ length: rows }).map((_, r) => (
              <tr key={r}>
                {Array.from({ length: Math.min(periods, 6) + 4 }).map((__, c) => (
                  <td key={c}>
                    <div
                      className="ca-skeleton"
                      style={{ width: c === 2 ? '80%' : '55%', height: 11, animationDelay: `${(r * 3 + c) * 40}ms` }}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function IndexLibraryArea() {
  const { activeTeamId, user } = useAuth();
  const isSuperAdmin = !!user?.is_super_admin;
  const [showDerived, setShowDerived] = useState(false);
  const { addToast } = useToast();
  const [syncing, setSyncing] = useState(false);
  const [data, setData] = useState([]);
  const [commodities, setCommodities] = useState([]);
  const [sources, setSources] = useState([]);
  const [regionsOpt, setRegionsOpt] = useState([]);
  const [loading, setLoading] = useState(true);
  // A failed grid fetch used to console.error and fall through to the "no data
  // yet" card, so a 500 was indistinguishable from an empty team.
  const [loadError, setLoadError] = useState(null);

  const [costModels, setCostModels] = useState([]); // team's cost models, for auto-follow

  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [regionFilter, setRegionFilter] = useState('all');
  // Defaults ON. Of 201 catalog rows, ~135 are seeded placeholders with no feed;
  // opening on the full list meant two of every three rows were solid em-dashes.
  const [followedOnly, setFollowedOnly] = useState(true);
  const [popupRow, setPopupRow] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [collapsed, toggleCollapsed] = useOpenSet([]); // keys present = collapsed group
  const [pairs, setPairs] = useState([]);          // all configured fx_pairs
  const [pairsLive, setPairsLive] = useState({}); // FX pair name -> live daily rate
  const [fxCustomAll, setFxCustomAll] = useState([]);   // team FX custom overrides
  const [fxPlatformAll, setFxPlatformAll] = useState([]); // platform quarterly FX rates
  const [canManagePairs, setCanManagePairs] = useState(false); // FX-manager permission
  const [editCell, setEditCell] = useState(null);  // non-FX cell being overridden
  const [fxEdit, setFxEdit] = useState(null);       // FX cell override context

  const fetchData = async () => {
    if (!activeTeamId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const now = new Date();
      const toY = now.getFullYear(), toQ = Math.ceil((now.getMonth() + 1) / 3);
      const params = { team_id: activeTeamId, from_year: toY - 2, from_quarter: toQ, to_year: toY, to_quarter: toQ };
      const [valRes, comRes, srcRes, cmRes] = await Promise.all([
        api.get('/api/indexes/values', { params }),
        api.get('/api/indexes'),
        api.get('/api/indexes/sources', { params: { team_id: activeTeamId } }),
        api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      ]);
      setData(valRes.data); setCommodities(comRes.data); setSources(srcRes.data);
      setCostModels(cmRes.data || []);
      try {
        const f = await api.get('/api/indexes/filter-options', { params: { team_id: activeTeamId } });
        setRegionsOpt(f.data.regions || []);
      } catch { /* non-critical */ }
      try {
        // FX: live rate per pair (Latest column) + team custom overrides + platform quarterly (for the 3-mode editor).
        const [pr, cu, pl] = await Promise.all([
          api.get('/api/fx-rates/pairs'),
          api.get('/api/fx-rates/custom', { params: { team_id: activeTeamId } }),
          api.get('/api/fx-rates/'),
        ]);
        const m = {};
        (pr.data || []).forEach(p => { if (p.live_rate != null) m[p.name] = Number(p.live_rate); });
        setPairs(pr.data || []);
        setPairsLive(m);
        setFxCustomAll(cu.data || []);
        setFxPlatformAll(pl.data || []);
      } catch (err) {
        // Not fatal — the commodity grid still renders — but silently swallowing
        // this made all 31 FX rows vanish with no explanation.
        setPairs([]);
        addToast(`FX rates unavailable — ${formatApiError(err)}. Commodity indexes are unaffected.`, 'error');
      }
      try {
        const cm = await api.get('/api/fx-rates/can-manage-pairs');
        setCanManagePairs(!!cm.data?.can_manage);
      } catch { setCanManagePairs(false); }
    } catch (err) {
      setLoadError(formatApiError(err));
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, [activeTeamId]);

  const commodityMap = useMemo(() => {
    const m = new Map();
    commodities.forEach(c => m.set(c.id, c));
    return m;
  }, [commodities]);

  // Auto-follow: commodities actually referenced by one of the team's current
  // formulas (simple-mode components, or advanced-mode index variables) —
  // the real signal behind "In use", not a stand-in for it.
  const usedCommodityIds = useMemo(() => {
    const s = new Set();
    costModels.forEach(cm => {
      const fv = cm.formula_versions?.[0];
      if (!fv) return;
      (fv.components || []).forEach(c => { if (c.commodity_id != null) s.add(c.commodity_id); });
      Object.values(fv.variables || {}).forEach(v => {
        if (v?.type === 'index' && v.commodity_id != null) s.add(v.commodity_id);
      });
    });
    return s;
  }, [costModels]);

  const periods = useMemo(() => {
    const set = new Set();
    data.forEach(d => set.add(`${d.year}-${d.quarter}`));
    fxPlatformAll.forEach(r => set.add(`${r.year}-${r.quarter}`)); // include FX-only quarters
    return [...set].map(p => { const [y, q] = p.split('-'); return { year: +y, quarter: +q }; })
      .sort((a, b) => a.year - b.year || a.quarter - b.quarter)
      .map(p => ({ ...p, label: `Q${p.quarter}-${String(p.year).slice(2)}` }));
  }, [data, fxPlatformAll]);
  const periodsDesc = useMemo(() => [...periods].reverse(), [periods]);

  /* Render only the last 8 quarters.
   *
   * The grid used to render every quarter it could find — 28 of them (Q4-19 →
   * Q3-26), which cost three things at once: the header claimed "2-yr trend" over
   * seven years of data; 37 columns forced an `overflow-x` wrapper, and that
   * wrapper became the sticky containing block so the header could never pin to
   * the viewport; and 201 × 28 cells is ~5,600 DOM nodes.
   *
   * Full history is one click away in the row's detail view, which already has a
   * per-quarter Historical Data table. `periods` (unwindowed) still feeds the
   * sparkline, the detail view and the override editors — only the COLUMNS are cut.
   */
  const PERIOD_WINDOW = 8;
  const gridPeriods = useMemo(() => periodsDesc.slice(0, PERIOD_WINDOW), [periodsDesc]);
  const hiddenPeriodCount = Math.max(0, periodsDesc.length - gridPeriods.length);

  const findSource = (cid, region) => sources.find(s => s.commodity_id === cid && s.region === region) || null;
  const getGlobalScraperInfo = (cid) => {
    const cell = data.find(d => d.commodity_id === cid && d.global_scraper);
    return cell ? { scraper: cell.global_scraper, scrape_at: cell.global_scrape_at } : null;
  };

  // Click a quarter cell in a row → edit its team override (FX = 3-mode editor, else fixed value).
  const openCellEdit = (e, r, p, cell) => {
    e.stopPropagation(); // don't open the view popup
    if (r.meta.category === 'FX') {
      const [from, to] = r.mat.split('/');
      const current = fxCustomAll.find(c => c.from_currency === from && c.to_currency === to && c.year === p.year && c.quarter === p.quarter) || null;
      const availableQuarters = fxPlatformAll
        .filter(x => x.from_currency === from && x.to_currency === to)
        .map(x => ({ year: x.year, quarter: x.quarter, rate: x.rate }));
      setFxEdit({ pair: { from, to }, period: { year: p.year, quarter: p.quarter, label: p.label }, current, availableQuarters, liveRate: pairsLive[r.mat] ?? null });
    } else {
      setEditCell(cell || { commodity_id: r.commodity_id, region: r.reg, year: p.year, quarter: p.quarter, value: null, source: 'scraped' });
    }
  };

  // FX commodity rows come from fx_pairs instead of /api/indexes/values, so the
  // library lists ALL configured pairs (not just the few with index_values).
  const fxCommodityIdByName = useMemo(() => {
    const m = {};
    commodities.forEach(c => { if (c.category === 'FX') m[c.name] = c.id; });
    return m;
  }, [commodities]);

  // Trailing periods with no value — feeds the "Stale" status.
  const trailingGap = (cells) => {
    let n = 0;
    for (let i = cells.length - 1; i >= 0 && cells[i]?.value == null; i--) n++;
    return n;
  };

  const rows = useMemo(() => {
    // Non-FX rows from /api/indexes/values (exclude FX commodities — handled below).
    const grouped = {};
    data.forEach(d => {
      if (canonicalCategory(commodityMap.get(d.commodity_id)?.category) === 'FX') return;
      const key = `${d.commodity_name}__${d.region}`;
      if (!grouped[key]) grouped[key] = { mat: d.commodity_name, reg: d.region, commodity_id: d.commodity_id, valMap: {} };
      grouped[key].valMap[`Q${d.quarter}-${String(d.year).slice(2)}`] = d;
    });
    // List EVERY tracked non-FX index, incl. seeded catalog entries with no values
    // yet — they get one empty GLOBAL row, labelled "No data" rather than left bare.
    commodities.forEach(c => {
      if (canonicalCategory(c.category) === 'FX') return;
      if (!Object.values(grouped).some(g => g.commodity_id === c.id)) {
        grouped[`${c.name}__GLOBAL`] = { mat: c.name, reg: 'GLOBAL', commodity_id: c.id, valMap: {} };
      }
    });
    const indexRows = Object.values(grouped).map(r => {
      const cells = periods.map(p => r.valMap[p.label] || null);
      const nums = cells.map(c => c?.value).filter(v => v != null);
      const base = nums[0] ?? null;
      const latest = nums[nums.length - 1] ?? null;
      const meta = commodityMap.get(r.commodity_id) || {};
      const delta = (base != null && latest != null && base !== 0) ? (latest / base - 1) * 100 : null;
      const cat = canonicalCategory(meta.category);
      return {
        ...r, cells, base, latest, meta, delta, hist: nums, cat,
        // Decimals from the row's own magnitude so small-magnitude series stop
        // rendering as a flat column of identical 2dp values.
        decimals: decimalsFor(latest ?? base),
        status: dataStatus({
          retrievalStatus: meta.retrieval_status,
          hasValues: nums.length > 0,
          staleQuarters: trailingGap(cells),
        }),
      };
    });

    // FX rows: one per configured pair; per-period value = team custom override (resolved) or platform quarterly.
    const fxRows = pairs.map(pair => {
      const from = pair.from_currency, to = pair.to_currency;
      const valMap = {};
      periods.forEach(p => {
        const plat = fxPlatformAll.find(x => x.from_currency === from && x.to_currency === to && x.year === p.year && x.quarter === p.quarter);
        const cust = fxCustomAll.find(x => x.from_currency === from && x.to_currency === to && x.year === p.year && x.quarter === p.quarter);
        let value = plat ? Number(plat.rate) : null;
        let source = 'scraped';
        if (cust) {
          source = 'team_override';
          if (cust.value_type === 'fixed') value = cust.rate != null ? Number(cust.rate) : value;
          else if (cust.value_type === 'live') value = pair.live_rate != null ? Number(pair.live_rate) : value;
          else if (cust.value_type === 'quarter_ref') {
            const ref = fxPlatformAll.find(x => x.from_currency === from && x.to_currency === to && x.year === cust.ref_year && x.quarter === cust.ref_quarter);
            value = ref ? Number(ref.rate) : value;
          }
        }
        if (value != null) valMap[p.label] = { commodity_id: fxCommodityIdByName[pair.name] ?? null, commodity_name: pair.name, region: 'GLOBAL', year: p.year, quarter: p.quarter, value, scraped_value: plat ? Number(plat.rate) : null, source };
      });
      const cells = periods.map(p => valMap[p.label] || null);
      const nums = cells.map(c => c?.value).filter(v => v != null);
      const base = nums[0] ?? null;
      const latest = pair.live_rate != null ? Number(pair.live_rate) : (nums[nums.length - 1] ?? null);
      const delta = (base != null && latest != null && base !== 0) ? (latest / base - 1) * 100 : null;
      return {
        mat: pair.name, reg: 'GLOBAL', commodity_id: fxCommodityIdByName[pair.name] ?? null,
        valMap, cells, base, latest, delta, hist: nums, cat: 'FX',
        // unit stays null: the pair name IS the unit and it's already the row label.
        // Passing it as a unit is what produced "0.61/AUD/EUR" in every cell.
        meta: {
          category: 'FX', unit: null, provider: PROVIDER_LABEL[pair.source_type] || pair.source_type,
          frequency: 'Daily', source_url: pair.scrape_url,
        },
        decimals: 4,  // FX needs 4dp — 2dp rounds real movement away entirely
        status: dataStatus({ hasValues: nums.length > 0 || pair.live_rate != null, staleQuarters: trailingGap(cells) }),
        _pair: pair,
      };
    });

    return [...indexRows, ...fxRows].sort((a, b) => a.mat.localeCompare(b.mat));
  }, [data, periods, commodityMap, commodities, pairs, fxPlatformAll, fxCustomAll, fxCommodityIdByName]);

  const regionList = useMemo(() => regionsOpt.length ? regionsOpt : [...new Set(rows.map(r => r.reg))].sort(), [regionsOpt, rows]);

  // "Followed" = actually in use by a formula, OR the team has real data or a
  // configured source for it — i.e. anything besides an untouched catalog
  // placeholder row the team has never engaged with.
  const isFollowed = (r) => usedCommodityIds.has(r.commodity_id) || r.latest != null || !!findSource(r.commodity_id, r.reg);

  const matches = (r) =>
    (typeFilter === 'all' || r.cat === typeFilter) &&
    (regionFilter === 'all' || r.reg === regionFilter) &&
    (!followedOnly || isFollowed(r)) &&
    (!search || `${r.mat} ${r.meta.provider || ''} ${r.meta.category || ''}`.toLowerCase().includes(search.toLowerCase()));

  // ONE filtered set, shared by the tiles, the table and the CSV export. They used
  // to disagree: the tiles counted every row regardless of filter, so searching
  // "EUR" narrowed 201 rows to 35 while all 43 tiles kept their unfiltered totals.
  const visibleRows = useMemo(() => rows.filter(matches), [rows, typeFilter, regionFilter, followedOnly, search, usedCommodityIds, sources]);

  // Canonical order, not alphabetical — Metal/Energy/Chemical are the families a
  // buyer scans first. Empty families are dropped so the list stays honest.
  const categories = useMemo(() => {
    const counts = {};
    visibleRows.forEach(r => { counts[r.cat] = (counts[r.cat] || 0) + 1; });
    return CANONICAL_CATEGORIES.filter(k => counts[k]).map(key => ({ key, count: counts[key] }));
  }, [visibleRows]);

  // Type chips need counts over everything EXCEPT the type filter, otherwise
  // selecting one family zeroes every other family's count.
  const typeCounts = useMemo(() => {
    const counts = {};
    rows.forEach(r => {
      if (regionFilter !== 'all' && r.reg !== regionFilter) return;
      if (followedOnly && !isFollowed(r)) return;
      if (search && !`${r.mat} ${r.meta.provider || ''} ${r.meta.category || ''}`.toLowerCase().includes(search.toLowerCase())) return;
      counts[r.cat] = (counts[r.cat] || 0) + 1;
    });
    return counts;
  }, [rows, regionFilter, followedOnly, search, usedCommodityIds, sources]);

  const filtersActive = typeFilter !== 'all' || regionFilter !== 'all' || !!search || !followedOnly;
  const clearFilters = () => { setTypeFilter('all'); setRegionFilter('all'); setSearch(''); setFollowedOnly(true); };

  // Four tiles instead of one per category. 43 tiles cost 816px to tell the user
  // things like "1 Guar Gum index"; these answer the questions actually being asked.
  const stats = useMemo(() => {
    const withData = visibleRows.filter(r => r.latest != null).length;
    const attention = visibleRows.filter(r => ['blocked', 'weak', 'nodata', 'stale'].includes(r.status.key)).length;
    return [
      { label: 'Shown', value: visibleRows.length, sub: `of ${rows.length} tracked`, color: 'var(--accent4)' },
      // Counted over ALL rows, not the filtered set: with "Followed only" on this
      // would otherwise just restate "Shown".
      { label: 'Followed', value: rows.filter(isFollowed).length, sub: 'in a formula, or has data', color: 'var(--accent)' },
      { label: 'Carrying data', value: withData, sub: visibleRows.length ? `${Math.round((withData / visibleRows.length) * 100)}% of shown` : 'none yet', color: 'var(--cat-metal)' },
      { label: 'Needs a source', value: attention, sub: 'blocked, weak or empty', color: attention ? 'var(--accent3)' : 'var(--muted)' },
    ];
  }, [visibleRows, rows, usedCommodityIds, sources]);

  // Sync platform FX rates — runs the ECB scrapers + Frankfurter quarterly
  // backfill for all pairs (POST /api/fx-rates/scrape). The seed loads commodity
  // index values but NOT FX rates, so this is what populates the FX rows.
  // FX-manager / super-admin only (server-gated); refetches the grid on success.
  const handleSync = async () => {
    setSyncing(true);
    try {
      const { data: res } = await api.post('/api/fx-rates/scrape');
      const n = res?.pairs?.length ?? res?.scraped ?? null;
      addToast(`FX rates synced${n != null ? ` — ${n} pair${n === 1 ? '' : 's'}` : ''}. Refreshing…`, 'success');
      await fetchData();
    } catch (err) {
      addToast(formatApiError(err) || 'Sync failed', 'error');
    } finally {
      setSyncing(false);
    }
  };

  // Sync commodity indexes — runs the registered scrapers for all scrape-enabled
  // commodities (POST /api/indexes/scrape-all, super-admin). Complements the FX
  // sync so an admin can pull live index data on demand, not just nightly.
  const [syncingIdx, setSyncingIdx] = useState(false);
  const handleSyncIndexes = async () => {
    setSyncingIdx(true);
    try {
      const { data: res } = await api.post('/api/indexes/scrape-all');
      addToast(`Indexes synced — ${res?.scrapers_run ?? 0} feeds, ${res?.values_updated ?? 0} values updated.`, 'success');
      await fetchData();
    } catch (err) {
      addToast(formatApiError(err) || 'Index sync failed', 'error');
    } finally {
      setSyncingIdx(false);
    }
  };

  // Export exactly what's on screen, in display order — same filtered set the
  // table renders, so the CSV can never disagree with the grid.
  const handleExport = () => {
    const headers = ['In use', 'Status', 'Index', 'Family', 'Raw category', 'Unit', 'Provider', 'Source URL',
      'Region', 'Frequency', 'Latest', 'vs base %', ...periodsDesc.map(p => p.label)];
    const out = [];
    categories.forEach(cat => {
      visibleRows.filter(r => r.cat === cat.key).forEach(r => {
        out.push([
          usedCommodityIds.has(r.commodity_id) ? 'Yes' : 'No',
          r.status.label,
          r.mat,
          r.cat,
          r.meta.category || '',
          r.meta.unit || '',
          r.meta.provider || '',
          r.meta.source_url || '',
          r.reg,
          r.meta.frequency || '',
          r.latest != null ? r.latest : '',
          r.delta != null ? `${r.delta.toFixed(1)}%` : '',
          ...periodsDesc.map(p => { const c = r.valMap[p.label]; return c?.value != null ? c.value : ''; }),
        ]);
      });
    });
    exportCsv('index-library.csv', headers, out);
  };

  if (!activeTeamId) {
    return <div className="ca-page ca-fade-in"><div className="ca-h1">Index library</div>
      <div className="ca-card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>Select a team to view indices.</div></div>;
  }

  const colCount = 10 + gridPeriods.length;

  return (
    <div className="ca-page ca-fade-in">
      {/* Title and the page's actions on one line — "+ Add Index" used to be
          pushed onto the fifth wrapped row of filter chips by margin-left:auto. */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div className="ca-h1">Index library</div>
          <p className="ca-subtitle" style={{ marginBottom: 0, maxWidth: '70ch' }}>
            Every public price feed your should-costs are built on. Each row shows how the value is
            obtained, so an estimate reads as a softer signal than a real feed. Open a row for its
            chart, statistics and portfolio impact.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          {canManagePairs && (
            <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={handleSync} disabled={syncing}
              title="Scrape latest ECB/Frankfurter FX rates and backfill quarterly platform data">
              {syncing ? 'Syncing…' : '⟳ Sync FX rates'}
            </button>
          )}
          {canManagePairs && (
            <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={handleSyncIndexes} disabled={syncingIdx}
              title="Scrape latest values for all scrape-enabled commodity indexes (super-admin)">
              {syncingIdx ? 'Syncing…' : '⟳ Sync indexes'}
            </button>
          )}
          {isSuperAdmin && (
            <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={() => setShowDerived(true)}
              title="Manage composite (calculated) and proxy indexes">Derived indexes</button>
          )}
          <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={handleExport} disabled={!visibleRows.length}>Export CSV</button>
          <button className="ca-btn ca-btn-sm ca-btn-primary" onClick={() => setShowAddModal(true)}>+ Add Index</button>
        </div>
      </div>

      {/* Four tiles, not one per category. Fixed 4-up grid so the last tile can't
          stretch to full width the way `flex: 1 1 150px` did with an orphan. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, margin: '20px 0 16px' }}>
        {stats.map(s => (
          <div key={s.label} className="ca-metric">
            <div className="ca-metric-val" style={{ color: s.color, fontFamily: "'JetBrains Mono', monospace" }}>{s.value}</div>
            <div className="ca-metric-lbl">{s.label}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Two selects replace 48 chips (44 of them near-duplicate categories). */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
        <input
          className="ca-input" style={{ maxWidth: 240 }} type="search"
          aria-label="Search indexes by name, provider or category"
          placeholder="Search indexes…"
          value={search} onChange={e => setSearch(e.target.value)}
        />
        <label className="ca-label" htmlFor="idx-type" style={{ margin: 0 }}>Family</label>
        <select id="idx-type" className="ca-select" style={{ width: 'auto' }} value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
          <option value="all">All families ({Object.values(typeCounts).reduce((a, b) => a + b, 0)})</option>
          {CANONICAL_CATEGORIES.filter(k => typeCounts[k]).map(k => (
            <option key={k} value={k}>{k} ({typeCounts[k]})</option>
          ))}
        </select>
        <label className="ca-label" htmlFor="idx-region" style={{ margin: 0 }}>Region</label>
        <select id="idx-region" className="ca-select" style={{ width: 'auto' }} value={regionFilter} onChange={e => setRegionFilter(e.target.value)}>
          <option value="all">All regions</option>
          {regionList.map(rg => <option key={rg} value={rg}>{rg}</option>)}
        </select>
        <button
          className={`ca-btn ca-btn-sm ${followedOnly ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setFollowedOnly(f => !f)} aria-pressed={followedOnly}
          title="Followed = used by one of your formulas, or carrying data, or with a configured source. Turn off to browse all tracked indexes, including seeded catalog entries with no feed."
        >
          {followedOnly ? `Followed only` : `Showing all ${rows.length}`}
        </button>
        {filtersActive && (
          <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={clearFilters}>Clear filters</button>
        )}
      </div>

      {loading ? (
        <SkeletonGrid periods={gridPeriods.length || 8} />
      ) : loadError ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 40 }}>
          <div style={{ color: 'var(--accent2)', fontWeight: 600, marginBottom: 6 }}>Couldn't load indexes</div>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 16 }}>{loadError}</div>
          <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={fetchData}>Try again</button>
        </div>
      ) : rows.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>No index data for this team yet. {canManagePairs ? 'Use "Sync FX rates" above to fetch FX data, or add a source / index.' : 'Add a source or index, or ask an FX manager to sync rates.'}</div>
      ) : !visibleRows.length ? (
        /* Filtering to zero used to render column headers over an empty tbody with
           no message at all. */
        <div className="ca-card" style={{ textAlign: 'center', padding: 40, color: 'var(--text-secondary)' }}>
          <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>No indexes match these filters</div>
          <div style={{ fontSize: 12, marginBottom: 16 }}>
            {[search && `search “${search}”`, typeFilter !== 'all' && `family ${typeFilter}`,
              regionFilter !== 'all' && `region ${regionFilter}`, followedOnly && 'followed only']
              .filter(Boolean).join(' · ')}
          </div>
          <button className="ca-btn ca-btn-sm ca-btn-primary" onClick={clearFilters}>Clear filters</button>
        </div>
      ) : (
        <div className="ca-card" style={{ padding: 0 }}>
          {/* NO overflow on this wrapper above 1100px. The old `ca-scroll-x` capped
              height at 440px (≈4 rows onto 12,000px of content) AND, as a scroll
              container, became the sticky containing block — so a sticky `thead`
              offset by the nav height rendered 58px down inside the card and never
              pinned to the viewport. With 8 period columns the table fits, so the
              page is the only scroller and the header pins properly. */}
          <div className="ca-grid-scroll">
            <table className="ca-table ca-table-sticky">
              <caption className="ca-sr-only">
                Tracked commodity indexes and FX pairs, grouped by family. Each row shows how its
                value is obtained, its latest value, change since the start of the series, and one
                column per quarter.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col" style={{ whiteSpace: 'nowrap' }} title="Referenced by a component or variable on one of your current cost-model formulas">In use</th>
                  <th scope="col">Index</th>
                  <th scope="col">Family</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Region</th>
                  <th scope="col">Freq.</th>
                  <th scope="col" className="right">Latest</th>
                  {/* Was "2-yr trend" over a table carrying 28 quarters. */}
                  <th scope="col" className="right" style={{ whiteSpace: 'nowrap' }}
                    title={`Change from ${periods[0]?.label ?? 'the start of the series'} to latest`}>vs {periods[0]?.label ?? 'base'}</th>
                  <th scope="col" style={{ minWidth: 90 }}>Trend</th>
                  {gridPeriods.map((p, i) => (
                    <th
                      key={p.label} scope="col" className="right"
                      /* The latest column was tinted with the semi-transparent
                         --info-bg, so rows scrolled visibly THROUGH the sticky
                         header. An opaque surface plus an accent underline marks it
                         without letting content bleed through. */
                      style={i === 0
                        ? { background: 'var(--surface)', color: 'var(--accent4)', boxShadow: 'inset 0 -2px 0 var(--accent4)', whiteSpace: 'nowrap' }
                        : { whiteSpace: 'nowrap' }}
                    >{p.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {categories.map(cat => {
                  const catRows = visibleRows.filter(r => r.cat === cat.key);
                  const open = !collapsed.has(cat.key);
                  return (
                    // Key belongs on the Fragment, not the inner <tr> — this was
                    // throwing "Each child in a list should have a unique key".
                    <Fragment key={cat.key}>
                      <tr><td colSpan={colCount} style={{ padding: 0 }}>
                        <GroupHeader
                          label={cat.key} count={catRows.length} open={open}
                          onToggle={() => toggleCollapsed(cat.key)}
                          color={CAT_COLOR[cat.key]}
                        />
                      </td></tr>
                      {open && catRows.map(r => {
                        const tone = pctTone(r.delta);
                        // In use — referenced by a component/variable on one of the
                        // team's current formulas (see usedCommodityIds above).
                        const inUse = usedCommodityIds.has(r.commodity_id);
                        const liveVal = r.cat === 'FX' && pairsLive[r.mat] != null ? pairsLive[r.mat] : r.latest;
                        const openRow = () => setPopupRow(r);
                        return (
                          <tr
                            key={`${r.mat}-${r.reg}`}
                            /* Rows were div-like <tr onClick> with no role and no
                               tabindex: 0 of 201 reachable by keyboard. */
                            tabIndex={0}
                            role="button"
                            aria-label={`${r.mat}, ${r.reg}, ${r.status.label}${r.latest != null ? `, latest ${fmtVal(liveVal, r.decimals)}${r.meta.unit ? ` ${r.meta.unit}` : ''}` : ', no value'}`}
                            onClick={openRow}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRow(); }
                            }}
                            style={{ cursor: 'pointer' }}
                          >
                            <td>
                              <span className="ca-badge" title={r.status.hint}
                                style={{ background: r.status.bg, color: r.status.color, whiteSpace: 'nowrap' }}>
                                {r.status.label}
                              </span>
                            </td>
                            <td>
                              <span className="ca-badge" style={{ background: inUse ? 'var(--success-bg)' : 'var(--neutral-bg)', color: inUse ? 'var(--accent)' : 'var(--muted)' }}>
                                {inUse ? 'Yes' : '—'}
                              </span>
                            </td>
                            <td style={{ fontWeight: 600, color: 'var(--accent4)' }}>
                              {r.mat}
                              {r.meta.unit && <span style={{ color: 'var(--muted)', fontWeight: 400, fontSize: 11 }}> {r.meta.unit}</span>}
                            </td>
                            {/* Raw seeded category kept in the tooltip so folding 43
                                values onto 7 families loses no detail. */}
                            <td title={r.meta.category && r.meta.category !== r.cat ? `Catalog category: ${r.meta.category}` : undefined}>
                              <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: CAT_COLOR[r.cat] }}>{r.cat}</span>
                            </td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                              {r.meta.source_url
                                ? <a href={r.meta.source_url} target="_blank" rel="noopener noreferrer" title={r.meta.source_url}
                                    onClick={(e) => e.stopPropagation()}
                                    style={{ color: 'var(--accent4)', textDecoration: 'underline' }}>{r.meta.provider || r.meta.free_source_name || 'source'}</a>
                                : (r.meta.provider || r.meta.free_source_name || '—')}
                            </td>
                            <td style={{ fontSize: 12 }}>{r.reg}</td>
                            <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.meta.frequency || '—'}</td>
                            <EditableCell
                              onActivate={(e) => { const lp = periods[periods.length - 1]; if (lp) openCellEdit(e, r, lp, r.valMap[lp.label]); }}
                              title={`Latest value — override ${periods[periods.length - 1]?.label ?? 'this period'} for your team`}
                              style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, color: 'var(--text)' }}
                            >{fmtVal(liveVal, r.decimals)}</EditableCell>
                            <td className="right" style={{ fontFamily: "'JetBrains Mono', monospace", color: tone.color }}>
                              {r.delta == null ? '—' : tone.flat ? 'flat' : `${tone.sign}${r.delta.toFixed(1)}%`}
                            </td>
                            <td>
                              <Sparkline
                                data={r.hist}
                                label={r.delta == null ? `${r.mat}: no trend data` : `${r.mat} trend, ${tone.flat ? 'flat' : `${tone.sign}${r.delta.toFixed(1)}%`} since ${periods[0]?.label}`}
                              />
                            </td>
                            {gridPeriods.map((p, i) => {
                              const cell = r.valMap[p.label];
                              const ov = cell?.source === 'team_override';
                              return (
                                <EditableCell
                                  key={p.label}
                                  onActivate={(e) => openCellEdit(e, r, p, cell)}
                                  title={`${r.mat} · ${p.label}${ov ? ' · team override' : ''} — click to set a team override`}
                                  style={{
                                    fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                                    fontWeight: i === 0 ? 700 : 400,
                                    color: ov ? 'var(--accent4)' : undefined,
                                    background: i === 0 ? 'var(--neutral-bg-soft)' : undefined,
                                  }}
                                >{fmtVal(cell?.value, r.decimals)}{ov ? ' •' : ''}</EditableCell>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {/* Say what was left out. A grid that silently truncates reads as
              "this is everything". */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
            padding: '10px 14px', borderTop: '1px solid var(--border)',
            fontSize: 11, color: 'var(--muted)',
          }}>
            <span>
              {visibleRows.length} {visibleRows.length === 1 ? 'index' : 'indexes'}
              {hiddenPeriodCount > 0 && <> · last {gridPeriods.length} quarters shown, {hiddenPeriodCount} older hidden — open a row for full history</>}
            </span>
            <span>Export CSV includes all {periodsDesc.length} quarters</span>
          </div>
        </div>
      )}

      <IndexPopupModal
        isOpen={!!popupRow}
        onClose={() => setPopupRow(null)}
        commodityId={popupRow?.commodity_id}
        commodityName={popupRow?.mat}
        region={popupRow?.reg}
        teamId={activeTeamId}
        commodity={popupRow?.meta || null}
        periods={periods}
        cellData={popupRow?.cells || []}
        source={popupRow ? findSource(popupRow.commodity_id, popupRow.reg) : null}
        globalScraper={popupRow ? getGlobalScraperInfo(popupRow.commodity_id) : null}
        fxPair={popupRow?._pair || null}
        canManagePairs={canManagePairs}
        onPairChanged={() => fetchData()}
        onPairRemoved={() => { setPopupRow(null); fetchData(); }}
        onSourceChanged={fetchData}
        onRemoved={() => { setPopupRow(null); fetchData(); }}
        /* The detail view's Historical Data list is the keyboard path to a
           per-quarter override — the grid's period cells are pointer-only. */
        onEditPeriod={(p) => {
          if (!popupRow || !p) return;
          openCellEdit({ stopPropagation() {} }, popupRow, p, popupRow.valMap[p.label]);
        }}
      />

      <AddIndexModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        commodities={commodities}
        teamId={activeTeamId}
        canManagePairs={canManagePairs}
        isSuperAdmin={isSuperAdmin}
        onAdded={fetchData}
      />

      {showDerived && (
        <DerivedIndexesModal onClose={() => { setShowDerived(false); fetchData(); }} />
      )}

      <EditCellModal
        isOpen={!!editCell}
        onClose={() => setEditCell(null)}
        cell={editCell}
        teamId={activeTeamId}
        teamSource={editCell ? findSource(editCell.commodity_id, editCell.region) : null}
        periods={periods}
        onSaved={() => fetchData()}
      />

      {fxEdit && (
        <FxCustomEditModal
          pair={fxEdit.pair}
          period={fxEdit.period}
          current={fxEdit.current}
          liveRate={fxEdit.liveRate}
          availableQuarters={fxEdit.availableQuarters}
          teamId={activeTeamId}
          onSaved={() => fetchData()}
          onClose={() => setFxEdit(null)}
        />
      )}
    </div>
  );
}
