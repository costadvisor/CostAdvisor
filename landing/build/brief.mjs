#!/usr/bin/env node
// The sample brief (plan F1): a 2-page A4 PDF rendered from data/story.json, never exported from
// the app (which would bring its AI paragraph and different rounding).
//   node build/brief.mjs            writes build/brief-sample.html and brief-sample.pdf
// Printing uses the Playwright already installed for tools/shots (no new dependency).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const st = JSON.parse(fs.readFileSync(path.join(root, 'data/story.json'), 'utf8'));
await import(path.join(root, 'assets/js/svg.js'));
const S = globalThis.CASvg;
const eur = (n, d = 0) => '€' + Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + '%';

const lines = st.recipe.map((l) => ({ ...l, eur: st.start.price * l.weight * l.level / 100 }));
const should = lines.reduce((a, l) => a + l.eur, 0) + st.freight.eur + st.margin.eur;
const floor = should - st.margin.eur;
const gap = Math.round(st.price - should);
const check = (claimed, actual) => (Math.abs(actual) < 0.5 ? 'That input did not move' : actual * claimed < 0 ? 'Contradicted — it moved the other way' : Math.abs(claimed) - Math.abs(actual) > 2 ? 'Overstated' : 'Real, and already inside the should-cost');
const chart = S.gapChart({ id: 'b', w: 640, h: 170, should: st.should_series, price: st.price_series, min: 272, max: 344, dots: true });
const css = fs.readFileSync(path.join(root, 'assets/css/tokens.css'), 'utf8');

const foot = (n) => `<footer class="pf"><span>Illustrative sample · Aquaverde and Supplier A are fictional</span><span>costadvisor.org · ${n}/2</span></footer>`;
const html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Negotiation brief — illustrative sample — CostAdvisor</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>${css}
@page{size:A4;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font-sans);color:var(--text);font-size:11pt;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{width:210mm;height:297mm;padding:16mm 16mm 14mm;position:relative;page-break-after:always;overflow:hidden}
.page:last-child{page-break-after:auto}
.top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid var(--teal);padding-bottom:8px;margin-bottom:14px}
.brand{font-family:var(--font-display);font-weight:700;font-size:14pt}.brand b{color:var(--blue)}
.stamp{font-family:var(--font-mono);font-size:8.5pt;font-weight:600;color:#805D11;background:rgba(245,158,11,.14);border:1px solid rgba(245,158,11,.45);border-radius:20px;padding:2px 10px}
h1{font-family:var(--font-display);font-size:20pt;line-height:1.15;margin-bottom:4px}
h2{font-family:var(--font-display);font-size:12.5pt;margin:16px 0 8px}
.meta{font-family:var(--font-mono);font-size:8.5pt;color:var(--muted)}
.pos{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
.pos div{border:1px solid var(--border);border-radius:8px;padding:8px 10px}
.k{display:block;font-family:var(--font-mono);font-size:7.5pt;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.v{display:block;font-family:var(--font-mono);font-size:17pt;font-weight:700}
.s{display:block;font-size:8pt;color:var(--dim)}
.say{margin-top:8px;font-size:10pt}
table{width:100%;border-collapse:collapse;font-size:9.5pt}
th,td{padding:5px 6px;border-bottom:1px solid var(--border);text-align:left}
th{font-family:var(--font-mono);font-size:7.5pt;color:var(--muted);text-transform:uppercase;background:var(--surface2)}
td.n{text-align:right;font-family:var(--font-mono)}
.chart{border:1px solid var(--border);border-radius:8px;padding:8px 10px}
.chart svg{width:100%;height:auto}
.gapchart .grid{stroke:rgba(0,0,0,.08)}.gapchart .ln-should{stroke:#10B981;stroke-width:2.2;fill:none}.gapchart .ln-price{stroke:#EF4444;stroke-width:2.2;fill:none}
.gapchart .stop-price-a{stop-color:#EF4444;stop-opacity:.22}.gapchart .stop-price-b{stop-color:#EF4444;stop-opacity:.04}.gapchart .pt-price{fill:#EF4444}.gapchart .pt-should{fill:#10B981}
.lab{display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:7.5pt;color:var(--dim);margin-top:2px}
.leg{font-family:var(--font-mono);font-size:8pt;color:var(--muted);margin-bottom:4px}
.leg i{display:inline-block;width:12px;height:2px;vertical-align:middle;margin:0 4px 0 10px}
.script{background:var(--ink);color:#d8f3ef;border-radius:8px;padding:12px 14px;font-family:var(--font-mono);font-size:9pt;line-height:1.55}
.script li{margin:0 0 6px 16px}
.note{font-size:8.5pt;color:var(--muted);line-height:1.5;margin-top:8px}
.pf span{white-space:nowrap}
.pf{position:absolute;left:16mm;right:16mm;bottom:9mm;display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:7.5pt;color:#805D11;border-top:1px solid var(--border);padding-top:5px}
.v-o{color:#805D11}.v-p{color:var(--green)}.v-c{color:var(--red)}
</style></head><body>
<section class="page">
  <div class="top"><span class="brand"><b>Cost</b>Advisor · Negotiation brief</span><span class="stamp">Illustrative sample</span></div>
  <h1>${st.product} · ${st.supplier}</h1>
  <p class="meta">${st.buyer} (fictional) · ${st.site} · ${st.incoterm} · ${st.quarter} · ${st.currency}/${st.unit} · about ${st.volume_t_yr.toLocaleString('en-US')} t a year</p>
  <h2>Negotiation position</h2>
  <div class="pos">
    <div><span class="k">Floor</span><span class="v" style="color:var(--blue)">${eur(floor)}</span><span class="s">cost before margin: a minimum, not a target</span></div>
    <div><span class="k">Target · the should-cost</span><span class="v" style="color:var(--green)">${eur(should)}</span><span class="s">open near here</span></div>
    <div><span class="k">Their price</span><span class="v" style="color:var(--red)">${eur(st.price)}</span><span class="s">${eur(gap)}/t above should-cost</span></div>
  </div>
  <p class="say">Defensible range ${eur(floor)}–${eur(should)}. Open near should-cost; treat the floor as your walk-away point, not your opening offer. Annual gap ≈ ${eur(gap * st.volume_t_yr)} (${eur(gap)} × ${st.volume_t_yr.toLocaleString('en-US')} t).</p>
  <h2>Cost drivers</h2>
  <table><thead><tr><th>Cost line</th><th>Weight</th><th>Index (Jan 2023 = 100)</th><th>12-month move</th><th>€/t today</th></tr></thead><tbody>
    ${lines.map((l) => `<tr><td>${l.label}</td><td class="n">${Math.round(l.weight * 100)}%</td><td class="n">${l.level}</td><td class="n">${pct(l.chg12m * 100)}</td><td class="n">${eur(l.eur)}</td></tr>`).join('')}
    <tr><td>${st.freight.label}, fixed</td><td class="n">${Math.round(st.freight.weight * 100)}%</td><td class="n">—</td><td class="n">—</td><td class="n">${eur(st.freight.eur)}</td></tr>
    <tr><td>Margin (fixed)</td><td class="n">${Math.round(st.margin.weight * 100)}%</td><td class="n">—</td><td class="n">—</td><td class="n">${eur(st.margin.eur)}</td></tr>
    <tr><td><b>Should-cost</b></td><td></td><td></td><td></td><td class="n"><b>${eur(should)}</b></td></tr>
  </tbody></table>
  <p class="note">Each index line = €${st.start.price} starting price (${st.start.label}) × weight × index ÷ 100. Freight and delivery to ${st.site} (${st.incoterm}) and the margin are fixed lines; the floor is the should-cost minus the margin. Illustrative weights, not the library recipe.</p>
  <h2>Price evolution · quarterly</h2>
  <div class="chart"><p class="leg">€/t ·<i style="background:#10B981"></i>Should-cost ${eur(st.should_series[0], Number.isInteger(st.should_series[0]) ? 0 : 1)} → ${eur(should)}<i style="background:#EF4444"></i>${st.supplier} ${eur(st.price_series[0], Number.isInteger(st.price_series[0]) ? 0 : 1)} → ${eur(st.price)}</p>${chart}<div class="lab">${st.quarters.map((q) => `<span>${q}</span>`).join('')}</div></div>
  <p class="note">In a year, the should-cost rose about €${st.year_moves.should_cost_rise} a tonne; ${st.supplier}'s price rose €${st.year_moves.price_rise.toFixed(2)}. The gap opened in ${st.gap_opened}.</p>
  ${foot(1)}
</section>
<section class="page">
  <div class="top"><span class="brand"><b>Cost</b>Advisor · Negotiation brief</span><span class="stamp">Illustrative sample</span></div>
  <h2 style="margin-top:0">${st.supplier}'s claims, checked against the indexes</h2>
  <table><thead><tr><th>They said</th><th>Index, 12 months</th><th>Verdict</th></tr></thead><tbody>
    ${st.claims.map((c) => { const a = st.recipe.find((l) => l.key === c.line).chg12m * 100; const v = check(c.claimed, a); const cls = v.startsWith('Over') ? 'v-o' : v.startsWith('Real') ? 'v-p' : 'v-c'; return `<tr><td>“${c.text}”</td><td class="n">${pct(a)}</td><td class="${cls}"><b>${v}</b></td></tr>`; }).join('')}
  </tbody></table>
  <p class="note">Rules: within 2 points of the index = already priced; under 0.5% = did not move; opposite sign = contradicted. Index moves explain about €${st.year_moves.should_cost_rise} of the €${st.year_moves.price_rise.toFixed(2)} increase in a year; the ${eur(gap)} above the should-cost is not explained by the indexes in the recipe, nor by freight and delivery.</p>
  <h2>Call script</h2>
  <ol class="script">
    <li>“We have modelled ${st.product.toLowerCase()} bottom-up from published indexes. The defensible number is ${eur(should)}/t; your current price is ${eur(st.price)}/t, ${eur(gap)}/t above that.”</li>
    <li>“Labour is up ${pct(st.recipe.find((l) => l.key === 'labour').chg12m * 100).replace('+', '')} on the index. That is real, and it is already inside the should-cost.”</li>
    <li>“We are ready to settle at ${eur(should)}/t. Below ${eur(floor)}/t there is no margin left in the chain, so that is not a number we expect you to accept.”</li>
  </ol>
  <p class="note">Every figure comes from the calculation engine, not from a language model.</p>
  <h2>How these numbers are built</h2>
  <p class="note" style="font-size:9.5pt;color:var(--text)">Should-cost = starting price × Σ (weight × index ÷ index at start) + margin, then Incoterm and exchange rate. Floor = should-cost − margin. Indexes are monthly, base 100 = January 2023; the library is built mostly on public statistics and exchange benchmarks, each series naming its source where one is documented. Full method: costadvisor.org/method</p>
  <h2>See it on your own product</h2>
  <p class="note" style="font-size:10pt;color:var(--text)">Bring one product and one price you pay. In a 30-minute demo we build its should-cost, its gap, its floor and this brief, on the call. Book at costadvisor.org/demo</p>
  ${foot(2)}
</section>
</body></html>`;
const htmlPath = path.join(root, 'build/brief-sample.html');
fs.writeFileSync(htmlPath, html);

const { chromium } = await import('/home/alexis/costadvisor/tools/shots/node_modules/playwright-core/index.mjs');
const dir = path.join(os.homedir(), '.cache/ms-playwright');
const chrome = fs.readdirSync(dir).filter((d) => d.startsWith('chromium-')).sort().pop();
const browser = await chromium.launch({ executablePath: path.join(dir, chrome, 'chrome-linux64/chrome'), args: ['--no-sandbox'] });
const pg = await browser.newPage();
await pg.goto('file://' + htmlPath, { waitUntil: 'networkidle' });
await pg.pdf({ path: path.join(root, 'brief-sample.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true });
await browser.close();
console.log('brief: ok · brief-sample.pdf', fs.statSync(path.join(root, 'brief-sample.pdf')).size, 'bytes');
