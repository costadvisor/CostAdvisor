import { useMemo, useRef, useState } from 'react';
import exportCsv from '../utils/exportCsv';

/* Scrum 20 — Procurement Priority Matrix.
 * A 2×2 scatter of index volatility (x) vs spend exposure (y) per product,
 * split at the portfolio medians into act-now / hedge / monitor / low-priority.
 * Custom inline SVG (the app builds its own charts — no chart lib). Colours are
 * resolved from CSS tokens to concrete values so the PNG export renders them. */

const QUADRANT = {
  act_now:      { label: 'Act now',      token: '--accent2' },
  hedge:        { label: 'Hedge',        token: '--accent3' },
  monitor:      { label: 'Monitor',      token: '--accent4' },
  low_priority: { label: 'Low priority', token: '--muted' },
};

const cssVar = (name, fallback) => {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');

export default function PriorityMatrix({ data, loading, error }) {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);

  const colors = useMemo(() => ({
    act_now: cssVar('--accent2', '#e74c3c'),
    hedge: cssVar('--accent3', '#e67e22'),
    monitor: cssVar('--accent4', '#0984e3'),
    low_priority: cssVar('--muted', '#7a8590'),
    axis: cssVar('--border', '#d0d5da'),
    text: cssVar('--text', '#1a1f24'),
    faint: cssVar('--muted', '#7a8590'),
    surface: cssVar('--surface', '#ffffff'),
  }), []);

  if (loading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Computing priority matrix…</div>;
  if (error) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>;
  if (!data) return null;

  const items = data.items || [];
  const priced = items.filter(i => i.has_volume);
  if (priced.length === 0) {
    return (
      <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
        <div style={{ color: 'var(--text-secondary)' }}>
          No spend exposure yet — add volumes to your cost models so products can be placed by spend against volatility.
        </div>
      </div>
    );
  }

  // Layout
  const W = 720, H = 460;
  const pad = { l: 78, r: 24, t: 24, b: 52 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;

  const maxVol = Math.max(data.volatility_threshold * 2, ...priced.map(i => i.volatility_pct), 1);
  const maxExp = Math.max(data.exposure_threshold * 2, ...priced.map(i => i.spend_exposure), 1);
  const sym = curSym(data.reporting_currency);

  const xOf = (v) => pad.l + Math.min(1, v / maxVol) * plotW;
  const yOf = (e) => pad.t + plotH - Math.min(1, e / maxExp) * plotH;   // invert: high exposure = top
  const xThr = xOf(data.volatility_threshold);
  const yThr = yOf(data.exposure_threshold);

  // Quadrant tint rects (top-left, top-right, bottom-left, bottom-right)
  const zones = [
    { key: 'hedge', x: pad.l, y: pad.t, w: xThr - pad.l, h: yThr - pad.t },                          // lo vol, hi exp
    { key: 'act_now', x: xThr, y: pad.t, w: pad.l + plotW - xThr, h: yThr - pad.t },                 // hi vol, hi exp
    { key: 'low_priority', x: pad.l, y: yThr, w: xThr - pad.l, h: pad.t + plotH - yThr },            // lo vol, lo exp
    { key: 'monitor', x: xThr, y: yThr, w: pad.l + plotW - xThr, h: pad.t + plotH - yThr },          // hi vol, lo exp
  ];

  const fmtExp = (e) => `${sym}${Math.round(e).toLocaleString()}`;

  const handleCsv = () => exportCsv(
    'priority-matrix.csv',
    ['Product', 'Supplier', 'Region', 'Volatility %', `Spend Exposure (${data.reporting_currency})`, 'Quadrant'],
    items.map(i => [i.product_name, i.supplier_name || '', i.region, i.volatility_pct, i.spend_exposure, QUADRANT[i.quadrant]?.label || i.quadrant]),
  );

  const handlePng = () => {
    const svg = svgRef.current;
    if (!svg) return;
    const xml = new XMLSerializer().serializeToString(svg);
    const svg64 = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(xml)));
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = W * 2; canvas.height = H * 2;          // 2× for crispness
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = colors.surface;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const a = document.createElement('a');
      a.download = 'priority-matrix.png';
      a.href = canvas.toDataURL('image/png');
      a.click();
    };
    img.src = svg64;
  };

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {Object.entries(QUADRANT).map(([k, q]) => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--muted)' }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: colors[k], display: 'inline-block' }} />
              {q.label}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handleCsv}>Export CSV</button>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handlePng}>Export PNG</button>
        </div>
      </div>

      <div className="ca-scroll-x">
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 520, maxWidth: W, display: 'block', margin: '0 auto' }}>
          {/* quadrant tints */}
          {zones.map(z => (
            <rect key={z.key} x={z.x} y={z.y} width={Math.max(0, z.w)} height={Math.max(0, z.h)}
              fill={colors[z.key]} opacity="0.06" />
          ))}
          {/* quadrant labels */}
          <text x={xThr + 8} y={pad.t + 16} fontSize="11" fontWeight="700" fill={colors.act_now} opacity="0.8">ACT NOW</text>
          <text x={pad.l + 8} y={pad.t + 16} fontSize="11" fontWeight="700" fill={colors.hedge} opacity="0.8">HEDGE</text>
          <text x={xThr + 8} y={pad.t + plotH - 8} fontSize="11" fontWeight="700" fill={colors.monitor} opacity="0.8">MONITOR</text>
          <text x={pad.l + 8} y={pad.t + plotH - 8} fontSize="11" fontWeight="700" fill={colors.low_priority} opacity="0.7">LOW PRIORITY</text>

          {/* axes */}
          <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + plotH} stroke={colors.axis} strokeWidth="1" />
          <line x1={pad.l} y1={pad.t + plotH} x2={pad.l + plotW} y2={pad.t + plotH} stroke={colors.axis} strokeWidth="1" />
          {/* threshold splitters (dashed) */}
          <line x1={xThr} y1={pad.t} x2={xThr} y2={pad.t + plotH} stroke={colors.faint} strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />
          <line x1={pad.l} y1={yThr} x2={pad.l + plotW} y2={yThr} stroke={colors.faint} strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />

          {/* axis titles */}
          <text x={pad.l + plotW / 2} y={H - 14} fontSize="12" fontWeight="600" fill={colors.text} textAnchor="middle">
            Index volatility (QoQ std-dev, %) →
          </text>
          <text x={16} y={pad.t + plotH / 2} fontSize="12" fontWeight="600" fill={colors.text} textAnchor="middle"
            transform={`rotate(-90 16 ${pad.t + plotH / 2})`}>
            Spend exposure ({data.reporting_currency}) →
          </text>

          {/* points */}
          {priced.map(i => {
            const cx = xOf(i.volatility_pct);
            const cy = yOf(i.spend_exposure);
            const c = colors[i.quadrant] || colors.low_priority;
            return (
              <g key={i.cost_model_id}
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                style={{ cursor: 'pointer' }}>
                <circle cx={cx} cy={cy} r={hover?.cost_model_id === i.cost_model_id ? 8 : 6}
                  fill={c} fillOpacity="0.85" stroke={colors.surface} strokeWidth="1.5" />
              </g>
            );
          })}
        </svg>
      </div>

      {hover && (
        <div style={{ marginTop: 8, padding: '8px 12px', background: 'var(--surface2)', borderRadius: 6, fontSize: 12 }}>
          <strong>{hover.product_name}</strong>
          {hover.supplier_name ? ` · ${hover.supplier_name}` : ''} · {hover.region}
          <span style={{ marginLeft: 10, color: 'var(--muted)' }}>
            volatility <strong style={{ color: 'var(--text)' }}>{hover.volatility_pct.toFixed(2)}%</strong>
            {'  ·  '}exposure <strong style={{ color: 'var(--text)' }}>{fmtExp(hover.spend_exposure)}</strong>
            {'  ·  '}<span style={{ color: colors[hover.quadrant] }}>{QUADRANT[hover.quadrant]?.label}</span>
          </span>
        </div>
      )}
    </div>
  );
}
