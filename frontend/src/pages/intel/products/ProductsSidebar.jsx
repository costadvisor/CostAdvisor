import { useMemo } from 'react';
import SupplierGroup from './SupplierGroup';
import {
  FilterSidebar, FilterGroup, SidebarItem, SidebarDivider, RegionDot, ErrorState, SkeletonLines,
  STATUS_CODES, STATUS_LABELS, DEFAULT_STATUSES, isDefaultStatus,
} from '../../../components/intel';
// Trend = the card's move since January 2023 (the badge), ±2 points.
const TREND_LABELS = { up: 'Up >2%', flat: 'Flat ±2%', down: 'Down >2%' };

/* A FilterGroup bound to one URL key. List keys take any number of values
 * (OR within the group); `single` keys hold one, drawn as radios. A value that
 * arrived via the URL but is not among the options is appended, so its
 * "selected" state stays visible and can be unticked. Options at 0 under the
 * other filters are hidden unless ticked (`hideZero`), as on the Lines page. */
function KeyGroup({ title, name, value, options, onPick, single = false, ...rest }) {
  const selected = useMemo(() => {
    if (single) return value ? [value] : [];
    return value || [];
  }, [single, value]);
  const opts = useMemo(() => {
    const known = new Set(options.map((o) => o.value));
    const extra = selected.filter((v) => !known.has(v)).map((v) => ({ value: v }));
    return extra.length ? [...options, ...extra] : options;
  }, [options, selected]);
  return (
    <FilterGroup
      title={title}
      options={opts}
      selected={selected}
      multi={!single}
      onChange={(next) => onPick(name, single ? (next[0] ?? null) : next)}
      {...rest}
    />
  );
}

/* Supply status: four checkboxes, opening on Verified makers + Concentrated
 * supply. The counts are product cards under the other filters; the badge
 * itself never carries a number. At least one box stays ticked. */
function StatusGroup({ facets, statuses, onStatuses }) {
  const byCode = new Map((facets?.statuses || []).map((x) => [x.code, x]));
  const options = STATUS_CODES.map((code) => {
    const x = byCode.get(code);
    return {
      value: code,
      label: (
        <span className="ixp-status-opt">
          <span className={`ixp-status-dot tone-${x?.tone || 'grey'}`} aria-hidden />
          {x?.label || STATUS_LABELS[code]}
        </span>
      ),
      count: x?.count,
    };
  });
  const all = statuses.length === STATUS_CODES.length;
  const footer = all ? (
    <button type="button" className="ix-link ixp-status-link" onClick={() => onStatuses(DEFAULT_STATUSES)}>
      Verified supply only
    </button>
  ) : (
    <button type="button" className="ix-link ixp-status-link" onClick={() => onStatuses(STATUS_CODES)}>
      Show all statuses
    </button>
  );
  return (
    <FilterGroup
      title="Supply status"
      options={options}
      selected={statuses}
      onChange={(next) => { if (next.length) onStatuses(next); }}
      searchable={false}
      limit={4}
      footer={footer}
    />
  );
}

export default function ProductsSidebar({
  facets, facetsError, onRetryFacets, filters, statuses, onStatuses, activeCount, searchText, onSearch,
  onPick, onClear,
}) {
  const f = facets || {};
  const counts = f.counts || {};

  const families = useMemo(() => (f.families || []).map((x) => ({
    value: String(x.id), label: x.name, count: x.count,
  })), [f.families]);
  const industries = useMemo(() => (f.industries || []).map((x) => ({ value: x.name, count: x.count })), [f.industries]);
  const functions = useMemo(() => (f.functions || []).map((x) => ({ value: x.name, count: x.count })), [f.functions]);
  // The API ranks the top 60 by product count; the list reads better by name.
  const suppliers = useMemo(() => (f.suppliers || [])
    .map((x) => ({ value: x.name, count: x.count }))
    .sort((a, b) => a.value.localeCompare(b.value)), [f.suppliers]);
  const regions = useMemo(() => (f.regions || []).map((x) => ({
    value: x.code,
    label: <span className="ixp-region-opt"><RegionDot region={x.code} />{x.name}</span>,
    count: x.count,
  })), [f.regions]);
  const trends = useMemo(() => (f.trends || []).map((x) => ({
    value: x.name, label: TREND_LABELS[x.name] || x.name, count: x.count,
  })), [f.trends]);

  const moreSuppliers = (f.suppliers_total ?? 0) - (f.suppliers?.length ?? 0);

  return (
    <FilterSidebar
      search={searchText}
      onSearch={onSearch}
      searchPlaceholder="Search products…"
      searchLabel="Search products by name, PID, line, family or CAS"
      activeCount={activeCount}
      onClear={onClear}
      ariaLabel="Product filters"
    >
      <div className="ixp-sb-label">View</div>
      <SidebarItem label="Verified supply" count={counts.default_view}
        active={activeCount === 0 && isDefaultStatus(statuses)}
        onClick={onClear} title="Products whose makers are verified, plus concentrated supply" />
      <SidebarItem label="Line has a market report" count={counts.has_report} active={!!filters.has_report}
        onClick={() => onPick('has_report', filters.has_report ? null : true)}
        title="Only products whose product line has a market report" />
      <SidebarDivider />
      <StatusGroup facets={facets} statuses={statuses} onStatuses={onStatuses} />

      {facetsError && (
        <div className="ixp-sb-pad">
          <ErrorState error={facetsError} onRetry={onRetryFacets} title="Filters did not load" />
        </div>
      )}
      {!facets && !facetsError && (
        <div className="ixp-sb-pad"><SkeletonLines lines={8} /></div>
      )}

      {facets && (
        <>
          <KeyGroup title="Family" name="family_id" value={filters.family_id} options={families}
            onPick={onPick} limit={40} searchable={false} hideZero />
          <KeyGroup title="Industry" name="industry" value={filters.industry} options={industries}
            onPick={onPick} limit={8} searchPlaceholder="Filter industries…" hideZero />
          <KeyGroup title="Function" name="fn" value={filters.fn} options={functions}
            onPick={onPick} limit={8} searchPlaceholder="Filter functions…" hideZero />
          <SupplierGroup options={suppliers} values={filters.supplier}
            onPick={(v) => onPick('supplier', v)} />
          {moreSuppliers > 0 && (
            <div className="ixp-sb-note">
              Listed: the producers counted as makers on the most products. The search finds the others.
            </div>
          )}
          <KeyGroup title="Region" name="region" value={filters.region} options={regions}
            onPick={onPick} limit={10} searchable={false} hideZero />
          <KeyGroup title="Trend" name="trend" value={filters.trend} options={trends}
            onPick={onPick} searchable={false} single />
        </>
      )}
    </FilterSidebar>
  );
}
