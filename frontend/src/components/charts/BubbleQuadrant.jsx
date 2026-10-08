import { useMemo, useState, useRef, useEffect } from 'react';
import { GEMSTONES, gemstone, gemstoneColor } from '../intel/palette';

/* Impact vs Ease bubble chart (Strategy › Opportunities).
 *
 *   <BubbleQuadrant
 *     points={levers.filter(l => l.plotted).map(l => ({
 *       id: l.id, number: l.number, x: l.ease, y: l.savings_score, gemstone: l.gemstone, title: l.title }))}
 *     selectedId={sel} onSelect={setSel} highlightIds={matchingObjectiveIds} />
 *
 * Orientation (from the strategy mockup's bubbleChartSvg): x = ease 1→5 (right
 * = easier), y = impact 1→5 (up = more), split at 3. Top-left EVALUATE, top-
 * right QUICK WINS, bottom-left DEPRIORITISE, bottom-right LOW EFFORT.
 *
 * Placement. A lone point sits on its exact score, as in the mockup (a score
 * of 3 sits on a divider). Points that share a score are packed into that
 * score's cell (one score step wide and tall, centred on the score), ordered
 * by number, shrinking the discs as needed. Nothing may cross a quadrant
 * divider: on a divider (score 3) the group lines up along the divider, so
 * every disc stays centred on it. A group that does not fit becomes one
 * cluster bubble "+N" on the exact score; hovering or clicking it opens the
 * list of its opportunities. Bubbles are keyboard-focusable buttons. */

const DEFAULT_QUADS = { tl: 'Evaluate', tr: 'Quick wins', bl: 'Deprioritise', br: 'Low effort' };

// The mockup's radii: 9.5 for one digit, 11 for two. Groups may shrink to R_MIN.
const discR = (num) => (String(num ?? '').length > 1 ? 11 : 9.5);
const R_MIN = 8;
const GAP = 1;
const CLUSTER_R = 14;

const byNumber = (a, b) => (Number(a.number) || 0) - (Number(b.number) || 0) || String(a.id).localeCompare(String(b.id));

export default function BubbleQuadrant({
  points = [], onSelect, selectedId = null, highlightIds, dimIds, size = 380, min = 1, max = 5,
  xLabel = 'Ease of implementation', yLabel = 'Impact', quadrantLabels = DEFAULT_QUADS,
  legend = true, ariaLabel = 'Impact versus ease of implementation',
}) {
  const W = size;
  const H = size;
  const pad = 44;
  const span = max - min || 1;
  const xFor = (v) => pad + ((v - min) / span) * (W - 2 * pad);
  const yFor = (v) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const midX = pad + (W - 2 * pad) / 2;
  const midY = pad + (H - 2 * pad) / 2;
  const midV = min + span / 2;
  const hl = highlightIds ? new Set(highlightIds) : null;
  const dim = dimIds ? new Set(dimIds) : null;

  const [open, setOpen] = useState(null); // { key, pinned }
  const closeTimer = useRef(null);
  const focusList = useRef(false); // opened from the keyboard: move focus into the list
  const plotRef = useRef(null);
  const clusterRefs = useRef(new Map());

  const { placed, clusters } = useMemo(() => {
    const unit = (W - 2 * pad) / span; // one score step, in px (same on both axes)

    /* Range of disc centres allowed on one axis for a score `v` drawn at `c`
     * px: inside the score's cell, never past the plot edge (an edge score
     * may overflow the frame as a lone bubble does), and never across the
     * divider. On the divider itself the range is the divider. */
    const range = (v, c, lo0, hi0, mid, r) => {
      if (v === midV) return [c, c];
      let lo = Math.max(c - unit / 2 + r, lo0);
      let hi = Math.min(c + unit / 2 - r, hi0);
      if (c < mid) hi = Math.min(hi, mid - r - 1);
      else lo = Math.max(lo, mid + r + 1);
      return lo > hi ? [c, c] : [lo, hi];
    };

    /* The largest disc (≤ the group's own size, ≥ R_MIN) whose grid fits the
     * ranges; among fitting grids, the squarest. null → cluster. */
    const pack = (n, ax, ay, vx, vy, rStart) => {
      for (let r = rStart; r >= R_MIN - 1e-9; r -= 0.5) {
        const [xlo, xhi] = range(vx, ax, pad, W - pad, midX, r);
        // y px grows downwards, so the plot's top edge is the low bound.
        const [ylo, yhi] = range(vy, ay, pad, H - pad, midY, r);
        const step = 2 * r + GAP;
        const maxCols = Math.floor((xhi - xlo) / step + 1e-6) + 1;
        const maxRows = Math.floor((yhi - ylo) / step + 1e-6) + 1;
        if (maxCols * maxRows < n) continue;
        let best = null;
        for (let cols = 1; cols <= Math.min(maxCols, n); cols += 1) {
          const rows = Math.ceil(n / cols);
          if (rows > maxRows) continue;
          const score = [Math.max(cols, rows), cols * rows - n, rows - cols];
          if (!best || score[0] < best.score[0] || (score[0] === best.score[0]
            && (score[1] < best.score[1] || (score[1] === best.score[1] && score[2] < best.score[2])))) {
            best = { cols, rows, score };
          }
        }
        if (!best) continue;
        const { cols, rows } = best;
        const bw = (cols - 1) * step;
        const bh = (rows - 1) * step;
        const cx0 = Math.min(Math.max(ax, xlo + bw / 2), xhi - bw / 2);
        const cy0 = Math.min(Math.max(ay, ylo + bh / 2), yhi - bh / 2);
        const out = [];
        for (let i = 0; i < n; i += 1) {
          const row = Math.floor(i / cols);
          const inRow = row === rows - 1 ? n - row * cols : cols;
          const col = i - row * cols;
          out.push({
            cx: cx0 - ((inRow - 1) * step) / 2 + col * step,
            cy: cy0 - bh / 2 + row * step,
            r,
          });
        }
        return out;
      }
      return null;
    };

    const valid = points.filter((p) => Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y)));
    const groups = new Map();
    valid.forEach((p) => {
      const k = `${Number(p.x)}|${Number(p.y)}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(p);
    });
    const out = [];
    const clusterList = [];
    groups.forEach((g, key) => {
      g.sort(byNumber);
      const n = g.length;
      const vx = Number(g[0].x);
      const vy = Number(g[0].y);
      const ax = xFor(vx);
      const ay = yFor(vy);
      if (n === 1) {
        out.push({ ...g[0], cx: ax, cy: ay, r: discR(g[0].number) });
        return;
      }
      const spots = pack(n, ax, ay, vx, vy, Math.max(...g.map((p) => discR(p.number))));
      if (spots) {
        g.forEach((p, i) => out.push({ ...p, ...spots[i] }));
      } else {
        clusterList.push({ key, cx: ax, cy: ay, n, sx: g[0].x, sy: g[0].y, items: g });
      }
    });
    // Draw the selected bubble last so its ring is never covered.
    out.sort((a, b) => (a.id === selectedId) - (b.id === selectedId));
    return { placed: out, clusters: clusterList };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, selectedId, size, min, max]);

  const openCluster = open ? clusters.find((c) => c.key === open.key) : null;

  const cancelClose = () => { if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; } };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setOpen((o) => (o && !o.pinned ? null : o)), 180);
  };
  const close = (refocus) => {
    cancelClose();
    if (open && refocus) clusterRefs.current.get(open.key)?.focus();
    setOpen(null);
  };
  useEffect(() => () => cancelClose(), []);

  // A pinned list closes on a click outside the cluster and the list.
  useEffect(() => {
    if (!open?.pinned) return undefined;
    const onDown = (e) => {
      const root = plotRef.current;
      if (!root) return;
      const inPop = root.querySelector('.ix-bubble-pop')?.contains(e.target);
      const inCluster = clusterRefs.current.get(open.key)?.contains(e.target);
      if (!inPop && !inCluster) setOpen(null);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  useEffect(() => {
    if (!open || !focusList.current) return;
    focusList.current = false;
    const root = plotRef.current;
    (root?.querySelector('button.ix-bubble-pop-item') || root?.querySelector('.ix-bubble-pop-close'))?.focus();
  }, [open]);

  // A cluster that disappears (filters, edits) takes its list with it.
  useEffect(() => {
    if (open && !openCluster) setOpen(null);
  }, [open, openCluster]);

  const legendItems = legend === true
    ? GEMSTONES.map((g) => ({ label: g.name, color: gemstoneColor(g.code) }))
    : Array.isArray(legend) ? legend : null;

  const q = { ...DEFAULT_QUADS, ...quadrantLabels };
  const qText = { fontSize: 9, fontWeight: 700, letterSpacing: 0.8, fontFamily: "'JetBrains Mono', monospace" };
  const ticks = [];
  for (let v = min; v <= max; v += 1) ticks.push(v);

  const scoreText = (x, y) => `ease ${x}, impact ${y}`;

  return (
    <div className="ix-bubble-wrap">
      <div className="ix-bubble-plot" ref={plotRef}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="group" aria-label={ariaLabel}
          style={{ maxWidth: '100%', height: 'auto', display: 'block' }}>
          <rect x={pad} y={pad} width={midX - pad} height={midY - pad} fill="var(--neutral-bg)" />
          <rect x={midX} y={pad} width={W - pad - midX} height={midY - pad} fill="var(--success-bg)" />
          <rect x={pad} y={midY} width={midX - pad} height={H - pad - midY} fill="var(--neutral-bg-soft)" />
          <rect x={midX} y={midY} width={W - pad - midX} height={H - pad - midY} fill="var(--warn-bg)" />
          <rect x={pad} y={pad} width={W - 2 * pad} height={H - 2 * pad} fill="none" stroke="var(--border)" />
          <line x1={pad} y1={midY} x2={W - pad} y2={midY} stroke="var(--border-light)" strokeDasharray="3 3" />
          <line x1={midX} y1={pad} x2={midX} y2={H - pad} stroke="var(--border-light)" strokeDasharray="3 3" />
          <text x={(pad + midX) / 2} y={pad + 14} textAnchor="middle" fill="var(--muted)" {...qText}>{q.tl.toUpperCase()}</text>
          <text x={(midX + W - pad) / 2} y={pad + 14} textAnchor="middle" fill="var(--accent-ink, var(--accent))" {...qText}>{q.tr.toUpperCase()}</text>
          <text x={(pad + midX) / 2} y={H - pad - 7} textAnchor="middle" fill="var(--muted)" {...qText}>{q.bl.toUpperCase()}</text>
          <text x={(midX + W - pad) / 2} y={H - pad - 7} textAnchor="middle" fill="var(--accent3-ink, var(--accent3))" {...qText}>{q.br.toUpperCase()}</text>
          {ticks.map((v) => (
            <g key={v}>
              <text x={xFor(v)} y={H - pad + 13} textAnchor="middle" fontSize={8.5} fill="var(--muted)">{v}</text>
              <text x={pad - 8} y={yFor(v) + 3} textAnchor="end" fontSize={8.5} fill="var(--muted)">{v}</text>
            </g>
          ))}
          <text x={W / 2} y={H - 8} textAnchor="middle" fontSize={8.5} fill="var(--muted)" letterSpacing={0.8}>
            {xLabel.toUpperCase()} →
          </text>
          <text x={12} y={H / 2} textAnchor="middle" fontSize={8.5} fill="var(--muted)" letterSpacing={0.8}
            transform={`rotate(-90 12 ${H / 2})`}>
            {yLabel.toUpperCase()} →
          </text>
          {clusters.map((c) => {
            const ids = c.items.map((p) => p.id);
            const allDim = dim && ids.every((id) => dim.has(id));
            const anyHl = hl && ids.some((id) => hl.has(id));
            const hasSel = selectedId != null && ids.includes(selectedId);
            const isOpen = open?.key === c.key;
            const R = CLUSTER_R;
            const circ = 2 * Math.PI * R;
            const seg = circ / c.n;
            const numbers = c.items.map((p) => `#${p.number ?? ''}`).join(', ');
            const label = `${c.n} opportunities at ${scoreText(c.sx, c.sy)}: ${numbers}. Show the list.`;
            const toggle = () => {
              cancelClose();
              setOpen((o) => (o?.key === c.key && o.pinned ? null : { key: c.key, pinned: true }));
            };
            return (
              <g key={`cluster-${c.key}`} className={`ix-bubble ix-bubble-cluster${allDim ? ' is-dim' : ''}`}
                ref={(el) => { if (el) clusterRefs.current.set(c.key, el); else clusterRefs.current.delete(c.key); }}
                role="button" tabIndex={0} aria-label={label} aria-expanded={isOpen} aria-haspopup="true"
                onClick={toggle}
                onMouseEnter={() => { cancelClose(); setOpen((o) => (o?.pinned ? o : { key: c.key, pinned: false })); }}
                onMouseLeave={scheduleClose}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    focusList.current = !(open?.key === c.key && open.pinned);
                    toggle();
                  }
                  else if (e.key === 'Escape') close(false);
                }}>
                <title>{`${c.n} opportunities at ${scoreText(c.sx, c.sy)}: ${numbers}`}</title>
                {hasSel && <circle cx={c.cx} cy={c.cy} r={R + 5} fill="none" stroke="var(--text)" strokeWidth={2} />}
                <circle className="ix-bubble-cluster-back" cx={c.cx} cy={c.cy} r={R + 2.2} fill="var(--surface)"
                  stroke={anyHl ? 'var(--accent)' : 'none'} strokeWidth={anyHl ? 2.2 : 0}
                  strokeDasharray={anyHl ? '2.5 1.5' : undefined} />
                <g transform={`rotate(-90 ${c.cx} ${c.cy})`}>
                  {c.items.map((p, i) => (
                    <circle key={p.id} cx={c.cx} cy={c.cy} r={R} fill="none"
                      stroke={p.color || gemstoneColor(p.gemstone)} strokeWidth={5}
                      strokeOpacity={dim?.has(p.id) ? 0.3 : 0.95}
                      strokeDasharray={`${Math.max(0.5, seg - (c.n > 1 ? 1.2 : 0))} ${circ}`}
                      strokeDashoffset={-i * seg} />
                  ))}
                </g>
                <circle cx={c.cx} cy={c.cy} r={R - 2.5} fill="var(--surface)" />
                <text x={c.cx} y={c.cy + 3.4} textAnchor="middle" fontSize={9.5} fontWeight={700}
                  fill="var(--text)" pointerEvents="none">{`+${c.n}`}</text>
              </g>
            );
          })}
          {placed.map((p) => {
            const color = p.color || gemstoneColor(p.gemstone);
            const isSel = selectedId != null && p.id === selectedId;
            const isHl = hl?.has(p.id);
            const isDim = dim?.has(p.id);
            const num = p.number ?? '';
            const { r } = p;
            const name = gemstone(p.gemstone)?.name;
            const label = `#${num}${p.title ? ` ${p.title}` : ''}${name ? ` (${name})` : ''} — ${scoreText(p.x, p.y)}`;
            const activate = () => onSelect?.(p.id);
            const small = r < 9.5;
            return (
              <g key={p.id} className={`ix-bubble${isDim ? ' is-dim' : ''}`}
                role={onSelect ? 'button' : 'img'} tabIndex={onSelect ? 0 : undefined} aria-label={label}
                aria-pressed={onSelect ? isSel : undefined}
                onClick={activate}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } }}>
                <title>{label}</title>
                {isSel && <circle cx={p.cx} cy={p.cy} r={r + 3.5} fill="none" stroke="var(--text)" strokeWidth={2} />}
                <circle className="ix-bubble-disc" cx={p.cx} cy={p.cy} r={r} fill={color} fillOpacity={0.92}
                  stroke={isHl ? 'var(--accent)' : 'var(--surface)'} strokeWidth={isHl ? 2.2 : 1.6}
                  strokeDasharray={isHl ? '2.5 1.5' : undefined} />
                <text x={p.cx} y={p.cy + (small ? 2.8 : 3.2)} textAnchor="middle"
                  fontSize={small ? (String(num).length > 1 ? 7.5 : 8) : 9}
                  fontWeight={700} fill="var(--ix-on-palette)" pointerEvents="none">{num}</text>
              </g>
            );
          })}
        </svg>
        {openCluster && (() => {
          const c = openCluster;
          const right = c.cx > W / 2;
          const side = right
            ? { right: `calc(${((W - c.cx) / W) * 100}% + ${CLUSTER_R + 8}px)` }
            : { left: `calc(${(c.cx / W) * 100}% + ${CLUSTER_R + 8}px)` };
          return (
            <div className="ix-bubble-pop" role="dialog" aria-label={`Opportunities at ${scoreText(c.sx, c.sy)}`}
              style={{ ...side, top: `${(c.cy / H) * 100}%` }}
              onMouseEnter={cancelClose} onMouseLeave={scheduleClose}
              onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); close(true); } }}>
              <div className="ix-bubble-pop-head">
                <span>{`${c.n} opportunities · ${scoreText(c.sx, c.sy)}`}</span>
                {open?.pinned && (
                  <button type="button" className="ix-bubble-pop-close" aria-label="Close the list"
                    onClick={() => close(true)}>×</button>
                )}
              </div>
              <ul className="ix-bubble-pop-list">
                {c.items.map((p) => {
                  const isSel = selectedId != null && p.id === selectedId;
                  const cls = `ix-bubble-pop-item${dim?.has(p.id) ? ' is-dim' : ''}${isSel ? ' is-selected' : ''}${hl?.has(p.id) ? ' is-hl' : ''}`;
                  const body = (
                    <>
                      <span className="ix-num-bubble" style={{ '--gem': p.color || gemstoneColor(p.gemstone) }}>{p.number ?? ''}</span>
                      <span className="ix-bubble-pop-title">{p.title || gemstone(p.gemstone)?.name || ''}</span>
                    </>
                  );
                  return (
                    <li key={p.id}>
                      {onSelect ? (
                        <button type="button" className={cls} aria-pressed={isSel}
                          onClick={() => { onSelect(p.id); setOpen(null); }}>
                          {body}
                        </button>
                      ) : <div className={cls}>{body}</div>}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })()}
      </div>
      {legendItems && (
        <div className="ix-legend vertical">
          {legendItems.map((l) => (
            <span key={l.label} className="ix-legend-item">
              <span className="ix-swatch dot" style={{ background: l.color }} />{l.label}
            </span>
          ))}
          {legend === true && clusters.length > 0 && (
            <span className="ix-legend-item ix-bubble-legend-cluster">
              <span className="ix-bubble-cluster-swatch" aria-hidden>+N</span>
              N opportunities with the same scores; hover or click to list them
            </span>
          )}
        </div>
      )}
    </div>
  );
}
