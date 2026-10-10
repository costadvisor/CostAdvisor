/* CostAdvisor landing — Stage 02 calculator (today's slider + donut demo, maths rewritten).
 *   each line   = start price × weight × index ÷ 100
 *   should-cost = Σ lines + freight & delivery (fixed, set by the Incoterm) + margin (fixed)
 *   floor       = should-cost − margin
 *   gap         = your price − should-cost
 *   annual gap  = gap (rounded to the euro, as shown) × volume
 * Defaults reproduce the story: €291 / €252 / €48 / €105,600. One currency (EUR). */
(function () {
  'use strict';
  const CA = window.CA;

  CA.ready.then(() => {
    const st = CA.story;
    const root = CA.$('#calc');
    if (!st || !root) return;
    const lines = st.recipe;
    const defaults = Object.fromEntries(lines.map((l) => [l.key, l.level]));
    const sliders = Object.fromEntries(lines.map((l) => [l.key, CA.$('#sl-' + l.key)]));
    const priceIn = CA.$('#calcPrice'), volIn = CA.$('#calcVol');
    const out = {
      should: CA.$('#calcShould'), floor: CA.$('#calcFloor'), gap: CA.$('#calcGap'), annual: CA.$('#calcAnnual'),
      verdict: CA.$('#calcVerdict'), donut: CA.$('#calcDonutVal'), legend: CA.$('#calcLegend'), formula: CA.$('#calcFormula'),
      cap: CA.$('#calcCap'), back: CA.$('#calcBack'), live: CA.$('#calcLive'),
    };
    let chart = null, liveTimer = null;
    const tracked = new Set();

    function compute() {
      const lv = Object.fromEntries(lines.map((l) => [l.key, Number(sliders[l.key].value)]));
      const parts = lines.map((l) => st.start.price * l.weight * lv[l.key] / 100);
      const should = parts.reduce((a, b) => a + b, 0) + st.freight.eur + st.margin.eur;
      const price = Math.max(0, Number(priceIn.value) || 0);
      const vol = Math.max(0, Number(volIn.value) || 0);
      const shouldR = Math.round(should), floorR = Math.round(should - st.margin.eur);
      const gapR = Math.round(price - should);
      const pct = should ? ((price - should) / should) * 100 : 0;
      return { lv, parts: parts.concat([st.freight.eur, st.margin.eur]), should, shouldR, floorR, price, vol, gapR, annual: gapR * vol, pct };
    }

    const money = (n) => CA.eur(Math.abs(n));
    function render(r) {
      lines.forEach((l) => {
        const v = r.lv[l.key];
        CA.$('#sl-' + l.key + '-v').textContent = v;
        sliders[l.key].setAttribute('aria-valuetext', `${l.label} index ${v}, base 100 = January 2023`);
        const pct = ((v - sliders[l.key].min) / (sliders[l.key].max - sliders[l.key].min)) * 100;
        sliders[l.key].style.background = `linear-gradient(to right,var(--blue) ${pct}%,var(--surface2) ${pct}%)`;
      });
      const band = st.verdict_band_pct;
      const sev = r.pct > band ? 'over' : r.pct < -band ? 'under' : 'fair';
      const col = sev === 'over' ? 'var(--red)' : sev === 'under' ? 'var(--blue)' : 'var(--green)';
      out.should.textContent = CA.eur(r.shouldR);
      out.donut.textContent = CA.eur(r.shouldR);
      out.floor.textContent = CA.eur(r.floorR);
      out.gap.textContent = (r.gapR >= 0 ? '+' : '−') + money(r.gapR);
      out.gap.style.color = r.gapR > 0 ? 'var(--red)' : 'var(--green)';
      out.annual.textContent = (r.annual < 0 ? '−' : '') + money(r.annual);
      out.annual.style.color = r.annual > 0 ? 'var(--red)' : 'var(--green)';
      out.should.style.color = col; out.donut.style.color = col;
      out.verdict.className = 'demo-verdict ' + sev;
      out.verdict.textContent = sev === 'over' ? '▲ Above should-cost — room to negotiate'
        : sev === 'under' ? '▼ Below should-cost — check the spec' : '✓ In line with the market';
      const gapTxt = (r.gapR >= 0 ? '' : '−') + money(r.gapR) + '/t';
      const gapEur = gapTxt.replace('/t', '');
      const row = (left, val) => `<span class="fr"><span class="fl">${left}</span>${val ? `<b>= ${val}</b>` : ''}</span>`;
      out.formula.innerHTML = [
        row(`each line   = €${st.start.price} × weight × index ÷ 100`),
        row(`should-cost = Σ lines + freight (€${st.freight.eur}) + margin (€${st.margin.eur})`, CA.eur(r.shouldR)),
        row('floor       = should-cost − margin', CA.eur(r.floorR)),
        row('gap         = your price − should-cost', gapTxt),
        row(`annual gap  = gap × volume = ${gapEur} × ${r.vol.toLocaleString('en-US')} t`, (r.annual < 0 ? '−' : '') + money(r.annual)),
      ].join('');
      const items = CA.$$('.demo-leg-val', out.legend);
      r.parts.forEach((p, i) => { if (items[i]) items[i].textContent = CA.eur(p); });
      if (chart) { chart.data.datasets[0].data = r.parts.map((p) => +p.toFixed(2)); chart.update('none'); }
      clearTimeout(liveTimer);
      liveTimer = setTimeout(() => {
        out.live.textContent = `Should-cost ${CA.eur(r.shouldR)} a tonne, floor ${CA.eur(r.floorR)}, gap ${gapTxt.replace('/t', '')} a tonne, annual gap ${(r.annual < 0 ? 'minus ' : '') + money(r.annual)}.`;
      }, 600);
      // Carry the visitor's own inputs into a demo request (plan D4).
      const yours = r.price !== st.price || r.vol !== st.volume_t_yr;
      out.cap.textContent = yours ? 'Your inputs. Not a CostAdvisor estimate.' : 'Illustrative weights, not the library recipe.';
      out.cap.classList.toggle('yours', yours);
      out.back.hidden = !yours;
      if (yours) { CA.lead.set('price', r.price); CA.lead.set('volume', r.vol); }
    }
    const update = () => render(compute());

    function setPreset(id) {
      const p = st.presets.find((x) => x.id === id);
      if (!p) return;
      lines.forEach((l) => { sliders[l.key].value = id === 'reset' ? defaults[l.key] : (p.set[l.key] != null ? p.set[l.key] : defaults[l.key]); });
      CA.$$('.preset').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.preset === id && id !== 'reset')));
      CA.track('calc_preset', { preset: id });
      update();
    }

    lines.forEach((l) => {
      let raf = null;
      sliders[l.key].addEventListener('input', () => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = null; update(); });
        CA.$$('.preset').forEach((b) => b.setAttribute('aria-pressed', 'false'));
        if (!tracked.has(l.key)) { tracked.add(l.key); CA.track('calc_change', { slider: l.key }); }
      });
    });
    [priceIn, volIn].forEach((el) => el.addEventListener('input', () => {
      update();
      if (!tracked.has(el.id)) { tracked.add(el.id); CA.track('calc_change', { slider: el.id === 'calcPrice' ? 'price' : 'volume' }); }
    }));
    CA.$$('.preset').forEach((b) => b.addEventListener('click', () => setPreset(b.dataset.preset)));
    out.back.addEventListener('click', () => { priceIn.value = st.price; volIn.value = st.volume_t_yr; setPreset('reset'); CA.lead.set('price', null); CA.lead.set('volume', null); });

    update();

    // Donut (Chart.js, created one viewport before it scrolls in).
    CA.lazy(CA.$('#calcDonut'), () => CA.whenChart((Chart) => {
      const r = compute();
      CA.ctx2d(CA.$('#calcDonut'));
      chart = new Chart(CA.$('#calcDonut'), {
        type: 'doughnut',
        data: { labels: lines.map((l) => l.label).concat([st.freight.short, st.margin.label]), datasets: [{ data: r.parts.map((p) => +p.toFixed(2)), backgroundColor: lines.map((l) => l.color).concat([st.freight.color, st.margin.color]), borderColor: '#ffffff', borderWidth: 3, hoverOffset: 6 }] },
        options: { cutout: '68%', responsive: true, maintainAspectRatio: false, animation: { duration: CA.reduced ? 0 : 300 },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.label}: €${c.raw.toFixed(2)}/t` } } } },
      });
    }));
  });
})();
