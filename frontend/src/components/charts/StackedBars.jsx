import { useState, useMemo } from 'react';
import { AXIS, useElementWidth, niceTicks, tickLabel, tipPosition } from './util';
import { seriesColor } from '../intel/palette';

/* Stacked vertical bars: periods × categories (cost build-up, spend by quarter).
 *
 *   <StackedBars
 *     data={data.stack}                    // [{ period: '2025-Q1', by_line: { 'Iron scrap': 32.1, … } }]
 *                                          // or [{ label, values: {…} }]
 *     categories={[{ key: 'Iron scrap', label: 'Iron scrap', color: 'var(--pie-1)' }]}  // optional
 *     formatValue={(v) => v.toFixed(1)} height={220} />
 *
 * Categories default to the union of keys in first-seen order, coloured from
 * the theme's categorical palette (fixed order, so a category keeps its colour).
 * Negative values stack below zero. Legend below; hover a bar for the breakdown. */
export default function StackedBars({
  data = [], categories, height = 220, formatValue = (v) => v.toFixed(1), legend = true,
  showTotals = false, totalLabel = 'Total', yMax, ariaLabel = 'Stacked bars', emptyText = 'No data to chart.',
}) {
  const [wrapRef, width] = useElementWidth(640);
  const [hover, setHover] = useState(null);

  const model = useMemo(() => {
    const rows = (data || []).map((d) => ({
      label: d.label ?? d.period ?? '',
      values: d.values || d.by_line || d.by_category || {},
    }));
    let cats = categories;
    if (!cats) {
      const seen = [];
      rows.forEach((r) => Object.keys(r.values).forEach((k) => { if (!seen.includes(k)) seen.push(k); }));
      cats = seen.map((k) => ({ key: k, label: k }));
    }
    cats = cats.map((c, i) => ({ ...c, label: c.label ?? c.key, color: c.color || seriesColor(i) }));
    let top = 0;
    let bottom = 0;
    rows.forEach((r) => {
      let pos = 0;
      let neg = 0;
      cats.forEach((c) => {
        const v = Number(r.values[c.key]) || 0;
        if (v >= 0) pos += v; else neg += v;
      });
      r.pos = pos; r.neg = neg; r.total = pos + neg;
      top = Math.max(top, pos);
      bottom = Math.min(bottom, neg);
    });
    return { rows, cats, top: yMax ?? top, bottom };
  }, [data, categories, yMax]);

  const { rows, cats, top, bottom } = model;
  if (!rows.length || top === bottom) {
    return <div ref={wrapRef} className="ix-chart ix-chart-note" style={{ padding: 16 }}>{emptyText}</div>;
  }

  const W = width;
  const H = height;
  const PAD = { l: 44, r: 10, t: showTotals ? 18 : 10, b: 26 };
  const pw = Math.max(10, W - PAD.l - PAD.r);
  const ph = Math.max(10, H - PAD.t - PAD.b);
  const ticks = niceTicks(bottom, top, Math.max(3, Math.round(ph / 40)));
  const lo = Math.min(bottom, ticks[0] ?? bottom);
  const hi = Math.max(top, ticks[ticks.length - 1] ?? top);
  const yS = (v) => PAD.t + ph * (1 - (v - lo) / (hi - lo || 1));
  const slot = pw / rows.length;
  const bw = Math.max(3, Math.min(56, slot * 0.64));
  const labelEvery = Math.max(1, Math.ceil(56 / slot));
  const tipW = 210;

  return (
    <div ref={wrapRef} className="ix-chart" role="img" aria-label={ariaLabel}>
      <svg width={W} height={H} onMouseLeave={() => setHover(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={PAD.l + pw} y1={yS(v)} y2={yS(v)}
              stroke={v === 0 ? 'var(--border-light)' : 'var(--chart-grid)'} strokeWidth={v === 0 ? 1 : 0.75} />
            <text x={PAD.l - 6} y={yS(v) + 3} textAnchor="end" {...AXIS}>{tickLabel(v)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cx = PAD.l + slot * i + slot / 2;
          const x = cx - bw / 2;
          let up = 0;
          let down = 0;
          const segs = cats.map((c) => {
            const v = Number(r.values[c.key]) || 0;
            if (!v) return null;
            const from = v >= 0 ? up : down;
            const to = from + v;
            if (v >= 0) up = to; else down = to;
            const y1 = yS(Math.max(from, to));
            const y2 = yS(Math.min(from, to));
            // 1px surface gap between stacked segments.
            const hgt = Math.max(0, y2 - y1 - 1);
            return <rect key={c.key} x={x} y={y1 + 0.5} width={bw} height={hgt} fill={c.color} rx={1.5}
              opacity={hover == null || hover === i ? 1 : 0.55} />;
          });
          return (
            <g key={`${r.label}-${i}`}>
              {segs}
              {showTotals && (
                <text x={cx} y={yS(r.pos) - 5} textAnchor="middle" {...AXIS} fill="var(--text-secondary)">
                  {formatValue(r.total)}
                </text>
              )}
              {i % labelEvery === 0 && (
                <text x={cx} y={H - 8} textAnchor="middle" {...AXIS}>{r.label}</text>
              )}
              <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={ph} fill="transparent"
                onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
      </svg>
      {hover != null && rows[hover] && (
        <div className="ix-chart-tip"
          style={{ left: tipPosition(PAD.l + slot * hover + slot / 2, W, tipW, bw / 2 + 10), top: PAD.t, width: tipW }}>
          <div className="ix-chart-tip-title"><span>{rows[hover].label}</span></div>
          {[...cats].reverse().map((c) => {
            const v = Number(rows[hover].values[c.key]);
            if (!Number.isFinite(v) || v === 0) return null;
            return (
              <div key={c.key} className="ix-chart-tip-row">
                <span className="ix-swatch" style={{ background: c.color }} />
                <span className="lbl">{c.label}</span>
                <span className="val">{formatValue(v)}</span>
              </div>
            );
          })}
          <div className="ix-chart-tip-row ix-chart-tip-total">
            <span className="lbl">{totalLabel}</span>
            <span className="val">{formatValue(rows[hover].total)}</span>
          </div>
        </div>
      )}
      {legend && cats.length > 0 && (
        <div className="ix-legend">
          {cats.map((c) => (
            <span key={c.key} className="ix-legend-item">
              <span className="ix-swatch" style={{ background: c.color }} />{c.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
