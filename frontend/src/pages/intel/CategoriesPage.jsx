import { useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  PageHeader, FilterSidebar, FilterGroup, SidebarItem, SidebarDivider,
  EmptyState, ErrorState, LoadingCards, StatusBadge, useApi, fmt,
} from '../../components/intel';
import IndustryCard from './categories/IndustryCard';
import { STATUSES, STATUS_DEF } from './categories/status';
import '../../styles/intel-categories.css';

/* Intelligence › Categories — the demand axis.
 * 50 industries, each with its reference buyer; everything filters client-side. */

const SORTS = [
  { id: 'name', label: 'Name (A–Z)' },
  { id: 'servable', label: 'Most servable categories' },
  { id: 'products', label: 'Most products' },
];

const SCOPE_OPTIONS = [
  { value: 'existing', label: 'Carried over' },
  { value: 'split', label: 'Split from an earlier industry' },
  { value: 'new', label: 'New in September 2026' },
];

/* Coverage views over each industry's category-status split. Single select;
 * `highlight` is the bar segment the cards emphasise while it is on. */
const COVERAGE = [
  {
    value: 'servable', label: 'Has servable categories', highlight: 'servable',
    test: (s) => (s.servable || 0) > 0,
  },
  {
    value: 'no-servable', label: 'No servable category yet', highlight: 'servable',
    test: (s) => !(s.servable || 0),
  },
  {
    value: 'mostly-build', label: 'Mostly to build', highlight: 'build',
    test: (s, total) => (s.build || 0) * 2 > total,
  },
];
const coverageOf = (value) => COVERAGE.find((c) => c.value === value) || null;
const matchesCoverage = (it, cov) => !cov || cov.test(it.categories_by_status || {}, it.category_count || 0);

const byName = (a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' });

function sortItems(items, sort) {
  const list = [...items];
  if (sort === 'servable') {
    list.sort((a, b) => {
      const sa = a.categories_by_status || {};
      const sb = b.categories_by_status || {};
      return (sb.servable || 0) - (sa.servable || 0)
        || (sb.partial || 0) - (sa.partial || 0)
        || byName(a, b);
    });
  } else if (sort === 'products') {
    list.sort((a, b) => (b.product_count || 0) - (a.product_count || 0) || byName(a, b));
  } else {
    list.sort(byName);
  }
  return list;
}

export default function CategoriesPage() {
  const { data, error, loading, reload } = useApi('/api/intel/industries');
  const [params, setParams] = useSearchParams();

  const q = params.get('q') || '';
  const coverage = coverageOf(params.get('coverage'));
  const scopeParam = params.get('scope') || '';
  const scopes = useMemo(
    () => scopeParam.split(',').filter((s) => SCOPE_OPTIONS.some((o) => o.value === s)),
    [scopeParam],
  );
  const sort = SORTS.some((s) => s.id === params.get('sort')) ? params.get('sort') : 'name';

  const setParam = useCallback((key, value) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) next.delete(key);
      else next.set(key, Array.isArray(value) ? value.join(',') : value);
      return next;
    }, { replace: true });
  }, [setParams]);

  const items = useMemo(() => data?.items || [], [data]);
  const totals = data?.categories_by_status || {};
  const categoryTotal = STATUSES.reduce((s, k) => s + (totals[k] || 0), 0);

  // Search narrows first; facet counts are over the searched set.
  const searched = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((it) => `${it.name} ${it.buyer_one_line || ''}`.toLowerCase().includes(needle));
  }, [items, q]);

  const coverageOptions = useMemo(() => COVERAGE.map((c) => ({
    value: c.value,
    label: c.label,
    count: searched.filter((it) => scopes.length === 0 || scopes.includes(it.scope_status))
      .filter((it) => matchesCoverage(it, c)).length,
  })), [searched, scopes]);

  const scopeOptions = useMemo(() => SCOPE_OPTIONS.map((o) => ({
    ...o,
    count: searched.filter((it) => it.scope_status === o.value)
      .filter((it) => matchesCoverage(it, coverage)).length,
  })), [searched, coverage]);

  const visible = useMemo(() => {
    const filtered = searched
      .filter((it) => matchesCoverage(it, coverage))
      .filter((it) => scopes.length === 0 || scopes.includes(it.scope_status));
    return sortItems(filtered, sort);
  }, [searched, coverage, scopes, sort]);

  const activeCount = (q ? 1 : 0) + (coverage ? 1 : 0) + (scopes.length ? 1 : 0);
  const clearAll = () => setParams((prev) => {
    const next = new URLSearchParams(prev);
    ['q', 'coverage', 'scope'].forEach((k) => next.delete(k));
    return next;
  }, { replace: true });

  const sortLabel = SORTS.find((s) => s.id === sort)?.label;

  return (
    <div className="ix-catalogue ca-fade-in">
      <FilterSidebar
        search={q}
        onSearch={(v) => setParam('q', v)}
        searchPlaceholder="Search industries…"
        searchLabel="Search industries and reference buyers"
        activeCount={activeCount}
        onClear={clearAll}
        ariaLabel="Industry filters"
      >
        <SidebarItem label="All industries" count={data ? items.length : null}
          active={activeCount === 0} onClick={clearAll} />
        <SidebarDivider />
        <FilterGroup
          title="Coverage"
          options={coverageOptions}
          selected={coverage ? [coverage.value] : []}
          onChange={(next) => setParam('coverage', next[0] || null)}
          multi={false}
        />
        <FilterGroup
          title="Industry list"
          options={scopeOptions}
          selected={scopes}
          onChange={(next) => setParam('scope', next)}
        />
      </FilterSidebar>

      <main className="ix-catalogue-main">
        <PageHeader
          title="Categories"
          meta={data ? [
            fmt.plural(data.total ?? items.length, 'industry', 'industries'),
            fmt.plural(categoryTotal, 'category', 'categories'),
            `${fmt.num(totals.servable)} servable`,
            `${fmt.num(totals.partial)} partial`,
            `${fmt.num(totals.build)} build`,
          ] : []}
          actions={(
            <label className="ixc-sort">
              <span className="ix-toolbar-label">Sort</span>
              <select className="ix-select" value={sort} onChange={(e) => setParam('sort', e.target.value === 'name' ? null : e.target.value)}>
                {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
          )}
        >
          <p className="ixc-intro">
            Each industry is described by one reference buyer: the plant or procurement team that buys for it.
            A category is one function bought in one industry, such as coagulation in Municipal Water.
            Each category is servable, partial or build, depending on how much of it the catalogue covers.
          </p>
        </PageHeader>

        <ul className="ixc-legend" aria-label="Category status">
          {STATUSES.map((k) => (
            <li key={k}>
              <StatusBadge status={k} />
              <span>{STATUS_DEF[k]}</span>
            </li>
          ))}
        </ul>

        {error && <ErrorState error={error} onRetry={reload} title="Could not load the industries" />}

        {loading && !data && <LoadingCards count={8} height={200} className="ix-grid ix-grid-lg" />}

        {data && (
          <section className="ix-group" aria-label="Industries">
            <div className="ix-group-head">
              <span className="ix-group-name">
                {visible.length === items.length ? 'All industries' : 'Matching industries'}
              </span>
              <span className="ix-group-rule" />
              <span className="ix-group-count">
                {visible.length} of {items.length} · {sortLabel?.toLowerCase()}
              </span>
            </div>
            {visible.length > 0 ? (
              <div className="ix-grid ix-grid-lg">
                {visible.map((it) => <IndustryCard key={it.slug} industry={it} highlight={coverage?.highlight} />)}
              </div>
            ) : (
              <EmptyState
                title="No industries match"
                body="Clear a filter or search for a different name."
                action={<button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={clearAll}>Clear filters</button>}
              />
            )}
          </section>
        )}
      </main>
    </div>
  );
}
