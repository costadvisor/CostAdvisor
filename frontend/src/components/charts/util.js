import { useRef, useState, useLayoutEffect } from 'react';

/* Shared helpers for the hand-rolled SVG charts.
 *
 * THEME RULE (same as pages/workspace/wsCharts.jsx): every colour is a CSS
 * variable so all four themes apply. The only palette colours come from
 * components/intel/palette.js, which resolves to theme-aware variables. */

export const AXIS = { fill: 'var(--muted)', fontSize: 9.5, fontFamily: "'JetBrains Mono', monospace" };

/* Width of a container, tracked with ResizeObserver, so SVG text renders at
 * its real pixel size instead of scaling with a viewBox. */
export function useElementWidth(fallback = 640, enabled = true) {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  // `enabled: false` skips measuring entirely — a fixed-width chart repeated
  // hundreds of times (catalogue sparklines) should not cost one observer each.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return undefined;
    const measure = () => setWidth(Math.round(el.getBoundingClientRect().width));
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled]);
  return [ref, width > 0 ? width : fallback];
}

export function niceStep(span, target = 5) {
  if (!(span > 0)) return 1;
  const rough = span / target;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / mag;
  const step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
  return step * mag;
}

/* Round-numbered ticks covering [min, max]. */
export function niceTicks(min, max, target = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) { min -= 1; max += 1; }
  const step = niceStep(max - min, target);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Math.round(v * 1e6) / 1e6);
  }
  return out;
}

export function tickLabel(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 1000) return `${(v / 1e3).toFixed(1)}k`;
  if (Number.isInteger(v)) return String(v);
  return a < 10 ? v.toFixed(2).replace(/0$/, '') : v.toFixed(1);
}

export const monthKey = (year, month) => Number(year) * 12 + (Number(month) - 1);
export const keyYear = (k) => Math.floor(k / 12);
export const keyMonth = (k) => (k % 12) + 1;

/* Push labels apart vertically so none overlap (y sorted ascending). */
export function spreadLabels(items, gap = 11, top = 0, bottom = Infinity) {
  const sorted = [...items].sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].y - sorted[i - 1].y < gap) sorted[i].y = sorted[i - 1].y + gap;
  }
  const overflow = sorted.length ? sorted[sorted.length - 1].y - bottom : 0;
  if (overflow > 0) sorted.forEach((s) => { s.y -= overflow; });
  for (let i = sorted.length - 2; i >= 0; i -= 1) {
    if (sorted[i + 1].y - sorted[i].y < gap) sorted[i].y = sorted[i + 1].y - gap;
  }
  sorted.forEach((s) => { if (s.y < top) s.y = top; });
  return sorted;
}

/* SVG-safe id from React's useId (which contains colons). */
export const svgId = (id, suffix = '') => `ix${String(id).replace(/[^a-zA-Z0-9_-]/g, '')}${suffix}`;

/* Position an HTML tooltip inside a chart box, flipping near the right edge. */
export function tipPosition(x, width, tipWidth = 200, offset = 14) {
  return x + offset + tipWidth > width ? Math.max(0, x - offset - tipWidth) : x + offset;
}
