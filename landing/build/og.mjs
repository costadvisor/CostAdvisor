#!/usr/bin/env node
// The social card (plan F1): 1200 × 630, generated from an HTML/SVG source so its text can be linted.
//   node build/og.mjs            writes build/og-cover.html, og-cover.png and build/og-cover.sha
// The card shows the new CA mark, the H1, the line "From the chemical market to the negotiation
// table" and the two-layer hero card with its Illustrative chip. Every number comes from
// data/story.json (the same values as the hero). tests/story.test.mjs lints build/og-cover.html with
// the page's banned-phrase list and checks og-cover.sha, so a stale PNG fails the tests.
// Printing uses the Playwright already installed for tools/shots (no new dependency).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const st = JSON.parse(fs.readFileSync(path.join(root, 'data/story.json'), 'utf8'));
await import(path.join(root, 'assets/js/svg.js'));
const S = globalThis.CASvg;
const eur = (n) => '€' + Math.round(n).toLocaleString('en-US');

const parts = st.recipe.map((l) => st.start.price * l.weight * l.level / 100);
const should = parts.reduce((a, b) => a + b, 0) + st.freight.eur + st.margin.eur;
const gap = Math.round(st.price - should), floor = should - st.margin.eur;
const spark = S.sparkline({ id: 'og1', w: 200, h: 54, actual: st.index_monthly.values, outlook: st.index_monthly.outlook, cls: 'ln-lib', min: 89.5, max: 100.5 });
const gapc = S.gapChart({ id: 'og2', w: 420, h: 84, should: st.should_series, price: st.price_series, min: 264, max: 344, dots: false });
const tokens = fs.readFileSync(path.join(root, 'assets/css/tokens.css'), 'utf8');
const logo = 'data:image/png;base64,' + fs.readFileSync(path.join(root, 'logo.png')).toString('base64');

const html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>CostAdvisor social card</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=block" rel="stylesheet">
<style>${tokens}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1200px;height:630px;overflow:hidden}
body{font-family:var(--font-sans);color:var(--text);background:var(--bg);position:relative;-webkit-font-smoothing:antialiased}
.dots{position:absolute;inset:0;opacity:.7;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='49' viewBox='0 0 56 49'%3E%3Cpath d='M14 1H42L55 24.5L42 48H14L1 24.5Z' fill='none' stroke='%2300A99D' stroke-opacity='0.16' stroke-width='1.2'/%3E%3C/svg%3E");background-size:56px 49px;-webkit-mask-image:radial-gradient(ellipse 70% 80% at 72% 40%,#000 20%,transparent 75%)}
.glow{position:absolute;top:-220px;right:-160px;width:720px;height:720px;border-radius:50%;background:radial-gradient(circle,rgba(0,169,157,.14) 0%,transparent 70%)}
.topbar{position:absolute;top:0;left:0;right:0;height:6px;background:linear-gradient(90deg,var(--blue),var(--teal))}
.l{position:absolute;left:64px;top:56px;width:590px}
.brand{display:flex;align-items:center;gap:12px}
.brand img{width:54px;height:54px;border-radius:13px;box-shadow:0 4px 14px rgba(15,34,40,.18)}
.brand span{font-family:var(--font-display);font-size:28px;font-weight:700;letter-spacing:-.02em}
.brand b{color:var(--blue)}.brand em{font-style:normal;font-weight:500}
.eyebrow{margin-top:40px;font-family:var(--font-mono);font-size:15px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--blue)}
h1{margin-top:14px;font-family:var(--font-display);font-size:50px;line-height:1.06;letter-spacing:-.025em;font-weight:700;max-width:570px;text-wrap:balance}
h1 .b{display:block;color:var(--blue)}
.sub{margin-top:20px;font-size:22px;line-height:1.35;color:var(--text);font-weight:600}
.sub2{margin-top:8px;font-size:18px;color:var(--muted);line-height:1.45}
.foot{position:absolute;left:64px;bottom:44px;font-family:var(--font-mono);font-size:16px;color:var(--muted)}
.foot b{color:var(--blue);font-weight:600}
.card{position:absolute;right:56px;top:62px;width:470px;background:var(--surface);border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow-lg);padding:16px 18px 16px;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--blue),var(--teal))}
.bar{display:flex;align-items:center;gap:6px;margin-bottom:10px}
.bar i{width:10px;height:10px;border-radius:50%;display:inline-block}
.bar .t{font-family:var(--font-mono);font-size:13px;color:var(--muted);margin-left:8px;flex:1}
.illus{display:inline-flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:13px;font-weight:700;color:#805D11;background:rgba(245,158,11,.14);border:1px solid rgba(245,158,11,.45);border-radius:20px;padding:3px 11px}
.illus::before{content:'';width:7px;height:7px;border-radius:50%;background:#F59E0B}
.layer{border-radius:12px;padding:10px 12px}
.lib{background:var(--lib-tint);border:1px solid var(--lib-line);border-left:3px solid var(--lib)}
.ws{border:1px solid var(--ws-line);border-left:3px solid var(--ws)}
.tag{font-family:var(--font-mono);font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:2px 7px;border-radius:5px;color:#fff}
.tag.l1{background:var(--lib-ink)}.tag.w1{background:var(--ws-ink)}
.note{font-family:var(--font-mono);font-size:12px;color:var(--muted);margin-left:8px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:8px}
.k{font-family:var(--font-mono);font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;display:block}
.big{font-family:var(--font-mono);font-size:24px;font-weight:700}
.big small{font-size:11px;font-weight:500;color:var(--muted);margin-left:4px}
.sp{width:180px;height:48px}.sp svg{width:100%;height:100%}
.spark .spark-line{stroke:var(--lib);stroke-width:2}.spark .spark-out{stroke:var(--lib);stroke-width:1.8;stroke-dasharray:3 3}
.spark .stop-a{stop-color:var(--lib);stop-opacity:.22}.spark .stop-b{stop-color:var(--lib);stop-opacity:0}.spark .spark-out-bg{fill:rgba(84,102,109,.06)}
.join{display:flex;align-items:center;gap:8px;margin:7px 0;padding:0 8px}
.join .ln{flex:1;border-top:1.5px dashed var(--teal-line)}
.join .p{font-size:13px;font-weight:700;color:var(--blue);border:1px solid var(--teal-line);border-radius:20px;padding:3px 12px;background:#fff}
.nums{display:flex;gap:18px;align-items:flex-end;margin-top:8px}
.nums b{font-family:var(--font-mono);font-size:20px;font-weight:700;display:block}
.pos{color:var(--green)}.neg{color:var(--red)}
.badge{margin-left:auto;font-family:var(--font-mono);font-size:13px;font-weight:700;color:var(--red);background:var(--price-tint);border:1px solid var(--price-line);border-radius:6px;padding:3px 9px;white-space:nowrap}
.chart{margin-top:8px;background:var(--surface2);border-radius:10px;padding:6px 10px 4px}
.chart svg{width:100%;height:auto}
.gapchart .grid{stroke:rgba(0,0,0,.07)}.gapchart .ln-should{stroke:#10B981;stroke-width:2.4;fill:none}.gapchart .ln-price{stroke:#EF4444;stroke-width:2.4;fill:none}
.gapchart .stop-price-a{stop-color:#EF4444;stop-opacity:.24}.gapchart .stop-price-b{stop-color:#EF4444;stop-opacity:.05}
.next{display:flex;flex-wrap:wrap;gap:4px 7px;align-items:center;margin-top:9px;padding-top:8px;border-top:1px dashed var(--border);font-family:var(--font-mono);font-size:12px;font-weight:600}
.next .nk{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:500}
.next i{font-style:normal;color:var(--blue)}
.fict{position:absolute;right:56px;top:504px;width:470px;font-size:13px;color:var(--muted);text-align:center}
</style></head><body>
<div class="dots"></div><div class="glow"></div><div class="topbar"></div>
<div class="l">
  <div class="brand"><img src="${logo}" alt=""><span><b>Cost</b><em>Advisor</em></span></div>
  <p class="eyebrow">Procurement intelligence for chemical buyers</p>
  <h1>Know what your products should cost — <span class="b">before your supplier tells you.</span></h1>
  <p class="sub">From the chemical market to the negotiation table.</p>
  <p class="sub2">Should-cost, the gap, the floor and the category plan, in one tool.</p>
</div>
<p class="foot">a StaminaChem company · <b>costadvisor.org</b></p>
<div class="card">
  <div class="bar"><i style="background:#ef4444"></i><i style="background:#f59e0b"></i><i style="background:#10b981"></i><span class="t">${st.product}</span><span class="illus">Illustrative</span></div>
  <div class="layer lib">
    <div><span class="tag l1">Market library</span><span class="note">same for every buyer</span></div>
    <div class="row"><div><span class="k">Should-cost index · Europe</span><span class="big">${st.expect.index_now.toFixed(1)}<small>Jan 2023 = 100</small></span></div><div class="sp">${spark}</div></div>
  </div>
  <div class="join"><span class="ln"></span><span class="p">+ Add to portfolio</span><span class="ln"></span></div>
  <div class="layer ws">
    <div><span class="tag w1">Your workspace</span><span class="note">${st.buyer} · private</span></div>
    <div class="nums"><div><span class="k">Should-cost</span><b class="pos">${eur(should)}/t</b></div><div><span class="k">${st.supplier} · ${st.incoterm}</span><b class="neg">${eur(st.price)}/t</b></div><span class="badge">▲ ${eur(gap)}/t above</span></div>
    <div class="chart">${gapc}</div>
    <p class="next"><span class="nk">Then</span><span>Brief · floor ${eur(floor)}</span><i>›</i><span>${st.strategy.category} plan</span><i>›</i><span>${st.actions.length} actions</span></p>
  </div>
</div>
<p class="fict">${st.buyer} and ${st.supplier} are fictional. Every number adds up.</p>
</body></html>`;

const htmlPath = path.join(root, 'build/og-cover.html');
fs.writeFileSync(htmlPath, html);

const { chromium } = await import('/home/alexis/costadvisor/tools/shots/node_modules/playwright-core/index.mjs');
const dir = path.join(os.homedir(), '.cache/ms-playwright');
const chrome = fs.readdirSync(dir).filter((d) => d.startsWith('chromium-')).sort().pop();
const browser = await chromium.launch({ executablePath: path.join(dir, chrome, 'chrome-linux64/chrome'), args: ['--no-sandbox'] });
const pg = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await pg.goto('file://' + htmlPath, { waitUntil: 'networkidle' });
await pg.evaluate(() => document.fonts.ready);
await pg.waitForTimeout(300);
await pg.screenshot({ path: path.join(root, 'og-cover.png'), clip: { x: 0, y: 0, width: 1200, height: 630 } });
await browser.close();
const sha = crypto.createHash('sha256').update(html).digest('hex');
fs.writeFileSync(path.join(root, 'build/og-cover.sha'), sha + '\n');
console.log('og: ok · og-cover.png', fs.statSync(path.join(root, 'og-cover.png')).size, 'bytes · sha', sha.slice(0, 12));
