/* CostAdvisor landing — core: config, per-host flags, data, events, lead context, nav, reveal.
 * Every other module waits for CA.ready (story, counts and site flags loaded). */
(function () {
  'use strict';
  document.documentElement.classList.add('js');

  const host = location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '' || location.protocol === 'file:';
  const isDev = /(^|\.)dev\.costadvisor\.org$/.test(host) || host.startsWith('dev.');
  const params = new URLSearchParams(location.search);
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const CA = (window.CA = {
    env: isDev ? 'dev' : isLocal ? 'local' : 'www',
    // PROTOTYPE: on localhost the production API does not allow this origin (CORS), so the page
    // runs on its dated snapshots (data/*.json) and never calls the network. On dev and www it
    // calls the same public endpoints as today's page.
    API: isDev ? 'https://api-dev.costadvisor.org' : isLocal ? null : 'https://api.costadvisor.org',
    APP: isDev ? 'https://app.dev.costadvisor.org' : 'https://app.costadvisor.org',
    reduced,
    params,
  });
  CA.offline = !CA.API;
  CA.siteKey = params.get('site') === 'www' ? 'www' : params.get('site') === 'dev' ? 'dev' : (isDev || isLocal ? 'dev' : 'www');

  // ── Deep links (/#negotiate from a sales e-mail): land on the anchor even when late web fonts,
  //    a closing disclosure or a chart change the height of the content above it. Re-anchors on
  //    font loads and on window load for the first 4 s, and stops as soon as the reader scrolls.
  (function keepAnchor() {
    if (!location.hash || location.hash.length < 2) return;
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { return; }
    let user = false;
    const stop = () => { user = true; };
    ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach((t) => addEventListener(t, stop, { passive: true, once: true }));
    const fix = () => {
      if (user) return;
      const t = document.getElementById(id);
      if (!t) return;
      const root = document.documentElement;
      const pad = parseFloat(getComputedStyle(root).scrollPaddingTop) || 0;
      const off = t.getBoundingClientRect().top - pad;
      if (Math.abs(off) < 2) return;
      const prev = root.style.scrollBehavior;
      root.style.scrollBehavior = 'auto';
      window.scrollTo(0, window.scrollY + off);
      root.style.scrollBehavior = prev;
    };
    CA.reanchor = fix;
    if (document.fonts) {
      document.fonts.ready.then(fix);
      if (document.fonts.addEventListener) document.fonts.addEventListener('loadingdone', fix);
    }
    addEventListener('load', fix);
    setTimeout(fix, 1200);
    setTimeout(() => { fix(); user = true; }, 4000);
  })();

  // ── Small helpers ──
  CA.$ = (s, r) => (r || document).querySelector(s);
  CA.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  CA.eur = (n, d) => '€' + Number(n).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  CA.signed = (n, d) => (n > 0 ? '+' : n < 0 ? '−' : '±') + Math.abs(n).toFixed(d == null ? 1 : d);
  CA.store = {
    get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: in-memory only */ } },
  };
  // Sub-pages live one folder down (<html data-base="../">).
  CA.base = document.documentElement.dataset.base || '';
  const getJSON = (p) => fetch(CA.base + p, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));

  // ── Data: story (illustrative), counts (real), site flags ──
  CA.ready = Promise.all([
    getJSON('data/story.json').catch(() => null),
    getJSON('data/site.json').catch(() => null),
  ]).then(([story, site]) => {
    CA.story = story;
    CA.site = site ? site[CA.siteKey] : null;
    applySite();
    return CA;
  });

  // ── Per-host flags (plan E1: site.json) ──
  function applySite() {
    const s = CA.site;
    if (!s) return;
    (s.rolling_out || []).forEach((k) => CA.$$(`[data-ro="${k}"]`).forEach((el) => { el.hidden = false; }));
    const c = s.claims || {};
    CA.$$('[data-claim]').forEach((el) => { if (el.dataset.claim in c) el.hidden = !c[el.dataset.claim]; });
    CA.$$('[data-claim-off]').forEach((el) => { if (c[el.dataset.claimOff]) el.hidden = true; });
    CA.$$('[data-claim-swap]').forEach((el) => { if (c[el.dataset.claimSwap]) el.textContent = el.dataset.on; });
    const d = s.demo || {};
    CA.demoCfg = { calendar: !!d.calendar, slotless_post: !!d.slotless_post, email: s.contact_email || 'hello@costadvisor.org' };
    const ch = c.alert_channels || [];
    CA.$$('[data-claim-list="alert_channels"]').forEach((el) => {
      el.hidden = !ch.length;
      el.innerHTML = ch.map((x) => `<span class="ch">${x}</span>`).join('');
    });
    CA.$$('[data-claim-text="alert_channels"]').forEach((el) => {
      el.textContent = ch.length ? ', by ' + ch.join(', ').replace(/, ([^,]*)$/, ' or $1') : '';
    });
  }

  // ── First-party events (plan D5). No cookies, no IP, no free text. ──
  const EVENTS = new Set(['page_view', 'cta_click', 'stage_view', 'rail_click', 'entry_chip', 'calc_change', 'calc_preset', 'claim_check', 'pdf_download', 'share_click', 'demo_open', 'demo_no_slots', 'demo_submit_ok', 'demo_submit_err', 'access_submit_ok', 'access_submit_err', 'widget_error']);
  window.caEvents = window.caEvents || [];
  CA.track = function (name, props) {
    if (!EVENTS.has(name)) return;
    const ev = { name, props: props || {}, t: Date.now(), page: location.pathname, device: innerWidth < 760 ? 'mobile' : innerWidth < 1100 ? 'tablet' : 'desktop' };
    window.caEvents.push(ev);
    // PROTOTYPE: the build batches these with navigator.sendBeacon to POST /api/public/events
    // (plan E2 #4, not built yet). Here they only collect in window.caEvents for inspection.
  };
  CA.track('page_view', { ref: document.referrer ? new URL(document.referrer).hostname : '' });

  // ── Lead context (plan D4): what the visitor did, sent only with a request ──
  const lead = Object.assign({ product: '', price: null, volume: null, chip: '', stages: [], source: '' }, CA.store.get('ca_lead') || {});
  CA.lead = {
    get: () => lead,
    set(k, v) { lead[k] = v; CA.store.set('ca_lead', lead); },
    stage(n) { if (!lead.stages.includes(n)) { lead.stages.push(n); lead.stages.sort(); CA.store.set('ca_lead', lead); } },
  };

  // ── Lazy init one viewport before an element scrolls in ──
  CA.lazy = function (el, fn) {
    if (!el) return;
    if (!('IntersectionObserver' in window)) { fn(); return; }
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); fn(); } }, { rootMargin: '100% 0px 100% 0px' });
    io.observe(el);
  };
  // A software (CPU) 2D context: Chart.js reuses the first context a canvas hands out, and a
  // non-accelerated one also shows up in print and in full-page captures. The charts are small.
  CA.ctx2d = (c) => c.getContext('2d', { willReadFrequently: true });
  CA.whenChart = function (fn, tries) {
    if (window.Chart) { fn(window.Chart); return; }
    if ((tries || 0) > 80) { CA.track('widget_error', { widget: 'chartjs' }); return; }
    setTimeout(() => CA.whenChart(fn, (tries || 0) + 1), 100);
  };

  function boot() {
    // Sign-in links (host-aware), app links (privacy, terms).
    if (CA.API) CA.$$('.js-signin').forEach((a) => { a.href = CA.API + '/auth/login'; });
    CA.$$('.js-app').forEach((a) => { a.href = CA.APP + a.dataset.path; });
    // Signed in already? "Sign in" becomes "CostAdvisor" (today's trick). Skipped offline.
    if (CA.API) {
      (async () => {
        const o = { credentials: 'include' };
        try {
          let r = await fetch(CA.API + '/auth/me', o);
          if (!r.ok) { const rr = await fetch(CA.API + '/auth/refresh', Object.assign({ method: 'POST' }, o)); if (rr.ok) r = await fetch(CA.API + '/auth/me', o); }
          if (!r.ok) return;
          const back = document.referrer.startsWith(CA.APP) ? document.referrer : CA.APP + '/dashboard';
          CA.$$('.js-signin').forEach((a) => { a.textContent = 'CostAdvisor'; a.href = back; });
        } catch (e) { /* logged out */ }
      })();
    }

    // Nav: scroll state, burger, workflow dropdown.
    const nav = CA.$('.nav');
    const onScroll = () => nav && nav.classList.toggle('scrolled', scrollY > 40);
    addEventListener('scroll', onScroll, { passive: true }); onScroll();
    const burger = CA.$('#lpBurger'), menu = CA.$('#lpMobileMenu');
    if (burger && menu) {
      burger.addEventListener('click', () => {
        const open = menu.classList.toggle('open');
        burger.setAttribute('aria-expanded', String(open));
        burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      });
      CA.$$('a', menu).forEach((a) => a.addEventListener('click', () => { menu.classList.remove('open'); burger.setAttribute('aria-expanded', 'false'); }));
    }
    const dd = CA.$('#navWorkflow');
    if (dd) {
      const btn = CA.$('.nav-dd-btn', dd);
      const set = (open) => { dd.classList.toggle('open', open); btn.setAttribute('aria-expanded', String(open)); };
      btn.addEventListener('click', () => set(!dd.classList.contains('open')));
      dd.addEventListener('mouseenter', () => matchMedia('(hover:hover)').matches && set(true));
      dd.addEventListener('mouseleave', () => matchMedia('(hover:hover)').matches && set(false));
      dd.addEventListener('focusout', (e) => { if (!dd.contains(e.relatedTarget)) set(false); });
      CA.$$('a', dd).forEach((a) => a.addEventListener('click', () => set(false)));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && dd.classList.contains('open')) { set(false); btn.focus(); } });
    }

    // CTA clicks (all placements) and leave-behind events.
    document.addEventListener('click', (e) => {
      const a = e.target.closest('[data-cta]');
      if (a) CA.track('cta_click', { location: a.dataset.cta });
      const ev = e.target.closest('[data-ev]');
      if (ev) { const st = ev.closest('.stage'); CA.track(ev.dataset.ev, { stage: st ? st.dataset.stage : 'book' }); }
    });

    // Ticker pause (plan B1: a pause button; the duplicated half is aria-hidden).
    const tp = CA.$('#tickerPause');
    if (tp) tp.addEventListener('click', () => {
      const w = tp.closest('.ticker-wrap');
      const paused = w.classList.toggle('paused');
      tp.setAttribute('aria-pressed', String(paused));
      tp.setAttribute('aria-label', paused ? 'Play the ticker' : 'Pause the ticker');
      tp.innerHTML = `<svg class="ic ic-sm"><use href="#ic-${paused ? 'play' : 'pause'}"/></svg>`;
    });

    // Side columns fold into "Real data and proof" when they stack under the frame (< 1100 px),
    // and the call script starts closed on phones (plan B9). Open everywhere without JS.
    const mqStack = matchMedia('(max-width: 1099px)'), mqPhone = matchMedia('(max-width: 759px)');
    const fold = () => {
      CA.$$('details.side-more').forEach((d) => { d.open = !mqStack.matches; });
      CA.$$('details.m-fold').forEach((d) => { d.open = !mqPhone.matches; });
      const sc = CA.$('.script-card');
      if (sc) sc.open = !mqPhone.matches;
      if (CA.reanchor) CA.reanchor();
    };
    fold();
    (mqStack.addEventListener ? mqStack.addEventListener('change', fold) : mqStack.addListener(fold));
    (mqPhone.addEventListener ? mqPhone.addEventListener('change', fold) : mqPhone.addListener(fold));

    // Scroll reveal: content is visible without JS; hidden only once JS is here.
    document.body.classList.add('reveal-ready');
    const ro = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('visible'); ro.unobserve(e.target); } }), { threshold: 0.08, rootMargin: '0px 0px -24px 0px' });
    CA.$$('.reveal').forEach((el) => ro.observe(el));

    // Mouse tilt on the index cards (desktop, motion allowed).
    if (!reduced && matchMedia('(hover:hover)').matches) {
      CA.$$('.tilt-card').forEach((card) => {
        card.addEventListener('mousemove', (e) => {
          const r = card.getBoundingClientRect(), x = e.clientX - r.left - r.width / 2, y = e.clientY - r.top - r.height / 2;
          card.style.transition = 'transform .08s ease-out';
          card.style.transform = `perspective(600px) rotateX(${-(y / r.height) * 8}deg) rotateY(${(x / r.width) * 8}deg) translateZ(4px)`;
        });
        card.addEventListener('mouseleave', () => { card.style.transition = 'transform .5s cubic-bezier(.23,1,.32,1)'; card.style.transform = ''; });
      });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
  window.addEventListener('error', (e) => CA.track('widget_error', { widget: String(e.filename || '').split('/').pop() }));
})();
