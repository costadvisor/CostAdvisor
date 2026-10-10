/* CostAdvisor landing — the journey shell: rail, baton, progress, hand-offs, entry chips
 * (path mode), stage_view events and the mobile sticky bar. Without JS the rail is a plain
 * sticky list of links and every number is already in the HTML. */
(function () {
  'use strict';
  const CA = window.CA;
  const NAMES = ['Discover', 'Model', 'Watch', 'Anticipate', 'Negotiate', 'Plan', 'Act'];

  CA.ready.then(() => {
    const story = CA.story;
    const baton = story ? story.baton : null;
    const rail = CA.$('#rail'), list = CA.$('#railList'), batonEl = CA.$('#railBaton'), batonVal = CA.$('#railBatonVal');
    const stages = CA.$$('.stage');
    const nodes = CA.$$('.rail-node');
    const journey = CA.$('#journey');
    const seen = new Set();
    let active = 0;
    const bar = CA.$('#stickyBar'), sbStage = CA.$('#sbStage'), sbTxt = CA.$('#sbStageTxt'), sbMenu = CA.$('#sbMenu');
    const book = CA.$('#book');
    let bookVisible = false;

    function moveBaton() {
      if (!batonEl || !active) return;
      const li = nodes[active - 1].parentElement;
      CA.$$('li', list).forEach((x) => x.classList.toggle('has-baton', x === li));
      // From the base row height, not offsetTop: the previous node's padding may still be animating.
      const rowH = nodes[0].offsetHeight;
      const y = list.offsetTop + nodes[0].parentElement.offsetTop + (active - 1) * rowH + rowH + 2;
      batonEl.style.transform = `translateY(${y}px)`;
    }

    function setActive(n) {
      if (n === active) return;
      active = n;
      nodes.forEach((a, i) => {
        if (i + 1 === n) a.setAttribute('aria-current', 'step'); else a.removeAttribute('aria-current');
        a.classList.toggle('done', i + 1 < n);
      });
      if (batonVal && baton) batonVal.textContent = baton[n - 1];
      moveBaton();
      if (rail) { rail.classList.toggle('show-cta', n >= 3); rail.classList.add('has-active'); }
      if (!seen.has(n)) { seen.add(n); CA.track('stage_view', { stage: n }); CA.lead.stage(n); }
      updateSticky();
    }

    // Active stage = the one crossing the middle of the viewport.
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((es) => {
        es.forEach((e) => { if (e.isIntersecting) setActive(Number(e.target.dataset.stage)); });
      }, { rootMargin: '-45% 0px -50% 0px' });
      stages.forEach((s) => io.observe(s));
    }

    // Progress line fills with scroll through the journey.
    let ticking = false;
    function progress() {
      ticking = false;
      if (!journey || !list) return;
      const r = journey.getBoundingClientRect();
      const p = Math.min(1, Math.max(0, (innerHeight * 0.5 - r.top) / r.height));
      list.style.setProperty('--p', p.toFixed(3));
      updateSticky();
    }
    addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(progress); } }, { passive: true });
    addEventListener('resize', () => { moveBaton(); progress(); });
    progress();

    // Rail clicks.
    nodes.forEach((a) => a.addEventListener('click', () => CA.track('rail_click', { stage: Number(a.dataset.stage) })));

    // Hand-off chips pulse once as they arrive.
    if ('IntersectionObserver' in window && !CA.reduced) {
      const ho = new IntersectionObserver((es) => es.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add('pulse'); ho.unobserve(e.target); }
      }), { rootMargin: '-30% 0px -30% 0px' });
      CA.$$('.handoff').forEach((h) => ho.observe(h));
    }

    // Entry chips: path mode (plan B3). Navigation still works without JS (they are anchors).
    function setPath(path, chip) {
      if (!rail) return;
      const txt = CA.$('#railPathTxt'), box = CA.$('#railPath');
      CA.$$('.entry-chip').forEach((c) => c.classList.toggle('active', c.dataset.chip === chip));
      if (!path) {
        rail.classList.remove('path-mode');
        nodes.forEach((a) => a.classList.remove('on-path'));
        if (box) box.hidden = true;
        CA.store.set('ca_path', null);
        return;
      }
      rail.classList.add('path-mode');
      nodes.forEach((a) => a.classList.toggle('on-path', path.includes(Number(a.dataset.stage))));
      if (box && txt) { txt.textContent = 'Your shortcut: ' + path.map((n) => '0' + n).join(' → '); box.hidden = false; }
      CA.store.set('ca_path', { path, chip });
    }
    CA.$$('.entry-chip').forEach((c) => c.addEventListener('click', () => {
      const path = c.dataset.path.split(',').map(Number);
      setPath(path, c.dataset.chip);
      CA.lead.set('chip', c.dataset.chip);
      CA.track('entry_chip', { chip: c.dataset.chip });
    }));
    const clear = CA.$('#railPathClear');
    if (clear) clear.addEventListener('click', () => setPath(null));
    const saved = CA.store.get('ca_path');
    if (saved && saved.path) setPath(saved.path, saved.chip);

    // "Add to portfolio" in the Stage 01 frame: carry the reader to Stage 02.
    const add = CA.$('.pc-add');
    if (add) add.addEventListener('click', () => { const t = CA.$('.demo-total-panel'); if (t && !CA.reduced) { t.classList.remove('pulse'); void t.offsetWidth; t.style.animation = 'pulseOnce .9s ease-out 1 .6s'; } });

    // ── Mobile sticky bar (under 760 px) ──
    if (book && 'IntersectionObserver' in window) {
      new IntersectionObserver((es) => { bookVisible = es[0].isIntersecting; updateSticky(); }, { threshold: 0.05 }).observe(book);
    }
    function updateSticky() {
      if (!bar) return;
      const show = scrollY > innerHeight * 0.7 && !bookVisible;
      bar.classList.toggle('show', show);
      if (!show && sbMenu) { sbMenu.hidden = true; sbStage && sbStage.setAttribute('aria-expanded', 'false'); }
      let inJourney = false;
      if (journey) { const r = journey.getBoundingClientRect(); inJourney = r.top < innerHeight * 0.5 && r.bottom > innerHeight * 0.5; }
      if (sbStage) sbStage.hidden = !(inJourney && active);
      if (sbTxt && active) {
        const short = story ? story.baton[active - 1] : '';
        sbTxt.textContent = `0${active}/07 · ${NAMES[active - 1]}${short ? ' · ' + short : ''}`;
      }
      if (sbMenu) CA.$$('a', sbMenu).forEach((a, i) => a.classList.toggle('on', i + 1 === active));
    }
    if (sbStage && sbMenu) {
      sbStage.addEventListener('click', () => {
        const open = sbMenu.hidden;
        sbMenu.hidden = !open;
        sbStage.setAttribute('aria-expanded', String(open));
      });
      CA.$$('a', sbMenu).forEach((a) => a.addEventListener('click', () => { sbMenu.hidden = true; sbStage.setAttribute('aria-expanded', 'false'); }));
    }
    updateSticky();
  });
})();
