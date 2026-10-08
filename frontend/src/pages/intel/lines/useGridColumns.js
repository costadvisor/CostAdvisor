import { useLayoutEffect, useState } from 'react';

/* How many `minWidth` columns (with `gap` between) fit in an element — the
 * same number a `repeat(auto-fill, minmax(minWidth, 1fr))` grid would make.
 * Lets the sub-family clusters span whole columns of the family grid.
 *
 *   const [ref, cols] = useGridColumns(212, 12);
 *   <div ref={ref}>…</div>
 *
 * `ref` is a callback ref, so an element that mounts later (after data loads)
 * is still measured. */
export default function useGridColumns(minWidth = 212, gap = 12, fallback = 4) {
  const [el, setEl] = useState(null);
  const [cols, setCols] = useState(fallback);
  useLayoutEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = (w) => setCols(Math.max(1, Math.floor((w + gap) / (minWidth + gap))));
    measure(el.getBoundingClientRect().width);
    const ro = new ResizeObserver(([entry]) => measure(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el, minWidth, gap]);
  return [setEl, cols];
}
