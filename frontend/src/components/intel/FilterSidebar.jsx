import { useState, useMemo, useId } from 'react';

/* Sticky filter column for catalogue pages. Pair it with `.ix-catalogue`:
 *
 *   <div className="ix-catalogue">
 *     <FilterSidebar search={q} onSearch={setQ} searchPlaceholder="Search products…"
 *                    activeCount={n} onClear={reset}>
 *       <SidebarItem label="All products" count={483} active onClick={…} />
 *       <SidebarDivider />
 *       <FilterGroup title="Family" options={facets.families.map(f => ({ value: f.name, count: f.count }))}
 *                    selected={families} onChange={setFamilies} />
 *     </FilterSidebar>
 *     <main className="ix-catalogue-main">…</main>
 *   </div>
 */
export function FilterSidebar({
  search, onSearch, searchPlaceholder = 'Search…', searchLabel,
  activeCount = 0, onClear, ariaLabel = 'Filters', children,
}) {
  return (
    <aside className="ix-sidebar" aria-label={ariaLabel}>
      {onSearch && (
        <div className="ix-sidebar-search">
          <input
            type="search"
            className="ix-search"
            value={search ?? ''}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchLabel || searchPlaceholder}
          />
        </div>
      )}
      {onClear && activeCount > 0 && (
        <button type="button" className="ix-sb-clear" onClick={onClear}>
          Clear {activeCount} filter{activeCount === 1 ? '' : 's'}
        </button>
      )}
      {children}
    </aside>
  );
}

/* A single "view" row (All products / My portfolio) — a toggle, not a checkbox. */
export function SidebarItem({ label, count, active = false, onClick, title }) {
  return (
    <button type="button" className={`ix-sb-item${active ? ' active' : ''}`}
      aria-pressed={active} onClick={onClick} title={title}>
      <span>{label}</span>
      {count != null && <span className="ix-count">{count}</span>}
    </button>
  );
}

export function SidebarDivider() {
  return <div className="ix-sb-div" role="separator" />;
}

/* A collapsible checkbox list with counts.
 *
 * options:  [{ value, label?, count? }]   (label defaults to value)
 * selected: array of values;  onChange(nextArray)
 * multi:    false → picking one replaces the selection (click again to clear)
 * limit:    rows shown before "Show all N" (selected rows always stay visible)
 * searchable: a filter box inside the group; defaults on above 15 options
 * hideZero: drop zero-count options (unless selected); "Show all N" then
 *           counts only what can be shown
 * footer:   a note or control under the list (e.g. "Top 60 by product count") */
export function FilterGroup({
  title, options = [], selected = [], onChange, multi = true,
  collapsible = true, defaultOpen = true, limit = 8, searchable,
  searchPlaceholder, hideZero = false, footer,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [expanded, setExpanded] = useState(false);
  const [q, setQ] = useState('');
  const bodyId = useId();
  const sel = useMemo(() => new Set(selected || []), [selected]);

  // Everything the group can show; `hideZero` removes options for good, so
  // the "Show all N (+M)" counts and the search default are taken from this
  // list, never from `options`. Only a count of 0 is zero: an option without
  // a count stays.
  const base = useMemo(
    () => (hideZero
      ? options.filter((o) => o.count == null || Number(o.count) !== 0 || sel.has(o.value))
      : options),
    [options, hideZero, sel],
  );
  const canSearch = searchable ?? base.length > 15;

  const visible = useMemo(() => {
    let list = base;
    const needle = q.trim().toLowerCase();
    if (needle) list = list.filter((o) => String(o.label ?? o.value).toLowerCase().includes(needle));
    if (expanded || needle || list.length <= limit) return list;
    const head = list.slice(0, limit);
    const extraSelected = list.slice(limit).filter((o) => sel.has(o.value));
    return [...head, ...extraSelected];
  }, [base, q, expanded, limit, sel]);

  const hiddenCount = base.length - visible.length;

  const toggle = (value) => {
    if (!onChange) return;
    if (multi) {
      const next = new Set(sel);
      if (next.has(value)) next.delete(value); else next.add(value);
      onChange(Array.from(next));
    } else {
      onChange(sel.has(value) ? [] : [value]);
    }
  };

  const head = (
    <>
      <span>{title}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        {sel.size > 0 && <span className="ix-fgroup-active">{sel.size} selected</span>}
        {collapsible && <span className="ix-chev" aria-hidden>▾</span>}
      </span>
    </>
  );

  return (
    <div className="ix-fgroup" role="group" aria-label={title}>
      {collapsible ? (
        <button type="button" className="ix-fgroup-head" aria-expanded={open} aria-controls={bodyId}
          onClick={() => setOpen((o) => !o)}>
          {head}
        </button>
      ) : (
        <div className="ix-fgroup-head" style={{ cursor: 'default' }}>{head}</div>
      )}
      {open && (
        <div className="ix-fgroup-body" id={bodyId}>
          {canSearch && (
            <div className="ix-fgroup-search">
              <input type="search" className="ix-search" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder={searchPlaceholder || `Filter ${String(title).toLowerCase()}…`}
                aria-label={`Filter ${title}`} />
            </div>
          )}
          {visible.map((o) => {
            const checked = sel.has(o.value);
            return (
              <label key={o.value}
                className={`ix-fitem${checked ? ' checked' : ''}${o.count === 0 && !checked ? ' zero' : ''}`}>
                <input type="checkbox" checked={checked} onChange={() => toggle(o.value)} />
                <span className="ix-fitem-label">{o.label ?? o.value}</span>
                {o.count != null && <span className="ix-count">{o.count}</span>}
              </label>
            );
          })}
          {visible.length === 0 && (
            <div className="ix-fitem ix-muted" style={{ cursor: 'default' }}>No matches</div>
          )}
          {!q && base.length > limit && (
            <button type="button" className="ix-fgroup-more" onClick={() => setExpanded((e) => !e)}>
              {expanded ? 'Show fewer' : `Show all ${base.length}`}
              {!expanded && hiddenCount > 0 ? ` (+${hiddenCount})` : ''}
            </button>
          )}
          {footer && <div className="ix-fgroup-foot">{footer}</div>}
        </div>
      )}
    </div>
  );
}

export default FilterSidebar;
