import { REGION_COLORS, REGION_VARS, GEMSTONES, gemstoneColor } from '../../../components/intel';

/* Helpers shared by the Product Intelligence tab (tab 2 of the product page). */

/* Authored content carries the mockup's light-theme hex colours (functionality
 * dots). Most of them are the region or gemstone palette;
 * those resolve to the theme-aware CSS variables so a navy dot does not vanish
 * on the dark theme. Anything else is used as authored. */
const HEX_TO_VAR = {};
Object.entries(REGION_COLORS).forEach(([code, hex]) => { HEX_TO_VAR[hex.toUpperCase()] = REGION_VARS[code]; });
GEMSTONES.forEach((g) => {
  const k = g.hex.toUpperCase();
  if (!HEX_TO_VAR[k]) HEX_TO_VAR[k] = gemstoneColor(g.code);
});

export function themeColor(hex, fallback = 'var(--accent4)') {
  if (!hex || typeof hex !== 'string') return fallback;
  return HEX_TO_VAR[hex.trim().toUpperCase()] || hex;
}

export { productHref, lineHref, industryHref, supplierHref, strategyHref } from '../../../components/intel';

/* The line page's Report tab. `report` picks one report when a line has several. */
export const reportHref = (lineId, slug) => {
  const q = new URLSearchParams({ tab: 'report' });
  if (slug) q.set('report', slug);
  return `/intelligence/lines/${encodeURIComponent(lineId)}?${q.toString()}`;
};

/* Authored text marks emphasis with **bold** and nothing else. */
export function withBold(text) {
  if (!text) return null;
  const parts = String(text).split('**');
  if (parts.length < 3) return text;
  return parts.map((part, i) => (i % 2 === 1 ? <strong key={i}>{part}</strong> : part));
}

/* "2026-08-01" / "2026-08" → "Aug 2026". */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function monthYear(v) {
  const m = String(v || '').match(/^(\d{4})-(\d{1,2})/);
  if (!m) return null;
  return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

export const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : s);
