import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';
import {
  PageHeader, EmptyState, ErrorState, LoadingCards, Skeleton, useApi, fmt,
  STATUS_CODES, DEFAULT_STATUSES, STATUS_LABELS, SUBFAMILY_LABEL,
} from '../../components/intel';
import ProductsSidebar from './products/ProductsSidebar';
import ProgressiveGroups from './products/ProgressiveGroups';
import useProductFilters from './products/useProductFilters';
import '../../styles/intel-products.css';

const FILTER_LABELS = {
  q: 'Search', family_id: 'Family', subfamily_id: SUBFAMILY_LABEL, line_id: 'Product line',
  industry: 'Industry', fn: 'Function', supplier: 'Supplier', region: 'Region', trend: 'Trend',
  has_report: 'Line', status: 'Status',
};
const TREND_TEXT = { up: 'Up >2% vs Jan 2023', flat: 'Within ±2% of Jan 2023', down: 'Down >2% vs Jan 2023' };
const BASE_NOTE = 'should-cost index, base 100 = Jan 2023';

const statusText = (codes) => (STATUS_CODES.every((c) => codes.includes(c))
  ? 'All statuses'
  : STATUS_CODES.filter((c) => codes.includes(c)).map((c) => STATUS_LABELS[c]).join(', '));

/* The page title names the status view: the default is the verified one. */
function viewTitle(statuses, statusIsDefault) {
  if (statusIsDefault) return 'Products with verified supply';
  if (statuses.length === STATUS_CODES.length) return 'All products';
  return `Products: ${statusText(statuses)}`;
}

// "the search “zzzz” and industry Municipal Water" — for the empty state.
function describeFilters(chips) {
  return chips.map(({ key, text }) => {
    if (key === 'q') return `the search ${text}`;
    if (key === 'has_report') return 'lines with a market report';
    return `${FILTER_LABELS[key].toLowerCase()} ${text}`;
  }).join(' and ');
}

/* A filter by id prints the name it stands for. Families come from the facets;
 * a sub-family or line filter narrows the grid to that one, so its cards carry
 * the name. Before either has loaded the id stands in. */
function useIdNames(facets, items) {
  return useMemo(() => {
    const names = { family_id: new Map(), subfamily_id: new Map(), line_id: new Map() };
    (facets?.families || []).forEach((f) => names.family_id.set(String(f.id), f.name));
    (items || []).forEach((it) => {
      if (it.family?.id != null) names.family_id.set(String(it.family.id), it.family.name);
      if (it.subfamily?.id != null && it.subfamily.name) names.subfamily_id.set(String(it.subfamily.id), it.subfamily.name);
      if (it.line?.id != null) names.line_id.set(String(it.line.id), it.line.name);
    });
    return names;
  }, [facets, items]);
}

/* The grid's geometry, measured once from a zero-height probe card laid out
 * in the same grid: the card's content width (every sparkline gets it as a
 * fixed width, so no card carries its own observer) and the column count
 * (placeholder heights while families render progressively). */
function useGridGeometry() {
  const ref = useRef(null);
  const [geo, setGeo] = useState({ inner: 0, cols: 4 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const card = el.parentElement;
    const grid = card?.parentElement;
    const measure = () => {
      const inner = Math.floor(el.getBoundingClientRect().width);
      const cardW = card.getBoundingClientRect().width;
      const gridW = grid.getBoundingClientRect().width;
      const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
      const cols = cardW > 0 ? Math.max(1, Math.round((gridW + gap) / (cardW + gap))) : 4;
      setGeo((g) => (g.inner === inner && g.cols === cols ? g : { inner, cols }));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(grid);
    return () => ro.disconnect();
  }, []);
  return [ref, geo];
}

/* Back from a product page lands where the reader was, not at the top of
 * the grid. The offset is kept per history entry. */
function useScrollMemory(ready) {
  const location = useLocation();
  const navType = useNavigationType();
  const storeKey = `ixp-scroll:${location.key}`;
  const lastY = useRef(0);
  const restored = useRef(false);

  // Saved on the way out, from the last scroll event: a layout-effect cleanup
  // runs before the next page can clamp the offset. Only after a real scroll,
  // so StrictMode's rehearsal unmount does not overwrite a saved offset with 0.
  useLayoutEffect(() => {
    let scrolled = false;
    const onScroll = () => { scrolled = true; lastY.current = window.scrollY; };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      if (!scrolled) return;
      try { sessionStorage.setItem(storeKey, String(Math.round(lastY.current))); } catch { /* storage off */ }
    };
  }, [storeKey]);

  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    if (navType !== 'POP') return;
    let y = 0;
    try { y = Number(sessionStorage.getItem(storeKey)) || 0; } catch { y = 0; }
    if (y > 0) requestAnimationFrame(() => window.scrollTo(0, y));
  }, [ready, navType, storeKey]);
}

export default function ProductsPage() {
  const {
    filters, statuses, statusIsDefault, params, activeCount, update, setStatuses, clearAll,
  } = useProductFilters();
  const navType = useNavigationType();

  // The sidebar counts and the grid read the same status filter. The grid
  // sends no limit: the API returns every matching card (T7).
  const facetsReq = useApi('/api/intel/facets', { status: params.status }, { keepPrevious: true });
  const listReq = useApi('/api/intel/products', params, { keepPrevious: true });
  const facets = facetsReq.data;
  const counts = facets?.counts || {};
  const list = listReq.data;
  const items = useMemo(() => list?.items || [], [list]);

  /* Search box: local text, committed to the URL after a pause. Starting or
   * clearing a search is a history entry; refining it replaces the entry. */
  const [searchText, setSearchText] = useState(filters.q || '');
  const committedQ = useRef(filters.q || '');
  useEffect(() => {
    const urlQ = filters.q || '';
    if (urlQ !== committedQ.current) {
      committedQ.current = urlQ;
      setSearchText(urlQ);
    }
  }, [filters.q]);
  useEffect(() => {
    const t = searchText.trim();
    if (t === committedQ.current) return undefined;
    const id = setTimeout(() => {
      const replace = !!committedQ.current && !!t;
      committedQ.current = t;
      update({ q: t || null }, { replace });
    }, 250);
    return () => clearTimeout(id);
  }, [searchText, update]);

  const pick = (key, value) => update({ [key]: value });
  const clear = () => {
    committedQ.current = '';
    setSearchText('');
    clearAll();
  };

  // A new filter from a click scrolls back to the first family.
  const filterKey = JSON.stringify(params);
  const prevFilterKey = useRef(filterKey);
  useEffect(() => {
    if (prevFilterKey.current === filterKey) return;
    prevFilterKey.current = filterKey;
    if (navType !== 'POP' && window.scrollY > 0) window.scrollTo({ top: 0 });
  }, [filterKey, navType]);

  /* The API returns cards sorted by family, then status (verified first), then
   * name: group in that order. Every card lands in a group, so the groups hold
   * exactly the cards the header counts. */
  const groups = useMemo(() => {
    const out = [];
    let cur = null;
    items.forEach((item) => {
      const id = item.family?.id ?? 'none';
      if (!cur || cur.id !== id) {
        cur = { id, family: item.family?.name || 'Family not given', items: [] };
        out.push(cur);
      }
      cur.items.push(item);
    });
    return out;
  }, [items]);

  const [probeRef, grid] = useGridGeometry();
  useScrollMemory(!!list);
  const idNames = useIdNames(facets, items);

  const shown = list ? items.length : null;
  const otherFilters = activeCount - (statusIsDefault ? 0 : 1);
  const listed = counts.listed;
  const defaultView = counts.default_view;
  const allStatuses = statuses.length === STATUS_CODES.length;

  /* "945 shown · Show all statuses (1,750)". The number is always the cards
   * on the page. */
  // The totals in brackets are the whole catalogue's, so they show only when
  // no other filter narrows the grid.
  const bracket = (n) => (otherFilters === 0 && n != null ? ` (${fmt.num(n)})` : '');
  const statusSwitch = allStatuses ? (
    <button type="button" className="ix-link ixp-view-link" onClick={() => setStatuses(DEFAULT_STATUSES)}>
      Verified supply only{bracket(defaultView)}
    </button>
  ) : (
    <button type="button" className="ix-link ixp-view-link" onClick={() => setStatuses(STATUS_CODES)}>
      Show all statuses{bracket(listed)}
    </button>
  );
  const meta = [
    shown != null && (
      <span className="ixp-shown" data-shown={shown}>
        {otherFilters > 0 && counts.products != null
          ? `${fmt.num(shown)} of ${fmt.num(counts.products)} shown`
          : `${fmt.num(shown)} shown`}
      </span>
    ),
    statusSwitch,
    shown ? fmt.plural(groups.length, 'family', 'families') : null,
    BASE_NOTE,
  ];

  const trendPills = (
    <div className="ixp-pills" role="group" aria-label="Trend since January 2023">
      {[
        [null, 'All', 'Every trend'],
        ['up', 'Trending up', 'Up more than 2% since January 2023'],
        ['down', 'Trending down', 'Down more than 2% since January 2023'],
      ].map(([v, label, hint]) => (
        <button key={label} type="button" className="ix-pill" title={hint}
          aria-pressed={(filters.trend || null) === v} onClick={() => pick('trend', v)}>
          {label}
        </button>
      ))}
    </div>
  );

  const chipText = (key, value) => {
    if (key === 'trend') return TREND_TEXT[value] || value;
    if (key === 'q') return `“${value}”`;
    if (key === 'has_report') return 'Has a market report';
    if (idNames[key]) return idNames[key].get(String(value)) || `#${value}`;
    return value;
  };
  const activeChips = [
    ...(statusIsDefault ? [] : [{ key: 'status', text: statusText(statuses) }]),
    ...Object.entries(filters).filter(([, v]) => v != null).map(([key, v]) => ({ key, text: chipText(key, v) })),
  ];
  const removeChip = (key) => {
    if (key === 'status') { setStatuses(DEFAULT_STATUSES); return; }
    if (key === 'q') { committedQ.current = ''; setSearchText(''); }
    pick(key, null);
  };

  let body;
  if (listReq.error && !list) {
    body = <ErrorState error={listReq.error} onRetry={listReq.reload} title="Could not load the catalogue" />;
  } else if (!list) {
    body = (
      <section className="ix-group" aria-busy="true">
        <div className="ix-group-head"><Skeleton width={160} height={10} /><div className="ix-group-rule" /></div>
        <LoadingCards count={8} height={292} className="ix-grid ixp-grid" />
      </section>
    );
  } else if (items.length === 0) {
    const otherChips = activeChips.filter((c) => c.key !== 'status');
    body = (
      <EmptyState
        title="No products match these filters"
        body={otherChips.length
          ? `Nothing in ${allStatuses ? 'the catalogue' : 'this status view'} matches ${describeFilters(otherChips)}.`
          : 'No product has this supply status.'}
        action={(
          <>
            {!allStatuses && (
              <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setStatuses(STATUS_CODES)}>
                Search all statuses
              </button>
            )}
            {' '}
            <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={clear}>Clear filters</button>
          </>
        )}
      />
    );
  } else {
    body = <ProgressiveGroups groups={groups} sparkWidth={grid.inner} cols={grid.cols} />;
  }

  return (
    <div className="ix-catalogue ixp-page ca-fade-in">
      <ProductsSidebar
        facets={facets}
        facetsError={facetsReq.error}
        onRetryFacets={facetsReq.reload}
        filters={filters}
        statuses={statuses}
        onStatuses={setStatuses}
        activeCount={activeCount}
        searchText={searchText}
        onSearch={setSearchText}
        onPick={pick}
        onClear={clear}
      />
      <main className={`ix-catalogue-main${listReq.loading && list ? ' ixp-refreshing' : ''}`}
        data-total={list ? list.total : undefined}>
        <PageHeader eyebrow="Product Intelligence" title={viewTitle(statuses, statusIsDefault)} meta={meta}
          actions={trendPills} />
        {activeChips.length > 0 && (
          <div className="ixp-active" aria-label="Active filters">
            {activeChips.map(({ key, text }) => (
              <button key={key} type="button" className="ix-chip ixp-chip" onClick={() => removeChip(key)}
                title={key === 'status' ? 'Back to the verified-supply view' : `Remove ${FILTER_LABELS[key].toLowerCase()} filter`}>
                <span className="ixp-chip-k">{FILTER_LABELS[key]}</span>
                <strong>{text}</strong>
                <span aria-hidden className="ixp-chip-x">×</span>
              </button>
            ))}
            {activeChips.length > 1 && (
              <button type="button" className="ix-link ixp-clear-link" onClick={clear}>Clear all</button>
            )}
          </div>
        )}
        {listReq.error && list && (
          <div className="ix-callout danger ixp-inline-error" role="alert">
            Could not refresh the list: {listReq.error}{' '}
            <button type="button" className="ix-link" onClick={listReq.reload}>Retry</button>
          </div>
        )}
        {/* Zero-height probe: one card box in the real grid, for the sparkline width. */}
        <div className="ix-grid ixp-grid ixp-probe" aria-hidden>
          <div className="ix-card"><div ref={probeRef} /></div>
        </div>
        {body}
      </main>
    </div>
  );
}
