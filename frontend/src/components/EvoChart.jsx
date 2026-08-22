import { PIE_COLORS } from '../utils/constants';

/* `height` sets the viewBox height (default 230, unchanged for existing callers).
 * Because the SVG renders at `width: 100%` with `height: auto`, the viewBox aspect
 * ratio decides the on-screen height — on a 1250px-wide card the default becomes
 * ~327px tall, which is a lot of room to give a chart that has only one flat line
 * to draw. Callers with nothing to compare can pass something shorter. */
export default function EvoChart({ periods, theoretical, actual, refCost, componentSeries, height = 230 }) {
  const W = 880, H = height;
  const PAD = { l: 50, r: 16, t: 16, b: 34 };

  const visibleComps = (componentSeries || []).filter(cs => cs.visible);
  const showBars = visibleComps.length > 0;

  // Collect all values for y-axis range
  const allVals = [...theoretical, ...actual.filter(v => v > 0)];
  if (allVals.length === 0) return null;

  // When showing stacked bars, include 0 so bars start at the bottom
  const minV = showBars ? 0 : Math.min(...allVals) * 0.97;
  const maxV = Math.max(...allVals) * 1.03;
  const N = periods.length;
  if (N < 2) return null;
  // When showing bars, use N slots (with half-slot padding on each side) so first bar doesn't sit on the Y axis
  const plotW = W - PAD.l - PAD.r;
  const xScale = showBars
    ? i => PAD.l + plotW * (i + 0.5) / N
    : i => PAD.l + plotW * i / (N - 1);
  const yScale = v => PAD.t + (H - PAD.t - PAD.b) * (1 - (v - minV) / (maxV - minV || 1));

  const gridLines = [];
  const step = Math.max(1, Math.ceil((maxV - minV) / 6));
  for (let v = Math.ceil(minV / step) * step; v <= maxV; v += step) {
    gridLines.push(v);
  }

  const thePath = theoretical.map((v, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(' ');
  const actPts = actual.map((v, i) => v > 0 ? { x: xScale(i), y: yScale(v), i, v } : null).filter(Boolean);
  const actPath = actPts.length > 1 ? actPts.map((p, j) => `${j === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ') : '';

  let fillPath = '';
  if (actPts.length > 1 && !showBars) {
    const fwd = actPts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`);
    const bwd = [...actPts].reverse().map(p => `${p.x.toFixed(1)},${yScale(theoretical[p.i]).toFixed(1)}`);
    fillPath = `M${fwd.join(' L')} L${bwd.join(' L')} Z`;
  }

  const ry = yScale(refCost);

  // Show every Nth label to avoid crowding
  const labelStep = N > 20 ? 3 : N > 12 ? 2 : 1;

  // Stacked bar geometry
  const barSlot = plotW / N;
  const barW = Math.min(barSlot * 0.6, 36);

  return (
    <svg width={W} height={H} style={{ display: 'block', width: '100%', height: 'auto' }} viewBox={`0 0 ${W} ${H}`}>
      {gridLines.map((v, i) => (
        <g key={i}>
          <line x1={PAD.l} y1={yScale(v)} x2={W - PAD.r} y2={yScale(v)} stroke="var(--chart-grid)" strokeWidth="1" />
          <text x={PAD.l - 6} y={yScale(v) + 3.5} fill="var(--muted)" fontSize="9" textAnchor="end" fontFamily="'JetBrains Mono', monospace">
            ${Math.round(v)}
          </text>
        </g>
      ))}
      {periods.map((p, i) => (
        i % labelStep === 0 && (
          <text key={i} x={xScale(i)} y={H - PAD.b + 16} fill="var(--muted)" fontSize="9" textAnchor="middle" fontFamily="'JetBrains Mono', monospace">
            {p}
          </text>
        )
      ))}
      <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} stroke="var(--border)" strokeWidth="1" />
      <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} stroke="var(--border)" strokeWidth="1" />
      <line x1={PAD.l} y1={ry} x2={W - PAD.r} y2={ry} stroke="var(--chart-ref-line)" strokeDasharray="4,4" strokeWidth="1" />
      <text x={W - PAD.r - 4} y={ry - 5} fill="var(--chart-ref-text)" fontSize="8" textAnchor="end" fontFamily="'JetBrains Mono', monospace">REF</text>
      {fillPath && <path d={fillPath} fill="var(--chart-fill)" />}

      {/* Stacked bars for components */}
      {showBars && periods.map((_, pi) => {
        const cx = xScale(pi);
        const bottom = yScale(0);
        let cumulative = 0;
        return (
          <g key={`bar-${pi}`}>
            {visibleComps.map((cs, ci) => {
              const val = cs.values[pi] || 0;
              const y0 = yScale(cumulative);
              cumulative += val;
              const y1 = yScale(cumulative);
              const segH = y0 - y1;
              if (segH <= 0) return null;
              return (
                <rect
                  key={ci}
                  x={cx - barW / 2}
                  y={y1}
                  width={barW}
                  height={segH}
                  fill={cs.color}
                  opacity="0.75"
                  rx="1"
                />
              );
            })}
          </g>
        );
      })}

      <path d={thePath} fill="none" stroke="var(--accent)" strokeWidth="2" strokeDasharray="6,3" />
      {actPath && <path d={actPath} fill="none" stroke="var(--accent4)" strokeWidth="2.5" />}
      {actPts.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="3.5" fill="var(--accent4)" />
      ))}
      {theoretical.map((v, i) => (
        <circle key={i} cx={xScale(i)} cy={yScale(v)} r="2.5" fill="var(--accent)" opacity="0.5" />
      ))}
    </svg>
  );
}
