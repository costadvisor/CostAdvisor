import { useState, useMemo, useRef } from 'react';
import {
  AXIS, useElementWidth, niceTicks, tickLabel, monthKey, keyYear, keyMonth, spreadLabels, tipPosition,
} from './util';
import { regionColor, seriesColor, REGION_CODES, normalizeRegion } from '../intel/palette';
import { MONTHS } from '../intel/fmt';

/* Multi-series monthly index lines (the product page's should-cost chart).
 *
 *   <IndexLineChart
 *     series={data.series_by_region}          // { EU: [{year, month, level, kind}], … } or
 *                                              // [{ key, label?, color?, points: [...] }]
 *     highlight={region}                        // emphasised series; others fade
 *     asOfLabel="Data to Jun 2026"              // the marker on the last actual month
 *     height={260} />
 *
 * Draws: base-100 reference line, a marker on the last actual month (the data
 * vintage, never "today"), solid actual → dashed forecast, year ticks, y ticks,
 * end-of-line labels, and a crosshair tooltip (mouse, or focus + ←/→). There is
 * no forecast cone: the source has no fitted interval (design §4.2). Region
 * keys get the region palette automatically. No legend: the page's region
 * chips are the key. */

function toList(series) {
  if (!series) return [];
  if (Array.isArray(series)) return series;
  return Object.entries(series).map(([key, points]) => ({ key, points }));
}

function normalise(series) {
  return toList(series).map((s, i) => {
    const raw = s.points || s.data || [];
    const pts = raw
      .filter((p) => p && p.year != null && p.month != null)
      .map((p) => ({
        k: monthKey(p.year, p.month),
        v: p.level == null || !Number.isFinite(Number(p.level)) ? null : Number(p.level),
        f: p.kind === 'forecast',
      }))
      .sort((a, b) => a.k - b.k);
    const isRegion = REGION_CODES.includes(normalizeRegion(s.key));
    return {
      key: s.key,
      label: s.label ?? s.key,
      color: s.color || (isRegion ? regionColor(s.key) : seriesColor(i)),
      pts,
      byK: new Map(pts.map((p) => [p.k, p])),
    };
  });
}

// Advance width of one AXIS character (JetBrains Mono, 9.5px: 0.6em), for
// width checks on SVG text.
const CHAR_W = 5.8;

const fmtMonth = (k, short = true) => `${MONTHS[keyMonth(k) - 1]} ${short ? String(keyYear(k)).slice(-2) : keyYear(k)}`;

export default function IndexLineChart({
  series, highlight = null, height = 260, baseValue = 100,
  endLabels = true, asOfLabel = 'Last actual', forecastLabel = 'Forecast →',
  formatValue = (v) => v.toFixed(1), yDomain, ariaLabel = 'Index over time', emptyText = 'No series to plot.',
}) {
  const [wrapRef, width] = useElementWidth(720);
  const svgRef = useRef(null);
  const [hoverK, setHoverK] = useState(null);

  const model = useMemo(() => {
    const list = normalise(series);
    const keys = list.flatMap((s) => s.pts.map((p) => p.k));
    if (keys.length < 2) return null;
    const k0 = Math.min(...keys);
    const k1 = Math.max(...keys);
    if (k1 === k0) return null;
    const vals = list.flatMap((s) => s.pts.map((p) => p.v).filter((v) => v != null));
    if (baseValue != null) vals.push(baseValue);
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = Math.max(1.5, (hi - lo) * 0.06);
    lo -= pad; hi += pad;
    if (yDomain) [lo, hi] = yDomain;
    const focus = list.find((s) => s.key === highlight);
    const actualKeys = (focus ? [focus] : list).flatMap((s) => s.pts.filter((p) => !p.f && p.v != null).map((p) => p.k));
    const asOfK = actualKeys.length ? Math.max(...actualKeys) : null;
    const hasForecast = list.some((s) => s.pts.some((p) => p.f));
    return { list, k0, k1, lo, hi, asOfK, hasForecast };
  }, [series, highlight, baseValue, yDomain]);

  if (!model) {
    return <div ref={wrapRef} className="ix-chart ix-chart-note" style={{ padding: 16 }}>{emptyText}</div>;
  }

  const { list, k0, k1, lo, hi, asOfK, hasForecast } = model;
  const W = width;
  const H = height;
  // End labels ("APAC 122.5") sit in the right gutter; size it to the longest
  // one so none is clipped at the card edge.
  const endText = (s) => {
    const last = [...s.pts].reverse().find((p) => p.v != null);
    return last ? `${s.label} ${formatValue(last.v)}` : '';
  };
  const gutter = endLabels
    ? Math.min(130, Math.max(54, Math.ceil(19 + CHAR_W * Math.max(0, ...list.map((s) => endText(s).length)))))
    : 16;
  const PAD = { l: 40, r: gutter, t: 20, b: 26 };
  const pw = Math.max(10, W - PAD.l - PAD.r);
  const ph = Math.max(10, H - PAD.t - PAD.b);
  const xS = (k) => PAD.l + ((k - k0) / (k1 - k0)) * pw;
  const yS = (v) => PAD.t + ph * (1 - (v - lo) / (hi - lo || 1));
  const yTicks = niceTicks(lo, hi, Math.max(3, Math.round(ph / 42)));
  const pxPerMonth = pw / (k1 - k0);

  // X ticks: every January, plus July when there is room.
  const xTicks = [];
  for (let k = k0; k <= k1; k += 1) {
    const m = keyMonth(k);
    if (m === 1 || (m === 7 && pxPerMonth * 6 >= 46)) xTicks.push(k);
  }
  const lastTick = xTicks[xTicks.length - 1];
  if (lastTick == null || (k1 - lastTick) * pxPerMonth >= 38) xTicks.push(k1);
  if (xTicks[0] !== k0 && (xTicks[0] - k0) * pxPerMonth >= 38) xTicks.unshift(k0);

  const hl = highlight != null && list.some((s) => s.key === highlight) ? highlight : null;
  const ordered = hl ? [...list.filter((s) => s.key !== hl), ...list.filter((s) => s.key === hl)] : list;

  const pathOf = (pts) => {
    let d = '';
    let pen = false;
    pts.forEach((p) => {
      if (p.v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xS(p.k).toFixed(1)},${yS(p.v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const segments = (s) => {
    const actual = s.pts.filter((p) => !p.f);
    const lastActual = [...actual].reverse().find((p) => p.v != null);
    const fc = s.pts.filter((p) => p.f);
    const fcPts = lastActual && fc.length ? [lastActual, ...fc] : fc;
    return { actual: pathOf(actual), forecast: fcPts.length > 1 ? pathOf(fcPts) : '' };
  };

  // End labels, de-overlapped.
  const labels = endLabels ? spreadLabels(
    list.map((s) => {
      const last = [...s.pts].reverse().find((p) => p.v != null);
      return last ? { key: s.key, label: s.label, color: s.color, v: last.v, x: xS(last.k), y: yS(last.v) + 3 } : null;
    }).filter(Boolean),
    11, PAD.t + 4, H - PAD.b,
  ) : [];

  const onMove = (e) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = e.clientX - rect.left;
    const k = Math.round(k0 + ((x - PAD.l) / pw) * (k1 - k0));
    setHoverK(Math.max(k0, Math.min(k1, k)));
  };
  const onKey = (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const d = e.key === 'ArrowRight' ? 1 : -1;
      setHoverK((k) => Math.max(k0, Math.min(k1, (k ?? asOfK ?? k1) + d)));
    } else if (e.key === 'Escape') setHoverK(null);
  };

  // "6-month forecast →": starts just right of the last-actual line inside the
  // forecast zone; when the zone is too narrow for it, it moves to the row
  // above the plot, after the last-actual label; when neither fits it is left out.
  let fcLabel = null;
  if (hasForecast && asOfK != null && asOfK < k1 && forecastLabel) {
    const tx = xS(asOfK);
    const textW = String(forecastLabel).length * CHAR_W;
    const inZone = tx + 6;
    const above = tx + (String(asOfLabel || '').length * CHAR_W) / 2 + 9;
    if (inZone + textW <= xS(k1) - 3) fcLabel = { x: inZone, y: PAD.t + 11 };
    else if (above + textW <= W - 2) fcLabel = { x: above, y: PAD.t - 6 };
  }

  const hoverRows = hoverK == null ? [] : list
    .map((s) => ({ s, p: s.byK.get(hoverK) }))
    .filter((r) => r.p && r.p.v != null)
    .sort((a, b) => (a.s.key === hl ? -1 : b.s.key === hl ? 1 : b.p.v - a.p.v));
  const hoverIsForecast = hoverRows.some((r) => r.p.f);
  const tipW = 190;

  return (
    <div ref={wrapRef} className="ix-chart" tabIndex={0} role="img" aria-label={ariaLabel}
      onKeyDown={onKey} onFocus={() => hoverK == null && setHoverK(asOfK ?? k1)} onBlur={() => setHoverK(null)}
      style={{ outline: 'none' }}>
      <svg ref={svgRef} width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHoverK(null)}>
        {/* forecast zone */}
        {hasForecast && asOfK != null && asOfK < k1 && (
          <rect x={xS(asOfK)} y={PAD.t} width={xS(k1) - xS(asOfK)} height={ph}
            fill="var(--accent3)" fillOpacity={0.06} />
        )}
        {/* y grid */}
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={PAD.l} x2={PAD.l + pw} y1={yS(v)} y2={yS(v)} stroke="var(--chart-grid)" strokeWidth={0.75} />
            <text x={PAD.l - 6} y={yS(v) + 3} textAnchor="end" {...AXIS}>{tickLabel(v)}</text>
          </g>
        ))}
        {/* base reference */}
        {baseValue != null && baseValue >= lo && baseValue <= hi && (
          <g>
            <line x1={PAD.l} x2={PAD.l + pw} y1={yS(baseValue)} y2={yS(baseValue)}
              stroke="var(--chart-ref-line)" strokeWidth={1.2} strokeDasharray="4 3" />
            {!yTicks.some((t) => Math.abs(yS(t) - yS(baseValue)) < 10) && (
              <text x={PAD.l - 6} y={yS(baseValue) + 3} textAnchor="end" {...AXIS} fontWeight={700}>{tickLabel(baseValue)}</text>
            )}
          </g>
        )}
        {/* x axis */}
        <line x1={PAD.l} x2={PAD.l + pw} y1={PAD.t + ph} y2={PAD.t + ph} stroke="var(--border)" strokeWidth={1} />
        {xTicks.map((k) => (
          <g key={`x${k}`}>
            <line x1={xS(k)} x2={xS(k)} y1={PAD.t + ph} y2={PAD.t + ph + 4} stroke="var(--border)" />
            <text x={xS(k)} y={H - 8} textAnchor="middle" {...AXIS}>{fmtMonth(k)}</text>
          </g>
        ))}
        {/* the last actual month (the data vintage) */}
        {asOfK != null && hasForecast && (
          <g>
            <line x1={xS(asOfK)} x2={xS(asOfK)} y1={PAD.t} y2={PAD.t + ph}
              stroke="var(--text-secondary)" strokeOpacity={0.45} strokeDasharray="2 3" />
            <text x={xS(asOfK)} y={PAD.t - 6} textAnchor="middle" {...AXIS} fill="var(--text-secondary)">{asOfLabel}</text>
            {fcLabel && (
              <text x={fcLabel.x} y={fcLabel.y} textAnchor="start" {...AXIS} fill="var(--accent3-ink, var(--accent3))">
                {forecastLabel}
              </text>
            )}
          </g>
        )}
        {/* lines (faded first, highlighted last) */}
        {ordered.map((s) => {
          const faded = hl && s.key !== hl;
          const { actual, forecast } = segments(s);
          const sw = hl ? (faded ? 1.4 : 2.4) : 2;
          return (
            <g key={`l-${s.key}`} opacity={faded ? 0.32 : 1}>
              {actual && <path d={actual} fill="none" stroke={s.color} strokeWidth={sw} strokeLinejoin="round" strokeLinecap="round" />}
              {forecast && <path d={forecast} fill="none" stroke={s.color} strokeWidth={sw * 0.85} strokeDasharray="5 4" strokeLinecap="round" />}
            </g>
          );
        })}
        {/* end labels */}
        {labels.map((l) => {
          const faded = hl && l.key !== hl;
          return (
            <g key={`e-${l.key}`} opacity={faded ? 0.55 : 1}>
              <line x1={PAD.l + pw + 5} x2={PAD.l + pw + 12} y1={l.y - 3} y2={l.y - 3} stroke={l.color} strokeWidth={3} strokeLinecap="round" />
              <text x={PAD.l + pw + 15} y={l.y} {...AXIS} fill={faded ? 'var(--muted)' : 'var(--text)'}
                fontWeight={l.key === hl ? 700 : 500}>
                {l.label} {formatValue(l.v)}
              </text>
            </g>
          );
        })}
        {/* hover crosshair */}
        {hoverK != null && (
          <g pointerEvents="none">
            <line x1={xS(hoverK)} x2={xS(hoverK)} y1={PAD.t} y2={PAD.t + ph} stroke="var(--text-secondary)" strokeOpacity={0.5} />
            {hoverRows.map(({ s, p }) => (
              <circle key={`h-${s.key}`} cx={xS(hoverK)} cy={yS(p.v)} r={s.key === hl || !hl ? 4 : 3}
                fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            ))}
          </g>
        )}
        <rect x={PAD.l} y={PAD.t} width={pw} height={ph} fill="transparent" />
      </svg>
      {hoverK != null && hoverRows.length > 0 && (
        <div className="ix-chart-tip" style={{ left: tipPosition(xS(hoverK), W, tipW), top: PAD.t, width: tipW }}>
          <div className="ix-chart-tip-title">
            <span>{fmtMonth(hoverK, false)}</span>
            {hoverIsForecast && <span className="ix-muted">forecast</span>}
          </div>
          {hoverRows.map(({ s, p }) => (
            <div key={s.key} className={`ix-chart-tip-row${hl && s.key !== hl ? ' is-dim' : ''}`}>
              <span className="ix-swatch line" style={{ background: s.color }} />
              <span className="lbl">{s.label}</span>
              <span className="val">{formatValue(p.v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
