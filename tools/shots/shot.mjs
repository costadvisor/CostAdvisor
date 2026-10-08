// Screenshot helper for the local demo build.
//
//   node tools/shots/shot.mjs <url-or-file> <out.png> [--width 1440] [--height 900]
//        [--full] [--wait <ms>] [--click "<css selector>"]... [--eval "<js>"]
//        [--cookie name=value]... [--errors <out.json>]
//        [--local key=value]...   localStorage entries set before any page script runs
//                                 (e.g. --local ca_active_team=<team uuid>)
//        [--theme <name>]         force a theme after load (default|light|amber|staminachem)
//        [--hide-onboarding]      hide the floating onboarding checklist
//
// Uses the Chromium that Playwright already installed under ~/.cache/ms-playwright.
// Local files (mockups) can be passed as a plain path. Console errors and page
// errors are written next to the PNG (or to --errors) so a reviewer can see what
// threw, not just what rendered.
import { chromium } from 'playwright-core';
import fs from 'fs';
import os from 'os';
import path from 'path';

const args = process.argv.slice(2);
if (args.length < 2) {
  console.error('usage: shot.mjs <url-or-file> <out.png> [options]');
  process.exit(2);
}
let [target, out] = args;
const opt = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : def;
};
const multi = (name) => args.flatMap((a, i) => (a === name ? [args[i + 1]] : []));
const width = Number(opt('--width', 1440));
const height = Number(opt('--height', 900));
const wait = Number(opt('--wait', 800));
const full = args.includes('--full');
const clicks = multi('--click');
const evals = multi('--eval');
const cookies = multi('--cookie');
const locals = multi('--local');
const theme = opt('--theme', null);
const hideOnboarding = args.includes('--hide-onboarding');
const errOut = opt('--errors', out.replace(/\.png$/, '') + '.errors.json');

if (!/^https?:|^file:/.test(target)) target = 'file://' + path.resolve(target);

const chromeDir = path.join(os.homedir(), '.cache/ms-playwright');
const chromeRoot = fs.readdirSync(chromeDir).filter((d) => d.startsWith('chromium-')).sort().pop();
const executablePath = path.join(chromeDir, chromeRoot, 'chrome-linux64/chrome');

const browser = await chromium.launch({ executablePath, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
if (cookies.length) {
  const u = new URL(target.startsWith('file:') ? 'http://localhost' : target);
  await ctx.addCookies(cookies.map((c) => {
    const [name, ...rest] = c.split('=');
    return { name, value: rest.join('='), domain: u.hostname, path: '/' };
  }));
}
if (locals.length) {
  const entries = locals.map((kv) => { const [k, ...v] = kv.split('='); return [k, v.join('=')]; });
  await ctx.addInitScript((pairs) => { for (const [k, v] of pairs) window.localStorage.setItem(k, v); }, entries);
}
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push({ type: 'pageerror', text: String(e) }));
page.on('console', (m) => { if (m.type() === 'error') errors.push({ type: 'console', text: m.text() }); });
page.on('response', (r) => { if (r.status() >= 400) errors.push({ type: 'http', status: r.status(), url: r.url() }); });

await page.goto(target, { waitUntil: 'networkidle', timeout: 60000 }).catch((e) => errors.push({ type: 'goto', text: String(e) }));
await page.waitForTimeout(wait);
const applyCosmetics = async () => {
  if (theme) await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme).catch(() => {});
  if (hideOnboarding) await page.evaluate(() => {
    document.querySelectorAll('div').forEach((d) => { if (d.style.zIndex === '9998') d.style.display = 'none'; });
  }).catch(() => {});
};
await applyCosmetics();
for (const sel of clicks) {
  try { await page.click(sel, { timeout: 10000 }); await page.waitForTimeout(wait); }
  catch (e) { errors.push({ type: 'click', selector: sel, text: String(e).slice(0, 300) }); }
}
for (const js of evals) {
  try { await page.evaluate(js); await page.waitForTimeout(wait); }
  catch (e) { errors.push({ type: 'eval', text: String(e).slice(0, 300) }); }
}
await applyCosmetics();
// Let the theme's CSS colour transition settle before capturing.
if (theme) await page.waitForTimeout(600);
fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });
await page.screenshot({ path: out, fullPage: full });
fs.writeFileSync(errOut, JSON.stringify(errors, null, 1));
console.log(`${out}  errors=${errors.length}`);
await browser.close();
