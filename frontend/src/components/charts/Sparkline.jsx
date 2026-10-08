import { useId } from 'react';
import { useElementWidth, svgId } from './util';

/* Tiny trend line for catalogue cards.
 *
 *   <Sparkline data={item.sparkline} />                 // fills its container's width (observed)
 *   <Sparkline data={levels} width={200} height={36} baseline={100} />  // fixed: no observer — use on cards
 *
 * Colour follows the trend (first → last): up = --accent2, down = --accent,
 * flat = --muted — the app's index colouring. Override with `color`, or pass
 * `trend` ('up'|'down'|'flat' or a % change) when the card's badge is computed
 * on a different window than the points shown.
 *
 * minRange: the y-axis always spans at least this many units (centred on the
 * data), so a near-flat series draws flat instead of scaling noise up to the
 * full height. The default 4 suits index levels (±2 points around base-100
 * data); pass 0 to scale to the data's own min/max, or a unit-appropriate
 * span for other values. */
export default function Sparkline({
  data = [], width, height = 36, color, trend, threshold = 0.5, area = true, dot = true,
  baseline = null, strokeWidth = 1.6, label, minRange = 4,
}) {
  const uid = useId();
  // Pass `width` on catalogue cards: a fixed width skips the ResizeObserver.
  const [ref, measured] = useElementWidth(120, width == null);
  const W = width ?? measured;
  const vals = (data || []).map((v) => (v == null ? null : Number(v)));
  const pts = vals.map((v, i) => ({ v, i })).filter((p) => p.v != null && Number.isFinite(p.v));
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true };
  const box = { width: width ?? '100%', height, display: 'block' };

  if (pts.length < 2) {
    return <div ref={ref} style={box} {...a11y} />;
  }

  const first = pts[0].v;
  const last = pts[pts.length - 1].v;
  let dir;
  if (typeof trend === 'string') dir = trend;
  else {
    const change = typeof trend === 'number' ? trend : (first ? (last / first - 1) * 100 : 0);
    dir = change > threshold ? 'up' : change < -threshold ? 'down' : 'flat';
  }
  const stroke = color || (dir === 'up' ? 'var(--accent2)' : dir === 'down' ? 'var(--accent)' : 'var(--muted)');

  const all = pts.map((p) => p.v);
  if (baseline != null) all.push(baseline);
  let min = Math.min(...all);
  let max = Math.max(...all);
  const floor = Number(minRange) || 0;
  if (floor > 0 && max - min < floor) {
    const mid = (min + max) / 2;
    min = mid - floor / 2;
    max = mid + floor / 2;
  }
  const pad = (max - min || Math.abs(max) * 0.02 || 1) * 0.12;
  min -= pad; max += pad;
  const n = vals.length;
  const x = (i) => 1.5 + (i / (n - 1)) * (W - 5);
  const y = (v) => 2 + (1 - (v - min) / (max - min)) * (height - 4);
  const line = pts.map((p, j) => `${j ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
  const fill = `${line}L${x(pts[pts.length - 1].i).toFixed(1)},${height}L${x(pts[0].i).toFixed(1)},${height}Z`;
  const gid = svgId(uid, 'g');
  const lp = pts[pts.length - 1];

  return (
    <div ref={ref} style={box} {...a11y}>
      <svg width={W} height={height} style={{ display: 'block', overflow: 'visible' }}>
        {area && (
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.22} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
        )}
        {baseline != null && (
          <line x1={0} x2={W} y1={y(baseline)} y2={y(baseline)} stroke="var(--chart-ref-line)" strokeDasharray="2 3" />
        )}
        {area && <path d={fill} fill={`url(#${gid})`} stroke="none" />}
        <path d={line} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
        {dot && <circle cx={x(lp.i)} cy={y(lp.v)} r={2.4} fill={stroke} />}
      </svg>
    </div>
  );
}
