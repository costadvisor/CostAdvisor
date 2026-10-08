import { pct as fmtPct, trend as trendOf } from './fmt';
import {
  gemstone, gemstoneColor, normalizeKraljicBadge, kraljicQuadrant, KRALJIC_QUADRANTS,
} from './palette';
import { STATUS_LABELS, STATUS_TONES } from './vocab';

/* Trend over a period: ↑ +9.7% / ↓ −5.8% / → 0.0%. A rising cost reads as risk
 * (the app's own index colouring: up = --accent2, down = --accent).
 * `dir` ('up'|'down'|'flat', e.g. the API's `trend_dir`) sets the arrow and
 * colour directly; otherwise they come from `pct` against `threshold`, which
 * defaults to the API's own ±2 points so a badge agrees with the filters. */
const TREND_DIRS = new Set(['up', 'down', 'flat']);
export function TrendBadge({ pct, dir: dirProp, threshold = 2, arrow = true, size, title }) {
  const dir = TREND_DIRS.has(dirProp) ? dirProp : pct == null ? 'flat' : trendOf(pct, threshold);
  const glyph = dir === 'up' ? '↑' : dir === 'down' ? '↓' : '→';
  return (
    <span className={`ix-badge trend-${dir}${size === 'lg' ? ' lg' : ''}`} title={title}>
      {arrow && <span aria-hidden>{glyph}</span>}
      {pct == null ? '—' : fmtPct(pct)}
    </span>
  );
}

/* Supply status of a catalogue card: whether the supplier floor is met.
 * `status` is the API's {code, label, tone[, description]}. A badge never
 * carries a number (house rules 4 and 10b). The description, when the payload
 * has one, is the tooltip. */
const SUPPLY_TONES = new Set(['green', 'green-amber', 'amber', 'grey']);
export function SupplyBadge({ status, size, title }) {
  if (!status) return null;
  const code = status.code;
  const tone = SUPPLY_TONES.has(status.tone) ? status.tone : (STATUS_TONES[code] || 'grey');
  const label = status.label || STATUS_LABELS[code] || code;
  return (
    <span className={`ix-badge plain sup sup-${tone}${size === 'lg' ? ' lg' : ''}`}
      title={title ?? status.description ?? label}>
      <span className="sup-dot" aria-hidden />
      {label}
    </span>
  );
}

/* One tone map for every status vocabulary the demo uses:
 *   demand-axis category  servable / partial / build
 *   strategy category     Active / Not started
 *   opportunity (lever)   Identified / Under evaluation / Approved / Actioned / Rejected
 *   action                Not started / In progress / Blocked / Done / Overdue
 *   review                Pending / Reviewed
 * `tone` overrides: good | warn | bad | info | neutral | struck. */
const STATUS_TONE = {
  servable: 'good', partial: 'warn', build: 'neutral',
  active: 'good', 'not started': 'neutral', 'not yet assessed': 'neutral',
  identified: 'neutral', 'under evaluation': 'warn', approved: 'info', actioned: 'good', rejected: 'struck',
  'in progress': 'info', blocked: 'bad', done: 'good', complete: 'good', completed: 'good', overdue: 'bad',
  pending: 'warn', reviewed: 'good', draft: 'neutral',
};
export function StatusBadge({ status, tone, size, title }) {
  if (!status) return null;
  const key = String(status).toLowerCase().replace(/_/g, ' ').trim();
  const t = tone || STATUS_TONE[key] || 'neutral';
  const label = String(status).replace(/_/g, ' ');
  const text = label.charAt(0).toUpperCase() + label.slice(1);
  return <span className={`ix-badge plain st-${t}${size === 'lg' ? ' lg' : ''}`} title={title}>{text}</span>;
}

/* Kraljic position badge. Pass the playbook's `kraljic` object
 * ({cx, cy, badge, label, boundary}) or the fields directly. A boundary case
 * ("Leverage / Strategic") renders split, as in the mockup. */
export function KraljicBadge({ kraljic, badge, label, boundary, size }) {
  const k = kraljic || {};
  const b = normalizeKraljicBadge(badge ?? k.badge) || kraljicQuadrant(k.cx, k.cy);
  const isBoundary = boundary ?? k.boundary;
  const text = label ?? k.label ?? (b ? KRALJIC_QUADRANTS[b].label : null);
  const lg = size === 'lg' ? ' lg' : '';
  if (!b && !text) return <span className={`ix-badge plain kq-none${lg}`}>Not yet assessed</span>;
  if (isBoundary && text && text.includes('/')) {
    const [a, c] = text.split('/').map((s) => s.trim());
    return (
      <span className={`ix-badge plain kq-boundary${lg}`} title={`${text} — on the boundary`}>
        <span className="kq-a">{a}</span><span aria-hidden>/</span><span className="kq-b">{c}</span>
      </span>
    );
  }
  return <span className={`ix-badge plain kq-${b || 'none'}${lg}`}>{text}</span>;
}

/* Gemstone lever category, by code ("VC") or name ("Volume Concentration").
 * variant: 'dot' (dot + name), 'boxed' (dot + name in a pill), 'code' (filled code chip). */
export function GemstoneTag({ gemstone: g, variant = 'dot', label }) {
  const info = gemstone(g);
  const name = label ?? info?.name ?? String(g ?? '');
  const style = { '--gem': gemstoneColor(g) };
  if (variant === 'code') {
    return <span className="ix-gem-code" style={style} title={name}>{info?.code ?? name.slice(0, 2).toUpperCase()}</span>;
  }
  return (
    <span className={`ix-gem${variant === 'boxed' ? ' boxed' : ''}`} style={style}>
      <span className="ix-gem-dot" aria-hidden />
      {name}
    </span>
  );
}
