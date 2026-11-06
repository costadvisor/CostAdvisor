import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PageHeader, FilterSidebar, FilterGroup, SidebarItem, SidebarDivider,
  EmptyState, ErrorState, LoadingCards, useApi,
} from '../../components/intel';
import SupplierCard from './suppliers/SupplierCard';
import GenericSuppliersNote from './suppliers/GenericSuppliersNote';
import {
  SUPPLIERS_URL, PAGE_SIZE, MIN_PRODUCT_STEPS,
  useSupplierFilters, apiParams, useFamilyColors, useTotals, rankFamilies,
} from './suppliers/supplierUtils';
import '../../styles/intel-suppliers.css';

const SEARCH_DEBOUNCE_MS = 250;

/* Intelligence › Suppliers — the maker directory.
 * Producers that are a counted maker on at least one product in the grid,
 * ranked by products tracked here. No supplier totals as headlines (house
 * rule 10b). Filters live in the URL; the list pages in steps of PAGE_SIZE. */
export default function SuppliersPage() {
  const { filters, setFilter, clearAll, activeCount } = useSupplierFilters();

  // Search box: local while typing, written to the URL after a pause.
  const [qInput, setQInput] = useState(filters.q);
  const lastWritten = useRef(filters.q);
  useEffect(() => {
    if (filters.q !== lastWritten.current) {
      lastWritten.current = filters.q;
      setQInput(filters.q);
    }
  }, [filters.q]);
  useEffect(() => {
    if (qInput === filters.q) return undefined;
    const t = setTimeout(() => {
      lastWritten.current = qInput;
      setFilter('q', qInput);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput, filters.q, setFilter]);

  // Paging: a growing limit, reset whenever the filters change.
  const filterKey = JSON.stringify(apiParams(filters));
  const [pages, setPages] = useState({ key: filterKey, n: 1 });
  const pageCount = pages.key === filterKey ? pages.n : 1;
  const limit = PAGE_SIZE * pageCount;

  const list = useApi(SUPPLIERS_URL, apiParams(filters, { limit }), { keepPrevious: true });
  // The unfiltered directory: the option lists, the grand total, generic names.
  const base = useApi(SUPPLIERS_URL, { limit: 1 });

  // Each group's counts come from the list with every other filter applied
  // (its own left out), so a count is what you get by ticking that option.
  const famFacet = useApi(filters.family_id.length ? SUPPLIERS_URL : null, apiParams(filters, { omit: 'family_id', limit: 1 }));
  const indFacet = useApi(filters.industry.length ? SUPPLIERS_URL : null, apiParams(filters, { omit: 'industry', limit: 1 }));
  const optionTotals = useTotals({
    integratedYes: apiParams(filters, { omit: 'integrated', integrated: 'true' }),
    integratedNo: apiParams(filters, { omit: 'integrated', integrated: 'false' }),
  });

  const data = list.data;
  const baseData = base.data;
  const familyNames = useMemo(() => rankFamilies(baseData?.families || []), [baseData]);
  const familyColor = useFamilyColors(familyNames);

  const familyOptions = useMemo(() => {
    const src = filters.family_id.length ? famFacet.data : data;
    const counts = new Map((src?.families || []).map((f) => [String(f.id), f.count]));
    const known = src ? counts : null;
    return [...(baseData?.families || [])]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((f) => ({
        value: String(f.id),
        label: f.name,
        count: known ? (counts.get(String(f.id)) || 0) : undefined,
      }));
  }, [baseData, data, famFacet.data, filters.family_id]);
  const familyName = useMemo(() => {
    const names = new Map((baseData?.families || []).map((f) => [String(f.id), f.name]));
    return (id) => names.get(id) || 'Family';
  }, [baseData]);

  const industryOptions = useMemo(() => {
    const src = filters.industry.length ? indFacet.data : data;
    const counts = new Map((src?.industries || []).map((i) => [i.name, i.count]));
    const known = src ? counts : null;
    return (baseData?.industries || []).map((i) => ({
      value: i.name,
      count: known ? (counts.get(i.name) || 0) : undefined,
    }));
  }, [baseData, data, indFacet.data, filters.industry]);

  const integratedOptions = [
    { value: 'true', label: 'Integrated on 1+ products', count: optionTotals.integratedYes },
    { value: 'false', label: 'Not integrated', count: optionTotals.integratedNo },
  ];

  const total = data?.total;
  const items = data?.items || [];
  const firstLoad = list.loading && !data;
  const refreshing = list.loading && !!data;

  // One chip per ticked value; its × takes that value off alone.
  const chips = [
    filters.q && { key: 'q', label: `“${filters.q}”` },
    ...filters.family_id.map((v) => ({ key: 'family_id', value: v, label: familyName(v) })),
    ...filters.industry.map((v) => ({ key: 'industry', value: v, label: v })),
    filters.integrated && {
      key: 'integrated',
      label: filters.integrated === 'true' ? 'Integrated on 1+ products' : 'Not integrated',
    },
    filters.min > 1 && { key: 'min', label: `${filters.min}+ products` },
  ].filter(Boolean);

  const clearSearchAndFilters = () => {
    lastWritten.current = '';
    setQInput('');
    clearAll();
  };

  const meta = [
    'Makers counted on at least one product in the grid',
    'ranked by products tracked here',
  ];

  return (
    <div className="ix-catalogue ca-fade-in ixs-page">
      <FilterSidebar
        search={qInput}
        onSearch={setQInput}
        searchPlaceholder="Search suppliers…"
        searchLabel="Search suppliers by name or alias"
        activeCount={activeCount}
        onClear={clearSearchAndFilters}
        ariaLabel="Supplier filters"
      >
        <SidebarItem label="All suppliers" active={activeCount === 0}
          onClick={clearSearchAndFilters} />
        <SidebarDivider />
        <FilterGroup title="Product family" options={familyOptions}
          selected={filters.family_id}
          onChange={(v) => setFilter('family_id', v)}
          limit={30} searchable={false} hideZero />
        <FilterGroup title="Industry" options={industryOptions}
          selected={filters.industry}
          onChange={(v) => setFilter('industry', v)}
          limit={8} searchPlaceholder="Filter industries…" hideZero />
        <FilterGroup title="Integration" options={integratedOptions}
          selected={filters.integrated ? [filters.integrated] : []}
          onChange={(v) => setFilter('integrated', v[0] || '')}
          multi={false} />
        <div className="ix-fgroup" role="group" aria-label="Minimum products">
          <div className="ix-fgroup-head" style={{ cursor: 'default' }}>
            <span>Minimum products</span>
          </div>
          <div className="ixs-min-pills">
            <button type="button" className="ix-pill" aria-pressed={filters.min <= 1}
              onClick={() => setFilter('min', 1)}>Any</button>
            {MIN_PRODUCT_STEPS.map((n) => (
              <button key={n} type="button" className="ix-pill" aria-pressed={filters.min === n}
                onClick={() => setFilter('min', filters.min === n ? 1 : n)}>
                {n}+
              </button>
            ))}
          </div>
        </div>
      </FilterSidebar>

      <main className="ix-catalogue-main">
        <PageHeader title="Suppliers" meta={meta} />

        {chips.length > 0 && (
          <div className="ixs-active" aria-label="Active filters">
            {chips.map((c) => (
              <button key={`${c.key}:${c.value ?? ''}`} type="button" className="ix-chip accent ixs-active-chip"
                onClick={() => {
                  if (c.key === 'q') { lastWritten.current = ''; setQInput(''); }
                  setFilter(c.key, c.value != null ? filters[c.key].filter((x) => x !== c.value) : '');
                }}
                aria-label={`Remove filter ${c.label}`}>
                {c.label}
                <span aria-hidden className="ixs-active-x">×</span>
              </button>
            ))}
            {chips.length > 1 && (
              <button type="button" className="ix-link ixs-active-clear" onClick={clearSearchAndFilters}>
                Clear all
              </button>
            )}
          </div>
        )}

        {list.error && !data && <ErrorState error={list.error} onRetry={list.reload} />}
        {firstLoad && <LoadingCards count={12} height={188} className="ix-grid ix-grid-lg" />}

        {data && items.length === 0 && (
          <EmptyState
            title="No suppliers match these filters"
            body="Try fewer filters, or a shorter search."
            action={activeCount > 0 && (
              <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={clearSearchAndFilters}>
                Clear filters
              </button>
            )}
          />
        )}

        {items.length > 0 && (
          <>
            <div className={`ix-grid ix-grid-lg ixs-grid${refreshing ? ' is-refreshing' : ''}`} aria-busy={refreshing}>
              {items.map((s) => (
                <SupplierCard key={s.id} supplier={s} familyColor={familyColor}
                  familyIds={filters.family_id} industries={filters.industry} />
              ))}
            </div>
            <div className="ixs-more">
              {items.length < total ? (
                <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm"
                  disabled={refreshing}
                  onClick={() => setPages({ key: filterKey, n: pageCount + 1 })}>
                  {refreshing ? 'Loading…' : 'Show more'}
                </button>
              ) : <span className="ix-muted">End of the list</span>}
            </div>
            {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
          </>
        )}

        <GenericSuppliersNote items={baseData?.generic_suppliers || []} />
      </main>
    </div>
  );
}
