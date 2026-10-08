/* Position-in-range gauge (cycle position, volatility percentile).
 *
 *   <Gauge value={cycle.percentile} valueLabel={`${fmt.ordinal(p)} percentile`}
 *          labels={{ left: '24M low', right: '24M high' }} ends={{ left: '95.2', right: '104.5' }} />
 *   <Gauge variant="arc" value={vol.percentile} valueLabel="62nd pct" size={240}
 *          zones={[{ to: 33, label: 'Calm' }, { to: 67, label: 'Moderate' }, { to: 100, label: 'Volatile' }]} />
 *
 * variant 'track' (default) is the mockup's gradient bar + marker (products_tab1);
 * 'arc' is a semicircle with a needle and zone labels.
 * zones: [{ to, color?, label? }] on the min…max scale, ascending. Default three
 * zones green → amber → red. value null → greyed, "n/a". */

const DEFAULT_ZONES = [
  { to: 100 / 3, color: 'var(--accent)', label: 'Low' },
  { to: 200 / 3, color: 'var(--accent3)', label: 'Mid' },
  { to: 100, color: 'var(--accent2)', label: 'High' },
];
const ZONE_COLORS = ['var(--accent)', 'var(--accent3)', 'var(--accent2)', 'var(--accent4)'];

function resolveZones(zones, min, max) {
  const list = zones && zones.length ? zones : DEFAULT_ZONES.map((z) => ({ ...z, to: min + ((z.to - 0) / 100) * (max - min) }));
  let from = min;
  return list.map((z, i) => {
    const out = { from, to: z.to, label: z.label, color: z.color || ZONE_COLORS[Math.min(i, ZONE_COLORS.length - 1)] };
    from = z.to;
    return out;
  });
}

const polar = (cx, cy, r, deg) => {
  const a = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
};

export default function Gauge({
  value, min = 0, max = 100, variant = 'track', zones, valueLabel, sub, labels, ends,
  showZoneLabels = true, size = 240, labelMargin = 52, ariaLabel,
}) {
  const has = value != null && Number.isFinite(Number(value));
  const v = has ? Math.max(min, Math.min(max, Number(value))) : null;
  const frac = has ? (v - min) / (max - min || 1) : 0;
  const zs = resolveZones(zones, min, max);
  const zone = has ? zs.find((z) => v <= z.to) || zs[zs.length - 1] : null;
  const shown = valueLabel ?? (has ? String(Math.round(v)) : 'n/a');
  const a11y = { role: 'img', 'aria-label': ariaLabel || `${shown}${zone?.label ? ` — ${zone.label}` : ''}` };

  if (variant === 'arc') {
    // Zone labels sit outside the arc, so the radius leaves `labelMargin`
    // each side for them (mono text is wide: "Volatile" is ~45px).
    const W = size;
    const r = Math.max(30, W / 2 - labelMargin);
    const cx = W / 2;
    const cy = r + 24;
    const H = cy + 30;
    const sw = Math.max(8, r * 0.16);
    const toDeg = (x) => 180 - ((x - min) / (max - min || 1)) * 180;
    const arc = (a0, a1, rr) => {
      const [x0, y0] = polar(cx, cy, rr, a0);
      const [x1, y1] = polar(cx, cy, rr, a1);
      return `M${x0.toFixed(2)},${y0.toFixed(2)} A${rr},${rr} 0 0 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
    };
    const needleDeg = 180 - frac * 180;
    const [nx, ny] = polar(cx, cy, r - sw * 0.2, needleDeg);
    return (
      <div className="ix-gauge" {...a11y}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: '100%', height: 'auto' }}>
          {zs.map((z, i) => (
            <path key={i} d={arc(toDeg(z.from) - (i ? 0.8 : 0), toDeg(z.to) + (i < zs.length - 1 ? 0.8 : 0), r)}
              fill="none" stroke={z.color} strokeWidth={sw} strokeLinecap="butt"
              opacity={!has ? 0.25 : zone === z ? 1 : 0.4} />
          ))}
          {showZoneLabels && zs.map((z, i) => {
            if (!z.label) return null;
            const mid = toDeg((z.from + z.to) / 2);
            const [lx, ly] = polar(cx, cy, r + sw / 2 + 10, mid);
            return (
              <text key={`l${i}`} x={lx} y={ly + 3} textAnchor={mid > 100 ? 'end' : mid < 80 ? 'start' : 'middle'}
                fontSize={9} fill={zone === z ? 'var(--text)' : 'var(--muted)'} fontWeight={zone === z ? 700 : 500}>
                {z.label}
              </text>
            );
          })}
          {has && (
            <g>
              <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="var(--text)" strokeWidth={2.2} strokeLinecap="round" />
              <circle cx={cx} cy={cy} r={4.5} fill="var(--text)" stroke="var(--surface)" strokeWidth={2} />
            </g>
          )}
          <text x={cx - r} y={cy + 16} textAnchor="middle" fontSize={9} fill="var(--muted)">{labels?.left ?? min}</text>
          <text x={cx + r} y={cy + 16} textAnchor="middle" fontSize={9} fill="var(--muted)">{labels?.right ?? max}</text>
        </svg>
        <div className="ix-gauge-value" style={{ color: zone ? zone.color : 'var(--muted)', marginTop: -6 }}>{shown}</div>
        {sub && <div className="ix-gauge-sub">{sub}</div>}
      </div>
    );
  }

  // Track: gradient bar through the zone colours, marker at the value.
  const stops = zs.map((z) => `${z.color} ${(((z.from + z.to) / 2 - min) / (max - min || 1)) * 100}%`).join(', ');
  return (
    <div className={`ix-track${has ? '' : ' is-empty'}`} {...a11y}>
      <div className="ix-track-top">
        <span>{labels?.left ?? ''}</span>
        <span className="ix-track-value" style={{ color: zone ? zone.color : 'var(--muted)' }}>{shown}</span>
        <span>{labels?.right ?? ''}</span>
      </div>
      <div className="ix-track-bar" style={{ background: `linear-gradient(90deg, ${stops})` }}>
        {has && <span className="ix-track-marker" style={{ left: `${frac * 100}%` }} />}
      </div>
      {ends && (
        <div className="ix-track-ends"><span>{ends.left}</span><span>{ends.right}</span></div>
      )}
      {showZoneLabels && zones && zs.some((z) => z.label) && (
        <div className="ix-track-zones">
          {zs.map((z, i) => (
            <span key={i} style={{ flex: `${z.to - z.from} 1 0`, fontWeight: zone === z ? 700 : 400, color: zone === z ? 'var(--text-secondary)' : undefined }}>
              {z.label}
            </span>
          ))}
        </div>
      )}
      {sub && <div className="ix-gauge-sub" style={{ textAlign: 'left', marginTop: 6 }}>{sub}</div>}
    </div>
  );
}
