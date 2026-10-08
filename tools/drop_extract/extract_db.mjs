// Extract the content programme's database into one JSON file per constant,
// WITHOUT executing any of it.
//
//   node tools/drop_extract/extract_db.mjs <DUMP_DIR> <OUT_DIR> [--overwrite] [--db-file PATH]
//
// DUMP_DIR is a folder holding ClaudeSweep/ and ClaudeReports/: either the
// unzipped Drive export (docs/Documents-.../Documents) or a clone of the live
// costadvisor-database repo. Both have the same layout:
//   ClaudeSweep/intelligence_mockup*.html       the live database page
//   ClaudeSweep/indexes_mockup.html             the Indexes page
//   ClaudeReports/data/                         tree, supply axis, scope files
//   ClaudeReports/reports/                      delivered reports + playbooks
// OUT_DIR should be under docs/ (gitignored: this is licensed content). It must
// be empty or missing, unless --overwrite is passed (which clears only the
// folders this script writes). tools/drop_extract/refresh.sh wraps this script
// for the routine refresh (extract to a .next folder, check, swap).
//
// The database page's file name carries a copy number that changes, e.g.
// "(19)" -> "(20)". By default the single match of
// ClaudeSweep/intelligence_mockup*.html is used; several matches stop the run.
// --db-file PATH (relative to DUMP_DIR, and inside it) picks one explicitly.
//
// Git LFS guard: every input is checked BEFORE anything is written. A file
// that starts with "version https://git-lfs.github.com/spec/v1" is a pointer,
// not the data, and stops the run with the owner's instruction. This script
// never runs git-lfs and never pulls: both write into the source working tree.
//
// Exit codes: 0 complete; 1 refused or failed (when the inputs are refused,
// nothing is written and an existing OUT_DIR is left untouched); 2 usage;
// 3 written but INCOMPLETE (a constant failed or was only partly extracted,
// or a script block did not parse; see _manifest.json). The loader refuses an
// incomplete drop, so a partial extraction never reaches a database.
//
// This replaces extract_sept.mjs, which evaluated each constant with
// `new Function`. Here the page is only PARSED (acorn builds a syntax tree,
// nothing runs) and the tree is converted to plain values by `toValue` below.
// It accepts only data syntax: object and array literals, string / number /
// boolean / null / regex literals (a regex becomes its source text, e.g.
// "/^Aerospace$/"), templates without ${}, unary - + ! ~ void and arithmetic
// on such values, `undefined` / `NaN` / `Infinity`, and `new Set([...])` /
// `new Map([...])` over literals (written as an array / an object). Anything
// else (calls, member access, references to other constants, functions inside
// data) is never evaluated: it is listed in the manifest with its node type
// and position. If the whole constant is unsupported, no file is written
// (ok: false); if only a part is, that part is written as null and the
// constant's manifest row lists it under `unsupported`.
//
// What it writes:
//   OUT/raw/<CONST>.json     every top-level UPPERCASE constant of the database
//                            page. Function constants are saved as their source
//                            text under raw/_functions/<NAME>.js (text only).
//   OUT/raw/_manifest.json   one row per constant: kind, count, ok / reason,
//                            plus the top-level declarations the name filter
//                            left out (`not_extracted`), so nothing is skipped
//                            silently.
//   OUT/indexes/             the same for the Indexes page (INDEXES, IDX, FORE).
//   OUT/tree/category_tree.json, OUT/axis/supply_axis.json, OUT/scope/v1_scope.json,
//   OUT/scope/industries_v2.json        copied verbatim.
//   OUT/reports/             the reports listed in MANIFEST.json + MANIFEST.json.
//   OUT/playbooks/           the playbook_*_appdata.json files.
//   OUT/_manifest.json       source, git commit (when DUMP_DIR is a git repo),
//                            resolved paths, copy counts, warnings, timing.
import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import * as acorn from 'acorn';

const t0 = Date.now();
const USAGE = 'usage: node extract_db.mjs <DUMP_DIR> <OUT_DIR> [--overwrite] [--db-file PATH]';
function usage(msg) { if (msg) console.error(`extract_db: ${msg}`); console.error(USAGE); process.exit(2); }
const args = process.argv.slice(2);
let OVERWRITE = false;
let DB_FILE = null;
const positional = [];
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === '--overwrite') OVERWRITE = true;
  else if (a === '--db-file') { if (i + 1 >= args.length) usage('--db-file needs a path'); DB_FILE = args[++i]; }
  else if (a.startsWith('--db-file=')) DB_FILE = a.slice('--db-file='.length);
  else if (a.startsWith('--')) usage(`unknown option ${a}`);
  else positional.push(a);
}
if (positional.length !== 2) usage();
if (DB_FILE === '') usage('--db-file needs a path');
const DUMP = path.resolve(positional[0]);
const OUT = path.resolve(positional[1]);

const mkdir = (p) => fs.mkdirSync(p, { recursive: true });
const writeJSON = (p, v) => { mkdir(path.dirname(p)); fs.writeFileSync(p, JSON.stringify(v)); };
const count = (v) => (Array.isArray(v) ? v.length : v && typeof v === 'object' ? Object.keys(v).length : null);
const warnings = [];
function fail(msg) { console.error(`extract_db: ${msg}`); process.exit(1); }

// Presentation-only constants: colours and CSS class maps.
const SKIP = new Set(['PALETTE', 'CLS', 'TC', 'RC']);
const UPPER = /^[A-Z][A-Z0-9_]{1,}$/;

// ── Layout ───────────────────────────────────────────────────────────────────
// Both known layouts are identical; the candidate lists keep this explicit and
// make a moved file fail loudly instead of being skipped.
function firstExisting(label, candidates) {
  for (const c of candidates) if (fs.existsSync(c)) return c;
  fail(`${label} not found; looked for:\n  ${candidates.join('\n  ')}`);
}
const inside = (dir, p) => { const r = path.relative(dir, p); return r === '' || (!r.startsWith('..') && !path.isAbsolute(r)); };
if (inside(DUMP, OUT)) fail(`OUT_DIR ${OUT} is inside the source ${DUMP}; never write into the source tree`);
const SWEEP = firstExisting('ClaudeSweep/', [path.join(DUMP, 'ClaudeSweep')]);
const DATA = firstExisting('data folder', [path.join(DUMP, 'ClaudeReports', 'data')]);
const REPORTS = firstExisting('reports folder', [path.join(DUMP, 'ClaudeReports', 'reports')]);

// The database page: --db-file, else the single match of intelligence_mockup*.html.
function databasePage() {
  if (DB_FILE !== null) {
    const p = path.resolve(DUMP, DB_FILE);
    if (!inside(DUMP, p)) fail(`--db-file ${DB_FILE} resolves outside the source ${DUMP}`);
    if (!fs.existsSync(p) || !fs.statSync(p).isFile()) fail(`--db-file ${DB_FILE}: no such file (${p})`);
    return p;
  }
  const matches = fs.readdirSync(SWEEP).filter((f) => /^intelligence_mockup.*\.html$/.test(f)).sort();
  if (!matches.length) fail(`database page not found: no ClaudeSweep/intelligence_mockup*.html in ${SWEEP}`);
  if (matches.length > 1) {
    fail(`several database pages match ClaudeSweep/intelligence_mockup*.html:\n  ${matches.join('\n  ')}\n` +
      'pass --db-file "ClaudeSweep/<the one to use>"');
  }
  return path.join(SWEEP, matches[0]);
}
const DB_PAGE = databasePage();
const IDX_PAGE = firstExisting('Indexes page', [path.join(SWEEP, 'indexes_mockup.html')]);
const AXIS = firstExisting('supply axis', [path.join(DATA, 'supply_axis_v3.json')]);
{
  // The copy is pinned to v3 (what the loaders were built on); say so if a newer one appears.
  const newer = fs.readdirSync(DATA).filter((f) => {
    const m = /^supply_axis_v(\d+)\.json$/.exec(f);
    return m && Number(m[1]) > 3;
  });
  if (newer.length) warnings.push(`newer supply axis present but not used: ${newer.join(', ')}`);
}
const copied = {
  'tree/category_tree.json': path.join(DATA, 'category_tree.json'),
  'axis/supply_axis.json': AXIS,
  'scope/v1_scope.json': path.join(DATA, 'v1_scope.json'),
  'scope/industries_v2.json': path.join(DATA, 'industries_v2.json'),
};
for (const src of Object.values(copied)) if (!fs.existsSync(src)) fail(`missing ${src}`);
const manPath = firstExisting('reports MANIFEST.json', [path.join(REPORTS, 'MANIFEST.json')]);
const reportFiles = fs.readdirSync(REPORTS);
const playbookFiles = reportFiles.filter((f) => /^playbook_.+_appdata\.json$/.test(f));

// ── Git LFS guard: every input, before anything is read in full or written ───
const LFS_PREFIX = 'version https://git-lfs.github.com/spec/v1';
function isLfsPointer(file) {
  const fd = fs.openSync(file, 'r');
  try {
    const buf = Buffer.alloc(200);
    const n = fs.readSync(fd, buf, 0, 200, 0);
    return buf.subarray(0, n).toString('utf8').startsWith(LFS_PREFIX);
  } finally { fs.closeSync(fd); }
}
function lfsGuard(files) {
  const pointers = files.filter((f) => fs.existsSync(f) && fs.statSync(f).isFile() && isLfsPointer(f));
  if (!pointers.length) return;
  console.error(`extract_db: ${pointers.length} input(s) are Git LFS pointer files, not data:`);
  for (const p of pointers.slice(0, 20)) console.error(`  ${path.relative(DUMP, p)}`);
  if (pointers.length > 20) console.error(`  ... and ${pointers.length - 20} more`);
  console.error(`LFS pointer: the owner runs \`git -C ${DUMP} lfs pull\`, then retry.`);
  console.error('Nothing was written.');
  process.exit(1);
}
lfsGuard([DB_PAGE, IDX_PAGE, ...Object.values(copied), manPath]);
const man = JSON.parse(fs.readFileSync(manPath, 'utf8'));
for (const r of man.reports) {
  // MANIFEST names files inside reports/; never follow a path out of it.
  if (typeof r.delivered !== 'string' || path.basename(r.delivered) !== r.delivered) {
    fail(`reports MANIFEST entry is not a bare file name: ${JSON.stringify(r.delivered)}`);
  }
}
lfsGuard([...man.reports.map((r) => path.join(REPORTS, r.delivered)),
  ...playbookFiles.map((f) => path.join(REPORTS, f))]);

// ── Output folder ────────────────────────────────────────────────────────────
const OWNED = ['raw', 'indexes', 'tree', 'axis', 'scope', 'reports', 'playbooks', '_manifest.json'];
if (fs.existsSync(OUT) && fs.readdirSync(OUT).length) {
  if (!OVERWRITE) fail(`${OUT} is not empty; pass --overwrite to replace this script's folders in it`);
  for (const f of OWNED) fs.rmSync(path.join(OUT, f), { recursive: true, force: true });
}
mkdir(OUT);

// ── Git (only when DUMP_DIR itself is the repository root) ───────────────────
function gitInfo(dir) {
  if (!fs.existsSync(path.join(dir, '.git'))) return null;
  // Read-only: no optional index refresh, no fsmonitor hook.
  const git = (...a) => execFileSync('git', ['--no-optional-locks', '-C', dir, '-c', 'core.fsmonitor=false', ...a],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  try {
    const [commit, date, subject] = git('log', '-1', '--format=%H%n%cI%n%s').split('\n');
    let branch = null, dirty = null;
    try { branch = git('rev-parse', '--abbrev-ref', 'HEAD'); } catch { /* detached or odd */ }
    try { dirty = git('status', '--porcelain', '--untracked-files=no') !== ''; } catch { /* unknown */ }
    return { commit, date, subject, branch, dirty };
  } catch (e) {
    // git unavailable: fall back to reading HEAD as text.
    try {
      const head = fs.readFileSync(path.join(dir, '.git', 'HEAD'), 'utf8').trim();
      const ref = /^ref: (.+)$/.exec(head);
      const commit = ref ? fs.readFileSync(path.join(dir, '.git', ref[1]), 'utf8').trim() : head;
      return { commit, date: null, subject: null, branch: ref ? ref[1].replace('refs/heads/', '') : null, dirty: null };
    } catch { return { error: String(e).slice(0, 200) }; }
  }
}
const GIT = gitInfo(DUMP);

// ── AST → value (never evaluates) ────────────────────────────────────────────
const UNSUPPORTED = Symbol('unsupported');
const isPrimitive = (v) => v === null || (typeof v !== 'object' && typeof v !== 'function');
const IDENT_KEY = /^[A-Za-z_$][\w$]*$/;

// A path is a linked list (parent, segment) so it costs nothing until it is printed.
function pathString(p) {
  const segs = [];
  for (; p; p = p.parent) segs.push(p.seg);
  segs.reverse();
  return segs.map((s, i) => (typeof s === 'number' ? `[${s}]`
    : IDENT_KEY.test(s) ? (i ? `.${s}` : s) : `[${JSON.stringify(s)}]`)).join('');
}

function nodeLabel(n) {
  if (n.type === 'Identifier') return `Identifier(${n.name})`;
  if ((n.type === 'CallExpression' || n.type === 'NewExpression') && n.callee) {
    const c = n.callee;
    const name = c.type === 'Identifier' ? c.name
      : c.type === 'MemberExpression' && !c.computed && c.property.type === 'Identifier' ? `….${c.property.name}` : c.type;
    return `${n.type}(${name})`;
  }
  if (n.type === 'Property') return `Property(${n.method ? 'method' : n.kind})`;
  if (n.type === 'Literal' && n.bigint !== undefined) return 'Literal(bigint)';
  return n.type;
}

// ctx: { body, bodyOffset, html, issues: [], notes: {} }
function toValue(node, p, ctx) {
  const bad = (n, why, where = p) => {
    const at = ctx.bodyOffset + n.start;
    const { line, column } = acorn.getLineInfo(ctx.html, at);
    const src = ctx.body.slice(n.start, Math.min(n.end, n.start + 80)).replace(/\s+/g, ' ');
    ctx.issues.push({ path: pathString(where) || '(root)', node: nodeLabel(n), ...(why ? { why } : {}),
      line, col: column + 1, offset: at, length: n.end - n.start, source: src });
    return UNSUPPORTED;
  };
  const child = (seg) => ({ parent: p, seg });

  switch (node.type) {
    case 'Literal':
      if (node.regex) { ctx.notes.regex = (ctx.notes.regex || 0) + 1; return `/${node.regex.pattern}/${node.regex.flags}`; }
      if (node.bigint !== undefined) return bad(node, 'BigInt has no JSON form');
      return node.value;

    case 'TemplateLiteral':
      if (node.expressions.length) return bad(node, 'template with ${} expressions');
      if (node.quasis[0].value.cooked == null) return bad(node, 'invalid escape in template');
      return node.quasis[0].value.cooked;

    case 'ParenthesizedExpression':
      return toValue(node.expression, p, ctx);

    case 'Identifier':
      if (node.name === 'undefined') return undefined;
      if (node.name === 'NaN') return NaN;
      if (node.name === 'Infinity') return Infinity;
      return bad(node, 'reference to another binding');

    case 'ArrayExpression': {
      const out = new Array(node.elements.length);
      node.elements.forEach((el, i) => {
        if (el === null) { out[i] = undefined; return; }            // hole → JSON null
        if (el.type === 'SpreadElement') { bad(el, 'spread'); out[i] = null; return; }
        const v = toValue(el, child(i), ctx);
        out[i] = v === UNSUPPORTED ? null : v;
      });
      return out;
    }

    case 'ObjectExpression': {
      const out = {};
      for (const prop of node.properties) {
        if (prop.type === 'SpreadElement') { bad(prop, 'spread'); continue; }
        if (prop.kind !== 'init' || prop.method) {
          const name = prop.computed ? null : prop.key.type === 'Identifier' ? prop.key.name : String(prop.key.value);
          bad(prop, 'getter, setter or method', name == null ? p : child(name));
          continue;
        }
        let key;
        if (prop.computed) {
          const k = toValue(prop.key, child('[computed key]'), ctx);
          if (k === UNSUPPORTED) continue;
          if (!isPrimitive(k)) { bad(prop.key, 'computed key is not a literal'); continue; }
          key = String(k);
        } else {
          key = prop.key.type === 'Identifier' ? prop.key.name : String(prop.key.value);
        }
        const v = toValue(prop.value, child(key), ctx);
        const val = v === UNSUPPORTED ? null : v;
        if (key === '__proto__') {
          // In a literal, `__proto__: x` would set the prototype rather than make a
          // property. Keep it as data, and say so.
          if (!prop.computed && !prop.shorthand) (ctx.notes.proto_keys ||= []).push(pathString(child(key)));
          Object.defineProperty(out, key, { value: val, enumerable: true, writable: true, configurable: true });
        } else {
          out[key] = val;
        }
      }
      return out;
    }

    case 'UnaryExpression': {
      if (!['-', '+', '!', '~', 'void'].includes(node.operator)) return bad(node, `operator ${node.operator}`);
      const a = toValue(node.argument, p, ctx);
      if (a === UNSUPPORTED) return UNSUPPORTED;
      if (!isPrimitive(a) || typeof a === 'bigint' || typeof a === 'symbol') return bad(node, 'operand is not a literal');
      switch (node.operator) {
        case '-': return -a;
        case '+': return +a;
        case '!': return !a;
        case '~': return ~a;
        default: return undefined;                                    // void
      }
    }

    case 'BinaryExpression': {
      if (!['+', '-', '*', '/', '%', '**'].includes(node.operator)) return bad(node, `operator ${node.operator}`);
      const l = toValue(node.left, p, ctx);
      const r = toValue(node.right, p, ctx);
      if (l === UNSUPPORTED || r === UNSUPPORTED) return UNSUPPORTED;
      if (!isPrimitive(l) || !isPrimitive(r) || typeof l === 'bigint' || typeof r === 'bigint') return bad(node, 'operands are not literals');
      switch (node.operator) {
        case '+': return l + r;
        case '-': return l - r;
        case '*': return l * r;
        case '/': return l / r;
        case '%': return l % r;
        default: return l ** r;
      }
    }

    case 'NewExpression': {
      const kind = node.callee.type === 'Identifier' ? node.callee.name : null;
      if (kind !== 'Set' && kind !== 'Map') return bad(node, 'only new Set / new Map over literals');
      const arg = node.arguments[0];
      let items = [];
      if (arg) {
        if (arg.type === 'SpreadElement') return bad(arg, 'spread');
        const v = toValue(arg, p, ctx);
        if (v === UNSUPPORTED) return UNSUPPORTED;
        if (v === undefined || v === null) items = [];
        else if (Array.isArray(v)) items = v;
        else return bad(arg, `${kind} over something other than an array literal`);
      }
      (ctx.notes.kinds ||= new Set()).add(kind);
      if (kind === 'Set') return [...new Set(items)];                 // same dedupe as the real Set
      const m = new Map();
      for (const [i, e] of items.entries()) {
        if (!Array.isArray(e)) { bad(arg.elements?.[i] || arg, 'Map entry is not a [key, value] pair'); continue; }
        if (!isPrimitive(e[0])) { bad(arg.elements?.[i] || arg, 'Map key is not a primitive'); continue; }
        m.set(e[0], e[1]);
      }
      const out = {};
      for (const [k, v] of m) {
        Object.defineProperty(out, String(k), { value: v, enumerable: true, writable: true, configurable: true });
      }
      return out;
    }

    default:
      return bad(node, null);
  }
}

// ── One page → one folder ────────────────────────────────────────────────────
function scripts(html) {
  const out = [];
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) out.push({ body: m[1], offset: m.index + m[0].length - '</script>'.length - m[1].length });
  return out;
}

function extractPage(file, outDir) {
  const html = fs.readFileSync(file, 'utf8');
  const manifest = [];
  const notExtracted = [];
  const scriptRows = [];
  scripts(html).forEach(({ body, offset }, si) => {
    let ast;
    try { ast = acorn.parse(body, { ecmaVersion: 'latest', sourceType: 'script' }); }
    catch (e) { scriptRows.push({ index: si, length: body.length, parsed: false, error: String(e).slice(0, 200) }); return; }
    scriptRows.push({ index: si, length: body.length, parsed: true, statements: ast.body.length });
    for (const node of ast.body) {
      if (node.type !== 'VariableDeclaration') continue;
      for (const d of node.declarations) {
        if (d.id.type !== 'Identifier' || !d.init) continue;
        const name = d.id.name;
        if (!UPPER.test(name)) {
          if (node.kind === 'const' && /^[A-Z]/.test(name)) {
            notExtracted.push({ name, kind: d.init.type, reason: 'name outside the UPPERCASE (2+ chars) filter' });
          }
          continue;
        }
        if (SKIP.has(name)) { manifest.push({ name, kind: 'skipped', reason: 'presentation only' }); continue; }
        const t = d.init.type;
        if (t === 'FunctionExpression' || t === 'ArrowFunctionExpression') {
          const src = body.slice(d.init.start, d.init.end);
          const p = path.join(outDir, '_functions', `${name}.js`);
          mkdir(path.dirname(p));
          fs.writeFileSync(p, `const ${name} = ${src};\n`);            // text only, never run
          manifest.push({ name, kind: 'function', ok: true });
          continue;
        }
        const ctx = { body, bodyOffset: offset, html, issues: [], notes: {} };
        const v = toValue(d.init, null, ctx);
        d.init = null;                                                // let the subtree go
        let kind = t;
        if (t === 'NewExpression' && ctx.notes.kinds?.size === 1) kind = [...ctx.notes.kinds][0];
        const extra = {};
        if (ctx.notes.regex) extra.regex_as_text = ctx.notes.regex;
        if (ctx.notes.proto_keys) extra.proto_keys = ctx.notes.proto_keys;
        if (v === UNSUPPORTED) {
          const i = ctx.issues[0];
          manifest.push({ name, kind: t, ok: false,
            reason: `unsupported ${i.node} at line ${i.line} col ${i.col}`, unsupported: ctx.issues, ...extra });
          continue;
        }
        writeJSON(path.join(outDir, `${name}.json`), v);
        const row = { name, kind, ok: true, count: count(v), ...extra };
        if (ctx.issues.length) { row.complete = false; row.unsupported = ctx.issues; }
        manifest.push(row);
      }
    }
  });
  return { manifest, notExtracted, scripts: scriptRows };
}

function copy(src, dst) { mkdir(path.dirname(dst)); fs.copyFileSync(src, dst); }

// ── 1. The live database page ────────────────────────────────────────────────
const rawDir = path.join(OUT, 'raw');
const db = extractPage(DB_PAGE, rawDir);
writeJSON(path.join(rawDir, '_manifest.json'),
  { source: DB_PAGE, git: GIT, extracted: db.manifest, not_extracted: db.notExtracted, scripts: db.scripts });

// ── 2. The Indexes page (card set + its own series) ──────────────────────────
const idx = extractPage(IDX_PAGE, path.join(OUT, 'indexes'));
writeJSON(path.join(OUT, 'indexes', '_manifest.json'),
  { source: IDX_PAGE, git: GIT, extracted: idx.manifest, not_extracted: idx.notExtracted, scripts: idx.scripts });

// ── 3. The taxonomy, axis, scope and reference-buyer files ───────────────────
for (const [dst, src] of Object.entries(copied)) copy(src, path.join(OUT, dst));

// ── 4. Reports (only those in MANIFEST) and playbooks (_appdata only) ────────
copy(manPath, path.join(OUT, 'reports', 'MANIFEST.json'));
let reports = 0;
const missingReports = [];
for (const r of man.reports) {
  const src = path.join(REPORTS, r.delivered);
  if (!fs.existsSync(src)) { missingReports.push(r.delivered); continue; }
  copy(src, path.join(OUT, 'reports', r.delivered));
  reports++;
}
if (missingReports.length) warnings.push(`reports in MANIFEST but missing on disk: ${missingReports.join(', ')}`);
const listed = new Set(man.reports.map((r) => r.delivered));
const unlistedAll = reportFiles.filter((f) => f.endsWith('.html') && !listed.has(f));
const unlisted = unlistedAll.filter((f) => !/\.bak/.test(f));             // backups are noise
const reportBackups = unlistedAll.length - unlisted.length;
let playbooks = 0;
for (const f of playbookFiles) { copy(path.join(REPORTS, f), path.join(OUT, 'playbooks', f)); playbooks++; }

// ── Summary ──────────────────────────────────────────────────────────────────
const ms = Date.now() - t0;
const rssMB = Math.round(process.memoryUsage().rss / 1e6);
const all = [...db.manifest, ...idx.manifest];
const failed = all.filter((m) => m.ok === false);
const partial = all.filter((m) => m.unsupported && m.ok);
const badScripts = [...db.scripts, ...idx.scripts].filter((s) => !s.parsed);
if (badScripts.length) warnings.push(`${badScripts.length} script block(s) failed to parse (see raw/indexes _manifest.json)`);
writeJSON(path.join(OUT, '_manifest.json'), {
  extractor: 'tools/drop_extract/extract_db.mjs (parse only, no evaluation)',
  extracted_at: new Date().toISOString(),
  dump_dir: DUMP,
  git: GIT,
  sources: { db_page: DB_PAGE, indexes_page: IDX_PAGE, data_dir: DATA, reports_dir: REPORTS, copied },
  constants: { database: db.manifest.filter((m) => m.ok).length, indexes: idx.manifest.filter((m) => m.ok).length,
    failed: failed.map((m) => m.name), partial: partial.map((m) => m.name) },
  reports, reports_not_in_manifest: unlisted, report_backups_not_copied: reportBackups, playbooks,
  warnings,
  run: { ms, rss_mb_at_end: rssMB },
});

console.log(`source: ${DUMP}` + (GIT?.commit ? ` @ ${GIT.commit.slice(0, 12)} (${GIT.date})${GIT.dirty ? ' DIRTY' : ''}` : ''));
console.log(`database page: ${db.manifest.filter((m) => m.ok).length} constants extracted, ${failed.length} failed` +
  (failed.length ? ` (${failed.map((f) => f.name).join(', ')})` : '') +
  (partial.length ? `, ${partial.length} partial (${partial.map((f) => f.name).join(', ')})` : ''));
for (const m of db.manifest) if (m.ok) console.log(`  ${m.name.padEnd(36)} ${String(m.kind).padEnd(18)} ${m.count ?? ''}`);
if (db.notExtracted.length) console.log(`  not extracted (name filter): ${db.notExtracted.map((n) => `${n.name}:${n.kind}`).join(', ')}`);
console.log(`indexes page: ${idx.manifest.filter((m) => m.ok).length} constants`);
console.log(`reports: ${reports} · playbooks: ${playbooks}` + (unlisted.length ? ` · not in MANIFEST (not copied): ${unlisted.join(', ')}` : ''));
for (const w of warnings) console.log(`warning: ${w}`);
console.log(`written to ${OUT} in ${(ms / 1000).toFixed(1)} s (rss ${rssMB} MB)`);
if (failed.length || partial.length || badScripts.length) {
  // Written for inspection, but not loadable: the loader refuses this drop.
  console.error(`extract_db: INCOMPLETE extraction (failed: ${failed.map((m) => m.name).join(', ') || 'none'}; ` +
    `partial: ${partial.map((m) => m.name).join(', ') || 'none'}; unparsed script blocks: ${badScripts.length}). ` +
    'The loader refuses this drop; see _manifest.json.');
  process.exit(3);
}
