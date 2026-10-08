/* One quarter format across the page, the API's own: "Q2 2026". Accepts an
 * evolution point ({year, quarter}) or a period code ("2026Q2"). */
export function quarterLabel(p) {
  if (p && typeof p === 'object') return p.year && p.quarter ? `Q${p.quarter} ${p.year}` : '—';
  const m = String(p || '').match(/^(\d{4})Q(\d)$/);
  return m ? `Q${m[2]} ${m[1]}` : (p || '—');
}
