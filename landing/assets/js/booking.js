/* CostAdvisor landing — the booking component (modal here, inline on /demo) and the access modal.
 * Plan D2: Step 0 about you → day (local time, UTC beside) → time → details → POST /api/demos,
 * with a request step when no day is open. Plan D4: leadContext travels with the request.
 *
 * Two site.json flags (per host) decide how far the flow goes, so the primary CTA never dead-ends:
 *   demo.calendar       false until an active DemoHost exists (today /api/demos/available-dates
 *                       returns [] on dev and www): skip the calendar, go straight to the request.
 *   demo.slotless_post  false until POST /api/demos accepts {slotless, preferred_times} and the lead
 *                       fields (plan E2 #5; today DemoRequestCreate requires a date and a slot and
 *                       answers 422): the request step becomes a pre-filled e-mail instead.
 * Any failed POST (422, 5xx, network) falls back to the same pre-filled e-mail and fires
 * demo_submit_err. PROTOTYPE: on localhost (CA.offline) nothing is sent; the flow says so. */
(function () {
  'use strict';
  const CA = window.CA;
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const cfg = () => CA.demoCfg || { calendar: false, slotless_post: false, email: 'hello@costadvisor.org' };
  let lastFocus = null, openId = null;

  function focusables(root) {
    return CA.$$('a[href],button:not([disabled]),input:not([type=hidden]),select,textarea,[tabindex]:not([tabindex="-1"])', root)
      .filter((el) => el.offsetParent !== null && !el.closest('[hidden]') && !el.closest('.hp'));
  }
  function openModal(id) {
    lastFocus = document.activeElement; openId = id;
    const m = CA.$('#' + id);
    m.classList.add('open');
    document.body.style.overflow = 'hidden';
    setTimeout(() => { const f = focusables(CA.$('.modal', m)).filter((x) => !x.classList.contains('modal-close'))[0]; f && f.focus(); }, 60);
  }
  function closeModal() {
    if (!openId) return;
    CA.$('#' + openId).classList.remove('open');
    document.body.style.overflow = '';
    openId = null;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  CA.$$('.modal-backdrop').forEach((b) => b.addEventListener('click', (e) => { if (e.target === b) closeModal(); }));
  CA.$$('[data-close]').forEach((b) => b.addEventListener('click', closeModal));
  document.addEventListener('keydown', (e) => {
    if (!openId) return;
    if (e.key === 'Escape') { closeModal(); return; }
    if (e.key === 'Tab') {
      const list = focusables(CA.$('#' + openId + ' .modal'));
      if (!list.length) return;
      const first = list[0], last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  const utm = () => ['utm_source', 'utm_medium', 'utm_campaign'].reduce((o, k) => { const v = CA.params.get(k); if (v) o[k] = v.slice(0, 80); return o; }, {});
  const context = () => {
    const l = CA.lead.get();
    const c = { price: l.price, volume: l.volume, chip: l.chip, stages: l.stages };
    const s = JSON.stringify(c);
    return s.length <= 2048 ? c : {};
  };
  async function post(path, body) {
    if (CA.offline) { await new Promise((r) => setTimeout(r, 450)); return { ok: true, status: 200, json: async () => ({ status: 'submitted' }), offline: true }; }
    return fetch(CA.API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  // ═══ Demo booking ═══ (a modal on the homepage, inline on /demo, absent elsewhere)
  const dm = CA.$('#lpDemoModal');
  const inline = !!(dm && dm.classList.contains('inline'));
  let source = '';
  if (dm) {
  let year, month, selDate = null, selSlot = null;
  const avail = {};
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

  function show(step) {
    CA.$$('.dm-step', dm).forEach((s) => { s.hidden = s.dataset.step !== String(step); });
    const n = { 0: 0, 1: 1, nos: 1, 2: 2, 3: 3, ok: 4, err: 4 }[step];
    applyMode();
    CA.$$('.demo-step-dot', dm).forEach((d, i) => { d.classList.toggle('active', i === n); d.classList.toggle('done', i < n); });
    const cur = CA.$(`.dm-step[data-step="${step}"]`, dm);
    const live = CA.$('#dmLive');
    const two = !cfg().calendar;
    if (live) live.textContent = { 0: two ? 'Step 1 of 2: about you' : 'Step 1 of 4: about you', 1: 'Step 2 of 4: choose a day', nos: two ? 'Step 2 of 2: your request' : 'No open slots. Leave your details instead.', 2: 'Step 3 of 4: choose a time', 3: 'Step 4 of 4: your details', ok: 'Request received', err: 'Your request did not go through. You can send it by e-mail instead.' }[step];
    if (inline && step === 0 && !show.used) { show.used = true; return; }
    setTimeout(() => { const f = focusables(cur)[0]; if (f) f.focus(); else if (cur) cur.focus(); }, 40);
  }

  // Labels and mode follow the host's flags (see the header comment).
  function applyMode() {
    const c = cfg(), box = CA.$('.modal-demo', dm) || dm;
    box.dataset.cal = c.calendar ? 'on' : 'off';
    box.dataset.mode = c.slotless_post ? 'post' : 'mail';
    const go = CA.$('#dmGo1');
    if (go) go.textContent = c.calendar ? 'Choose a day' : 'Continue';
    const title = CA.$('#lpDemoTitle');
    if (title && !inline) title.textContent = c.calendar ? 'Book a 30-minute demo' : 'Request a 30-minute demo';
    CA.$$('.js-demo-label').forEach((el) => { el.textContent = c.calendar ? 'Pick a time' : 'Request a demo'; });
  }
  // A pre-filled e-mail: product, role, industry and the times the visitor typed (plan D4 context).
  function mailtoHref() {
    const c = cfg();
    const v = (sel) => { const el = CA.$(sel); return el ? el.value.trim() : ''; };
    const product = v('#dmProduct'), role = v('#dmRole'), industry = v('#dmIndustry'), times = v('#nsTimes');
    const body = [
      'Hello,', '', `I would like a ${CA.site && CA.site.demo_minutes || 30}-minute CostAdvisor demo.`, '',
      `Product or category: ${product || '(not given)'}`,
      `My role: ${role || '(not given)'}`,
      `My industry: ${industry || '(not given)'}`,
      `Two times that suit me: ${times || '(please suggest)'}`, '',
      'Name:', 'Company:', '', `(sent from costadvisor.org, ${source || 'page'})`,
    ].join('\n');
    const subject = 'Demo request' + (product ? ': ' + product : '');
    return `mailto:${c.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }
  function refreshMailto() { CA.$$('.js-mailto', dm).forEach((a) => { a.href = mailtoHref(); }); }
  ['#dmProduct', '#dmRole', '#dmIndustry', '#nsTimes'].forEach((sel) => { const el = CA.$(sel, dm); if (el) el.addEventListener('input', refreshMailto); if (el) el.addEventListener('change', refreshMailto); });
  CA.$$('.js-mailto', dm).forEach((a) => a.addEventListener('click', () => { refreshMailto(); CA.track('cta_click', { location: 'demo-mailto' }); }));
  function showRequest(reason) {
    const c = cfg();
    const intro = CA.$('#nsIntro');
    if (intro) intro.innerHTML = reason === 'noslots'
      ? "<b>No open slots right now.</b> Tell us two times that suit you; we'll reply by e-mail with a meeting link."
      : "<b>Tell us two times that suit you.</b> We'll reply by e-mail with a meeting link.";
    refreshMailto();
    show('nos');
    // The slotless POST is not live yet on this host: the request goes by e-mail (counted, plan D5).
    if (!c.slotless_post) CA.track('demo_submit_err', { source, reason: 'slotless_off' });
  }

  function openDemo(src, product) {
    if (inline) { dm.scrollIntoView({ behavior: CA.reduced ? 'auto' : 'smooth', block: 'start' }); if (product) CA.$('#dmProduct').value = product.slice(0, 120); show(0); return; }
    source = src || '';
    CA.lead.set('source', source);
    const l = CA.lead.get();
    const p = product || CA.params.get('product') || l.product || '';
    const pIn = CA.$('#dmProduct');
    pIn.value = p.slice(0, 120);
    CA.$('#dmFromVisit').hidden = !(p && !product && !CA.params.get('product') && l.product);
    if (CA.params.get('role')) CA.$('#dmRole').value = CA.params.get('role');
    if (CA.params.get('industry')) CA.$('#dmIndustry').value = CA.params.get('industry');
    selDate = null; selSlot = null;
    show(0);
    openModal('lpDemoModal');
    CA.track('demo_open', { source });
  }
  CA.openDemo = openDemo;

  async function fetchMonth(y, m) {
    const key = `${y}-${m}`;
    if (avail[key] !== undefined) return avail[key];
    if (CA.offline) return (avail[key] = new Set());
    try {
      const r = await fetch(`${CA.API}/api/demos/available-dates?year=${y}&month=${m + 1}`);
      avail[key] = new Set(r.ok ? await r.json() : []);
    } catch (e) { avail[key] = new Set(); }
    return avail[key];
  }

  async function goDays() {
    const now = new Date();
    year = now.getFullYear(); month = now.getMonth();
    const a = await fetchMonth(year, month);
    const ny = month === 11 ? year + 1 : year, nm = (month + 1) % 12;
    const b = await fetchMonth(ny, nm);
    if (!a.size && !b.size) { CA.track('demo_no_slots', { source }); showRequest('noslots'); return; }
    if (!a.size) { year = ny; month = nm; }
    renderCal();
    show(1);
  }

  function renderCal() {
    const label = CA.$('#lpCalMonthLabel');
    label.textContent = new Date(year, month, 1).toLocaleString('en-GB', { month: 'long', year: 'numeric' });
    const cal = CA.$('#lpCal');
    cal.innerHTML = '';
    ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].forEach((d) => { const h = document.createElement('div'); h.className = 'lp-cal-dow'; h.textContent = d; h.setAttribute('aria-hidden', 'true'); cal.appendChild(h); });
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const startDow = (new Date(year, month, 1).getDay() + 6) % 7;
    for (let i = 0; i < startDow; i++) cal.appendChild(document.createElement('div'));
    const set = avail[`${year}-${month}`] || new Set();
    const days = new Date(year, month + 1, 0).getDate();
    for (let d = 1; d <= days; d++) {
      const dt = new Date(year, month, d);
      const ds = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      if (dt < today) { cal.appendChild(document.createElement('div')); continue; }
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'lp-cal-day'; b.textContent = d;
      const full = dt.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
      if (!set.has(ds)) { b.disabled = true; b.setAttribute('aria-label', full + ', no open slots'); }
      else { b.setAttribute('aria-label', full); b.addEventListener('click', () => pickDate(ds)); }
      b.setAttribute('aria-pressed', String(ds === selDate));
      if (dt.getTime() === today.getTime()) b.classList.add('today');
      cal.appendChild(b);
    }
    CA.$('#dmTz').textContent = `Times are shown in your time zone (${tz}), with UTC beside.`;
  }
  CA.$('#lpCalPrev').addEventListener('click', async () => {
    const now = new Date();
    if (year < now.getFullYear() || (year === now.getFullYear() && month <= now.getMonth())) return;
    month--; if (month < 0) { month = 11; year--; }
    await fetchMonth(year, month); renderCal();
  });
  CA.$('#lpCalNext').addEventListener('click', async () => {
    month++; if (month > 11) { month = 0; year++; }
    await fetchMonth(year, month); renderCal();
  });

  async function pickDate(ds) {
    selDate = ds; renderCal();
    let slots = [];
    try { const r = await fetch(`${CA.API}/api/demos/available-slots?date=${ds}`); slots = r.ok ? await r.json() : []; } catch (e) { slots = []; }
    if (!Array.isArray(slots) || !slots.length) { CA.track('demo_no_slots', { source }); showRequest('noslots'); return; }
    const sub = CA.$('#lpDemoSlotSub');
    sub.textContent = new Date(ds + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
    const grid = CA.$('#lpSlotGrid'); grid.innerHTML = '';
    const local = (t) => new Date(`${ds}T${t}:00Z`).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    slots.forEach((s) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'lp-slot-btn'; b.setAttribute('aria-pressed', 'false');
      b.innerHTML = `${local(s.start_time)}–${local(s.end_time)}<small>${s.start_time}–${s.end_time} UTC</small>`;
      b.addEventListener('click', () => {
        selSlot = { start: s.start_time, end: s.end_time };
        CA.$$('.lp-slot-btn', grid).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        CA.$('#lpDemoDetailsSub').textContent = `${sub.textContent} · ${local(s.start_time)} your time (${s.start_time} UTC) · 30 minutes`;
        setTimeout(() => show(3), 150);
      });
      grid.appendChild(b);
    });
    show(2);
  }

  CA.$$('[data-go]', dm).forEach((b) => b.addEventListener('click', () => {
    const to = b.dataset.go;
    if (to === '1') { CA.lead.set('product', CA.$('#dmProduct').value.trim().slice(0, 120)); if (cfg().calendar) goDays(); else showRequest('request'); }
    else show(to);
  }));

  function fields() {
    return {
      role: CA.$('#dmRole').value || undefined,
      industry: CA.$('#dmIndustry').value.trim().slice(0, 80) || undefined,
      product: CA.$('#dmProduct').value.trim().slice(0, 120) || undefined,
      context: context(), source: source || 'unknown', visitor_timezone: tz,
      ...utm(),
    };
  }
  async function finish(res) {
    const proto = CA.$('#dmProto');
    if (res.ok) { proto.hidden = !res.offline; show('ok'); CA.track('demo_submit_ok', { source }); return; }
    CA.track('demo_submit_err', { source, reason: String(res.status) });
    // 409: a request already exists. Anything else (422 from the old schema, 5xx): never a dead end,
    // the same request goes by e-mail.
    CA.$('#lpDemoErrorMsg').textContent = res.status === 409
      ? 'A request already exists for this e-mail. Check your inbox, or send us a note.'
      : 'Your request did not go through. Send it by e-mail instead: your product, role and times are filled in.';
    refreshMailto();
    show('err');
  }

  CA.$('#dmDetailsForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.website && f.website.value) return; // honeypot
    const err = CA.$('#lpDemoFormError');
    const name = CA.$('#lpDemoName').value.trim(), email = CA.$('#lpDemoEmail').value.trim(), company = CA.$('#lpDemoCompany').value.trim(), phone = CA.$('#lpDemoPhone').value.trim();
    err.textContent = '';
    if (!name || !email || !company) { err.textContent = 'Please fill in your name, work e-mail and company.'; return; }
    if (!EMAIL_RE.test(email)) { err.textContent = 'Please enter a valid work e-mail.'; CA.$('#lpDemoEmail').setAttribute('aria-invalid', 'true'); return; }
    if (!selDate || !selSlot) { err.textContent = 'Please go back and choose a day and a time.'; return; }
    const btn = CA.$('#lpDemoSubmitBtn'); btn.disabled = true; btn.innerHTML = '<span class="btn-spin"></span>Sending…';
    try {
      const res = await post('/api/demos', { name, email, phone: phone || undefined, company, requested_date: selDate, requested_start: selSlot.start, requested_end: selSlot.end, ...fields() });
      await finish(res);
    } catch (x) { CA.track('demo_submit_err', { source, reason: 'network' }); CA.$('#lpDemoErrorMsg').textContent = 'Network error. Send your request by e-mail instead.'; refreshMailto(); show('err'); }
    btn.disabled = false; btn.textContent = 'Request this time';
  });

  CA.$('#dmNoSlotForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.website && f.website.value) return;
    if (!cfg().slotless_post) { location.href = mailtoHref(); return; }
    const err = CA.$('#nsError');
    const name = CA.$('#nsName').value.trim(), email = CA.$('#nsEmail').value.trim(), company = CA.$('#nsCompany').value.trim();
    err.textContent = '';
    if (!name || !email || !company) { err.textContent = 'Please fill in your name, work e-mail and company.'; return; }
    if (!EMAIL_RE.test(email)) { err.textContent = 'Please enter a valid work e-mail.'; CA.$('#nsEmail').setAttribute('aria-invalid', 'true'); return; }
    try {
      const res = await post('/api/demos', { name, email, company, slotless: true, preferred_times: CA.$('#nsTimes').value.trim().slice(0, 300), ...fields() });
      await finish(res);
    } catch (x) { CA.track('demo_submit_err', { source, reason: 'network' }); CA.$('#lpDemoErrorMsg').textContent = 'Network error. Send your request by e-mail instead.'; refreshMailto(); show('err'); }
  });

  CA.ready.then(applyMode);
  if (inline) {
    // /demo?product=&role=&industry=&from= pre-fill the first step (plan B20).
    const p = CA.params.get('product') || CA.lead.get().product || '';
    CA.$('#dmProduct').value = p.slice(0, 120);
    CA.$('#dmFromVisit').hidden = !(p && !CA.params.get('product'));
    if (CA.params.get('role')) CA.$('#dmRole').value = CA.params.get('role');
    if (CA.params.get('industry')) CA.$('#dmIndustry').value = CA.params.get('industry');
    source = CA.params.get('from') || 'demo-page';
    CA.lead.set('source', source);
    show(0);
    CA.track('demo_open', { source });
  }
  } // end if (dm)

  // ═══ Access request ═══
  const am = CA.$('#lpAccessModal');
  function openAccess(src) {
    if (!am) { location.href = (document.documentElement.dataset.base || '') + '#book'; return; }
    source = src || '';
    CA.$('#lpAccessForm').hidden = false;
    CA.$('#lpModalSuccess').classList.remove('show');
    CA.$('#lpModalError').textContent = '';
    openModal('lpAccessModal');
  }
  if (am) CA.$('#lpAccessForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.website && f.website.value) return;
    const email = CA.$('#lpModalEmail').value.trim(), name = CA.$('#lpModalName').value.trim(), company = CA.$('#lpModalCompany').value.trim();
    const err = CA.$('#lpModalError');
    if (!EMAIL_RE.test(email)) { err.textContent = 'Please enter a valid work e-mail.'; CA.$('#lpModalEmail').setAttribute('aria-invalid', 'true'); CA.$('#lpModalEmail').focus(); return; }
    err.textContent = '';
    const btn = CA.$('#lpModalSubmit'); btn.disabled = true; btn.innerHTML = '<span class="btn-spin"></span>Sending…';
    try {
      const res = await post('/api/access-requests', { email, name: name || undefined, company: company || undefined, product: CA.lead.get().product || undefined, source: source || 'unknown', context: context(), ...utm() });
      const data = await res.json().catch(() => ({}));
      if (data.status === 'accepted' || data.status === 'exists') { err.textContent = 'You may already have access. Try signing in.'; CA.track('access_submit_ok', { source }); }
      else if (!res.ok) { err.textContent = 'Something went wrong. Try again, or e-mail ' + cfg().email + '.'; CA.track('access_submit_err', { source }); }
      else {
        CA.$('#lpAccessForm').hidden = true;
        CA.$('#lpModalSuccessMsg').textContent = res.offline ? "We'll reply by e-mail. (Prototype: nothing was sent.)" : "We'll reply by e-mail. Keep an eye on your inbox.";
        CA.$('#lpModalSuccess').classList.add('show');
        CA.$('#lpModalSuccess').focus();
        CA.track('access_submit_ok', { source });
      }
    } catch (x) { err.textContent = 'Network error. Try again, or e-mail ' + cfg().email + '.'; CA.track('access_submit_err', { source }); }
    btn.disabled = false; btn.textContent = 'Send request';
  });

  // ═══ Triggers ═══
  document.addEventListener('click', (e) => {
    const d = e.target.closest('.js-demo');
    if (d && dm && !(e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1)) { e.preventDefault(); CA.openDemo(d.dataset.cta); return; }
    const a = e.target.closest('.js-access');
    if (a) { e.preventDefault(); openAccess(a.dataset.cta); }
  });
  const book = CA.$('#bookForm');
  if (book) {
    const l = CA.lead.get();
    const pIn = CA.$('#bookProduct');
    if (pIn && l.product) { pIn.value = l.product; CA.$('#bookFromVisit').hidden = false; }
    // Carry what the visitor typed in the demo step or chose on the page (plan D4).
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((es) => {
        if (!es[0].isIntersecting) return;
        const p = CA.lead.get().product;
        if (pIn && !pIn.value && p) { pIn.value = p; CA.$('#bookFromVisit').hidden = false; }
      });
      io.observe(book);
    }
    book.addEventListener('submit', (e) => { e.preventDefault(); const v = pIn.value.trim(); if (v) CA.lead.set('product', v.slice(0, 120)); if (CA.openDemo) CA.openDemo('book', v); else book.submit(); });
  }
})();
