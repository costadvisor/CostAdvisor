/* CostAdvisor landing — small stage interactions: Stage 03 alert toast, Stage 06 objective chips
 * and bubble tooltips, Stage 07 timeline/table toggle. */
(function () {
  'use strict';
  const CA = window.CA;

  CA.ready.then(() => {
    // Stage 03: the alert slides in once (inline and static under reduced motion / mobile).
    const toast = CA.$('#watchToast');
    if (toast) {
      if (CA.reduced || !('IntersectionObserver' in window)) toast.classList.add('in');
      else {
        const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { toast.classList.add('in'); io.disconnect(); } }, { rootMargin: '0px 0px -15% 0px' });
        io.observe(toast);
      }
    }

    // Stage 06: objectives ring the opportunities that serve them (as in the app).
    const chips = CA.$$('#objChips .obj');
    const bubbles = CA.$$('#ieChart .ie-b');
    const count = CA.$('#objCount');
    function ring() {
      const sel = new Set(chips.filter((c) => c.getAttribute('aria-pressed') === 'true').map((c) => c.dataset.obj));
      let n = 0;
      bubbles.forEach((b) => {
        const on = (b.dataset.obj || '').split('|').some((o) => sel.has(o));
        b.classList.toggle('on', on);
        if (on) n++;
      });
      // two renderings (desktop and phone) carry the same 17 bubbles
      const per = bubbles.length / 2;
      if (count) count.textContent = sel.size ? `${n / 2} of ${per} ringed` : 'Pick an objective to ring its opportunities';
    }
    chips.forEach((c) => c.addEventListener('click', () => {
      c.setAttribute('aria-pressed', String(c.getAttribute('aria-pressed') !== 'true'));
      ring();
    }));
    ring();

    // Bubble tooltip: a generic lever type only (no lever titles, no playbook text).
    const wrap = CA.$('#ieChart');
    if (wrap) {
      const tip = document.createElement('div');
      tip.className = 'ie-tip'; tip.hidden = true; tip.setAttribute('role', 'tooltip');
      wrap.appendChild(tip);
      const show = (b) => {
        const t = b.querySelector('title');
        const r = b.getBoundingClientRect(), w = wrap.getBoundingClientRect();
        tip.textContent = t ? t.textContent : '';
        tip.style.left = (r.left - w.left + r.width / 2) + 'px';
        tip.style.top = (r.top - w.top) + 'px';
        tip.hidden = false;
      };
      bubbles.forEach((b) => {
        b.addEventListener('mouseenter', () => show(b));
        b.addEventListener('focus', () => show(b));
        b.addEventListener('mouseleave', () => { tip.hidden = true; });
        b.addEventListener('blur', () => { tip.hidden = true; });
      });
    }

    // Stage 07: Timeline / Table toggle (aria-pressed).
    const tgs = CA.$$('.act-toggle .tg');
    tgs.forEach((t) => t.addEventListener('click', () => {
      tgs.forEach((x) => x.setAttribute('aria-pressed', String(x === t)));
      CA.$$('.act-view').forEach((v) => { v.hidden = v.dataset.view !== t.dataset.view; });
    }));
  });
})();
