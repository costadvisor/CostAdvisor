// Prototype of the plan's two guards (Part G1), zero dependencies:
//   node --test tests/story.test.mjs
// 1. story.test: recomputes every derived number in data/story.json and checks the page prints it.
// 2. claims lint: fails on banned phrases, maker counts, real supplier, staff or personal e-mail
//    addresses in visible copy, the sample brief and the social card source.
// Browser checks (ticker width, deep links, contrast, page height) are in tests/qa.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const story = JSON.parse(read('data/story.json'));
const counts = JSON.parse(read('data/counts.snapshot.json'));
const idx = JSON.parse(read('data/indexes.snapshot.json'));
const site = JSON.parse(read('data/site.json'));
const html = read('index.html');
const subpages = ['method/index.html', 'security/index.html', 'demo/index.html', '404.html'].map(read);
const r1 = (v) => Math.round(v * 10) / 10;

// Visible text: tags, scripts, styles and SVG paths stripped; attribute text (aria-label, alt,
// title) kept, because screen readers and crawlers read it too.
const textOf = (h) => {
  const attrText = [...h.matchAll(/\s(?:aria-label|alt|title|placeholder|content)="([^"]*)"/g)].map((m) => m[1]).join(' \n ');
  return h
    .replace(/<script[\s\S]*?<\/script>/g, ' ')
    .replace(/<style[\s\S]*?<\/style>/g, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ') + ' ' + attrText;
};
const visible = textOf(html);
const has = (s) => assert.ok(html.includes(s) || visible.includes(s), `page should print "${s}"`);

// ── The model ──
const start = story.start.price;
const lines = story.recipe.map((l) => start * l.weight * l.level / 100);
const fixed = story.freight.eur + story.margin.eur;
const should = lines.reduce((a, b) => a + b, 0) + fixed;
const lastYear = story.recipe.reduce((a, l) => a + start * l.weight * (l.level / (1 + l.chg12m)) / 100, 0) + fixed;

test('weights, freight and margin add up to the starting price', () => {
  const w = story.recipe.reduce((a, l) => a + l.weight, 0) + story.freight.weight + story.margin.weight;
  assert.equal(r1(w * 100), 100);
  assert.equal(story.margin.eur, start * story.margin.weight);
  assert.equal(story.freight.eur, start * story.freight.weight);
});

test('should-cost, floor, gap and annual gap', () => {
  assert.equal(r1(should * 100) / 100, story.expect.should_cost_exact); // 290.88
  assert.equal(Math.round(should), story.expect.should_cost);           // 291
  const floor = should - story.margin.eur;                              // freight is a real cost: it stays in the floor
  assert.equal(Math.round(floor * 100) / 100, story.expect.floor_exact); // 251.88
  assert.equal(Math.round(floor), story.expect.floor);                   // 252
  const gap = Math.round(story.price - should);
  assert.equal(gap, story.expect.gap);                                   // 48
  assert.equal(gap * story.volume_t_yr, story.expect.annual_gap);        // 105,600
  const donut = lines.concat([story.freight.eur, story.margin.eur]).map(Math.round);
  assert.deepEqual(donut, story.expect.donut);
  assert.equal(donut.reduce((a, b) => a + b, 0), story.expect.should_cost, 'the donut legend adds up to the should-cost');
  ['€291', '€252', '€48', '€105,600', '€339', '2,200', 'DAP Rotterdam', 'Freight & delivery', 'set by the Incoterm'].forEach(has);
  assert.match(html, /should-cost = Σ lines \+ freight \(€30\) \+ margin \(€39\)<\/span><b>= €291<\/b>/, 'formula block prints the freight line');
});

test('the library index hands Stage 02 an exact number', () => {
  assert.equal(r1(should / start * 100), story.expect.index_now);        // 97.0
  const m = story.index_monthly;
  assert.equal(m.values.length, 42);
  assert.equal(m.values[0], 100);
  assert.equal(m.values[41], story.expect.index_now);
  assert.equal(m.outlook.length, 6);
  story.outlook_eur.values.forEach((v, i) => assert.equal(r1(v / start * 100), m.outlook[i], `outlook month ${i}`));
  // the monthly series averages to the quarterly should-cost (Q3-24 … Q2-26)
  story.quarters.forEach((q, i) => {
    const mo = m.values.slice(18 + i * 3, 21 + i * 3);
    assert.ok(Math.abs(mo.reduce((a, b) => a + b, 0) / 3 * start / 100 - story.should_series[i]) <= 1, `monthly vs quarterly ${q}`);
  });
  has('97.0'); has('94.7');
});

test('the recipe reproduces last year\'s should-cost (Q2 2025) within €1', () => {
  const q = story.quarters.indexOf('Q2-25');
  assert.ok(Math.abs(lastYear - story.should_series[q]) <= 1, `${lastYear.toFixed(2)} vs ${story.should_series[q]}`);
  // "In a year, the should-cost rose about €13 a tonne. Supplier A's price rose €54.50."
  assert.equal(Math.round(should - lastYear), story.year_moves.should_cost_rise);
  assert.equal(story.price - story.price_series[q], story.year_moves.price_rise);
  has(`about €${story.year_moves.should_cost_rise} a tonne`); has('€54.50');
  has(`Index moves explain about €${story.year_moves.should_cost_rise} of Supplier A's €54.50`);
});

test('the example agrees with the real index series shown beside it (sign and rough size)', () => {
  const real = Object.fromEntries(idx.items.map((it) => [it.code, it]));
  story.recipe.forEach((l) => {
    assert.ok(Array.isArray(l.aligned_with) && l.aligned_with.length, `${l.key} names the public series it follows`);
    l.aligned_with.forEach((code) => {
      const it = real[code];
      assert.ok(it, `${code} is in the index snapshot`);
      const mv = l.chg12m * 100;
      assert.equal(Math.sign(mv), Math.sign(it.chg12m_pct), `${l.key} ${mv}% vs ${code} ${it.chg12m_pct}%: same direction`);
      assert.ok(Math.abs(mv - it.chg12m_pct) <= 3, `${l.key} ${mv}% vs ${code} ${it.chg12m_pct}%: within 3 points`);
      assert.ok(Math.abs(l.level - it.latest) <= 12, `${l.key} level ${l.level} vs ${code} ${it.latest}`);
    });
  });
  // every pulse card and the ticker show only series that agree (or are not a driver)
  const drivers = new Set(story.recipe.flatMap((l) => l.aligned_with));
  idx.pulse.forEach((c) => assert.ok(real[c], 'pulse series ' + c));
  assert.ok(idx.pulse.every((c) => drivers.has(c)), 'pulse cards are the four driver series');
});

test('quarterly series end where the model is', () => {
  assert.equal(story.should_series.at(-1), story.expect.should_cost);
  assert.equal(story.price_series.at(-1), story.price);
  const n = story.quarters.length;
  assert.equal(story.should_series[n - 1] - story.should_series[n - 2], story.signal.index_move);
  assert.equal(story.price_series[n - 1] - story.price_series[n - 2], story.signal.price_move);
  has('−€2.50/t'); has('+€14/t');
  const opened = story.quarters.find((q, i) => story.price_series[i] - story.should_series[i] > 10);
  assert.equal(opened.replace(/Q(\d)-(\d\d)/, 'Q$1 20$2'), story.gap_opened);
  has(story.gap_opened);
});

test('buy window: four prior quarters, ±3% band', () => {
  const s = story.should_series, n = s.length;
  const avg = s.slice(n - 5, n - 1).reduce((a, b) => a + b, 0) / 4;
  assert.equal(Math.round(avg), story.buy_window.avg4q);
  const pct = (s[n - 1] / avg - 1) * 100;
  assert.equal(r1(pct), story.buy_window.pct);
  assert.equal(Math.abs(pct) < story.buy_window.band_pct ? 'Neutral' : pct < 0 ? 'Buy now' : 'Hold', story.buy_window.verdict);
  has(`(+${story.buy_window.pct}%)`); has(`average of €${story.buy_window.avg4q}`);
});

test('claim verdicts follow the app\'s rules (negotiation_prep.py)', () => {
  const check = (claimed, actual) => {
    if (Math.abs(actual) < 0.5) return 'no_movement';
    if (actual * claimed < 0) return 'contradicted';
    if (Math.abs(claimed) - Math.abs(actual) > 2.0) return 'overstated';
    return 'already_priced';
  };
  const label = { overstated: 'Overstated', already_priced: 'Already priced', contradicted: 'Contradicted' };
  story.claims.forEach((c) => {
    const line = story.recipe.find((l) => l.key === c.line);
    const v = check(c.claimed, line.chg12m * 100);
    assert.equal(v, c.expect, c.text);
    const chip = new RegExp(`data-cid="${c.id}"[^>]*>[\\s\\S]*?<b class="[^"]*">${label[v]}</b>`);
    assert.match(html, chip, `chip for ${c.id}`);
    assert.ok(html.includes(`value="${c.id}">“${c.text}”</option>`), `select option for ${c.id}`);
  });
  // the contradicted example rests on a real move: the real energy series fell too
  const contra = story.claims.find((c) => c.expect === 'contradicted');
  const ln = story.recipe.find((l) => l.key === contra.line);
  const real = idx.items.filter((it) => ln.aligned_with.includes(it.code));
  assert.ok(real.every((it) => Math.sign(it.chg12m_pct) === Math.sign(ln.chg12m)), 'contradicted claim agrees with the public series');
  assert.ok(!/unexplained by costs/.test(visible), 'softened: "not explained by the indexes in the recipe"');
});

test('Monitor rows: gaps and statuses', () => {
  story.monitor_rows.forEach((r) => {
    const g = r.actual - r.should;
    const st = g >= story.alert_threshold ? 'Alert' : g / r.should * 100 >= 2 ? 'Watch' : 'On track';
    assert.equal(st, r.status, r.product + ' ' + r.supplier);
    assert.ok(html.includes(`<td class="num gap">+${g}</td>`), `gap +${g} printed`);
  });
});

test('Strategy and actions', () => {
  const st = story.strategy;
  assert.equal(st.supplier_shares.reduce((a, s) => a + s.share, 0), 1);
  assert.equal(story.impact_ease.length, st.plotted);
  const q = { qw: 0, ev: 0, le: 0, de: 0 };
  story.impact_ease.forEach((b) => { q[b.impact >= 3 ? (b.ease >= 3 ? 'qw' : 'ev') : (b.ease >= 3 ? 'le' : 'de')]++; });
  assert.deepEqual(q, { qw: 11, ev: 2, le: 2, de: 2 });
  const sel = new Set(st.objectives_selected);
  const ringed = story.impact_ease.filter((b) => b.obj.some((o) => sel.has(o))).length;
  has(`${ringed} of ${st.plotted} ringed`);
  has(`${st.plotted} of ${st.opportunities} opportunities scored`);
  const acts = story.actions;
  assert.equal(acts.length, 5);
  assert.equal(acts.filter((a) => a.status === 'In progress').length, 2);
  const overdue = acts.filter((a) => a.status === 'Overdue');
  assert.equal(overdue.length, 1);
  overdue.forEach((a) => assert.ok(a.due < story.today, 'overdue means due before today'));
  acts.filter((a) => a.status !== 'Overdue').forEach((a) => assert.ok(a.due >= story.today, a.title + ' not overdue'));
  assert.equal(story.handoffs.length, 7);
  story.handoffs.forEach((h) => has(h.chip));
  has(`${acts.length} actions`); // the hero card's last row
});

test('the loop returns to 02 Model, never to 01 Discover', () => {
  const last = story.handoffs.at(-1);
  assert.deepEqual([last.from, last.to], [7, 2]);
  const back = html.match(/<path class="lr-arc back" data-from="(\d+)" data-to="(\d+)"[^>]*marker-end="url\(#lr-arrow-back\)"/);
  assert.ok(back, 'the ring draws a back arc with an arrow');
  assert.deepEqual([back[1], back[2]], ['07', '02'], 'the back arc targets node 02');
  const closing = html.match(/<path class="lr-arc[^"]*" data-from="07" data-to="01"[^>]*>/);
  assert.ok(!closing || !/marker-end/.test(closing[0]), 'no arrow from 07 to 01');
  assert.ok(html.includes('back to 02'), 'the rail says back to 02');
});

test('hero: the seven steps are labelled and print their outcome', () => {
  has('One tool, seven steps, from market to negotiation');
  const outs = [...html.matchAll(/<span class="hs-o">((?:[^<]|<span class="nowrap">[^<]*<\/span>)+)<\/span>/g)].map((m) => m[1].replace(/<[^>]+>/g, '').replace(/&amp;/g, '&'));
  assert.deepEqual(outs, ['what drives cost', 'your should-cost', 'the gap', 'timing', 'floor & brief', 'category plan', 'owners & dates']);
});

test('real counts on the page equal the reviewed snapshot', () => {
  const fmt = (v) => (typeof v === 'number' ? v.toLocaleString('en-US') : v);
  for (const m of html.matchAll(/data-c="(\w+)"[^>]*>([^<]*)</g)) {
    const k = m[1];
    const want = k === 'data_to_short' ? counts.data_to.replace(/^(\w{3})\w*/, '$1') : fmt(counts[k]);
    assert.equal(m[2], want, `count ${k}`);
  }
  assert.equal(counts.cards, 1750); assert.equal(counts.cards_verified_makers, 935);
  assert.equal(counts.families, 25); assert.equal(counts.industries, 50); assert.equal(counts.indexes, 99);
  assert.equal(counts.family_names.length, counts.families);
  assert.equal(counts.industry_names.length, counts.industries);
});

test('per-host flags: every rolling-out key and claim flag matches the page', () => {
  const all = [html, ...subpages].join('\n');
  const ro = new Set([...all.matchAll(/data-ro="([\w-]+)"/g)].map((m) => m[1]));
  for (const env of ['dev', 'www']) {
    site[env].rolling_out.forEach((k) => assert.ok(ro.has(k), `${env}.rolling_out "${k}" matches an element`));
    const flags = [...all.matchAll(/data-claim(?:-off|-swap|-list|-text)?="(\w+)"/g)].map((m) => m[1]);
    flags.forEach((f) => assert.ok(f in site[env].claims, `${env}.claims has "${f}"`));
    assert.equal(typeof site[env].demo.calendar, 'boolean');
    assert.equal(typeof site[env].demo.slotless_post, 'boolean');
  }
  // what production lacks (origin/main) carries a chip on www (plan H1)
  ['discover', 'floor', 'claim-check', 'script', 'plan', 'act', 'mod-pricelist'].forEach((k) => assert.ok(site.www.rolling_out.includes(k), 'www rolls out ' + k));
  // undecided claims stay off and out of the structured data
  assert.equal(site.www.claims.ai_assisted_research, false);
  assert.equal(site.www.claims.show_sources_under_review, false);
  const ld = html.match(/<!--@jsonld:faq--><script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1];
  assert.ok(!/AI-assisted/.test(ld), 'FAQPage data leaves out the AI-assisted sentence while the flag is off');
  assert.match(html, /<span data-claim="ai_assisted_research" hidden>/);
  assert.match(subpages[0], /data-claim="show_sources_under_review" hidden><span class="sb-k">Source under review/);
});

test('the social card is generated from its source and is current', () => {
  const src = read('build/og-cover.html');
  const sha = crypto.createHash('sha256').update(src).digest('hex');
  assert.equal(read('build/og-cover.sha').trim(), sha, 'og-cover.png was rendered from the current build/og-cover.html (run node build/og.mjs)');
  const png = fs.readFileSync(path.join(root, 'og-cover.png'));
  assert.equal(png.readUInt32BE(16), 1200); assert.equal(png.readUInt32BE(20), 630);
  const t = textOf(src);
  ['Know what your products should cost', 'From the chemical market to the negotiation table', 'Illustrative', 'Market library', 'Your workspace'].forEach((s) => assert.ok(t.includes(s), 'card says ' + s));
  assert.ok(/og-cover\.png/.test(html) && subpages.slice(0, 3).every((p) => /og-cover\.png/.test(p)));
});

test('claims lint: no banned phrase, maker count, real supplier, staff name or personal address', () => {
  const banned = [
    /live (?:index|indexes|indices|data|rates|commodity)/i, /\blive\b/i, /real[- ]time/i, /sub-?family/i, /\bindices\b/i, /Frankfurt/, /AES-256/,
    /SOC ?2/, /ISO 27001/, /\bSSO\b/, /every chemical/i, /expert-reviewed/i, /append-only/i, /enterprise-grade/i,
    /\b\d[\d,]*\+?\s+(?:makers|suppliers|producers)\b/i, /(?<![\d.,])0%/, /overcharg/i, /\bsaved\b|\brecovered\b/i,
    /unexplained by costs/i,
    // real names from the demo seed and the staging screens
    /Kemira|Feralco|INEOS|Jacobi|Kronos|Norit|\bOlin\b|\bSNF\b|Solenis|Univar|Westlake|Chemtrade/,
    /Noor Demo|Pieter Demo|Lotte Demo|Alexis Thomas/,
    // one role mailbox on the public site (finding: no personal addresses)
    /laurent\.thomas@|alexis@|@staminachem\.com/i,
  ];
  const brief = textOf(read('build/brief-sample.html'));
  const og = textOf(read('build/og-cover.html'));
  const sources = { homepage: visible, subpages: subpages.map(textOf).join(' '), brief, og };
  const hits = Object.entries(sources).flatMap(([k, text]) => banned.flatMap((re) => { const m = text.match(re); return m ? [`${k}: ${re} → "${m[0]}"`] : []; }));
  // the JSON-LD is crawled too
  const ld = [...[html, ...subpages].join('\n').matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map((m) => m[1]).join(' ');
  banned.forEach((re) => { const m = ld.match(re); if (m) hits.push(`json-ld: ${re} → "${m[0]}"`); });
  assert.deepEqual(hits, []);
  // every frame and the rail say "Illustrative"
  const frames = (html.match(/<figure class="frame/g) || []).length;
  const pills = (html.match(/<span class="pill-illus">Illustrative<\/span>/g) || []).length;
  assert.ok(frames === 7 && pills >= frames + 1, `${frames} frames, ${pills} pills`);
});
