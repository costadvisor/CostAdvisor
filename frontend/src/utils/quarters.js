export function qLabel(y, q) { return `Q${q}-${String(y).slice(-2)}`; }

/** The quarter "today" falls in — the sensible default for any period picker. */
export function currentQuarter() {
  const now = new Date();
  return { year: now.getFullYear(), quarter: Math.floor(now.getMonth() / 3) + 1 };
}

/** First day of a quarter, as YYYY-MM-DD — for charting quarterly series on a date axis. */
export function quarterStartDate(y, q) {
  return `${y}-${String((q - 1) * 3 + 1).padStart(2, '0')}-01`;
}

/** Sort/compare key so quarters order correctly across year boundaries. */
export function quarterKey(y, q) { return y * 4 + q; }

// Range is relative to the current year, not hardcoded: the old fixed 2020–2027
// window would have silently stopped offering quarters after Q4-2027.
export function generateQuarterOptions(
  startYear = currentQuarter().year - 6,
  endYear = currentQuarter().year + 1,
) {
  const opts = [];
  for (let y = startYear; y <= endYear; y++) {
    for (let q = 1; q <= 4; q++) {
      opts.push({ year: y, quarter: q, label: qLabel(y, q) });
    }
  }
  return opts;
}

export const QUARTER_OPTS = generateQuarterOptions();
