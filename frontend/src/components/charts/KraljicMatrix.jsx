import { KRALJIC_SPACE, kraljicQuadrant, normalizeKraljicBadge } from '../intel/palette';

/* Kraljic 2×2 with the category's dot, in the delivered reports' own
 * coordinate system (viewBox 0 0 260 260; quadrants x 30–250 / y 10–230,
 * split at 140 / 120). The playbook's `category.kraljic` carries {cx, cy}
 * in exactly that space, so the dot is placed as-is — never rescaled.
 *
 *   <KraljicMatrix kraljic={playbook.kraljic} />          // {cx, cy, badge, label, boundary}
 *   <KraljicMatrix cx={100} cy={65} size={220} />
 *
 * Axis captions default to the reports' wording (SUPPLY COMPLEXITY → /
 * BUSINESS IMPACT →); pass xLabel / yLabel to change them. A boundary case
 * gets a "?" in the dot, as in the mockup. */
export default function KraljicMatrix({
  kraljic, cx, cy, boundary, size = 240,
  xLabel = 'Supply complexity', yLabel = 'Business impact', ariaLabel,
}) {
  const k = kraljic || {};
  const x = cx ?? k.cx;
  const y = cy ?? k.cy;
  const isBoundary = boundary ?? k.boundary;
  const has = Number.isFinite(Number(x)) && Number.isFinite(Number(y));
  const active = has ? (normalizeKraljicBadge(k.badge) || kraljicQuadrant(Number(x), Number(y))) : null;
  const { x0, x1, y0, y1, midX, midY } = KRALJIC_SPACE;
  const qw = midX - x0;
  const qh = midY - y0;
  // Labels sit in each quadrant's outer corner rather than its centre, so the
  // dot (which tends to land mid-quadrant) never covers its own label.
  const quads = [
    { key: 'leverage', label: 'Leverage', x: x0, y: y0, lx: x0 + 8, ly: y0 + 15, anchor: 'start' },
    { key: 'strategic', label: 'Strategic', x: midX, y: y0, lx: x1 - 8, ly: y0 + 15, anchor: 'end' },
    { key: 'noncritical', label: 'Non-critical', x: x0, y: midY, lx: x0 + 8, ly: y1 - 8, anchor: 'start' },
    { key: 'bottleneck', label: 'Bottleneck', x: midX, y: midY, lx: x1 - 8, ly: y1 - 8, anchor: 'end' },
  ];
  const label = ariaLabel || (has
    ? `Kraljic position: ${k.label || active}${isBoundary ? ' (boundary)' : ''}`
    : 'Kraljic position not yet assessed');

  return (
    <svg viewBox="0 0 260 260" width={size} height={size} role="img" aria-label={label}
      style={{ maxWidth: '100%', height: 'auto', display: 'block', flexShrink: 0 }}>
      {quads.map((q) => (
        <g key={q.key}>
          <rect x={q.x} y={q.y} width={qw} height={qh} rx={4}
            fill={`var(--kq-${q.key}-bg)`} stroke="var(--surface)" strokeWidth={1.5} />
          <text x={q.lx} y={q.ly} textAnchor={q.anchor} fontSize={10}
            fontWeight={active === q.key ? 800 : 600} fill={`var(--kq-${q.key}-fg)`}
            opacity={!active || active === q.key ? 1 : 0.7}>
            {q.label}
          </text>
        </g>
      ))}
      <text x={(x0 + x1) / 2} y={248} textAnchor="middle" fontSize={8} letterSpacing={0.6} fill="var(--muted)">
        {xLabel.toUpperCase()} →
      </text>
      <text x={12} y={(y0 + y1) / 2} textAnchor="middle" fontSize={8} letterSpacing={0.6} fill="var(--muted)"
        transform={`rotate(-90 12 ${(y0 + y1) / 2})`}>
        {yLabel.toUpperCase()} →
      </text>
      {has ? (
        <g>
          <circle cx={Number(x)} cy={Number(y)} r={10} fill="var(--kq-dot)" stroke="var(--surface)" strokeWidth={2.5} />
          {isBoundary && (
            <text x={Number(x)} y={Number(y) + 3.5} textAnchor="middle" fontSize={10} fontWeight={800}
              fill="var(--ix-on-palette)">?</text>
          )}
        </g>
      ) : (
        <text x={midX} y={midY + 4} textAnchor="middle" fontSize={10} fill="var(--muted)">Not yet assessed</text>
      )}
    </svg>
  );
}
