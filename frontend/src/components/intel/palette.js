/**
 * The three fixed palettes the Intelligence/Strategy mockups define: regions,
 * gemstone lever categories and Kraljic quadrants.
 *
 * Hex values are the mockup's own and read well on the light themes. For
 * RENDERING use the theme-aware helpers (`regionColor`, `gemstoneColor`,
 * `REGION_VARS`): they resolve to CSS variables declared in styles/intel.css,
 * which swap to lightened twins on the dark themes (the mockup's navy NA and
 * charcoal APAC are invisible on Mint's near-black background).
 */

/* ── Regions ───────────────────────────────────────────────────────── */
// Drop codes, in the order the product page lists them.
export const REGION_CODES = ['EU', 'NA', 'CN', 'APAC', 'IN', 'MEA', 'LA', 'GLOBAL'];

export const REGION_COLORS = {
  EU: '#0F6E56',
  CN: '#A53030',
  NA: '#1B2B4B',
  APAC: '#6B6560',
  IN: '#0B6E6E',
  MEA: '#BA7517',
  LA: '#534AB7',
  GLOBAL: '#7B8794',
};

export const REGION_VARS = Object.fromEntries(
  Object.entries(REGION_COLORS).map(([code, hex]) => [code, `var(--rg-${code}, ${hex})`]),
);

// Display names match the API's region objects (`name` in /api/intel/facets),
// so a region reads the same in chips, filters and panels.
export const REGION_LABELS = {
  EU: 'Europe',
  CN: 'China',
  NA: 'North America',
  APAC: 'Asia Pacific',
  IN: 'India',
  MEA: 'Middle East & Africa',
  LA: 'Latin America',
  GLOBAL: 'Global',
};

// The app stores its own region codes (catalog_loader REGION_MAP); APIs return
// the drop code as `region`, but accept either here so a stray `app_region`
// still gets the right colour.
const APP_TO_DROP = {
  EUROPE: 'EU', EU: 'EU',
  CHINA: 'CN', CN: 'CN',
  NA: 'NA', 'NORTH AMERICA': 'NA', US: 'NA',
  APAC: 'APAC', ASIA: 'APAC',
  INDIA: 'IN', IN: 'IN',
  MEA: 'MEA',
  LATAM: 'LA', LA: 'LA',
  GLOBAL: 'GLOBAL', GL: 'GLOBAL',
};

export function normalizeRegion(code) {
  if (!code) return null;
  return APP_TO_DROP[String(code).trim().toUpperCase()] || String(code).toUpperCase();
}

export function regionColor(code) {
  return REGION_VARS[normalizeRegion(code)] || 'var(--muted)';
}

export function sortRegions(codes = []) {
  const rank = (c) => {
    const i = REGION_CODES.indexOf(normalizeRegion(c));
    return i < 0 ? 99 : i;
  };
  return [...codes].sort((a, b) => rank(a) - rank(b));
}

/* ── Gemstones (lever categories), fixed order ─────────────────────── */
export const GEMSTONES = [
  { code: 'VC', name: 'Volume Concentration', hex: '#0F6E56' },
  { code: 'PC', name: 'Pricing & Conditions', hex: '#0B6E6E' },
  { code: 'GS', name: 'Global Sourcing', hex: '#146B8C' },
  { code: 'CM', name: 'Category Management', hex: '#3D6B99' },
  { code: 'PS', name: 'Product Spec Improvement', hex: '#BA7517' },
  { code: 'JP', name: 'Joint Process Improvement', hex: '#A0522D' },
  { code: 'RR', name: 'Relationship Restructuring', hex: '#534AB7' },
  { code: 'DF', name: 'Differentiation', hex: '#8C3A6B' },
];

const GEM_INDEX = new Map();
GEMSTONES.forEach((g) => {
  GEM_INDEX.set(g.code.toLowerCase(), g);
  GEM_INDEX.set(g.name.toLowerCase(), g);
});

/** Look a gemstone up by code ("VC") or display name ("Volume Concentration"). */
export function gemstone(codeOrName) {
  if (!codeOrName) return null;
  if (typeof codeOrName === 'object') return gemstone(codeOrName.code || codeOrName.name);
  return GEM_INDEX.get(String(codeOrName).trim().toLowerCase()) || null;
}

export function gemstoneColor(codeOrName) {
  const g = gemstone(codeOrName);
  return g ? `var(--gem-${g.code}, ${g.hex})` : 'var(--muted)';
}

/* ── Kraljic quadrants ─────────────────────────────────────────────── */
// Coordinates are the delivered reports' own SVG space: viewBox 0 0 260 260,
// quadrants on x 30–250 / y 10–230, split at (140, 120). x = supply
// complexity (right = harder), y = business impact (UP = higher, so a SMALLER
// cy is more impact).
export const KRALJIC_SPACE = { x0: 30, x1: 250, y0: 10, y1: 230, midX: 140, midY: 120 };

export const KRALJIC_QUADRANTS = {
  leverage: { label: 'Leverage' },
  strategic: { label: 'Strategic' },
  bottleneck: { label: 'Bottleneck' },
  noncritical: { label: 'Non-critical' },
};

export function normalizeKraljicBadge(badge) {
  if (!badge) return null;
  const b = String(badge).toLowerCase().replace(/[^a-z]/g, '');
  if (b.startsWith('lev')) return 'leverage';
  if (b.startsWith('strat')) return 'strategic';
  if (b.startsWith('bottle')) return 'bottleneck';
  if (b.startsWith('non') || b.startsWith('routine')) return 'noncritical';
  return null;
}

export function kraljicQuadrant(cx, cy) {
  if (cx == null || cy == null) return null;
  const { midX, midY } = KRALJIC_SPACE;
  if (cy <= midY) return cx >= midX ? 'strategic' : 'leverage';
  return cx >= midX ? 'bottleneck' : 'noncritical';
}

/* ── Categorical fallback for anything else (cost lines, suppliers) ── */
export const SERIES_PALETTE = [
  'var(--pie-1)', 'var(--pie-2)', 'var(--pie-3)', 'var(--pie-4)', 'var(--pie-5)',
  'var(--pie-6)', 'var(--pie-7)', 'var(--pie-8)', 'var(--pie-9)',
];

export function seriesColor(i) {
  return SERIES_PALETTE[((i % SERIES_PALETTE.length) + SERIES_PALETTE.length) % SERIES_PALETTE.length];
}
