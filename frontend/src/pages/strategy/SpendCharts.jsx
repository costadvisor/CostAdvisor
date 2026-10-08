import { useMemo, useState } from 'react';
import { AXIS, useElementWidth, niceTicks, tickLabel, tipPosition } from '../../components/charts/util';
import { seriesColor } from '../../components/intel';
import { quarterLabel } from './SpendFormat';

/* Hand-rolled SVG charts for the Spend Analysis tab. Colours are theme
 * variables only. */

/* ── Donut: spend by supplier ──────────────────────────────────────── */
export function SpendDonut({ items = [], size = 200, centerValue, centerLabel = 'Suppliers', colorOf = (_, i) => seriesColor(i) }) {
  const total = items.reduce((s, d) => s + Math.max(0, Number(d.value) || 0), 0);
  const cx = size / 2;
  const rOuter = size / 2 - 2;
  const rInner = rOuter * 0.56;
  let angle = -Math.PI / 2;
  const arcs = total > 0 ? items.map((d, i) => {
    const v = Math.max(0, Number(d.value) || 0);
    const slice = (v / total) * Math.PI * 2;
    const a0 = angle;
    const a1 = angle + slice;
    angle = a1;
    if (slice <= 0) return null;
    // A single 100% slice cannot be drawn as one arc: draw two halves.
    if (slice >= Math.PI * 2 - 1e-6) {
      return (
        <g key={d.key ?? i}>
          <circle cx={cx} cy={cx} r={(rOuter + rInner) / 2} fill="none" stroke={colorOf(d, i)} strokeWidth={rOuter - rInner}>
            <title>{`${d.label}: ${d.pctText ?? ''}`}</title>
          </circle>
        </g>
      );
    }
    const large = slice > Math.PI ? 1 : 0;
    const p = (r, a) => `${(cx + r * Math.cos(a)).toFixed(2)},${(cx + r * Math.sin(a)).toFixed(2)}`;
    const dAttr = `M${p(rInner, a0)} L${p(rOuter, a0)} A${rOuter},${rOuter} 0 ${large} 1 ${p(rOuter, a1)} `
      + `L${p(rInner, a1)} A${rInner},${rInner} 0 ${large} 0 ${p(rInner, a0)} Z`;
    return (
      <path key={d.key ?? i} d={dAttr} fill={colorOf(d, i)} stroke="var(--surface)" strokeWidth={1.5}>
        <title>{`${d.label}: ${d.pctText ?? ''}`}</title>
      </path>
    );
  }) : null;

  return (
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img"
      aria-label={`${centerValue ?? items.length} ${centerLabel.toLowerCase()}`} style={{ flexShrink: 0, display: 'block' }}>
      {!arcs && <circle cx={cx} cy={cx} r={(rOuter + rInner) / 2} fill="none" stroke="var(--surface2)" strokeWidth={rOuter - rInner} />}
      {arcs}
      <text x={cx} y={cx + 2} textAnchor="middle" fontSize={size * 0.13} fontWeight={800} fill="var(--text)">
        {centerValue ?? items.length}
      </text>
      <text x={cx} y={cx + size * 0.11} textAnchor="middle" fontSize={9} letterSpacing={0.9} fill="var(--muted)">
        {centerLabel.toUpperCase()}
      </text>
    </svg>
  );
}

/* ── Columns: spend by site, highest to lowest ─────────────────────── */
export function SiteColumns({ items = [], formatValue = (v) => String(v), height = 210, color = 'var(--accent3)' }) {
  const sorted = [...items].sort((a, b) => (b.value || 0) - (a.value || 0));
  const n = sorted.length;
  if (!n) return null;
  const barW = n <= 3 ? 76 : n <= 6 ? 56 : 40;
  const gap = n <= 3 ? 44 : 22;
  const W = Math.max(260, n * barW + (n + 1) * gap);
  const padTop = 24;
  const padBottom = 40;
  const ph = height - padTop - padBottom;
  const max = Math.max(...sorted.map((s) => s.value || 0), 1);
  return (
    <svg width={W} height={height} role="img" aria-label="Spend by site">
      {sorted.map((s, i) => {
        const h = Math.max(2, ph * ((s.value || 0) / max));
        const x = gap + i * (barW + gap);
        const y = padTop + ph - h;
        return (
          <g key={s.key ?? i}>
            <rect x={x} y={y} width={barW} height={h} rx={3} fill={color}>
              <title>{`${s.label}: ${formatValue(s.value)}`}</title>
            </rect>
            <text x={x + barW / 2} y={y - 7} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--text)">
              {formatValue(s.value)}
            </text>
            <text x={x + barW / 2} y={padTop + ph + 15} textAnchor="middle" fontSize={10.5} fill="var(--text-secondary)">
              {s.label}
            </text>
            {s.sub && (
              <text x={x + barW / 2} y={padTop + ph + 28} textAnchor="middle" fontSize={9} fill="var(--muted)">{s.sub}</text>
            )}
          </g>
        );
      })}
      <line x1={4} x2={W - 4} y1={padTop + ph} y2={padTop + ph} stroke="var(--border)" />
    </svg>
  );
}

/* ── Quarterly index lines: should-cost vs actual price ─────────────── */
export function QuarterLines({ points = [], series = [], height = 260, baseValue = 100, ariaLabel = 'Index by quarter' }) {
  const [wrapRef, width] = useElementWidth(720);
  const [hover, setHover] = useState(null);

  const model = useMemo(() => {
    if (points.length < 2) return null;
    const vals = [];
    series.forEach((s) => points.forEach((p) => {
      const v = p[s.key];
      if (v != null && Number.isFinite(Number(v))) vals.push(Number(v));
    }));
    if (!vals.length) return null;
    if (baseValue != null) vals.push(baseValue);
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = Math.max(1, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    return { lo, hi };
  }, [points, series, baseValue]);

  if (!model) {
    return <div ref={wrapRef} className="ix-chart-note">Not enough quarters with prices to draw the evolution.</div>;
  }

  const W = width;
  const H = height;
  const PAD = { l: 40, r: 128, t: 16, b: 28 };
  const pw = Math.max(10, W - PAD.l - PAD.r);
  const ph = Math.max(10, H - PAD.t - PAD.b);
  const n = points.length;
  const xS = (i) => PAD.l + (n === 1 ? pw / 2 : (i / (n - 1)) * pw);
  const yS = (v) => PAD.t + ph * (1 - (v - model.lo) / (model.hi - model.lo || 1));
  const yTicks = niceTicks(model.lo, model.hi, Math.max(3, Math.round(ph / 44)));
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 62))));

  const val = (p, key) => (p[key] == null || !Number.isFinite(Number(p[key])) ? null : Number(p[key]));
  const pathOf = (key) => {
    let d = '';
    let pen = false;
    points.forEach((p, i) => {
      const v = val(p, key);
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xS(i).toFixed(1)},${yS(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  // Shade where the first series (actual) runs above the second (should-cost).
  const [a, b] = series;
  const gapPolys = [];
  if (a && b && a.shadeAbove) {
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        const top = run.map(({ i, va }) => `${xS(i).toFixed(1)},${yS(va).toFixed(1)}`);
        const bot = [...run].reverse().map(({ i, vb }) => `${xS(i).toFixed(1)},${yS(vb).toFixed(1)}`);
        gapPolys.push([...top, ...bot].join(' '));
      }
      run = [];
    };
    // Top edge = max(actual, should-cost), bottom edge = should-cost: the
    // shape has area only where the actual line runs above.
    points.forEach((p, i) => {
      const va = val(p, a.key);
      const vb = val(p, b.key);
      if (va == null || vb == null) { flush(); return; }
      run.push({ i, va: Math.max(va, vb), vb });
    });
    flush();
  }

  const last = n - 1;
  const endLabels = series.map((s) => {
    let i = last;
    while (i >= 0 && val(points[i], s.key) == null) i -= 1;
    return i < 0 ? null : { s, i, v: val(points[i], s.key) };
  }).filter(Boolean);
  if (endLabels.length === 2 && Math.abs(yS(endLabels[0].v) - yS(endLabels[1].v)) < 13) {
    const [hiL, loL] = endLabels[0].v >= endLabels[1].v ? endLabels : [endLabels[1], endLabels[0]];
    hiL.dy = -6; loL.dy = 7;
  }

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const i = Math.round(((x - PAD.l) / pw) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const onKey = (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const d = e.key === 'ArrowRight' ? 1 : -1;
      setHover((h) => Math.max(0, Math.min(n - 1, (h ?? last) + d)));
    } else if (e.key === 'Escape') setHover(null);
  };
  const tipW = 210;
  const hp = hover != null ? points[hover] : null;

  return (
    <div ref={wrapRef} className="ix-chart" tabIndex={0} role="img" aria-label={ariaLabel}
      onKeyDown={onKey} onFocus={() => hover == null && setHover(last)} onBlur={() => setHover(null)} style={{ outline: 'none' }}>
      <svg width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={PAD.l} x2={PAD.l + pw} y1={yS(v)} y2={yS(v)} stroke="var(--chart-grid)" strokeWidth={0.75} />
            <text x={PAD.l - 6} y={yS(v) + 3} textAnchor="end" {...AXIS}>{tickLabel(v)}</text>
          </g>
        ))}
        {baseValue != null && baseValue >= model.lo && baseValue <= model.hi && (
          <line x1={PAD.l} x2={PAD.l + pw} y1={yS(baseValue)} y2={yS(baseValue)}
            stroke="var(--chart-ref-line)" strokeWidth={1.2} strokeDasharray="4 3" />
        )}
        <line x1={PAD.l} x2={PAD.l + pw} y1={PAD.t + ph} y2={PAD.t + ph} stroke="var(--border)" />
        {points.map((p, i) => (i % every === 0 ? (
          <text key={`x${p.period}`} x={xS(i)} y={H - 9} textAnchor="middle" {...AXIS}>{quarterLabel(p)}</text>
        ) : null))}
        {gapPolys.map((pts, i) => (
          <polygon key={`g${i}`} points={pts} fill={a.color} fillOpacity={0.12} stroke="none" />
        ))}
        {series.map((s) => (
          <path key={s.key} d={pathOf(s.key)} fill="none" stroke={s.color} strokeWidth={s.width || 2.2}
            strokeDasharray={s.dash || undefined} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {series.map((s) => points.map((p, i) => {
          const v = val(p, s.key);
          return v == null ? null : (
            <circle key={`${s.key}-${p.period}`} cx={xS(i)} cy={yS(v)} r={hover === i ? 4.2 : 2.6}
              fill={s.color} stroke="var(--surface)" strokeWidth={1.5} />
          );
        }))}
        {endLabels.map(({ s, i, v, dy = 0 }) => (
          <text key={`e-${s.key}`} x={xS(i) + 9} y={yS(v) + 3.5 + dy} {...AXIS} fill={s.color} fontWeight={700}>
            {s.short || s.label} {v.toFixed(1)}
          </text>
        ))}
        {hover != null && (
          <line x1={xS(hover)} x2={xS(hover)} y1={PAD.t} y2={PAD.t + ph} stroke="var(--text-secondary)" strokeOpacity={0.45} pointerEvents="none" />
        )}
        <rect x={PAD.l} y={PAD.t} width={pw} height={ph} fill="transparent" />
      </svg>
      {hp && (
        <div className="ix-chart-tip" style={{ left: tipPosition(xS(hover), W, tipW), top: PAD.t, width: tipW }}>
          <div className="ix-chart-tip-title"><span>{quarterLabel(hp)}</span></div>
          {series.map((s) => (
            <div key={s.key} className="ix-chart-tip-row">
              <span className="ix-swatch line" style={{ background: s.color }} />
              <span className="lbl">{s.label}</span>
              <span className="val">{val(hp, s.key) == null ? '—' : val(hp, s.key).toFixed(1)}</span>
            </div>
          ))}
          {a && b && val(hp, a.key) != null && val(hp, b.key) != null && (
            <div className="ix-chart-tip-row ix-chart-tip-total">
              <span className="lbl">Gap</span>
              <span className="val">{`${val(hp, a.key) - val(hp, b.key) >= 0 ? '+' : '−'}${Math.abs(val(hp, a.key) - val(hp, b.key)).toFixed(1)} pts`}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
