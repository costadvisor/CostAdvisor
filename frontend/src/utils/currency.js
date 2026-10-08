// Currency symbol printed in front of a price: € for EUR, $ for USD, £ for GBP,
// otherwise the ISO code and a space ("CHF 12.30"). The same rule the workspace
// pages (Monitor, Negotiate, Portfolio, BuyWindows, PriorityMatrix) define inline
// as `curSym`; new callers import it from here.
export const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');

// The currency most of these rows are priced in (ties: first seen), or null.
// Monitor and Negotiate pick a team's reporting currency this way.
export const dominantCurrency = (rows) => {
  const counts = {};
  (rows || []).forEach(r => { if (r?.currency) counts[r.currency] = (counts[r.currency] || 0) + 1; });
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
};

// Decimals from magnitude: 1234.56 → 0, 89.5 → 2, 0.42 → 4. A $3/kg price keeps
// its cents and a €300/t price does not carry noise digits.
export const decimalsFor = (magnitude) => {
  const m = Math.abs(magnitude ?? 0);
  if (m >= 100) return 0;
  if (m >= 1) return 2;
  return 4;
};

// Money with the sign in front of the symbol and a fixed en-US grouping, so the
// same number reads the same in every browser locale:
//   fmtMoney(-9.0152, 'EUR', { decimals: 2 })  → "−€9.02"
//   fmtMoney(323115.36, 'EUR')                  → "€323,115"
//   fmtMoney(67922.46, 'EUR', { signed: true }) → "+€67,922"
// `signed` adds "+" to positive values; negatives always get "−" (U+2212).
// A value that rounds to zero gets no sign. Null / non-numeric → "—".
export const fmtMoney = (v, currency, { signed = false, decimals } = {}) => {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const dp = decimals != null ? decimals : decimalsFor(n);
  const abs = Math.abs(n);
  const body = abs.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const zero = Number(abs.toFixed(dp)) === 0;
  const sign = zero ? '' : n < 0 ? '−' : signed ? '+' : '';
  return `${sign}${curSym(currency)}${body}`;
};

// Gap-style money: always signed ("+€48", "−€9.02").
export const fmtSignedMoney = (v, currency, opts = {}) => fmtMoney(v, currency, { ...opts, signed: true });

// What "Exposure" means wherever GET /api/portfolio/summary is shown (Dashboard,
// Monitor, Forecast): the latest gap per unit times every quarter of volume on
// record. A brief's "Total financial impact" (POST /api/costing/brief) sums each
// quarter's own gap × that quarter's volume over the brief's period instead, so
// the two figures differ for the same cost model.
export const EXPOSURE_NOTE = "Exposure = today's gap per unit (latest actual price − today's should-cost) × all volume on record for the cost model; the per-unit gap when no volume is on record. A negotiation brief's total financial impact is a different figure: each quarter's own gap × that quarter's volume, summed over the brief's period.";
