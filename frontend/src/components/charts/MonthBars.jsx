import { useState } from 'react';
import { MONTH_INITIALS, MONTHS } from '../intel/fmt';

/* Twelve seasonal bars diverging from a baseline.
 *
 *   <MonthBars values={seasonality.factors} />                 // factors around 100 (the backend's scale)
 *   <MonthBars values={factors} baseline={1} formatValue={(v) => v.toFixed(3)} />
 *   <MonthBars values={deviationPct} baseline={0} highlight={6} />
 *
 * Above baseline = --accent3 (seasonally dearer), below = --accent4. The
 * current month (`highlight`, 1–12) is outlined. Hover shows the value. */
export default function MonthBars({
  values = [], baseline = 100, height = 84, highlight, labels = MONTH_INITIALS,
  formatValue = (v) => v.toFixed(1), upColor = 'var(--accent3)', downColor = 'var(--accent4)',
  ariaLabel = 'Seasonal profile by month',
}) {
  const [hover, setHover] = useState(null);
  const vals = values.slice(0, 12).map((v) => (v == null ? null : Number(v)));
  const devs = vals.map((v) => (v == null ? 0 : v - baseline));
  const maxDev = Math.max(...devs.map(Math.abs), 1e-9);
  const W = 240;
  const plotH = Math.max(24, height - 16);
  const H = plotH + 4;
  const mid = plotH / 2 + 2;
  const slot = W / 12;
  const bw = slot * 0.62;
  const scale = (plotH / 2 - 2) / maxDev;

  return (
    <div className="ix-chart" role="img" aria-label={ariaLabel}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
        style={{ display: 'block' }} onMouseLeave={() => setHover(null)}>
        <line x1={0} x2={W} y1={mid} y2={mid} stroke="var(--border-light)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        {vals.map((v, i) => {
          const d = devs[i];
          const h = Math.max(1.5, Math.abs(d) * scale);
          const x = slot * i + (slot - bw) / 2;
          const y = d >= 0 ? mid - h : mid;
          const isHl = highlight === i + 1;
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={slot * i} y={0} width={slot} height={plotH + 4} fill="transparent" />
              <rect x={x} y={y} width={bw} height={v == null ? 0 : h} rx={1.5}
                fill={d >= 0 ? upColor : downColor}
                opacity={hover == null || hover === i ? 1 : 0.5}
                stroke={isHl ? 'var(--text)' : 'none'} strokeWidth={isHl ? 1.2 : 0} vectorEffect="non-scaling-stroke" />
            </g>
          );
        })}
      </svg>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', marginTop: 3 }}>
        {labels.slice(0, 12).map((l, i) => (
          <span key={i} style={{
            textAlign: 'center', fontSize: 9,
            color: highlight === i + 1 || hover === i ? 'var(--text)' : 'var(--muted)',
            fontWeight: highlight === i + 1 ? 700 : 400,
          }}>{l}</span>
        ))}
      </div>
      <div className="ix-chart-note" style={{ marginTop: 3, minHeight: 16 }} aria-live="polite">
        {hover != null && vals[hover] != null && (
          <>
            <strong style={{ color: 'var(--text)' }}>{MONTHS[hover]}</strong> {formatValue(vals[hover])}
            {' '}({devs[hover] >= 0 ? '+' : '−'}{formatValue(Math.abs(devs[hover]))} vs {formatValue(baseline)})
          </>
        )}
      </div>
    </div>
  );
}
