import { useState, useMemo, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';

/**
 * Searchable index picker.
 *
 * WHY THIS EXISTS — the index selector was a plain `<select>` over the whole
 * catalog (200+ entries), listing bare feed codes in alphabetical order: 2EH, AA,
 * ACN, AL, AROM, then "Aluminium" and "Aluminum" as separate neighbouring rows.
 * Two problems compounded:
 *
 * 1. No way to search. Finding one index meant scrolling a 200-row native dropdown.
 * 2. Nothing distinguished near-identical entries. The catalog carries the same
 *    commodity for several regions, and the name alone doesn't say which — so
 *    picking the right one was guesswork.
 *
 * This gives typeahead filtering plus, on every row, the regions that index
 * actually holds values for (`regions`, attached by GET /api/indexes) alongside
 * its unit and family. Interaction model matches the commodity autocomplete in
 * AddIndexModal: ↑/↓ to move, Enter to pick, Escape to clear, mouseDown to select.
 *
 * `regions` is informational, not part of the selection: a variable binds to a
 * commodity, and the resolver picks the region at evaluation time (team override →
 * requested region → GLOBAL → any). The tag tells the user what coverage exists.
 */
export default function IndexCombo({
  value,                 // selected commodity id (number) or null
  region = null,         // pinned region of the current selection (byRegion mode)
  onChange,              // (id|null, region|null) => void
  commodities = [],
  /* byRegion: offer one entry PER REGION the index covers, so "Iron · Europe" and
   * "Iron · GLOBAL" are separately selectable. Only correct where the consumer can
   * actually store the region — composite index variables can (the spec carries an
   * optional `region`). Cost-model and formula-template variables cannot: their
   * region comes from the model or the coverage row, so a per-region choice there
   * would be silently ignored, and they use the default single-entry mode. */
  byRegion = false,
  placeholder = 'Search indexes…',
  emptyLabel = 'Select index…',
  style,
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const [rect, setRect] = useState(null);   // viewport coords for the fixed dropdown
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  /* The dropdown is `position: fixed`, not `absolute`.
   * These combos live inside `.ca-modal`, which is `overflow-y: auto` — an
   * absolutely-positioned list would be clipped by that scroll container the
   * moment it extended past the modal edge. Fixed coords escape it, at the cost of
   * having to keep them in sync with the anchor.
   *
   * Measured on EVERY render while open, not once on open. A single snapshot went
   * stale as soon as the layout moved underneath it — filtering changes the list
   * height, picking a value adds region tags that shift the row — which put the
   * list 76px over its own input in one case and 1,500px off-screen in another. */
  const measure = () => {
    const el = inputRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vh = window.innerHeight;
    const below = vh - r.bottom;
    const above = r.top;
    const MAX_H = 260;
    const MIN_H = 120;
    // Flip only when below is genuinely too tight AND above is roomier, so the
    // list doesn't jump sides on small layout shifts.
    const flip = below < MIN_H && above > below;
    const space = (flip ? above : below) - 12;
    const next = {
      left: Math.max(8, Math.min(r.left, window.innerWidth - 8 - Math.max(r.width, 260))),
      width: Math.max(r.width, 260),
      flip,
      // Clamp into the viewport so a bad anchor can never park the list off-screen.
      top: flip ? null : Math.max(8, Math.min(r.bottom + 4, vh - MIN_H)),
      bottom: flip ? Math.max(8, vh - r.top + 4) : null,
      maxHeight: Math.max(MIN_H, Math.min(MAX_H, space)),
    };
    // Only commit real changes — this runs in a layout effect on every render, so
    // an unconditional setState would loop.
    setRect(prev => {
      if (prev
        && prev.left === next.left && prev.width === next.width
        && prev.top === next.top && prev.bottom === next.bottom
        && prev.maxHeight === next.maxHeight && prev.flip === next.flip) return prev;
      return next;
    });
  };

  useLayoutEffect(() => {
    if (!open) { setRect(null); return; }
    measure();
  });

  useEffect(() => {
    if (!open) return;
    const onMove = () => measure();
    // `true` — catch scrolls on the modal's own scroll container, not just the page.
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open]);

  /* One entry per selectable thing.
   *
   * In byRegion mode an index that covers several regions becomes several entries —
   * "Iron · Europe" and "Iron · GLOBAL" — because they are genuinely different
   * series with different values, and collapsing them into one row tagged with both
   * regions left no way to choose between them. Where a commodity covers 2+ regions
   * an explicit "any region" entry is also offered, which stores no pin and keeps
   * the resolver's requested-region → Europe → GLOBAL → any fallback. */
  const entries = useMemo(() => {
    if (!byRegion) {
      return commodities.map(c => ({ key: String(c.id), commodity: c, region: null }));
    }
    const out = [];
    commodities.forEach(c => {
      const regs = c.regions || [];
      if (regs.length === 0) {
        out.push({ key: String(c.id), commodity: c, region: null });
        return;
      }
      regs.forEach(r => out.push({ key: `${c.id}:${r}`, commodity: c, region: r }));
      if (regs.length > 1) out.push({ key: `${c.id}:any`, commodity: c, region: null });
    });
    return out;
  }, [commodities, byRegion]);

  const selected = useMemo(() => entries.find(e =>
    e.commodity.id === value
    && (!byRegion || (e.region || null) === (region || null))) || null,
  [entries, value, region, byRegion]);

  const allMatches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    // Match on name, family, unit and this entry's own region, so "iron europe"
    // lands directly on the pinned Europe entry.
    return entries.filter(e => {
      const c = e.commodity;
      const hay = `${c.name} ${c.category || ''} ${c.unit || ''} ${e.region || (byRegion ? 'any region' : (c.regions || []).join(' '))}`;
      return hay.toLowerCase().includes(q);
    });
  }, [entries, query, byRegion]);

  /* Render a bounded window. The catalog is ~200 indexes and growing; the position
   * of the list is re-measured after each render, and forcing layout over 200 rows
   * on every keystroke is wasted work when nobody scrolls that far. The count of
   * what's hidden is shown, so a truncated list never reads as the whole set. */
  const RENDER_CAP = 50;
  const matches = allMatches.slice(0, RENDER_CAP);
  const hiddenCount = allMatches.length - matches.length;

  // Close on outside click — the dropdown is absolutely positioned, so a stray
  // click elsewhere should dismiss it rather than leave it hanging over the form.
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e) => {
      // The list is portalled to <body>, so it is NOT inside wrapRef — it has to be
      // excluded explicitly, or this handler closes the dropdown on mousedown and
      // unmounts the row before its own onMouseDown can register the selection.
      if (wrapRef.current?.contains(e.target)) return;
      if (listRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    return () => document.removeEventListener('mousedown', onDocDown);
  }, [open]);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    if (!open || highlight < 0) return;
    listRef.current?.children[highlight]?.scrollIntoView({ block: 'nearest' });
  }, [highlight, open]);

  const pick = (entry) => {
    onChange(entry ? entry.commodity.id : null, entry ? entry.region : null);
    setQuery('');
    setOpen(false);
    setHighlight(-1);
  };
  // What the closed field reads as — the name plus its pinned region, so the
  // distinction survives after the dropdown shuts.
  const selectedLabel = selected
    ? (byRegion && selected.region ? `${selected.commodity.name} · ${selected.region}` : selected.commodity.name)
    : '';

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setHighlight(i => Math.min(i + 1, matches.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight(i => Math.max(i - 1, -1));
    } else if (e.key === 'Enter') {
      if (open && highlight >= 0 && highlight < matches.length) {
        e.preventDefault();
        pick(matches[highlight]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (open) { setOpen(false); setHighlight(-1); } else { pick(null); }
    }
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative', ...style }}>
      <input
        ref={inputRef}
        className="ca-input"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-label="Search and select an index"
        // While closed, the field reads as the current selection; typing switches
        // it into search mode without destroying that selection until a new pick.
        value={open ? query : selectedLabel}
        placeholder={selectedLabel || emptyLabel}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlight(-1); }}
        onFocus={() => { setQuery(''); setOpen(true); }}
        onKeyDown={onKeyDown}
        autoComplete="off"
        style={{ cursor: 'text' }}
      />

      {/* Coverage note for the current selection. In byRegion mode the pinned region
          is already in the field text, so this only explains the unpinned case. */}
      {!open && selected && byRegion && !selected.region && (selected.commodity.regions || []).length > 1 && (
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3 }}>
          Any region — follows the requested region, then falls back
        </div>
      )}
      {!open && selected && !byRegion && (selected.commodity.regions || []).length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
          {selected.commodity.regions.map(r => <RegionTag key={r} region={r} />)}
        </div>
      )}

      {/* Portalled to <body>.
          `position: fixed` alone was not enough: `.ca-page` carries `.ca-fade-in`,
          whose keyframes leave `transform: translateY(8px)` on the element, and a
          transformed ancestor becomes the containing block for fixed descendants.
          The list was resolving its coordinates against that div instead of the
          viewport and landing ~1,500px off-screen. A portal has no such ancestor. */}
      {open && rect && createPortal((
        <div
          ref={listRef}
          role="listbox"
          style={{
            position: 'fixed',
            left: rect.left, width: rect.width,
            ...(rect.flip ? { bottom: rect.bottom } : { top: rect.top }),
            zIndex: 300,   // above .ca-modal (200)
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-popover)',
            maxHeight: rect.maxHeight, overflowY: 'auto',
          }}
        >
          {matches.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--muted)' }}>
              No index matches “{query}”.
            </div>
          ) : matches.map((e, i) => {
            const c = e.commodity;
            const isSel = selected && selected.key === e.key;
            return (
              <div
                key={e.key}
                role="option"
                aria-selected={!!isSel}
                onMouseDown={() => pick(e)}
                onMouseEnter={() => setHighlight(i)}
                style={{
                  padding: '8px 10px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: highlight === i ? 'var(--bg)' : isSel ? 'var(--accent-dim)' : '',
                  borderBottom: i < matches.length - 1 ? '1px solid var(--border-light)' : 'none',
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', minWidth: 0, flex: '1 1 auto' }}>
                  {c.name}
                  {c.unit && <span style={{ fontWeight: 400, color: 'var(--muted)', fontSize: 10 }}> {c.unit}</span>}
                </span>
                <span style={{ display: 'flex', gap: 3, flexShrink: 0, alignItems: 'center' }}>
                  {byRegion
                    ? (e.region
                        ? <RegionTag region={e.region} />
                        : ((c.regions || []).length > 1
                            ? <RegionTag region="any region" muted />
                            : <RegionTag region="no data" muted />))
                    : ((c.regions || []).length > 0
                        ? c.regions.slice(0, 3).map(r => <RegionTag key={r} region={r} />)
                        : <RegionTag region="no data" muted />)}
                  {!byRegion && (c.regions || []).length > 3 && (
                    <span style={{ fontSize: 9, color: 'var(--muted)' }}>+{c.regions.length - 3}</span>
                  )}
                </span>
              </div>
            );
          })}
          {hiddenCount > 0 && (
            <div style={{
              padding: '7px 10px', fontSize: 10, color: 'var(--muted)',
              borderTop: '1px solid var(--border)', background: 'var(--neutral-bg-soft)',
              position: 'sticky', bottom: 0,
            }}>
              {hiddenCount} more — keep typing to narrow
            </div>
          )}
        </div>
      ), document.body)}
    </div>
  );
}

/* Region chip. Deliberately quiet — it's a disambiguator sitting next to the name,
   not a status signal, so it must not compete with the index name it qualifies. */
function RegionTag({ region, muted = false }) {
  return (
    <span
      title={muted ? 'No values loaded for this index yet' : `Has values for ${region}`}
      style={{
        fontSize: 9, lineHeight: 1.6, padding: '0 5px', borderRadius: 3,
        whiteSpace: 'nowrap',
        background: muted ? 'var(--neutral-bg)' : 'var(--info-bg)',
        color: muted ? 'var(--muted)' : 'var(--accent4)',
        border: '1px solid transparent',
      }}
    >
      {region}
    </span>
  );
}
