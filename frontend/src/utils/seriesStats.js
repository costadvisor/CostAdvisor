/**
 * Deterministic statistics over a slice of a time series — no LLM, no deps.
 * Used by the Index Library drill-in to summarise a two-point selection (or the
 * full visible window) of a chart.
 *
 * points: ascending array of { label, value, date? }
 *   - date present (FX daily)  → span/annualisation uses calendar days
 *   - date absent (quarterly)  → span/annualisation uses quarters (4 = 1yr)
 *
 * Returns null when there are fewer than 2 valued points.
 */
export function computeStats(points) {
  const pts = (points || []).filter((p) => p && p.value != null && Number.isFinite(Number(p.value)));
  if (pts.length < 2) return null;

  const vals = pts.map((p) => Number(p.value));
  const first = pts[0];
  const last = pts[pts.length - 1];
  const start = Number(first.value);
  const end = Number(last.value);

  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;

  const change = end - start;
  const changePct = start !== 0 ? (end / start - 1) * 100 : null;

  // Annualised growth (CAGR). Year fraction from dates when available, else quarters/4.
  let years;
  if (first.date && last.date) {
    years = (new Date(last.date) - new Date(first.date)) / (365.25 * 86400000);
  } else {
    years = (pts.length - 1) / 4; // one step = one quarter
  }
  let cagrPct = null;
  if (years > 0 && start > 0 && end > 0) {
    cagrPct = (Math.pow(end / start, 1 / years) - 1) * 100;
  }

  // Volatility = stdev of period-over-period % changes.
  const rets = [];
  for (let i = 1; i < vals.length; i++) {
    if (vals[i - 1] !== 0) rets.push((vals[i] / vals[i - 1] - 1) * 100);
  }
  let volatilityPct = null;
  if (rets.length > 0) {
    const rMean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const variance = rets.reduce((a, b) => a + (b - rMean) ** 2, 0) / rets.length;
    volatilityPct = Math.sqrt(variance);
  }

  return {
    start, end, change, changePct, cagrPct,
    min, max, mean, volatilityPct,
    n: pts.length,
    startLabel: first.label, endLabel: last.label,
  };
}
