/**
 * Display formatters for the Intelligence/Strategy pages. Every one returns
 * '—' for a missing value rather than "NaN" or "0" — an absent number is not
 * a zero.
 */

const DASH = '—';
const MINUS = '−';
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const MONTH_INITIALS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

const isNum = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));

/** Signed percentage: +4.6% / −9.8% / 0.0%. `v` is already in percent units. */
export function pct(v, { dp = 1, sign = true } = {}) {
  if (!isNum(v)) return DASH;
  const n = Number(v);
  const abs = Math.abs(n).toFixed(dp);
  if (Number(abs) === 0) return `${(0).toFixed(dp)}%`;
  if (n < 0) return `${MINUS}${abs}%`;
  return `${sign ? '+' : ''}${abs}%`;
}

/** Plain percentage with no sign, for a cost-line weight: 32%. Never a
 *  supplier's market share: those are not shown (house rule 6). */
export function share(v, { dp = 0 } = {}) {
  if (!isNum(v)) return DASH;
  return `${Number(v).toFixed(dp)}%`;
}

/** Index level, base 100, one decimal: 101.5 */
export function index(v, dp = 1) {
  if (!isNum(v)) return DASH;
  return Number(v).toFixed(dp);
}

/** Signed plain number (weighted impact in points): +1.5 / −3.4 */
export function signed(v, dp = 1) {
  if (!isNum(v)) return DASH;
  const n = Number(v);
  const abs = Math.abs(n).toFixed(dp);
  if (Number(abs) === 0) return (0).toFixed(dp);
  return `${n < 0 ? MINUS : '+'}${abs}`;
}

export function num(v, dp = 0) {
  if (!isNum(v)) return DASH;
  return Number(v).toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** Money with its currency: €2,380,000 · $1.2M (compact). */
export function money(v, currency = 'EUR', { dp = 0, compact = false } = {}) {
  if (!isNum(v)) return DASH;
  const n = Number(v);
  try {
    return new Intl.NumberFormat('en-GB', {
      style: 'currency',
      currency: currency || 'EUR',
      notation: compact ? 'compact' : 'standard',
      minimumFractionDigits: compact ? 0 : dp,
      maximumFractionDigits: compact ? 1 : dp,
    }).format(n);
  } catch {
    return `${currency ? `${currency} ` : ''}${num(n, dp)}`;
  }
}

function toYearMonth(a, b) {
  if (a == null) return null;
  if (a instanceof Date) return [a.getFullYear(), a.getMonth() + 1];
  if (typeof a === 'object') return a.year ? [Number(a.year), Number(a.month || 1)] : null;
  if (b != null) return [Number(a), Number(b)];
  const m = String(a).match(/^(\d{4})-(\d{1,2})/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/** "Jun 2026". Accepts (2026, 6), "2026-06", {year, month} or a Date. */
export function month(a, b) {
  const ym = toYearMonth(a, b);
  if (!ym || !ym[1]) return DASH;
  return `${MONTHS[ym[1] - 1]} ${ym[0]}`;
}

/** "Jun 26" — for axes. */
export function monthShort(a, b) {
  const ym = toYearMonth(a, b);
  if (!ym || !ym[1]) return DASH;
  return `${MONTHS[ym[1] - 1]} ${String(ym[0]).slice(-2)}`;
}

/** ISO date → "2026-07-22" (the strategy tables' own format). */
export function date(v) {
  if (!v) return DASH;
  return String(v).slice(0, 10);
}

export function ordinal(v) {
  if (!isNum(v)) return DASH;
  const n = Math.round(Number(v));
  const s = ['th', 'st', 'nd', 'rd'];
  const r = n % 100;
  return `${n}${s[(r - 20) % 10] || s[r] || s[0]}`;
}

/** Trend direction from a percentage change. */
export function trend(v, threshold = 0.5) {
  if (!isNum(v)) return 'flat';
  const n = Number(v);
  if (n > threshold) return 'up';
  if (n < -threshold) return 'down';
  return 'flat';
}

export function plural(n, one, many = `${one}s`) {
  return `${num(n)} ${Number(n) === 1 ? one : many}`;
}

const fmt = {
  pct, share, index, signed, num, money, month, monthShort, date, ordinal, trend, plural,
  MONTHS, MONTH_INITIALS,
};
export default fmt;
