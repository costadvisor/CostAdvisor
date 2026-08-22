/**
 * Shared presentational SVG chart helpers for the workspace areas.
 *
 * THEME RULE: every colour is a CSS variable (var(--…)) so all portal themes
 * (default / light / amber / staminachem) apply automatically. Never hardcode
 * a hex colour here or in any area component — pass a token string like
 * "var(--accent)" via the `color` props.
 *
 * Token cheat-sheet: --accent (positive/green), --accent2 (alert/red),
 * --accent3 (warn/amber), --accent4 (info/blue), --muted/--text-secondary
 * (labels), --chart-grid (gridlines), --border, --surface2.
 */
import { useState } from 'react';

const AXIS = { fill: 'var(--muted)', fontSize: 9, fontFamily: "'JetBrains Mono', monospace" };

/* ── Sparkline — tiny inline trend line ─────────────────────────────── */
// `label` is required for anything user-facing: an unlabelled <svg> is announced
// as nothing, so a whole trend column was invisible to assistive tech.
export function Sparkline({ data = [], color, width = 84, height = 26, label }) {
  const a11y = label
    ? { role: 'img', 'aria-label': label }
    : { role: 'presentation', 'aria-hidden': true };
  if (!data || data.length < 2) return <svg width={width} height={height} {...a11y} />;
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  // A series that ends where it started is FLAT, not up. `last >= first` painted
  // pegged FX pairs as a solid danger-red rule that read like an error underline.
  const first = data[0], last = data[data.length - 1];
  const flat = first !== 0 && Math.abs(last / first - 1) < 0.0005;
  const stroke = color || (flat ? 'var(--muted)' : last > first ? 'var(--accent2)' : 'var(--accent)');
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (width - 2) + 1;
    const y = height - 2 - ((v - min) / span) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={width} height={height} style={{ display: 'block' }} {...a11y}>
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth={1.4} strokeLinecap="round" />
    </svg>
  );
}

/* ── MultiLineChart — axes, grid, N lines, optional ref + forecast split ─ */
export function MultiLineChart({
  series = [], xLabels = [], height = 200, refValue = null, refLabel,
  splitIndex = null, splitLabel = 'Forecast',
}) {
  const W = 720, H = height, PAD = { l: 48, r: 14, t: 16, b: 28 };
  const all = series.flatMap(s => s.values).filter(v => v != null);
  if (all.length < 2) return <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16 }}>No data.</div>;
  const minV = Math.min(...all, refValue ?? Infinity) * 0.98;
  const maxV = Math.max(...all, refValue ?? -Infinity) * 1.02;
  const N = xLabels.length || Math.max(...series.map(s => s.values.length));
  const pw = W - PAD.l - PAD.r, ph = H - PAD.t - PAD.b;
  const xS = i => PAD.l + (N <= 1 ? 0 : pw * i / (N - 1));
  const yS = v => PAD.t + ph * (1 - (v - minV) / (maxV - minV || 1));
  const grid = []; const step = (maxV - minV) / 4;
  for (let k = 0; k <= 4; k++) grid.push(minV + step * k);
  const labelStep = Math.max(1, Math.ceil(N / 8));
  const path = vals => vals.map((v, i) => v == null ? null : `${i === 0 || vals[i - 1] == null ? 'M' : 'L'}${xS(i).toFixed(1)},${yS(v).toFixed(1)}`).filter(Boolean).join(' ');
  return (
    <div style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {grid.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} y1={yS(v)} x2={W - PAD.r} y2={yS(v)} stroke="var(--chart-grid)" strokeWidth={0.5} />
            <text x={PAD.l - 6} y={yS(v) + 3} textAnchor="end" {...AXIS}>{v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(v < 10 ? 2 : 0)}</text>
          </g>
        ))}
        {refValue != null && (
          <g>
            <line x1={PAD.l} y1={yS(refValue)} x2={W - PAD.r} y2={yS(refValue)} stroke="var(--chart-ref-line)" strokeWidth={1} strokeDasharray="4 3" />
            {refLabel && <text x={W - PAD.r} y={yS(refValue) - 4} textAnchor="end" {...AXIS}>{refLabel}</text>}
          </g>
        )}
        {splitIndex != null && splitIndex < N && (
          <g>
            <line x1={xS(splitIndex)} y1={PAD.t} x2={xS(splitIndex)} y2={H - PAD.b} stroke="var(--border-light)" strokeWidth={1} strokeDasharray="2 3" />
            <text x={xS(splitIndex) + 4} y={PAD.t + 9} {...AXIS}>{splitLabel}</text>
          </g>
        )}
        {series.map((s, si) => (
          <path key={si} d={path(s.values)} fill="none" stroke={s.color || 'var(--accent)'} strokeWidth={2}
            strokeDasharray={s.dashed ? '5 4' : undefined} />
        ))}
        {xLabels.map((lab, i) => i % labelStep === 0 && (
          <text key={i} x={xS(i)} y={H - 8} textAnchor="middle" {...AXIS}>{lab}</text>
        ))}
      </svg>
      {series.some(s => s.name) && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 6 }}>
          {series.map((s, i) => s.name && (
            <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-secondary)' }}>
              <span style={{ width: 14, height: 2, background: s.color || 'var(--accent)', display: 'inline-block' }} />{s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── DriftBar — small horizontal magnitude bar ──────────────────────── */
export function DriftBar({ value = 0, max = 100, color = 'var(--accent2)', width = 90 }) {
  const pct = Math.max(0, Math.min(100, (Math.abs(value) / (max || 1)) * 100));
  return (
    <div style={{ width, height: 8, background: 'var(--surface2)', borderRadius: 4, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4 }} />
    </div>
  );
}

/* ── StackedBar — horizontal stacked segments (weights / margin range) ─ */
export function StackedBar({ segments = [], height = 14 }) {
  return (
    <div style={{ display: 'flex', height, borderRadius: 4, overflow: 'hidden', background: 'var(--surface2)' }}>
      {segments.map((s, i) => (
        <div key={i} title={s.label} style={{ width: `${s.pct}%`, background: s.color || 'var(--accent)', height: '100%' }} />
      ))}
    </div>
  );
}

/* ── Collapsible group header (shared interaction) ──────────────────── */
// `color` optionally shows a category dot; `meta` is an optional trailing line of
// secondary detail (e.g. "3 products · 1 formula complete"). Was a div with onClick
// and no keyboard path, so collapsed groups could not be reopened without a mouse.
export function GroupHeader({ label, count, open, onToggle, color, meta }) {
  return (
    <div
      onClick={onToggle}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
        padding: '8px 4px', userSelect: 'none', borderBottom: '1px solid var(--border)',
      }}
    >
      <span aria-hidden style={{ fontSize: 11, color: 'var(--muted)', transition: 'transform .15s', transform: open ? 'none' : 'rotate(-90deg)' }}>▾</span>
      {color && <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0 }} />}
      <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>{label}</span>
      {count != null && <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: 'var(--muted)' }}>{count}</span>}
      {meta && <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>{meta}</span>}
    </div>
  );
}

/* small hook helper for collapsible groups */
export function useOpenSet(initialOpen = []) {
  const [openSet, setOpenSet] = useState(() => new Set(initialOpen));
  const toggle = key => setOpenSet(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; });
  return [openSet, toggle];
}
