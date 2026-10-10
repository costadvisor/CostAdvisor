/* CostAdvisor landing — the three larger Chart.js charts (plan B0: Chart.js only for the larger
 * line and doughnut charts; everything small is SVG in the HTML). Each is created one viewport
 * before it scrolls in, and each canvas carries an aria-label with its numbers plus a
 * "See the numbers" table. All three read story.json: euros only, never a live index. */
(function () {
  'use strict';
  const CA = window.CA;
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  function defaults(Chart) {
    Chart.defaults.font.family = "'JetBrains Mono', ui-monospace, monospace";
    Chart.defaults.font.size = 10;
    Chart.defaults.color = '#63737a';
    Chart.defaults.animation = CA.reduced ? false : Chart.defaults.animation;
  }

  CA.ready.then(() => {
    const st = CA.story;
    if (!st) return;
    const SHOULD = css('--chart-should') || '#10B981', PRICE = css('--chart-price') || '#EF4444';
    const grid = { color: 'rgba(0,0,0,.06)' };

    // Stage 03 — the evolution chart (kept: today's buildEvoChart(), fed by the story).
    CA.lazy(CA.$('#evoChart'), () => CA.whenChart((Chart) => {
      defaults(Chart);
      const c = CA.$('#evoChart');
      const g = CA.ctx2d(c).createLinearGradient(0, 0, 0, 220);
      g.addColorStop(0, 'rgba(239,68,68,.22)'); g.addColorStop(1, 'rgba(239,68,68,.03)');
      new Chart(c, {
        type: 'line',
        data: { labels: st.quarters, datasets: [
          { label: 'Supplier A', data: st.price_series, borderColor: PRICE, backgroundColor: g, fill: '+1', tension: 0.3, pointRadius: 3, pointBackgroundColor: PRICE, borderWidth: 2.4 },
          { label: 'Should-cost', data: st.should_series, borderColor: SHOULD, backgroundColor: 'transparent', fill: false, tension: 0.3, pointRadius: 3, pointBackgroundColor: SHOULD, borderWidth: 2.4 },
        ] },
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
          plugins: { legend: { display: false }, tooltip: { callbacks: {
            label: (x) => ` ${x.dataset.label}: €${Number(x.raw).toFixed(x.raw % 1 ? 2 : 0)}/t`,
            afterBody: (items) => { const s = items.find((i) => i.datasetIndex === 0), m = items.find((i) => i.datasetIndex === 1); return s && m ? `Gap: ${s.raw - m.raw >= 0 ? '+' : '−'}€${Math.abs(s.raw - m.raw).toFixed(2)}/t` : ''; },
          } } },
          scales: { x: { grid }, y: { grid, suggestedMin: 265, suggestedMax: 345, ticks: { callback: (v) => '€' + v } } } },
      });
    }));

    // Stage 04 — should-cost history, then the modelled projection (dashed, shaded months).
    CA.lazy(CA.$('#outlookChart'), () => CA.whenChart((Chart) => {
      defaults(Chart);
      const hist = st.should_series, proj = st.outlook_eur.values;
      const labels = st.quarters.concat(st.outlook_eur.months.map((m) => m + '-26'));
      const n = hist.length;
      const histData = hist.concat(proj.map(() => null));
      const projData = hist.map((v, i) => (i === n - 1 ? v : null)).concat(proj);
      const shade = { id: 'projShade', beforeDraw(ch) {
        const x = ch.scales.x, a = ch.chartArea, x0 = x.getPixelForValue(n - 1), g = ch.ctx;
        g.save(); g.fillStyle = 'rgba(16,185,129,.07)'; g.fillRect(x0, a.top, a.right - x0, a.bottom - a.top);
        g.fillStyle = '#0A714F'; g.font = "600 10px 'JetBrains Mono', monospace"; g.fillText(a.right - x0 < 170 ? 'projection' : 'projection · modelled', x0 + 8, a.top + 12); g.restore();
      } };
      CA.ctx2d(CA.$('#outlookChart'));
      new Chart(CA.$('#outlookChart'), {
        type: 'line', plugins: [shade],
        data: { labels, datasets: [
          { label: 'Should-cost', data: histData, borderColor: SHOULD, backgroundColor: SHOULD, tension: 0.3, pointRadius: 3, borderWidth: 2.4, spanGaps: false },
          { label: 'Projection', data: projData, borderColor: SHOULD, backgroundColor: '#fff', borderDash: [6, 5], tension: 0.3, pointRadius: (ctx) => (ctx.dataIndex >= n ? 3 : 0), pointBorderColor: SHOULD, borderWidth: 2.2, spanGaps: false },
        ] },
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
          plugins: { legend: { display: false }, tooltip: { filter: (i) => i.raw != null && !(i.datasetIndex === 1 && i.dataIndex === n - 1), callbacks: { label: (x) => ` ${x.datasetIndex ? 'Projection, library forecast' : 'Should-cost'}: €${Number(x.raw).toFixed(x.raw % 1 ? 2 : 0)}/t` } } },
          scales: { x: { grid, ticks: { autoSkip: innerWidth < 760, maxTicksLimit: innerWidth < 760 ? 5 : 14, maxRotation: 0, font: { size: 9.5 } } }, y: { grid, suggestedMin: 268, suggestedMax: 298, ticks: { callback: (v) => '€' + v } } } },
      });
    }));

    // Stage 06 — spend by supplier (reuses the calculator's doughnut pattern).
    CA.lazy(CA.$('#spendChart'), () => CA.whenChart((Chart) => {
      defaults(Chart);
      const sh = st.strategy.supplier_shares;
      CA.ctx2d(CA.$('#spendChart'));
      new Chart(CA.$('#spendChart'), {
        type: 'doughnut',
        data: { labels: sh.map((s) => s.name), datasets: [{ data: sh.map((s) => Math.round(s.share * 100)), backgroundColor: [PRICE, css('--chart-1') || '#0EA5E9'], borderColor: '#fff', borderWidth: 3 }] },
        options: { cutout: '64%', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.label}: ${c.raw}% of Coagulants spend` } } } },
      });
    }));
  });
})();
