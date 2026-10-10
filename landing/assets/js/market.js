/* CostAdvisor landing — real market data: ticker and FX tiles.
 * The HTML already holds a dated snapshot (written by build/prerender.mjs), so the page is
 * complete without this file. On dev and www it fetches fresher ECB rates and rebuilds the
 * ticker once, after the load finishes (fixes today's stale-FX bug). */
(function () {
  'use strict';
  const CA = window.CA;
  const PAIRS = [
    { code: 'USD', from: 'EUR', to: 'USD', invert: false },
    { code: 'GBP', from: 'GBP', to: 'EUR', invert: true },
    { code: 'CNY', from: 'CNY', to: 'EUR', invert: true },
  ];
  const arrow = (v) => (v > 0.05 ? '▲' : v < -0.05 ? '▼' : '■');
  const cls = (v) => (v > 0.05 ? 'chg-up' : v < -0.05 ? 'chg-dn' : 'chg-flat');
  const pct = (v, d) => (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(d) + '%';

  async function fetchPair(p) {
    // Endpoint exists today. The plan's fix (E2 #3) adds `days` and newest-first paging;
    // until then the oldest-first list of about 1,700 rows is trimmed here.
    const r = await fetch(`${CA.API}/api/fx-rates/public-daily?from_currency=${p.from}&to_currency=${p.to}&limit=2000`);
    if (!r.ok) throw new Error('fx ' + r.status);
    const rows = await r.json();
    if (!Array.isArray(rows) || rows.length < 2) throw new Error('fx empty');
    return rows.slice(-90).map((x) => [x.date, p.invert ? 1 / Number(x.rate) : Number(x.rate)]);
  }

  function renderFx(series) {
    // Tiles
    PAIRS.forEach((p, i) => {
      const tile = CA.$(`.fx-current-tile[data-pair="${p.code}"]`);
      const rows = series[p.code];
      if (!tile || !rows) return;
      const vals = rows.map((r) => r[1]);
      const last = vals[vals.length - 1], first = vals[0], c = (last / first - 1) * 100;
      CA.$('.fx-current-tile-val', tile).textContent = last.toFixed(4);
      CA.$('.fx-spark', tile).innerHTML = window.CASvg.sparkline({ id: 'fxl' + i, w: 120, h: 34, actual: vals, cls: 'ln-fx' });
      CA.$('.fx-spark', tile).setAttribute('aria-label', `1 euro in ${p.code}, last 90 days: ${first.toFixed(4)} to ${last.toFixed(4)}`);
      const chg = CA.$('.fx-chg', tile);
      chg.className = 'fx-chg ' + cls(c);
      chg.innerHTML = `${arrow(c)} ${pct(c, 1)}<small>90 d</small>`;
    });
    // Ticker: rebuild only the FX items, after the load (both sets, the second stays aria-hidden).
    const sets = CA.$$('.ticker-set');
    sets.forEach((set) => {
      CA.$$('.ticker-item', set).filter((it) => /1 EUR/.test(it.textContent)).forEach((it) => it.remove());
      PAIRS.forEach((p) => {
        const rows = series[p.code]; if (!rows) return;
        const last = rows[rows.length - 1][1], prev = rows[rows.length - 2][1], c = (last / prev - 1) * 100;
        set.insertAdjacentHTML('beforeend', `<span class="ticker-item"><span class="ticker-name">1 EUR =</span><span class="ticker-val">${last.toFixed(4)} ${p.code}</span><span class="ticker-chg ${cls(c)}">${arrow(c)} ${pct(c, 2)} <small>d/d</small></span></span>`);
      });
    });
    const lastDate = series.USD && series.USD[series.USD.length - 1][0];
    const stamp = CA.$('#fxStamp');
    if (stamp && lastDate) stamp.textContent = 'Rates to ' + new Date(lastDate + 'T00:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }) + '.';
  }

  CA.ready.then(() => {
    // Index series: no public endpoint yet (plan E2 #2, GET /api/public/indexes?set=landing).
    // The pulse cards and ticker keep their dated snapshot until it exists.
    if (CA.offline) return; // snapshot only (see core.js)
    const go = () => Promise.all(PAIRS.map((p) => fetchPair(p).then((rows) => [p.code, rows])))
      .then((list) => renderFx(Object.fromEntries(list)))
      .catch(() => CA.track('widget_error', { widget: 'fx' }));
    if ('requestIdleCallback' in window) requestIdleCallback(go, { timeout: 2500 }); else setTimeout(go, 1200);
  });
})();
