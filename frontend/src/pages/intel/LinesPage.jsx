import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  PageHeader, FilterSidebar, FilterGroup, SidebarItem, SidebarDivider,
  EmptyState, ErrorState, LoadingCards, useApi, SUBFAMILY_LABEL, SUBFAMILY_LABEL_PLURAL,
} from '../../components/intel';
import LineCard from './lines/LineCard';
import useGridColumns from './lines/useGridColumns';
import { useLineFilters, useFilteredLines, groupLines } from './lines/useLineFilters';
import { count, ssfValue, famValue } from './lines/lineUtil';
import '../../styles/intel-lines.css';

/* Intelligence › Product lines — the supply axis's lines grouped by family,
 * then by sub-family (a cluster of cards under a thin label), with the
 * products tracked, industries, top maker names and the market-report badge.
 * No supplier counts (house rule 10b). Lines with no tracked product fold
 * away unless asked. */

const CARD_MIN = 230;
const GAP = 12;

function FamilyGroup({ group, cols, fromSearch, onPickSubfamily }) {
  const subCount = group.clusters.length;
  return (
    <section className="ix-group ixl-family" aria-label={group.family}>
      <div className="ix-group-head">
        <h2 className="ix-group-name ixl-family-name">{group.family}</h2>
        <span className="ix-group-sub">
          {count(subCount, SUBFAMILY_LABEL.toLowerCase(), SUBFAMILY_LABEL_PLURAL.toLowerCase())}
        </span>
        <span className="ix-group-rule" />
        <span className="ix-group-count">{count(group.count, 'line')}</span>
      </div>
      <div className="ixl-fam-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {group.clusters.map((c) => {
          const span = Math.min(c.items.length, cols);
          return (
            <div key={c.key} className="ixl-cluster" style={{ gridColumn: `span ${span}` }}>
              <div className="ixl-cluster-head">
                {c.unplaced ? (
                  <span className="ixl-cluster-name is-plain">{c.name}</span>
                ) : (
                  <button type="button" className="ixl-cluster-name"
                    title={`${c.name} — show only this ${SUBFAMILY_LABEL.toLowerCase()}`}
                    onClick={() => onPickSubfamily(String(group.id), c.key)}>
                    {c.name}
                  </button>
                )}
                {c.items.length > 1 && <span className="ixl-cluster-count">{c.items.length}</span>}
                <span className="ixl-cluster-rule" aria-hidden />
              </div>
              <div className="ixl-cluster-grid" style={{ gridTemplateColumns: `repeat(${span}, minmax(0, 1fr))` }}>
                {c.items.map((line) => <LineCard key={line.id} line={line} fromSearch={fromSearch} />)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function LinesPage() {
  const location = useLocation();
  const { filters, update, clear, activeCount } = useLineFilters();

  // Search box: typed locally, pushed to the URL after a pause. An outside
  // URL change (Clear, back button) flows back into the box.
  const [q, setQ] = useState(filters.q);
  const pushed = useRef(filters.q);
  useEffect(() => {
    if (filters.q !== pushed.current) {
      pushed.current = filters.q;
      setQ(filters.q);
    }
  }, [filters.q]);
  useEffect(() => {
    const next = q.trim();
    if (next === filters.q) return undefined;
    const t = setTimeout(() => {
      pushed.current = next;
      update({ q: next });
    }, 250);
    return () => clearTimeout(t);
  }, [q, filters.q, update]);

  const { data, error, loading, reload } = useApi('/api/intel/lines', { q: filters.q }, { keepPrevious: true });
  const items = useMemo(() => data?.items || [], [data]);
  const { rows, facets, shownTotal, emptyCount } = useFilteredLines(items, filters);
  const groups = useMemo(() => groupLines(rows), [rows]);
  const [gridRef, cols] = useGridColumns(CARD_MIN, GAP);

  const familyCount = groups.length;
  const withReport = rows.filter((r) => r.has_report).length;
  const filtered = activeCount > 0 || !!filters.q;

  const onFamily = (fams) => {
    // Keep only the sub-families that still sit under the chosen families.
    const keep = fams.length
      ? filters.subfamily_id.filter((v) => items.some((it) => ssfValue(it) === v && fams.includes(famValue(it))))
      : filters.subfamily_id;
    update({ family_id: fams, subfamily_id: keep });
  };
  const toggleFlag = (key, value) => update({ [key]: filters[key] === value ? '' : value });
  const pickSubfamily = (familyId, ssf) => update({ family_id: [familyId], subfamily_id: [ssf] });
  const clearAll = () => { pushed.current = ''; setQ(''); clear(); };

  /* Lines with a tracked product: at least one card in the Products grid,
   * whatever its supply status. */
  const countHint = 'Lines with at least one product in the Products grid (any supply status).';
  const headline = filtered && data
    ? `${rows.length.toLocaleString('en-GB')} of ${count(shownTotal, 'product line')}`
    : (
      <span className="ixl-tip" title={countHint}>
        {count(rows.length, 'product line')}{filters.empty ? '' : ' with tracked products'}
      </span>
    );

  return (
    <div className="ix-catalogue ixl-page ca-fade-in">
      <FilterSidebar search={q} onSearch={setQ} searchPlaceholder="Search lines…"
        searchLabel="Search product lines by name, product name or code" activeCount={activeCount} onClear={clearAll}
        ariaLabel="Product line filters">
        <SidebarItem label="All product lines" count={shownTotal}
          active={!filtered} onClick={clearAll} />
        <SidebarItem label="With a market report" count={facets.report.yes}
          active={filters.report === 'yes'} onClick={() => toggleFlag('report', 'yes')} />
        <SidebarItem label="No report yet" count={facets.report.no}
          active={filters.report === 'no'} onClick={() => toggleFlag('report', 'no')} />
        <SidebarItem label="Show lines with no tracked product" count={emptyCount}
          active={filters.empty} onClick={() => update({ empty: !filters.empty })}
          title="Lines of the supply axis that have no product in the grid yet" />
        <SidebarDivider />
        <FilterGroup title="Family" options={facets.families} selected={filters.family_id}
          onChange={onFamily} limit={30} searchable={false} hideZero />
        <FilterGroup title={SUBFAMILY_LABEL} options={facets.subfamilies} selected={filters.subfamily_id}
          onChange={(v) => update({ subfamily_id: v })} limit={8} searchable
          searchPlaceholder={`Filter ${SUBFAMILY_LABEL_PLURAL.toLowerCase()}…`} />
        <FilterGroup title="Industry" options={facets.industries} selected={filters.industry}
          onChange={(v) => update({ industry: v })} limit={8} searchable
          searchPlaceholder="Filter industries…" />
        <FilterGroup title="Function" options={facets.functions} selected={filters.fn}
          onChange={(v) => update({ fn: v })} limit={8} searchable
          searchPlaceholder="Filter functions…" />
      </FilterSidebar>

      <main className="ix-catalogue-main">
        <PageHeader
          title="Product lines"
          subtitle={data ? headline : 'Loading product lines…'}
          meta={data ? [
            count(familyCount, 'family', 'families'),
            `${withReport.toLocaleString('en-GB')} with a market report`,
          ] : []}
        />

        {error && !data && <ErrorState error={error} onRetry={reload} title="Could not load the product lines" />}
        {loading && !data && <LoadingCards count={10} className="ixl-loading-grid" height={150} />}

        {data && rows.length === 0 && (
          <EmptyState title="No product lines match these filters"
            body={filters.q ? `Nothing matches “${filters.q}” with the filters on the left.` : 'Loosen a filter on the left to see more lines.'}
            action={<button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={clearAll}>Clear filters</button>} />
        )}

        <div ref={gridRef} className={`ixl-families${loading && data ? ' is-refreshing' : ''}`}>
          {groups.map((g) => (
            <FamilyGroup key={g.id ?? g.family} group={g} cols={cols} fromSearch={location.search}
              onPickSubfamily={pickSubfamily} />
          ))}
        </div>
      </main>
    </div>
  );
}
