#!/usr/bin/env node
// Builds the three small pages and the 404 (plan A5, B20) from the homepage's own partials:
//   node build/pages.mjs
// Head, icon sprite, nav, footer and modals are lifted from index.html (so they cannot drift),
// links are rebased one folder down, and counts come from data/counts.snapshot.json.
// Writes method/index.html, security/index.html, demo/index.html and 404.html.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const home = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const counts = JSON.parse(fs.readFileSync(path.join(root, 'data/counts.snapshot.json'), 'utf8'));
const pick = (re, name) => { const m = home.match(re); if (!m) throw new Error('missing ' + name); return m[0]; };
// Same three font preloads as the homepage head (one source of truth).
const fontPreloads = (home.match(/  <link rel="preload" as="font"[^>]*>/g) || []).join('\n');

const sprite = pick(/<svg width="0" height="0"[\s\S]*?<\/svg>/, 'sprite');
const nav = pick(/<nav class="nav"[\s\S]*?<\/nav>\n/, 'nav');
const footer = pick(/<footer class="footer">[\s\S]*?<\/footer>/, 'footer');
const access = pick(/<!-- ── Access-request modal[\s\S]*?(?=<!-- ── Demo-booking modal)/, 'access modal');
const demo = pick(/<div class="modal-backdrop" id="lpDemoModal">[\s\S]*?(?=\n<!-- ── Mobile sticky bar)/, 'demo modal');

// One folder down: rebase relative links and assets.
const rebase = (h, pre = '../') => h
  .replace(/(<a\b[^>]*?\bhref=")#/g, `$1${pre}#`)
  .replace(/href="(method|security|demo)\//g, `href="${pre}$1/`)
  .replace(/href="brief-sample\.pdf"/g, `href="${pre}brief-sample.pdf"`)
  .replace(/src="logo\.png"/g, `src="${pre}logo.png"`);

const fmt = (v) => (typeof v === 'number' ? v.toLocaleString('en-US') : v);
const fillCounts = (h) => h.replace(/(<(\w+)\b[^>]*\sdata-c="(\w+)"[^>]*>)([^<]*)(<\/\2>)/g, (m, open, tag, k, _o, close) => {
  if (!(k in counts)) throw new Error('unknown count ' + k);
  return open + fmt(counts[k]) + close;
});

function page({ slug, title, desc, crumb, body, scripts = ['core'], extra = '' }) {
  // the 404 is served at any path, so it uses root-absolute links
  const pre = slug ? '../' : '/';
  const url = `https://costadvisor.org/${slug ? slug + '/' : ''}`;
  const crumbs = slug ? `<script type="application/ld+json">${JSON.stringify({ '@context': 'https://schema.org', '@type': 'BreadcrumbList', itemListElement: [{ '@type': 'ListItem', position: 1, name: 'CostAdvisor', item: 'https://costadvisor.org/' }, { '@type': 'ListItem', position: 2, name: crumb, item: url }] })}</script>` : '';
  return `<!DOCTYPE html>
<html lang="en" data-base="${pre}">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <title>${title}</title>
  <meta name="description" content="${desc}" />
  <!-- PROTOTYPE: the build writes noindex on dev only. -->
  <meta name="robots" content="noindex" />
  <link rel="canonical" href="${url}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="${url}" />
  <meta property="og:title" content="${title}" />
  <meta property="og:description" content="${desc}" />
  <meta property="og:image" content="https://costadvisor.org/og-cover.png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:image:alt" content="CostAdvisor: know what your products should cost, before your supplier tells you. From the chemical market to the negotiation table. An illustrative example card." />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="https://costadvisor.org/og-cover.png" />
  <meta name="theme-color" content="#eef2f5" />
  <link rel="icon" type="image/png" href="${pre}logo.png" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
${fontPreloads}
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="${pre}assets/css/tokens.css" />
  <link rel="stylesheet" href="${pre}assets/css/base.css" />
  <link rel="stylesheet" href="${pre}assets/css/components.css" />
  <link rel="stylesheet" href="${pre}assets/css/sections.css" />
  <link rel="stylesheet" href="${pre}assets/css/pages.css" />
${scripts.map((s) => `  <script defer src="${pre}assets/js/${s}.js"></script>`).join('\n')}
  ${crumbs}
</head>
<body class="subpage">
<a href="#main" class="skip-link">Skip to content</a>
${sprite}
${rebase(nav, pre)}
<main id="main">
${fillCounts(body)}
</main>
${rebase(footer, pre)}
${extra}
</body>
</html>
`;
}

const head = (crumb, eyebrow, h1, sub, toc) => `
<section class="page-hero">
  <div class="hero-dots" aria-hidden="true"></div>
  <div class="container">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="../">CostAdvisor</a><span aria-hidden="true">›</span><span aria-current="page">${crumb}</span></nav>
    <p class="eyebrow">${eyebrow}</p>
    <h1>${h1}</h1>
    <p class="page-sub">${sub}</p>
    ${toc ? `<ul class="page-toc">${toc.map(([id, t]) => `<li><a href="#${id}">${t}</a></li>`).join('')}</ul>` : ''}
  </div>
</section>`;

// ── /method ──────────────────────────────────────────────────────────────
const sg = counts.source_groups;
const sgMax = Math.max(...sg.map((g) => g.n));
const method = head('Method', 'Method', 'Where every number comes from.',
  'A should-cost is a recipe priced on public indexes. Here is the formula, what the library holds, where the indexes come from, how fresh they are, and the rules we hold ourselves to.',
  [['formula', 'The formula'], ['library', 'The library'], ['sources', 'Sources'], ['freshness', 'Freshness'], ['badges', 'How far each card is checked'], ['promises', 'Five promises'], ['your-data', 'Your own data'], ['method-faq', 'Questions']]) + `
<section class="page-sec" id="formula">
  <div class="container page-grid">
    <div><h2>The formula</h2><p>Every product card holds a recipe: cost lines, each tied to an index, with a weight. Your cost model prices that recipe from a starting price you set.</p>
      <p>Then two adjustments: the Incoterm (Incoterms 2020, for landed cost) and the exchange rate (daily ECB reference rates) when a line or a price is in another currency.</p>
      <p>The floor is the cost before the supplier's margin: a minimum, not a target. <a class="link" href="../#model">See it worked through on the homepage →</a></p></div>
    <div class="formula-box"><pre class="formula">should-cost = starting price
              × Σ (weight × index ÷ index at start)
              + margin
            → then Incoterm adjustment and FX

floor       = should-cost − margin
gap         = your price − should-cost
exposure    = today's gap × volume on record</pre></div>
  </div>
</section>

<section class="page-sec alt" id="library">
  <div class="container">
    <h2>What the library holds</h2>
    <p class="page-lead">As of <span data-c="as_of">October 2026</span>: <b><span data-c="cards">1,750</span> product cards</b> in <span data-c="families">25</span> chemical families and <span data-c="product_lines_label">800+</span> product lines, mapped to <span data-c="buying_categories_label">900+</span> buying categories in <span data-c="industries">50</span> industries. The library grows with each research release; it is never the whole market.</p>
    <p id="regions" class="page-note"><b>Regions.</b> Recipes are written per region: Europe, North America, China, Asia-Pacific, India, Middle East &amp; Africa and Latin America. A card carries the regions it has been researched for; not every card covers all seven.</p>
  </div>
</section>

<section class="page-sec" id="sources">
  <div class="container page-grid">
    <div><h2>Sources</h2><p>Built mostly on public statistics and exchange benchmarks: US BLS, World Bank, FRED, USGS, Eurostat, EIA, LME and others. Every series names its source where one is documented.</p>
      <!-- "Source under review" waits for Laurent's sign-off (plan H3 #6): it shows only when
           site.json claims.show_sources_under_review is true. Until then the chart shows the documented groups. -->
      <p data-claim="show_sources_under_review" hidden>Some series still have their source under review. We show that number rather than hide it.</p>
    </div>
    <figure class="src-bars" aria-labelledby="srcCap">
      ${sg.map((g) => `<div class="sb"${/under review/i.test(g.group) ? ' data-claim="show_sources_under_review" hidden' : ''}><span class="sb-k">${g.group}</span><span class="sb-bar"><i style="width:${((g.n / sgMax) * 100).toFixed(1)}%"></i></span><b>${g.n}</b></div>`).join('\n      ')}
      <figcaption id="srcCap"><span data-claim="show_sources_under_review" hidden>The <span data-c="indexes">99</span> cost indexes by source group, <span data-c="as_of">October 2026</span>.</span><span data-claim-off="show_sources_under_review">Cost indexes with a documented source, by source group, <span data-c="as_of">October 2026</span>.</span></figcaption>
    </figure>
  </div>
</section>

<section class="page-sec alt" id="freshness">
  <div class="container">
    <h2>Freshness</h2>
    <ul class="fact-list">
      <li><b>Monthly since <span data-c="index_history_from">January 2023</span>.</b> The current release runs to <span data-c="data_to">June 2026</span>. Every chart shows its data date, so you always know how old a number is.</li>
      <li><b><span data-c="indexes_with_outlook">97</span> of the <span data-c="indexes">99</span> indexes have a six-month outlook:</b> <span data-c="indexes_modelled_outlook">53</span> modelled, <span data-c="indexes_flat_outlook">44</span> flat carry-forwards, each labelled. An outlook is a projection, never a promise.</li>
      <li><b>Exchange rates update daily</b> from ECB reference rates.</li>
      <li><b>Updated with each research release.</b> There is no fixed calendar.</li>
    </ul>
  </div>
</section>

<section class="page-sec" id="badges">
  <div class="container">
    <h2>How far each card is checked</h2>
    <p class="page-lead">Every card says how far its supply has been checked. We never show how many makers a product has.</p>
    <div class="badge-grid">
      <div class="bdg"><span class="pc-badge"><i></i>Verified makers</span><p>Independent makers of this exact product are verified on their own pages. <b><span data-c="cards_verified_makers">935</span> cards</b> carry this badge.</p></div>
      <div class="bdg"><span class="pc-badge amber"><i></i>Concentrated supply</span><p>Supply is concentrated for a structural reason, and the reason is documented on the card.</p></div>
      <div class="bdg"><span class="pc-badge amber2"><i></i>Supply pending</span><p>The supply check for this card is under way.</p></div>
      <div class="bdg"><span class="pc-badge grey"><i></i>Makers not yet verified</span><p>The card's makers have not been checked against their own pages yet. The cost recipe still works.</p></div>
    </div>
  </div>
</section>

<section class="page-sec alt" id="promises">
  <div class="container">
    <h2>Five promises</h2>
    <ol class="promise-list">
      <li>No invented numbers.</li>
      <li>No maker counts as headlines.</li>
      <li>An undisclosed share is never shown as zero.</li>
      <li>Demo data is always labelled illustrative.</li>
      <li>Forecasts are never presented as certain.</li>
    </ol>
  </div>
</section>

<section class="page-sec" id="your-data">
  <div class="container page-grid">
    <div><h2>Your own data</h2><p>Already pay for a price agency? Use your own index values in your workspace. Your prices, volumes and suppliers stay in your team's private workspace and never go into the library. <a class="link" href="../security/">How we keep them apart →</a></p></div>
    <div class="side-card"><p class="mockup-label">In one line</p><p class="page-quote">The library is the same for every buyer. Your workspace is yours alone.</p></div>
  </div>
</section>

<section class="page-sec alt" id="method-faq">
  <div class="container">
    <h2>Questions</h2>
    <div class="faq-list one">
      <details class="faq-item" name="mfaq" open><summary>How is this different from a price agency?</summary><div class="faq-a"><p>A price agency reports what a commodity costs. CostAdvisor turns index moves into your should-cost, your gap and your negotiation position, and you can feed it your agency's values.</p></div></details>
      <details class="faq-item" name="mfaq"><summary>How are recipes written?</summary><div class="faq-a"><p>By StaminaChem's research, region by region, with the source of each index named where one is documented.<span data-claim="ai_assisted_research" hidden> Research is AI-assisted and evidence-checked.</span> Your own models start from a library recipe, and you can edit or extend them.</p></div></details>
      <details class="faq-item" name="mfaq"><summary>What does "projection" mean?</summary><div class="faq-a"><p>The six months after the last data point. A modelled projection follows the index's own model; a flat carry-forward repeats the last value and is labelled as such. Neither is a forecast you should treat as certain.</p></div></details>
    </div>
  </div>
</section>`;

// ── /security ────────────────────────────────────────────────────────────
const halves = pick(/<div class="halves reveal">[\s\S]*?<p class="halves-line">[\s\S]*?<\/p>\n      <\/div>/, 'halves').replace(/\n\s*<ol class="loop-list only-m"[\s\S]*?<\/ol>/, '');
const secTiles = pick(/<div class="security-grid sec5">[\s\S]*?\n    <\/div>\n    <p class="sec-link">/, 'security tiles').replace(/\n    <p class="sec-link">$/, '');
const security = head('Security', 'Security', 'Your prices stay yours.',
  'A shared, read-only library and a private workspace for each team. Data flows one way. This page is for IT and security reviewers, and it holds the fine print.',
  [['architecture', 'Architecture'], ['controls', 'Controls'], ['hosting', 'Hosting'], ['fine-print', 'The fine print']]) + `
<section class="page-sec" id="architecture">
  <div class="container">
    <h2>Two halves, one direction</h2>
    <p class="page-lead">The library is platform data with no team column; teams can read it, never write to it. Everything your team adds lives in your workspace, isolated per team. "Add to portfolio" copies a recipe into your workspace; nothing flows back.</p>
    ${halves.replace('class="halves reveal"', 'class="halves"')}
  </div>
</section>
<section class="page-sec alt security" id="controls">
  <div class="container">
    <h2>Controls</h2>
    ${secTiles.replace(/ reveal/g, '')}
    <ul class="fact-list compact">
      <li><b>Google sign-in.</b> We never store passwords.</li>
      <li><b>Invite-only.</b> Access is granted by request and review.</li>
      <li><b>Security questionnaire on request:</b> <a class="link" href="mailto:hello@costadvisor.org?subject=Security%20questionnaire">hello@costadvisor.org</a></li>
    </ul>
  </div>
</section>
<section class="page-sec" id="hosting">
  <div class="container">
    <h2>Hosting and data location</h2>
    <!-- PROTOTYPE: stays this way until the owner checks the Railway region (plan Part H, check 1). No location claim before then. -->
    <p class="page-lead">Hosted on Railway. Region and data-location details on request.</p>
  </div>
</section>
<section class="page-sec alt" id="fine-print">
  <div class="container">
    <h2>The fine print, in plain language</h2>
    <p class="page-lead">Short versions. The full texts are the <a class="link js-app" data-path="/privacy" href="https://app.costadvisor.org/privacy">privacy policy</a> and the <a class="link js-app" data-path="/terms" href="https://app.costadvisor.org/terms">terms of service</a>.</p>
    <div class="legal-grid">
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-file"/></svg></div><h3>Terms of service</h3><p>Use CostAdvisor to build and analyse should-cost models for your organisation's purchasing. No redistribution, resale or reverse-engineering. It supports decisions; it does not make them, and we are not liable for the commercial outcome of a figure or a brief.</p></div>
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-lock"/></svg></div><h3>Privacy and GDPR</h3><p>We process personal data to run the service you signed up for. You can access, correct, export or delete your data at any time. We never sell personal data.</p></div>
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-landmark"/></svg></div><h3>Your data, your IP</h3><p>Your cost models, uploads and briefs stay your intellectual property. The library is built mostly on public statistics and exchange benchmarks, each series naming its source where one is documented. It is information: check it before you negotiate.</p></div>
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-globe"/></svg></div><h3>Deleting your account</h3><p>Delete your account from Settings. If you are the only member of your team, the team and its data are deleted with it. Backups are kept for 30 days, then permanently deleted.</p></div>
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-key"/></svg></div><h3>Sign-in and AI</h3><p>Google sign-in; we never store passwords. AI runs on a model we host, and nothing goes to a third-party AI provider.</p></div>
      <div class="legal-card"><div class="legal-card-icon"><svg class="ic"><use href="#ic-mail"/></svg></div><h3>Questions?</h3><p>Privacy, security and legal questions go to <a class="link" href="mailto:hello@costadvisor.org">hello@costadvisor.org</a>.</p></div>
    </div>
  </div>
</section>`;

// ── /demo ────────────────────────────────────────────────────────────────
const inlineDemo = demo
  .replace('<div class="modal-backdrop" id="lpDemoModal">', '<div class="booking-inline inline" id="lpDemoModal">')
  .replace(/<button class="modal-close"[^>]*>[\s\S]*?<\/button>\n/, '')
  .replace('role="dialog" aria-modal="true" aria-labelledby="lpDemoTitle"', 'aria-labelledby="lpDemoTitle"')
  .replace('<h3 id="lpDemoTitle">Request a 30-minute demo</h3>', '<h2 id="lpDemoTitle">Tell us when suits you</h2>')
  .replace(/\n<\/div>\s*$/, '\n</div>');
const demoPage = `
<section class="page-hero demo-hero">
  <div class="hero-dots" aria-hidden="true"></div>
  <div class="container">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="../">CostAdvisor</a><span aria-hidden="true">›</span><span aria-current="page">Book a demo</span></nav>
    <div class="demo-grid">
      <div>
        <p class="eyebrow">Book a demo</p>
        <h1>See CostAdvisor on a product you buy.</h1>
        <p class="page-sub">Thirty minutes, in the product, on your own category. Bring one price you pay. We'll show the rest.</p>
        <ol class="agenda">
          <li><b>5 min</b><span>Your product in the library: its recipe and the indexes that drive it</span></li>
          <li><b>10 min</b><span>Its should-cost, and the gap to the price you pay</span></li>
          <li><b>10 min</b><span>The category playbook it opens</span></li>
          <li><b>5 min</b><span>The brief, the floor and your questions</span></li>
        </ol>
        <ul class="nr-list demo-alt">
          <li><a href="../brief-sample.pdf" data-ev="pdf_download"><svg class="ic"><use href="#ic-download"/></svg><span><b>Not ready to talk? Download the sample brief</b><small>PDF, 2 pages, illustrative</small></span></a></li>
          <li><a class="js-signin" href="https://api.costadvisor.org/auth/login"><svg class="ic"><use href="#ic-key"/></svg><span><b>Already invited? Sign in</b><small>Google sign-in</small></span></a></li>
        </ul>
      </div>
      <div class="booking-card skeuo">
        ${inlineDemo}
      </div>
    </div>
  </div>
</section>`;

const notFound = `
<section class="page-hero nf">
  <div class="hero-dots" aria-hidden="true"></div>
  <div class="container">
    <p class="eyebrow">404</p>
    <h1>This page is not in the library.</h1>
    <p class="page-sub">The link may be old, or the page may have moved.</p>
    <p class="nf-links"><a class="btn btn-primary" href="/">Back to the homepage</a> <a class="btn btn-ghost" href="/#discover">Follow one purchase</a></p>
  </div>
</section>`;

const pages = [
  ['method/index.html', page({ slug: 'method', title: 'How a should-cost is built — CostAdvisor', desc: 'The formula, the sources, the freshness and the rules behind every number on CostAdvisor.', crumb: 'Method', body: method, scripts: ['core', 'booking'], extra: rebase(access) })],
  ['security/index.html', page({ slug: 'security', title: 'Security and privacy — CostAdvisor', desc: 'A shared, read-only library and a private workspace for each team. Controls, hosting and the fine print.', crumb: 'Security', body: security, scripts: ['core', 'booking'], extra: rebase(access) })],
  ['demo/index.html', page({ slug: 'demo', title: 'Book a demo — CostAdvisor', desc: 'See CostAdvisor on a product you buy: should-cost, gap, playbook and brief, in 30 minutes.', crumb: 'Book a demo', body: demoPage, scripts: ['core', 'booking'], extra: rebase(access) })],
  ['404.html', page({ slug: '', title: 'Page not found — CostAdvisor', desc: 'This page is not on costadvisor.org.', crumb: '404', body: notFound, scripts: ['core'] })],
];
for (const [f, html] of pages) {
  fs.mkdirSync(path.dirname(path.join(root, f)), { recursive: true });
  fs.writeFileSync(path.join(root, f), html);
}
console.log('pages: ok ·', pages.map((p) => p[0]).join(', '));
