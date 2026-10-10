#!/usr/bin/env node
// Browser QA for the prototype (plan G3 "Phase 1 is done when", plus the review findings).
//   python3 -m http.server 4610 --directory docs/landing/prototype   (in another shell)
//   node tests/qa.mjs [http://localhost:4610]
// Uses the Playwright already installed for tools/shots (no new dependency). Exits 1 on any failure.
// Checks:
//   1. the ticker track is visible (width > 0) at 390, 480, 700, 1024 and 1440 px
//   2. /#negotiate lands with the stage heading below the fixed nav, even when the web fonts arrive late
//   3. no visible text under WCAG AA contrast (4.5:1, or 3:1 for large text) at 1440 and 390
//   4. page height within the plan's budget (≤ 13,400 px at 1440, ≤ 22,000 px at 390), no sideways scroll
//   5. zero console errors, page errors or failed requests on the home page and the three small pages
//   6. ?site=www shows a Rolling-out chip on every dev-only piece production lacks (plan H1)
//   7. the demo request never dead-ends: with the slotless POST off it becomes a pre-filled e-mail
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = (process.argv[2] || process.env.QA_URL || 'http://localhost:4610').replace(/\/$/, '');
const { chromium } = await import('/home/alexis/costadvisor/tools/shots/node_modules/playwright-core/index.mjs');
const dir = path.join(os.homedir(), '.cache/ms-playwright');
const chrome = fs.readdirSync(dir).filter((d) => d.startsWith('chromium-')).sort().pop();
const browser = await chromium.launch({ executablePath: path.join(dir, chrome, 'chrome-linux64/chrome'), args: ['--no-sandbox'] });

const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? '  ·  ' + detail : ''}`); };
async function open(width, url = '/', opts = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 500 ? 844 : 900 }, ...opts.ctx });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror ' + e));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console ' + m.text()); });
  page.on('response', (r) => { if (r.status() >= 400) errors.push(`http ${r.status()} ${r.url()}`); });
  if (opts.route) await opts.route(page);
  await page.goto(BASE + url, { waitUntil: 'networkidle' });
  return { ctx, page, errors };
}
const settle = async (page) => {
  await page.evaluate(async () => { const H = document.documentElement.scrollHeight; for (let y = 0; y < H; y += 600) { scrollTo(0, y); await new Promise((r) => setTimeout(r, 30)); } scrollTo(0, 0); });
  await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}.reveal-ready .reveal{opacity:1!important;transform:none!important}' });
  await page.waitForTimeout(400);
};

// 1 · ticker
for (const w of [390, 480, 700, 1024, 1440]) {
  const { ctx, page } = await open(w);
  const r = await page.evaluate(() => { const t = document.querySelector('.ticker-track').getBoundingClientRect(), p = document.querySelector('.ticker-pause').getBoundingClientRect(); return { w: Math.round(t.width), h: Math.round(t.height), right: Math.round(t.right), pause: Math.round(p.width) }; });
  check(`ticker visible at ${w}px`, r.w > 100 && r.h >= 30 && r.right <= w && r.pause > 0, `track ${r.w}×${r.h}`);
  await ctx.close();
}

// 2 · deep link, with the three web fonts held back 1.5 s (the swap that used to move /#negotiate by 158 px)
for (const w of [1440, 1024, 390]) {
  const { ctx, page } = await open(w, '/#negotiate', {
    route: (p) => p.route(/fonts\.gstatic\.com/, async (route) => { await new Promise((r) => setTimeout(r, 1500)); route.continue(); }),
  });
  await page.waitForTimeout(4200);
  const r = await page.evaluate(() => {
    const nav = document.querySelector('.nav').getBoundingClientRect().bottom;
    const bars = [...document.querySelectorAll('.rail')].filter((e) => getComputedStyle(e).position === 'sticky' && e.getBoundingClientRect().top < 200).map((e) => e.getBoundingClientRect().bottom);
    const cover = Math.max(nav, ...bars);
    const eb = document.querySelector('#negotiate .stage-eyebrow').getBoundingClientRect().top;
    const h2 = document.querySelector('#negotiate h2').getBoundingClientRect().top;
    return { cover: Math.round(cover), eb: Math.round(eb), h2: Math.round(h2), fonts: document.fonts.check('700 20px "Space Grotesk"') };
  });
  check(`/#negotiate lands below the nav at ${w}px (fonts late)`, r.eb >= r.cover - 1 && r.h2 > r.cover && r.eb - r.cover < 140, `eyebrow ${r.eb}, h2 ${r.h2}, nav/bar bottom ${r.cover}, web font in: ${r.fonts}`);
  await ctx.close();
}

// 3 · contrast
const contrastJs = () => {
  const parse = (c) => { const m = c && c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const L1 = lum(a), L2 = lum(b); return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05); };
  const over = (top, base) => ({ r: top.r * top.a + base.r * (1 - top.a), g: top.g * top.a + base.g * (1 - top.a), b: top.b * top.a + base.b * (1 - top.a) });
  const stops = (img) => (img.match(/rgba?\([^)]+\)/g) || []).map(parse).filter((c) => c && c.a >= 0.9);
  function bases(el, box) {
    // Walk up through ancestors whose box contains the text; compose semi-transparent fills;
    // an opaque gradient ends the walk with each of its stops as a candidate background.
    const layers = []; let e = el; let cands = null;
    while (e && e.nodeType === 1) {
      const cs = getComputedStyle(e), r = e.getBoundingClientRect();
      // a scroll container paints its background behind content scrolled out of view too
      const scroller = e !== el && /(auto|scroll)/.test(cs.overflowX + cs.overflowY);
      const contains = scroller || (r.left <= box.left + 1 && r.right >= box.right - 1 && r.top <= box.top + 1 && r.bottom >= box.bottom - 1);
      if (contains) {
        const bg = parse(cs.backgroundColor);
        if (bg && bg.a > 0) { layers.push(bg); if (bg.a >= 0.99) break; }
        if (cs.backgroundImage && /gradient/.test(cs.backgroundImage)) { const st = stops(cs.backgroundImage); if (st.length) { cands = st; break; } }
      }
      e = e.parentElement;
    }
    const roots = cands || [{ r: 238, g: 242, b: 245, a: 1 }];
    return roots.map((b0) => layers.slice().reverse().reduce((acc, l) => over(l, acc), b0));
  }
  const out = [];
  const seen = new Set();
  const consider = (el, color, text) => {
    if (seen.has(el)) return; seen.add(el);
    if (el.closest('[hidden],.sr-only,[aria-hidden="true"],.stage-bg-num,.modal-backdrop:not(.open),.nav-mobile:not(.open),.sb-menu,[disabled],.hp,details:not([open])>:not(summary)')) return;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height || box.bottom < -2000) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden') return;
    let op = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) op *= Number(getComputedStyle(e).opacity);
    if (op < 0.05) return;
    const fg = parse(color); if (!fg) return;
    const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const worst = Math.min(...bases(el, box).map((b) => ratio(over({ ...fg, a: fg.a * op }, b), b)));
    if (worst < need - 0.005) out.push({ text: text.trim().slice(0, 40), cls: (typeof el.className === 'string' ? el.className : el.getAttribute('class')) || el.tagName, size, ratio: +worst.toFixed(2), need });
  };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const t = w.currentNode; if (!t.textContent.trim()) continue;
    const el = t.parentElement; if (!el || el.closest('script,style,noscript')) continue;
    if (el.closest('svg')) {
      const tx = el.closest('text'); if (!tx) continue;
      // a number drawn on a filled circle (impact-vs-ease bubbles) is read against that circle
      const disc = tx.parentElement && tx.parentElement.querySelector(':scope > circle.dc');
      if (disc) {
        const cs = getComputedStyle(disc), f = parse(cs.fill), fo = Number(cs.fillOpacity || 1), fg = parse(getComputedStyle(tx).fill);
        if (f && fg) {
          const page = bases(tx.closest('svg'), tx.closest('svg').getBoundingClientRect())[0];
          const bg = over({ ...f, a: fo }, page), r = ratio(over(fg, bg), bg);
          if (r < 4.5) out.push({ text: tx.textContent.trim(), cls: tx.getAttribute('class'), size: parseFloat(getComputedStyle(tx).fontSize), ratio: +r.toFixed(2), need: 4.5 });
        }
        seen.add(tx); continue;
      }
      consider(tx, getComputedStyle(tx).fill, tx.textContent); continue;
    }
    consider(el, getComputedStyle(el).color, t.textContent);
  }
  return out;
};
for (const w of [1440, 390]) {
  const { ctx, page } = await open(w);
  await settle(page);
  const low = await page.evaluate(contrastJs);
  check(`contrast AA at ${w}px`, low.length === 0, low.length ? JSON.stringify(low.slice(0, 8)) : 'all visible text ≥ 4.5:1 (3:1 large)');
  // 4 · height and sideways scroll
  const m = await page.evaluate(() => ({ H: document.documentElement.scrollHeight, sw: document.documentElement.scrollWidth }));
  const budget = w === 1440 ? 13400 : 22000;
  check(`page height at ${w}px ≤ ${budget}`, m.H <= budget, `${m.H} px`);
  check(`no sideways scroll at ${w}px`, m.sw <= w, `scrollWidth ${m.sw}`);
  await ctx.close();
}
for (const url of ['/method/', '/security/', '/demo/']) {
  const { ctx, page } = await open(1440, url);
  await settle(page);
  const low = await page.evaluate(contrastJs);
  check(`contrast AA on ${url}`, low.length === 0, low.length ? JSON.stringify(low.slice(0, 6)) : 'ok');
  await ctx.close();
}
{
  const { ctx, page } = await open(1024);
  const sw = await page.evaluate(() => document.documentElement.scrollWidth);
  check('no sideways scroll at 1024px', sw <= 1024, `scrollWidth ${sw}`);
  await ctx.close();
}

// 5 · errors
for (const [url, w] of [['/', 1440], ['/', 390], ['/method/', 1440], ['/security/', 1440], ['/demo/', 390]]) {
  const { ctx, page, errors } = await open(w, url);
  await settle(page);
  await page.waitForTimeout(600);
  check(`no errors on ${url} at ${w}px`, errors.length === 0, errors.slice(0, 3).join(' | '));
  await ctx.close();
}

// 6 · www flags
{
  const { ctx, page } = await open(1440, '/?site=www');
  await page.waitForTimeout(500);
  const r = await page.evaluate(() => {
    const vis = (sel) => { const e = document.querySelector(sel); return !!e && !e.hidden && e.getBoundingClientRect().width > 0; };
    return {
      library: vis('.mockup-layer--lib [data-ro="discover"]'), discover: vis('#discover [data-ro="discover"]'), floor: vis('#negotiate .pos [data-ro="floor"]'),
      claim: vis('#claim [data-ro="claim-check"]'), script: vis('.script-card [data-ro="script"]'), plan: vis('#plan [data-ro="plan"]'), act: vis('#act [data-ro="act"]'),
      coverage: vis('#coverage [data-ro="discover"]'), pricelist: vis('#modules [data-ro="mod-pricelist"]'),
      devHidden: null,
    };
  });
  const miss = Object.entries(r).filter(([k, v]) => v === false).map(([k]) => k);
  check('?site=www shows every Rolling-out chip', miss.length === 0, miss.length ? 'missing: ' + miss.join(', ') : Object.keys(r).filter((k) => r[k]).join(', '));
  await ctx.close();
  const d = await open(1440, '/');
  const n = await d.page.evaluate(() => [...document.querySelectorAll('[data-ro]')].filter((e) => !e.hidden).length);
  check('dev shows no Rolling-out chip', n === 0, `${n} visible`);
  await d.ctx.close();
}

// 7 · demo request: with no calendar and no slotless POST, the request becomes a pre-filled e-mail
{
  const { ctx, page } = await open(1440, '/');
  await page.click('.nav .js-demo');
  await page.fill('#dmProduct', 'ferric chloride 40%');
  await page.selectOption('#dmRole', 'Buyer');
  await page.click('[data-step="0"] [data-go="1"]');
  await page.fill('#nsTimes', 'Tue 10:00 CET');
  await page.waitForTimeout(200);
  const r = await page.evaluate(() => {
    const a = document.querySelector('[data-step="nos"] .js-mailto');
    return { step: !document.querySelector('[data-step="nos"]').hidden, mailVisible: a && a.getBoundingClientRect().width > 0, href: a && a.getAttribute('href'), post: [...document.querySelectorAll('[data-step="nos"] .ns-post-only')].some((e) => e.getBoundingClientRect().height > 0), events: (window.caEvents || []).map((e) => e.name + (e.props.reason ? ':' + e.props.reason : '')) };
  });
  const href = decodeURIComponent(r.href || '');
  check('demo request falls back to a pre-filled e-mail', r.step && r.mailVisible && !r.post && /^mailto:hello@costadvisor\.org\?/.test(r.href || '') && /ferric chloride 40%/.test(href) && /Buyer/.test(href) && /Tue 10:00 CET/.test(href) && r.events.includes('demo_submit_err:slotless_off'), `events ${r.events.join(', ')}`);
  const label = await page.evaluate(() => document.querySelector('#bookForm button').textContent.trim());
  check('Book button promises no calendar', /^Request a demo/.test(label), label);
  await ctx.close();
}

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
fs.writeFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'qa-results.json'), JSON.stringify(results, null, 1));
process.exit(failed.length ? 1 : 0);
