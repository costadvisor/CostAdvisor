#!/usr/bin/env node
// Prototype stand-in for the plan's build.mjs (Part G1), zero dependencies.
//
//   node build/prerender.mjs          rewrites the generated blocks in index.html in place
//
// It reads data/story.json, data/counts.snapshot.json, data/indexes.snapshot.json and
// data/fx.snapshot.json, and fills every marked block:
//   <!--@svg:name--> … <!--@/svg-->     charts drawn by assets/js/svg.js (same code the browser uses)
//   <!--@html:name--> … <!--@/html-->   tables, cards, chips, ticker items
//   <!--@jsonld:faq--> … <!--@/jsonld--> FAQPage structured data, built from the visible FAQ
//   <x data-c="key">…</x>               catalogue counts from counts.snapshot.json
// So every number is in the HTML (no-JS and crawlers see it) and cannot drift from the data.
// The real build would also do partials, per-host flags, the claims lint and dist/ output.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const json = (p) => JSON.parse(read(p));
await import(path.join(root, 'assets/js/svg.js'));
const S = globalThis.CASvg;
const esc = S.esc;

const story = json('data/story.json');
const counts = json('data/counts.snapshot.json');
const idx = json('data/indexes.snapshot.json');
const fx = json('data/fx.snapshot.json');
// The real library data (the blue thread): one product card's composite should-cost index and the
// structure of its report and playbook. No weights, cost lines, codes, makers or prose (see its _comment).
const lib = json('data/library.snapshot.json');
// Per-host flags: the real build runs once per worker with SITE_ENV=dev or www (plan E1).
const SITE_ENV = process.env.SITE_ENV === 'www' ? 'www' : 'dev';
const site = json('data/site.json')[SITE_ENV];

const fmtInt = (n) => n.toLocaleString('en-US');
const eur = (n, d = 0) => '€' + Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(d) + '%';
const arrow = (v) => (v > 0.05 ? '▲' : v < -0.05 ? '▼' : '■');
const chgCls = (v) => (v > 0.05 ? 'chg-up' : v < -0.05 ? 'chg-dn' : 'chg-flat');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ym = (s) => { const [y, m] = s.split('-').map(Number); return MONTHS[m - 1] + ' ' + y; };

const blocks = { svg: {}, html: {}, jsonld: {} };

// ── Library values printed in the page: <x data-l="key">…</x> (checked by tests/story.test.mjs) ──
const card = lib.card, ser = card.series;
const qavg = (i) => (ser.actual[i] + ser.actual[i + 1] + ser.actual[i + 2]) / 3;
const qStart = qavg(0), qNow = qavg(39), qPrev = qavg(27);
const ord = (n) => n + (n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th');
const libv = {
  name: card.name, short: card.short, family: card.family, line: card.line, industry: card.industry, category: card.category,
  badge: card.badge.label, badge_note: card.badge.note,
  data_to_label: lib.data_to_label,
  jun: ser.actual.at(-1).toFixed(1), dec: ser.forecast.at(-1).toFixed(1),
  q_start: qStart.toFixed(1), q_now: qNow.toFixed(1), q_prev: qPrev.toFixed(1), yoy: pct((qNow / qPrev - 1) * 100),
  m3_from: card.move_3m.from.toFixed(1), m3_to: card.move_3m.to.toFixed(1), m3_pts: pct(card.move_3m.to - card.move_3m.from).replace('%', ' pts'),
  cyc_pct: ord(card.cycle.percentile), cyc_low: card.cycle.low.toFixed(1), cyc_high: card.cycle.high.toFixed(1), cyc_pos: card.cycle.position,
  seas_peak: card.seasonality.peak, seas_trough: card.seasonality.trough, seas_spread: card.seasonality.spread_pts.toFixed(1),
  vol_pct: ord(card.volatility.percentile), vol_label: card.volatility.label,
  cost_lines: String(card.recipe_line_count), regions_n: String(card.regions.length),
  cats: String(card.bought_in.categories), inds: String(card.bought_in.industries),
  report: lib.report.name, report_written: lib.report.written, report_lines: String(lib.report.product_lines), report_sections: String(lib.report.sections.length),
  levers: String(lib.playbook.levers), levers_apply: String(lib.playbook.levers_apply), families: String(lib.playbook.families.length),
};
// The workspace's should-cost follows the library index: €300 starting point in Q1 2023 (story.start).
if (Math.abs(story.start.price * qNow / qStart - story.should_series.at(-1)) > 0.01) throw new Error('story.should_series does not follow the library index');

// ── Hero: library sparkline and workspace gap chart ──
blocks.svg['hero-lib'] = S.sparkline({ id: 'hl', w: 200, h: 54, actual: ser.actual, outlook: ser.forecast, cls: 'ln-lib', min: 94, max: 105.5 });
blocks.svg['hero-ws'] = S.gapChart({ id: 'hw', w: 420, h: 84, should: story.should_series, price: story.price_series, min: 264, max: 344, dots: false });

// ── Stage 01 product chart: the real should-cost index, Europe ──
blocks.svg['s1-chart'] = S.indexChart({
  id: 's1', w: 660, h: 176, actual: ser.actual, outlook: ser.forecast,
  min: 92, max: 106, yTicks: [95, 100, 105], baseLine: 100, cls: 'ln-lib',
  xTicks: [{ i: 0, label: 'Jan 2023', anchor: 'start' }, { i: 12, label: 'Jan 2024' }, { i: 24, label: 'Jan 2025' }, { i: 36, label: 'Jan 2026' }, { i: 47, label: 'Dec 2026', anchor: 'end' }],
  markerLabel: 'Data to ' + lib.data_to_label, endLabels: [libv.jun, libv.dec], endBelow: true,
});

blocks.svg['s1-chart-m'] = S.indexChart({
  id: 's1m', w: 360, h: 196, actual: ser.actual, outlook: ser.forecast,
  min: 92, max: 106, yTicks: [95, 100, 105], baseLine: 100, cls: 'ln-lib', padL: 26, padR: 8,
  xTicks: [{ i: 0, label: '2023', anchor: 'start' }, { i: 24, label: '2025' }, { i: 47, label: 'Dec 26', anchor: 'end' }],
  markerLabel: 'Data to ' + lib.data_to_label.replace(' 20', ' '), endLabels: [libv.jun, libv.dec], endBelow: true,
});

// ── Stage 01 region chips: the card's should-cost index in each region, Jun 2026 ──
blocks.html.regions = card.regions.map((r) => `<span class="rg${r.code === card.region ? ' on' : ''}">${esc(r.code)}<b>${r.jun.toFixed(1)}</b></span>`).join('');

// Stage 01 text summary, written from the snapshot so it cannot drift from the chart.
{
  const RN = { EU: 'Europe', NA: 'North America', CN: 'China', APAC: 'Asia-Pacific', IN: 'India', MEA: 'Middle East and Africa' };
  const hiAt = ser.actual.indexOf(Math.max(...ser.actual)), mon = MONTHS[hiAt % 12] + ' ' + (2023 + Math.floor(hiAt / 12));
  blocks.html['s1-sum'] = esc(`The library's real product card, data to ${ym(lib.data_to)}. ${card.name}, made by ${card.line.toLowerCase()} in the ${card.family} family; bought for ${card.category.toLowerCase()} in ${card.industry.toLowerCase()}, and in ${card.bought_in.categories} categories across ${card.bought_in.industries} industries. ${card.badge.label} badge: ${card.badge.note} Should-cost index by region in ${ym(lib.data_to)} (January 2023 = 100): ` +
    card.regions.map((r) => `${RN[r.code]} ${r.jun.toFixed(1)}`).join(', ') + `. In Europe the index peaked at ${card.cycle.high.toFixed(1)} in ${mon} and stands at ${libv.jun}; the six-month forecast (${ser.forecast_kind}) eases to ${libv.dec} by December 2026. Cost drivers: ${card.drivers.join(', ')}; ${card.recipe_line_count} cost lines with the margin.`);
}

// ── Stages 03 and 05: the library's driver indexes, 12 months to Jun 2026 (public series) ──
blocks.html['lib-moves'] = idx.pulse.map((code) => {
  const it = idx.items.find((x) => x.code === code), c = it.chg12m_pct;
  return `<li><span>${esc(it.name.replace(', Henry Hub', ''))}${it.region === 'Europe' ? ', EU' : ''} <small>${esc(it.provider)}</small></span><b class="${chgCls(c)}">${arrow(c)} ${pct(c)}</b></li>`;
}).join('');

// ── Stage 06: the report's sections and the playbook's strategy families (structure only) ──
blocks.html['report-sections'] = lib.report.sections.map((t) => `<li>${esc(t)}</li>`).join('');
blocks.html['playbook-families'] = lib.playbook.families.map((t) => `<li>${esc(t)}</li>`).join('');

// ── Ticker: 8 public index series (base 100) + 3 ECB rates ──
const fxPairs = ['USD', 'GBP', 'CNY'];
const tickerItems = idx.items.map((it) => {
  const c = it.chg12m_pct;
  return `<span class="ticker-item"><span class="ticker-name">${esc(it.name)} · ${esc(it.provider)}</span><span class="ticker-val">${it.latest.toFixed(1)}</span><span class="ticker-chg ${chgCls(c)}">${arrow(c)} ${pct(c)} <small>12 mo</small></span></span>`;
}).concat(fxPairs.map((p) => {
  const rows = fx.pairs[p], last = rows[rows.length - 1][1], prev = rows[rows.length - 2][1];
  const c = (last / prev - 1) * 100;
  return `<span class="ticker-item"><span class="ticker-name">1 EUR =</span><span class="ticker-val">${last.toFixed(4)} ${p}</span><span class="ticker-chg ${chgCls(c)}">${arrow(c)} ${pct(c, 2)} <small>d/d</small></span></span>`;
}));
blocks.html.ticker = `<div class="ticker-set">${tickerItems.join('')}</div><div class="ticker-set" aria-hidden="true">${tickerItems.join('')}</div>`;

// ── Stage 01 pulse cards (real data) ──
blocks.html.pulse = idx.pulse.map((code, i) => {
  const it = idx.items.find((x) => x.code === code);
  const c = it.chg12m_pct;
  const flat = it.outlook_kind === 'flat';
  const spark = S.sparkline({ id: 'pc' + i, w: 150, h: 32, actual: it.actual, outlook: it.outlook, cls: 'ln-c' + i });
  return `<div class="pulse-card skeuo tilt-card" data-code="${it.code}">
          <div class="pulse-card-name">${esc(it.name)}</div>
          <div class="pulse-card-src"><span>${esc(it.provider)} · ${esc({ 'North America': 'NA', Europe: 'EU' }[it.region] || it.region)}</span><span class="pulse-formula" aria-hidden="true">${esc(it.badge)}</span></div>
          <div class="pulse-card-row"><span class="pulse-card-val">${it.latest.toFixed(1)}</span><span class="pulse-card-chg ${chgCls(c)}">${arrow(c)} ${pct(c)}</span></div>
          <div class="pulse-canvas-wrap" role="img" aria-label="${esc(it.name)}, ${esc(it.provider)}: monthly index, base 100 = January 2023, ${it.actual[0]} to ${it.latest} in June 2026; six-month outlook ${flat ? 'flat at ' + it.latest : 'to ' + it.outlook[5]}.">${spark}${flat ? '<span class="flat-tag" title="All six outlook months equal the last value">flat outlook</span>' : ''}</div>
        </div>`;
}).join('\n        ');

// ── Stage 02 FX tiles (ECB, 90 days) ──
blocks.html.fx = fxPairs.map((p, i) => {
  const rows = fx.pairs[p].slice(-90);
  const vals = rows.map((r) => r[1]);
  const last = vals[vals.length - 1], first = vals[0];
  const c = (last / first - 1) * 100;
  const spark = S.sparkline({ id: 'fx' + i, w: 120, h: 34, actual: vals, cls: 'ln-fx', fill: true });
  return `<div class="fx-current-tile" data-pair="${p}">
          <span class="fx-tile-k"><span class="fx-current-tile-pair">1 EUR in ${p}</span><span class="fx-current-tile-val">${last.toFixed(4)}</span></span>
          <span class="fx-spark" role="img" aria-label="1 euro in ${p}, last 90 days: ${first.toFixed(4)} to ${last.toFixed(4)}">${spark}</span>
          <span class="fx-chg ${chgCls(c)}">${arrow(c)} ${pct(c)}<small>90 d</small></span>
        </div>`;
}).join('\n        ');

// ── Stage 03 Monitor rows + evolution table ──
blocks.html.monitor = story.monitor_rows.map((r) => {
  const g = r.actual - r.should;
  const st = { Alert: 'alert', Watch: 'watch', 'On track': 'ok' }[r.status];
  return `<tr class="row-${st}"><th scope="row">${esc(r.product)}</th><td>${esc(r.supplier)}</td><td class="num">${r.should}</td><td class="num">${r.actual}</td><td class="num gap">+${g}</td><td><span class="st st-${st}">${r.status}</span></td></tr>`;
}).join('');
const tbl = (cap, head, rows) => `<table class="num-table"><caption class="sr-only">${esc(cap)}</caption><thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => (i ? `<td>${c}</td>` : `<th scope="row">${c}</th>`)).join('')}</tr>`).join('')}</tbody></table>`;
blocks.html['evo-table'] = tbl('Should-cost and Supplier A price by quarter, euros per tonne (illustrative)', ['Quarter', 'Should-cost', 'Supplier A', 'Gap'],
  story.quarters.map((q, i) => { const g = story.price_series[i] - story.should_series[i]; return [q, story.should_series[i].toFixed(2), story.price_series[i], (g > 0 ? '+' : g < 0 ? '−' : '') + Math.abs(g).toFixed(2)]; }));
blocks.html['outlook-table'] = tbl('Should-cost history and projection, euros per tonne (illustrative)', ['Period', 'Should-cost', 'Kind'],
  story.quarters.map((q, i) => [q, story.should_series[i].toFixed(2), 'history']).concat(story.outlook_eur.months.map((m, i) => [m + ' 2026', story.outlook_eur.values[i], 'projection'])));

// ── Stage 04 priority matrix ──
const mx = story.matrix;
const quadOf = (x, y) => (y >= mx.median_y ? (x >= mx.median_x ? 'act' : 'hedge') : (x >= mx.median_x ? 'mon' : 'low'));
blocks.svg.matrix = S.quadrant({
  w: 310, h: 232, padL: 28, padB: 28, padT: 6, padR: 6, split: { x: mx.median_x, y: mx.median_y }, dashed: true, rx: 0,
  ariaLabel: 'Priority matrix, illustrative: ferric chloride 40% in the Act now quadrant (high exposure, high volatility), with eleven other unlabelled products.',
  xLabel: 'Index volatility →', yLabel: 'Spend exposure →',
  quads: { tl: { label: 'HEDGE', cls: 'q-hedge' }, tr: { label: 'ACT NOW', cls: 'q-act' }, bl: { label: 'LOW PRIORITY', cls: 'q-low' }, br: { label: 'MONITOR', cls: 'q-mon' } },
  dots: mx.dots.map(([x, y]) => ({ x, y, r: 4.5, cls: 'd-' + quadOf(x, y) })).concat([{ x: mx.focus.x, y: mx.focus.y, r: 6.5, cls: 'd-act focus', ring: true, label: 'Ferric chloride 40%', labelDx: 0, labelDy: 26, labelAnchor: 'middle' }]),
});

// ── Stage 06 Kraljic + impact vs ease ──
blocks.svg.kraljic = S.quadrant({
  w: 210, h: 160, padL: 22, padB: 22, padT: 4, padR: 4, gutter: 2, rx: 6,
  ariaLabel: 'Kraljic matrix, illustrative: Coagulants sits in Leverage.',
  xLabel: 'Supply complexity →', yLabel: 'Business impact →', yLabelX: 9,
  quads: { tl: { label: 'Leverage', cls: 'k-lev' }, tr: { label: 'Strategic', cls: 'k-str' }, bl: { label: 'Non-critical', cls: 'k-non' }, br: { label: 'Bottleneck', cls: 'k-bot' } },
  dots: [{ x: story.strategy.kraljic_dot[0], y: story.strategy.kraljic_dot[1], r: 8, cls: 'k-dot', title: 'Coagulants: Leverage' }],
});
const sel = new Set(story.strategy.objectives_selected);
const pad01 = (t) => 0.05 + 0.9 * t; // keep bubbles off the plot edge
blocks.svg.impact = S.quadrant({
  w: 540, h: 272, padL: 36, padB: 34, padT: 18, padR: 10, rx: 0,
  ariaLabel: 'Impact versus ease, illustrative: 17 of 19 opportunities scored. Quick wins 11, Evaluate 2, Low effort 2, Deprioritise 2.',
  xLabel: 'Ease of implementation →', yLabel: 'Impact →', yLabelX: 11,
  quads: { tl: { label: 'EVALUATE', cls: 'ie-ev' }, tr: { label: 'QUICK WINS', cls: 'ie-qw' }, bl: { label: 'DEPRIORITISE', cls: 'ie-de' }, br: { label: 'LOW EFFORT', cls: 'ie-le' } },
  xTicks: [1, 2, 3, 4, 5].map((v) => ({ v: pad01((v - 1) / 4), label: v })), yTicks: [1, 2, 3, 4, 5].map((v) => ({ v: pad01((v - 1) / 4), label: v })), split: { x: pad01(0.5), y: pad01(0.5) }, topLabelsOutside: true,
  dots: story.impact_ease.map((b) => ({
    x: pad01((b.ease - 1) / 4), y: pad01((b.impact - 1) / 4), r: 10, num: b.n, tab: true,
    cls: 'ie-b t-' + b.type.split(' ')[0] + (b.obj.some((o) => sel.has(o)) ? ' on' : ''), ring: true,
    title: `Opportunity ${b.n}: ${b.type}, ease ${b.ease < 3 ? 'low' : 'high'}, impact ${b.impact < 3 ? 'low' : 'high'}`,
    data: { obj: b.obj.join('|') },
  })),
});

blocks.svg['impact-m'] = S.quadrant({
  w: 340, h: 300, padL: 28, padB: 30, padT: 18, padR: 6, rx: 0, topLabelsOutside: true,
  ariaLabel: 'Impact versus ease, illustrative: 17 of 19 opportunities scored. Quick wins 11, Evaluate 2, Low effort 2, Deprioritise 2.',
  xLabel: 'Ease →', yLabel: 'Impact →', yLabelX: 9,
  quads: { tl: { label: 'EVALUATE', cls: 'ie-ev' }, tr: { label: 'QUICK WINS', cls: 'ie-qw' }, bl: { label: 'DEPRIORITISE', cls: 'ie-de' }, br: { label: 'LOW EFFORT', cls: 'ie-le' } },
  split: { x: pad01(0.5), y: pad01(0.5) },
  dots: story.impact_ease.map((b) => ({
    x: pad01((b.ease - 1) / 4), y: pad01((b.impact - 1) / 4), r: 9, num: b.n, tab: true,
    cls: 'ie-b t-' + b.type.split(' ')[0] + (b.obj.some((o) => sel.has(o)) ? ' on' : ''), ring: true,
    title: `Opportunity ${b.n}: ${b.type}`, data: { obj: b.obj.join('|') },
  })),
});

// ── Section 5 loop ring ──
blocks.svg.loop = S.loopRing({
  w: 540, h: 500, r: 168, labelGap: 30,
  ariaLabel: 'The loop, with the market library at its centre feeding all seven steps: 01 Discover hands the recipe to 02 Model, which hands the €291 should-cost to 03 Watch, which hands the €48 gap (about €105,600 a year) to 04 Anticipate, which hands the timing to 05 Negotiate, which hands the €252 floor and €291 target to 06 Plan, which hands 2 objectives and 19 opportunities to 07 Act. The price agreed in 07 Act goes back to 02 Model as the new starting point.',
  nodes: [{ n: '01', label: 'Discover', cls: 'lib' }, { n: '02', label: 'Model' }, { n: '03', label: 'Watch' }, { n: '04', label: 'Anticipate' }, { n: '05', label: 'Negotiate' }, { n: '06', label: 'Plan', cls: 'plan' }, { n: '07', label: 'Act' }],
  arcs: ['recipe', '€291 should-cost', '€48 gap|≈ €105,600/yr', 'act now', 'floor €252|target €291', '2 objectives|19 opportunities', null],
  // The story's last hand-off (07 → 02) drives the chord, so the ring cannot drift from story.json.
  back: (() => { const h = story.handoffs.at(-1); return { from: '0' + h.from, to: '0' + h.to, label: 'agreed price →|new starting point' }; })(),
  hub: { label: 'MARKET|LIBRARY', sub: 'feeds every step', r: 40, cy: 268 },
});

// ── Stage 05 claim chips (all four claims, verdicts under the app's rules) ──
{
  const check = (claimed, actual) => (Math.abs(actual) < 0.5 ? 'no_movement' : actual * claimed < 0 ? 'contradicted' : Math.abs(claimed) - Math.abs(actual) > 2 ? 'overstated' : 'already_priced');
  const lab = { overstated: ['v-overstated', 'Overstated'], contradicted: ['v-contra', 'Contradicted'], already_priced: ['v-priced', 'Already priced'], no_movement: ['v-flat', 'Did not move'] };
  blocks.html.claims = story.claims.map((c, i) => {
    const v = check(c.claimed, story.recipe.find((l) => l.key === c.line).chg12m * 100);
    return `<li><button type="button" data-cid="${c.id}" aria-pressed="${i === 0}"><span>${esc(c.short)}</span><b class="${lab[v][0]}">${lab[v][1]}</b></button></li>`;
  }).join('');
}

// ── Section 7 names ──
// Families: the first eleven plus the example's own family, then "+ N more" (as for industries).
{
  const fam = counts.family_names;
  const shown = fam.slice(0, 11).concat(fam.includes(story.family) && !fam.slice(0, 11).includes(story.family) ? [story.family] : []);
  blocks.html.families = shown.map((n) => `<li${n === story.family ? ' class="hl"' : ''}>${esc(n)}</li>`).join('');
  const more = fam.filter((n) => !shown.includes(n));
  blocks.html['families-more'] = more.map((n) => `<li>${esc(n)}</li>`).join('');
  blocks.html['families-more-label'] = `+ ${more.length} more`;
}
const featured = counts.industry_names_featured;
blocks.html.industries = featured.map((n) => `<li${n === story.industry ? ' class="hl"' : ''}>${esc(n)}</li>`).join('');
const rest = counts.industry_names.filter((n) => !featured.includes(n));
blocks.html['industries-more'] = rest.map((n) => `<li>${esc(n)}</li>`).join('');
blocks.html['industries-more-label'] = `+ ${rest.length} more`;
blocks.html['industry-options'] = counts.industry_names.map((n) => `<option value="${esc(n)}"></option>`).join('');

// ── Stage 07 timeline + table ──
const t0 = Date.parse(story.timeline.from), t1 = Date.parse(story.timeline.to) + 864e5;
const at = (d) => Math.max(0, Math.min(100, ((Date.parse(d) - t0) / (t1 - t0)) * 100));
const today = at(story.today);
const dShort = (d) => { const x = new Date(d + 'T00:00:00Z'); return x.getUTCDate() + ' ' + MONTHS[x.getUTCMonth()]; };
const stCls = { 'In progress': 'prog', 'Not started': 'todo', Overdue: 'late', Done: 'done' };
const months = [];
for (let m = 6; m < 12; m++) months.push(MONTHS[m]);
blocks.html.timeline = `<div class="tl" role="table" aria-label="Actions on a timeline, July to December 2026">
          <div class="tl-row tl-head" role="row"><span class="tl-t" role="columnheader">Action · owner · due</span><span class="tl-track" role="columnheader"><span class="tl-months">${months.map((m) => `<span>${m}</span>`).join('')}</span><span class="tl-today-k" style="left:${today.toFixed(2)}%">today</span></span><span class="tl-s" role="columnheader">Status</span></div>
          ${story.actions.map((a) => {
            const l = at(a.start), w = Math.max(2, at(a.due) - l);
            return `<div class="tl-row row-${stCls[a.status]}" role="row"><span class="tl-t" role="cell"><b>${esc(a.title)}</b><small>${a.owner} · due ${dShort(a.due)}<span class="fam-chip" title="Strategy family in the playbook">◆ ${esc(a.family)}</span></small></span><span class="tl-track" role="cell" aria-label="${dShort(a.start)} to ${dShort(a.due)}"><span class="tl-bar" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%"><i style="width:${Math.round(a.progress * 100)}%"></i></span><span class="tl-today" style="left:${today.toFixed(2)}%"></span></span><span class="tl-s" role="cell"><span class="st st-${stCls[a.status]}">${a.status}</span></span></div>`;
          }).join('\n          ')}
        </div>`;
blocks.html['actions-table'] = `<table class="act-table"><caption class="sr-only">Actions for the Coagulants strategy (illustrative)</caption><thead><tr><th scope="col">Action</th><th scope="col">Playbook family</th><th scope="col">Owner</th><th scope="col">Due</th><th scope="col">Status</th></tr></thead><tbody>${story.actions.map((a) => `<tr><th scope="row">${esc(a.title)}</th><td>${esc(a.family)}</td><td>${a.owner}</td><td>${dShort(a.due)} 2026</td><td><span class="st st-${stCls[a.status]}">${a.status}</span></td></tr>`).join('')}</tbody></table>`;

// ── Stage 02 formula block (same layout as calc.js writes) ──
{
  const parts = story.recipe.map((l) => story.start.price * l.weight * l.level / 100);
  const should = parts.reduce((a, b) => a + b, 0) + story.freight.eur + story.margin.eur;
  const sR = Math.round(should), fR = Math.round(should - story.margin.eur), g = Math.round(story.price - should);
  // One row per line: the left side keeps its aligned spaces, the result sits in its own column
  // (so a phone wraps the left side instead of hiding the result off to the right).
  const row = (left, val) => `<span class="fr"><span class="fl">${left}</span>${val ? `<b>= ${val}</b>` : ''}</span>`;
  blocks.html.formula = [
    row(`each line   = €${story.start.price} × weight × index ÷ 100`),
    row(`should-cost = Σ lines + freight (€${story.freight.eur}) + margin (€${story.margin.eur})`, eur(sR)),
    row('floor       = should-cost − margin', eur(fR)),
    row('gap         = your price − should-cost', eur(g) + '/t'),
    row(`annual gap  = gap × volume = ${eur(g)} × ${fmtInt(story.volume_t_yr)} t`, eur(g * story.volume_t_yr)),
  ].join('');
}

// ── Fill the blocks ──
let html = read('index.html');
const fill = (kind, close) => {
  const re = new RegExp(`(<!--@${kind}:([\\w-]+)-->)([\\s\\S]*?)(<!--@/${close}-->)`, 'g');
  html = html.replace(re, (m, open, name, _old, end) => {
    if (kind === 'jsonld') return open + end; // filled below, after counts
    const v = blocks[kind][name];
    if (v == null) throw new Error(`no generator for @${kind}:${name}`);
    return open + v + end;
  });
};
fill('svg', 'svg');
fill('html', 'html');

// ── Counts into data-c spans ──
const cval = (k) => {
  if (k === 'data_to_short') return counts.data_to.replace(/^(\w{3})\w*/, '$1');
  const v = counts[k];
  if (v == null) throw new Error('unknown count ' + k);
  return typeof v === 'number' ? fmtInt(v) : String(v);
};
html = html.replace(/(<(\w+)\b[^>]*\sdata-c="([\w]+)"[^>]*>)([^<]*)(<\/\2>)/g, (m, open, tag, key, _old, close) => open + esc(cval(key)) + close);

// ── Library values into data-l spans ──
html = html.replace(/(<(\w+)\b[^>]*\sdata-l="([\w]+)"[^>]*>)([^<]*)(<\/\2>)/g, (m, open, tag, key, _old, close) => {
  if (!(key in libv)) throw new Error('unknown library value ' + key);
  return open + esc(libv[key]) + close;
});

// ── FAQPage JSON-LD from the visible FAQ ──
// A sentence wrapped in <span data-claim="flag" hidden> is left out of the structured data unless
// that claim is switched on for this host (plan H3 #5: "research is AI-assisted").
const claimOn = (k) => !!(site.claims || {})[k];
const dropOff = (h) => h.replace(/<span data-claim="(\w+)"[^>]*>([\s\S]*?)<\/span>/g, (m, k, inner) => (claimOn(k) ? inner : ''));
const faq = [...html.matchAll(/<details class="faq-item"[^>]*><summary>([\s\S]*?)<\/summary><div class="faq-a">([\s\S]*?)<\/div><\/details>/g)]
  .map((m) => ({ q: m[1].replace(/<[^>]+>/g, '').trim(), a: dropOff(m[2]).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim() }));
if (faq.length !== 10) throw new Error('expected 10 FAQ items, found ' + faq.length);
const ld = { '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: faq.map((f) => ({ '@type': 'Question', name: f.q, acceptedAnswer: { '@type': 'Answer', text: f.a } })) };
html = html.replace(/(<!--@jsonld:faq-->)[\s\S]*?(<!--@\/jsonld-->)/, `$1<script type="application/ld+json">${JSON.stringify(ld)}</script>$2`);

fs.writeFileSync(path.join(root, 'index.html'), html);
console.log('prerender (' + SITE_ENV + '): ok ·', Object.keys(blocks.svg).length, 'svg ·', Object.keys(blocks.html).length, 'html blocks ·', faq.length, 'FAQ items');
