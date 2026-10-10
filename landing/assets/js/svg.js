/* CostAdvisor landing — pure SVG string generators.
 *
 * One file, two uses:
 *  - at build time, build/prerender.mjs runs these in Node and writes the SVG into index.html,
 *    so every small chart (sparklines, quadrants, loop ring, product charts) is in the HTML
 *    with its final numbers, readable without JavaScript;
 *  - at run time, market.js re-draws the FX and index sparklines when fresher data arrives.
 * No DOM access here. Colours come from CSS classes (see components.css, "SVG chart classes"),
 * so the named chart tokens stay the single source of colour.
 */
(function (root) {
  'use strict';

  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const r1 = (v) => Math.round(v * 10) / 10;

  function scale(values, min, max, x0, x1, y0, y1, n) {
    const len = n || values.length;
    const span = (max - min) || 1;
    return values.map((v, i) => [r1(x0 + (len === 1 ? 0 : (i / (len - 1)) * (x1 - x0))), r1(y1 - ((v - min) / span) * (y1 - y0))]);
  }
  const path = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ' ' + p[1]).join(' ');

  /** Sparkline: solid history, optional dashed outlook tail, soft fill under the history. */
  function sparkline(o) {
    const w = o.w || 160, h = o.h || 48, pad = o.pad == null ? 3 : o.pad;
    const actual = o.actual || [], outlook = o.outlook || [];
    const all = actual.concat(outlook);
    const min = o.min != null ? o.min : Math.min.apply(null, all);
    const max = o.max != null ? o.max : Math.max.apply(null, all);
    const n = all.length;
    const pts = scale(all, min, max, pad, w - pad, pad, h - pad, n);
    const a = pts.slice(0, actual.length);
    const f = outlook.length ? [a[a.length - 1]].concat(pts.slice(actual.length)) : [];
    const id = o.id || 'sp';
    const cls = o.cls || 'ln-teal';
    const last = a[a.length - 1];
    let s = `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true" focusable="false">`;
    if (o.fill !== false) {
      s += `<defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="stop-a"/><stop offset="1" class="stop-b"/></linearGradient></defs>`;
      s += `<path class="spark-fill" fill="url(#${id}-g)" d="${path(a)} L${last[0]} ${h} L${a[0][0]} ${h} Z"/>`;
    }
    if (f.length) {
      s += `<rect class="spark-out-bg" x="${last[0]}" y="0" width="${r1(w - last[0])}" height="${h}"/>`;
      s += `<path class="spark-out" d="${path(f)}" fill="none" vector-effect="non-scaling-stroke"/>`;
    }
    s += `<path class="spark-line" d="${path(a)}" fill="none" vector-effect="non-scaling-stroke"/>`;
    s += `</svg>`;
    return s;
  }

  /** Two-line chart (should-cost vs price) with the gap shaded between them. */
  function gapChart(o) {
    const w = o.w || 420, h = o.h || 80, padX = o.padX == null ? 4 : o.padX, padY = o.padY == null ? 6 : o.padY;
    const lo = o.min != null ? o.min : Math.min.apply(null, o.should.concat(o.price)) - 4;
    const hi = o.max != null ? o.max : Math.max.apply(null, o.should.concat(o.price)) + 4;
    const sp = scale(o.should, lo, hi, padX, w - padX, padY, h - padY);
    const pp = scale(o.price, lo, hi, padX, w - padX, padY, h - padY);
    const id = o.id || 'gap';
    const grid = [0.25, 0.5, 0.75].map((t) => `<line class="grid" x1="0" x2="${w}" y1="${r1(h * t)}" y2="${r1(h * t)}"/>`).join('');
    const poly = pp.concat(sp.slice().reverse()).map((p) => p.join(',')).join(' ');
    let s = `<svg class="gapchart" viewBox="0 0 ${w} ${h}" ${o.ratio === false ? 'preserveAspectRatio="none"' : ''} aria-hidden="true" focusable="false">`;
    s += `<defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="stop-price-a"/><stop offset="1" class="stop-price-b"/></linearGradient></defs>`;
    s += grid;
    s += `<polygon fill="url(#${id}-g)" points="${poly}"/>`;
    s += `<path class="ln-should draw-path" d="${path(sp)}" fill="none"/>`;
    s += `<path class="ln-price draw-path" d="${path(pp)}" fill="none"/>`;
    if (o.dots) {
      s += pp.map((p) => `<circle class="pt-price" cx="${p[0]}" cy="${p[1]}" r="2.6"/>`).join('');
      s += sp.map((p) => `<circle class="pt-should" cx="${p[0]}" cy="${p[1]}" r="2.6"/>`).join('');
    }
    s += `</svg>`;
    return s;
  }

  /** Index chart with axes: monthly history, then a dashed outlook over a shaded band. */
  function indexChart(o) {
    const w = o.w || 640, h = o.h || 220;
    const L = o.padL || 34, R = o.padR || 14, T = o.padT || 16, B = o.padB || 26;
    const actual = o.actual, outlook = o.outlook || [];
    const all = actual.concat(outlook);
    const min = o.min, max = o.max, n = all.length;
    const pts = scale(all, min, max, L, w - R, T, h - B, n);
    const a = pts.slice(0, actual.length);
    const f = [a[a.length - 1]].concat(pts.slice(actual.length));
    const last = a[a.length - 1];
    const id = o.id || 'ix';
    const yOf = (v) => r1(h - B - ((v - min) / (max - min)) * (h - T - B));
    let s = `<svg class="ixchart ${o.cls || 'ln-lib'}" viewBox="0 0 ${w} ${h}" aria-hidden="true" focusable="false">`;
    s += `<defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="stop-a"/><stop offset="1" class="stop-b"/></linearGradient></defs>`;
    (o.yTicks || []).forEach((t) => {
      const y = yOf(t);
      s += `<line class="grid" x1="${L}" x2="${w - R}" y1="${y}" y2="${y}"/><text class="tick" x="${L - 6}" y="${y + 3}" text-anchor="end">${t}</text>`;
    });
    if (o.baseLine != null) {
      const y = yOf(o.baseLine);
      s += `<line class="base" x1="${L}" x2="${w - R}" y1="${y}" y2="${y}"/>`;
    }
    (o.xTicks || []).forEach((t) => {
      const x = pts[t.i][0];
      s += `<text class="tick" x="${x}" y="${h - 8}" text-anchor="${t.anchor || 'middle'}">${esc(t.label)}</text>`;
    });
    s += `<rect class="out-band" x="${last[0]}" y="${T - 6}" width="${r1(w - R - last[0])}" height="${r1(h - B - T + 6)}"/>`;
    s += `<line class="marker" x1="${last[0]}" x2="${last[0]}" y1="${T - 6}" y2="${h - B}"/>`;
    if (o.markerLabel) s += `<text class="marker-label" x="${last[0] - 6}" y="${T + 4}" text-anchor="end">${esc(o.markerLabel)}</text>`;
    if (o.outlookLabel) s += `<text class="out-label" x="${last[0] + 6}" y="${T + 4}">${esc(o.outlookLabel)}</text>`;
    s += `<path fill="url(#${id}-g)" d="${path(a)} L${last[0]} ${h - B} L${a[0][0]} ${h - B} Z"/>`;
    s += `<path class="ix-line" d="${path(a)}" fill="none"/>`;
    s += `<path class="ix-out" d="${path(f)}" fill="none"/>`;
    s += `<circle class="ix-dot" cx="${last[0]}" cy="${last[1]}" r="4"/>`;
    const fe = f[f.length - 1];
    s += `<circle class="ix-dot-out" cx="${fe[0]}" cy="${fe[1]}" r="3.2"/>`;
    if (o.endLabels) {
      // o.endBelow: put the last value under the line (when the line peaks just before the last point)
      s += `<text class="end-label" x="${last[0] - 8}" y="${o.endBelow ? last[1] + 18 : last[1] - 9}" text-anchor="end">${esc(o.endLabels[0])}</text>`;
      s += `<text class="end-label out" x="${fe[0]}" y="${fe[1] + 16}" text-anchor="end">${esc(o.endLabels[1])}</text>`;
    }
    s += `</svg>`;
    return s;
  }

  /** Quadrant chart (priority matrix, Kraljic, impact vs ease). Coordinates are 0..1, y up. */
  function quadrant(o) {
    const w = o.w || 320, h = o.h || 320;
    const L = o.padL == null ? 30 : o.padL, R = o.padR == null ? 10 : o.padR;
    const T = o.padT == null ? 10 : o.padT, B = o.padB == null ? 30 : o.padB;
    const pw = w - L - R, ph = h - T - B;
    const sx = o.split ? o.split.x : 0.5, sy = o.split ? o.split.y : 0.5;
    const X = (v) => r1(L + v * pw), Y = (v) => r1(T + (1 - v) * ph);
    const gap = o.gutter || 0;
    const q = o.quads || {};
    let s = `<svg class="quad ${o.cls || ''}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel || '')}">`;
    const rect = (x0, y0, x1, y1, k) => {
      const d = q[k]; if (!d) return '';
      return `<rect class="qf ${d.cls || ''}" x="${r1(x0 + gap)}" y="${r1(y0 + gap)}" width="${r1(x1 - x0 - 2 * gap)}" height="${r1(y1 - y0 - 2 * gap)}" rx="${o.rx || 4}"/>`;
    };
    s += rect(X(0), Y(1), X(sx), Y(sy), 'tl') + rect(X(sx), Y(1), X(1), Y(sy), 'tr') + rect(X(0), Y(sy), X(sx), Y(0), 'bl') + rect(X(sx), Y(sy), X(1), Y(0), 'br');
    if (o.dashed) {
      s += `<line class="median" x1="${X(sx)}" x2="${X(sx)}" y1="${Y(1)}" y2="${Y(0)}"/><line class="median" x1="${X(0)}" x2="${X(1)}" y1="${Y(sy)}" y2="${Y(sy)}"/>`;
    }
    const lab = (k, x, y, anchor) => q[k] && q[k].label ? `<text class="ql ${q[k].lcls || q[k].cls || ''}" x="${x}" y="${y}" text-anchor="${anchor}">${esc(q[k].label)}</text>` : '';
    const ty = o.topLabelsOutside ? Y(1) - 5 : Y(1) + 15;
    s += lab('tl', X(0) + (o.topLabelsOutside ? 0 : 7), ty, 'start') + lab('tr', X(1) - (o.topLabelsOutside ? 0 : 7), ty, 'end') + lab('bl', X(0) + 7, Y(0) - 8, 'start') + lab('br', X(1) - 7, Y(0) - 8, 'end');
    (o.xTicks || []).forEach((t) => { s += `<text class="tick" x="${X(t.v)}" y="${Y(0) + 13}" text-anchor="middle">${esc(t.label)}</text>`; });
    (o.yTicks || []).forEach((t) => { s += `<text class="tick" x="${X(0) - 6}" y="${Y(t.v) + 3}" text-anchor="end">${esc(t.label)}</text>`; });
    if (o.xLabel) s += `<text class="axis" x="${r1(L + pw / 2)}" y="${h - 4}" text-anchor="middle">${esc(o.xLabel)}</text>`;
    if (o.yLabel) s += `<text class="axis" transform="translate(${o.yLabelX || 10} ${r1(T + ph / 2)}) rotate(-90)" text-anchor="middle">${esc(o.yLabel)}</text>`;
    (o.dots || []).forEach((d) => {
      const cx = X(d.x), cy = Y(d.y), rr = d.r || 5;
      const data = d.data ? Object.keys(d.data).map((k) => ` data-${k}="${esc(d.data[k])}"`).join('') : '';
      s += `<g class="dot ${d.cls || ''}"${data}${d.tab ? ' tabindex="0" role="img" aria-label="' + esc(d.title || '') + '"' : ''}>`;
      if (d.ring) s += `<circle class="ring" cx="${cx}" cy="${cy}" r="${rr + 5}"/>`;
      s += `<circle class="dc" cx="${cx}" cy="${cy}" r="${rr}"/>`;
      if (d.num != null) s += `<text class="dn" x="${cx}" y="${r1(cy + 3.4)}" text-anchor="middle">${d.num}</text>`;
      if (d.title) s += `<title>${esc(d.title)}</title>`;
      s += `</g>`;
      if (d.label) s += `<text class="dl" x="${r1(cx + (d.labelDx || -rr - 8))}" y="${r1(cy + (d.labelDy || 4))}" text-anchor="${d.labelAnchor || 'end'}">${esc(d.label)}</text>`;
    });
    s += `</svg>`;
    return s;
  }

  /** Loop ring: seven nodes on a circle, each arc labelled with the value it hands on.
   *  o.back = { from, to, label }: the return hand-off (07 → 02) is drawn as an inner chord with
   *  its own arrow; the ring segment from the last node back to the first then has no arrow and no
   *  label (01 Discover is the entry, not part of the repeating loop). */
  function loopRing(o) {
    const w = o.w || 520, h = o.h || 520, cx = w / 2, cy = h / 2, r = o.r || 170;
    const nodes = o.nodes, n = nodes.length, nodeR = o.nodeR || 30;
    const ang = (i) => -Math.PI / 2 + (i / n) * Math.PI * 2;
    const P = (i, rr) => [r1(cx + Math.cos(ang(i)) * rr), r1(cy + Math.sin(ang(i)) * rr)];
    let s = `<svg class="loopring" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(o.ariaLabel || '')}">`;
    s += `<defs><marker id="lr-arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1 L8 5 L0 9 z" class="lr-arrowhead"/></marker>`;
    s += `<marker id="lr-arrow-back" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1 L8 5 L0 9 z" class="lr-arrowhead back"/></marker></defs>`;
    // library sector (behind node 01) and plan wedge
    const sector = (i0, i1, cls) => {
      const a0 = ang(i0), a1 = ang(i1), R0 = r + 34, R1 = r - 34;
      const p = (a, rr) => r1(cx + Math.cos(a) * rr) + ' ' + r1(cy + Math.sin(a) * rr);
      return `<path class="${cls}" d="M${p(a0, R0)} A${R0} ${R0} 0 0 1 ${p(a1, R0)} L${p(a1, R1)} A${R1} ${R1} 0 0 0 ${p(a0, R1)} Z"/>`;
    };
    s += sector(-0.5, 0.5, 'lr-sector lib') + sector(4.5, 5.5, 'lr-sector plan');
    const back = o.back;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const a0 = ang(i) + 0.22, a1 = ang(i + 1) - 0.22;
      const p0 = [r1(cx + Math.cos(a0) * r), r1(cy + Math.sin(a0) * r)], p1 = [r1(cx + Math.cos(a1) * r), r1(cy + Math.sin(a1) * r)];
      const closing = back && i === n - 1; // last node → first node: no arrow, no label
      s += `<path class="lr-arc${closing ? ' idle' : ''}" data-from="${esc(nodes[i].n)}" data-to="${esc(nodes[j].n)}" d="M${p0[0]} ${p0[1]} A${r} ${r} 0 0 1 ${p1[0]} ${p1[1]}"${closing ? '' : ' marker-end="url(#lr-arrow)"'}/>`;
      if (closing || o.arcs[i] == null) continue;
      const mid = ang(i + 0.5), lr = r + (o.labelGap || 26);
      const lx = r1(cx + Math.cos(mid) * lr), ly = r1(cy + Math.sin(mid) * lr);
      const c = Math.cos(mid), anchor = Math.abs(c) < 0.25 ? 'middle' : c > 0 ? 'start' : 'end';
      const lines = String(o.arcs[i]).split('|');
      s += `<text class="lr-label" x="${lx}" y="${r1(ly - (lines.length - 1) * 7 + 4)}" text-anchor="${anchor}">` +
        lines.map((t, k) => `<tspan x="${lx}" dy="${k ? 14 : 0}">${esc(t)}</tspan>`).join('') + `</text>`;
    }
    if (back) {
      // Inner chord from node `from` to node `to`, bowing toward the centre, ending at the target's rim.
      const fi = nodes.findIndex((x) => x.n === back.from), ti = nodes.findIndex((x) => x.n === back.to);
      const A = P(fi, r), B = P(ti, r);
      const dip = back.dip == null ? 0.42 : back.dip;
      const ctrl = [r1((A[0] + B[0]) / 2 + (cx - (A[0] + B[0]) / 2) * dip), r1((A[1] + B[1]) / 2 + (cy - (A[1] + B[1]) / 2) * dip)];
      const pull = (from, to, d) => { const dx = to[0] - from[0], dy = to[1] - from[1], L = Math.hypot(dx, dy); return [r1(from[0] + (dx / L) * d), r1(from[1] + (dy / L) * d)]; };
      const start = pull(A, ctrl, nodeR + 4), end = pull(B, ctrl, nodeR + 6);
      s += `<path class="lr-arc back" data-from="${esc(back.from)}" data-to="${esc(back.to)}" d="M${start[0]} ${start[1]} Q${ctrl[0]} ${ctrl[1]} ${end[0]} ${end[1]}" marker-end="url(#lr-arrow-back)"/>`;
      // label under the chord's lowest point
      const mx = r1(0.25 * A[0] + 0.5 * ctrl[0] + 0.25 * B[0]), my = r1(0.25 * A[1] + 0.5 * ctrl[1] + 0.25 * B[1]);
      const lines = String(back.label).split('|');
      s += `<text class="lr-label back" x="${mx}" y="${r1(my + 18)}" text-anchor="middle">` +
        lines.map((t, k) => `<tspan x="${mx}" dy="${k ? 14 : 0}">${esc(t)}</tspan>`).join('') + `</text>`;
    }
    nodes.forEach((nd, i) => {
      const p = P(i, r);
      s += `<g class="lr-node ${nd.cls || ''}"><circle cx="${p[0]}" cy="${p[1]}" r="${nodeR}"/><text class="lr-num" x="${p[0]}" y="${r1(p[1] - 2)}" text-anchor="middle">${esc(nd.n)}</text><text class="lr-name" x="${p[0]}" y="${r1(p[1] + 10)}" text-anchor="middle">${esc(nd.label)}</text></g>`;
    });
    if (o.center) {
      const lines = o.center.split('|');
      const cyy = o.centerY != null ? o.centerY : cy;
      s += `<text class="lr-center" x="${cx}" y="${r1(cyy - (lines.length - 1) * 10 + 5)}" text-anchor="middle">` + lines.map((t, k) => `<tspan x="${cx}" dy="${k ? 20 : 0}">${esc(t)}</tspan>`).join('') + `</text>`;
    }
    s += `</svg>`;
    return s;
  }

  root.CASvg = { sparkline, gapChart, indexChart, quadrant, loopRing, esc };
})(typeof globalThis !== 'undefined' ? globalThis : this);
