import { useState, useRef } from 'react';

/**
 * Interactive SVG line chart for a daily price series (stock-app style).
 * Hovering shows a crosshair + a tooltip with the point's date, rate, and the
 * change versus the previous day. Scaling/grid math mirrors IndexTrendChart.jsx.
 *
 * Props:
 *   series : ascending array of { date: 'YYYY-MM-DD', rate: number }
 *   color  : optional line colour (defaults to green/red by overall trend)
 */
export default function PriceChart({ series, color }) {
  const [hover, setHover] = useState(null); // index or null
  const wrapRef = useRef(null);

  const W = 760, H = 220;
  const PAD = { l: 52, r: 12, t: 14, b: 26 };

  if (!series || series.length < 2) {
    return <div style={{ color: 'var(--muted)', fontSize: 12, padding: 24, textAlign: 'center' }}>Not enough data to chart.</div>;
  }

  const vals = series.map(d => Number(d.rate));
  const minRaw = Math.min(...vals), maxRaw = Math.max(...vals);
  const pad = (maxRaw - minRaw || maxRaw || 1) * 0.06;
  const minV = minRaw - pad, maxV = maxRaw + pad;
  const N = series.length;

  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const xScale = i => PAD.l + plotW * i / (N - 1);
  const yScale = v => PAD.t + plotH * (1 - (v - minV) / (maxV - minV || 1));

  const up = vals[vals.length - 1] >= vals[0];
  const stroke = color || (up ? 'var(--accent2)' : 'var(--accent)');

  // y grid
  const range = maxV - minV;
  const dec = maxRaw < 2 ? 4 : maxRaw < 100 ? 2 : 0;
  const gridLines = [];
  let step = Math.pow(10, Math.floor(Math.log10(range || 1)));
  if (range / step < 3) step /= 2;
  if (range / step > 8) step *= 2;
  for (let v = Math.ceil(minV / step) * step; v <= maxV; v += step) gridLines.push(v);

  // Abbreviate to "k" only when the gridline step is coarse enough for one decimal
  // to still separate adjacent labels. A 1042–1110 price series steps by 10, and
  // blanket `(v/1000).toFixed(1)` rendered every single gridline as "1.1k".
  const useK = step >= 100;
  const kDec = step >= 1000 ? 0 : 1;
  const fmtY = v => (v >= 1000 && useK ? `${(v / 1000).toFixed(kDec)}k` : v.toFixed(dec));

  const linePath = series.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(Number(d.rate)).toFixed(1)}`).join(' ');
  const fillPath = `${linePath} L${xScale(N - 1).toFixed(1)},${(H - PAD.b).toFixed(1)} L${xScale(0).toFixed(1)},${(H - PAD.b).toFixed(1)} Z`;

  // sparse x labels (~6), formatted by span
  const spanDays = (new Date(series[N - 1].date) - new Date(series[0].date)) / 86400000;
  const fmt = ds => {
    const d = new Date(ds + 'T00:00:00');
    return spanDays > 200
      ? d.toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
      : d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
  };
  const labelStep = Math.max(1, Math.ceil(N / 6));

  const onMove = e => {
    const rect = e.currentTarget.getBoundingClientRect();
    const fracX = (e.clientX - rect.left) / rect.width;       // 0..1 across rendered width
    const internalX = fracX * W;                               // back into viewBox space
    let idx = Math.round(((internalX - PAD.l) / plotW) * (N - 1));
    idx = Math.max(0, Math.min(N - 1, idx));
    setHover(idx);
  };

  const hp = hover != null ? series[hover] : null;
  const prev = hover != null && hover > 0 ? series[hover - 1] : null;
  const hx = hover != null ? xScale(hover) : 0;
  const hy = hp ? yScale(Number(hp.rate)) : 0;
  let dDelta = null, dPct = null;
  if (hp && prev) {
    dDelta = Number(hp.rate) - Number(prev.rate);
    dPct = (dDelta / Number(prev.rate)) * 100;
  }
  const tipLeftPct = hover != null ? Math.max(4, Math.min(96, (hx / W) * 100)) : 0;

  return (
    <div ref={wrapRef} className="ca-fade-in" style={{ position: 'relative', width: '100%' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', width: '100%', height: 'auto' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {gridLines.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} y1={yScale(v)} x2={W - PAD.r} y2={yScale(v)} stroke="var(--chart-grid, var(--border))" strokeWidth={0.5} />
            <text x={PAD.l - 6} y={yScale(v) + 3} textAnchor="end" fill="var(--muted)" fontSize={9} fontFamily="'JetBrains Mono', monospace">
              {fmtY(v)}
            </text>
          </g>
        ))}

        <path d={fillPath} fill={stroke} opacity={0.08} />
        <path d={linePath} fill="none" stroke={stroke} strokeWidth={1.6} />

        {/* crosshair */}
        {hp && (
          <g>
            <line x1={hx} y1={PAD.t} x2={hx} y2={H - PAD.b} stroke="var(--muted)" strokeWidth={0.75} strokeDasharray="3 3" />
            <circle cx={hx} cy={hy} r={3.5} fill={stroke} stroke="var(--surface)" strokeWidth={1.5} />
          </g>
        )}

        {series.map((d, i) => {
          if (i % labelStep !== 0) return null;
          const x = xScale(i);
          // A centred label on the first/last point hangs outside the viewBox and
          // gets clipped — anchor the edge labels inward instead.
          const anchor = x <= PAD.l + 1 ? 'start' : x >= W - PAD.r - 1 ? 'end' : 'middle';
          return (
            <text key={i} x={x} y={H - 6} textAnchor={anchor} fill="var(--muted)" fontSize={9} fontFamily="'JetBrains Mono', monospace">
              {fmt(d.date)}
            </text>
          );
        })}
      </svg>

      {hp && (
        <div style={{
          position: 'absolute', top: 0, left: `${tipLeftPct}%`, transform: 'translateX(-50%)',
          background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6,
          padding: '6px 9px', fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5, boxShadow: '0 4px 14px rgba(0,0,0,0.3)',
        }}>
          <div style={{ color: 'var(--text)', fontWeight: 600 }}>{Number(hp.rate).toFixed(4)}</div>
          <div style={{ color: 'var(--muted)', fontSize: 10 }}>{hp.date}</div>
          {dDelta != null && (
            <div style={{ color: dDelta >= 0 ? 'var(--accent2)' : 'var(--accent)', fontSize: 10, marginTop: 2 }}>
              {dDelta >= 0 ? '▲' : '▼'} {Math.abs(dDelta).toFixed(4)} ({dPct >= 0 ? '+' : ''}{dPct.toFixed(2)}%) vs prev
            </div>
          )}
        </div>
      )}
    </div>
  );
}
