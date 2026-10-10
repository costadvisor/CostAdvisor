/* CostAdvisor landing — Stage 05 claim checker.
 * Mirrors the app's own rules (backend/app/services/negotiation_prep.py, origin/dev) so the
 * page and the product give the same verdict:
 *   |index move| < 0.5%                       → did not move
 *   claimed and index have opposite signs      → contradicted
 *   |claimed| − |index move| > 2 points        → overstated
 *   otherwise                                  → real, and already inside the should-cost */
(function () {
  'use strict';
  const CA = window.CA;
  const CLAIM_TOLERANCE_PCT = 2.0, FLAT_PCT = 0.5;
  const LABELS = {
    already_priced: 'Real, and already inside the should-cost',
    overstated: 'Overstated',
    contradicted: 'Contradicted — it moved the other way',
    no_movement: 'That input did not move',
  };
  const CLS = { already_priced: 'v-priced', overstated: 'v-overstated', contradicted: 'v-contra', no_movement: 'v-flat' };
  const INDEX_NAME = { iron: 'the iron index', acid: 'the acid index', energy: 'the energy index', labour: 'the labour index' };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const f1 = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + '%';

  function check(claimed, actual) {
    if (Math.abs(actual) < FLAT_PCT) return 'no_movement';
    if (claimed != null && actual * claimed < 0) return 'contradicted';
    if (claimed != null && Math.abs(claimed) - Math.abs(actual) > CLAIM_TOLERANCE_PCT) return 'overstated';
    return 'already_priced';
  }
  function note(v, line, claimed, actual) {
    const n = INDEX_NAME[line];
    if (v === 'no_movement') return `${cap(n)} is flat over this window (${f1(actual)}). There is nothing here to pass on.`;
    if (v === 'contradicted') return `${cap(n)} moved ${f1(actual)}, the opposite direction. This argues the price down, not up.`;
    if (v === 'overstated') return `Our reading of ${n} is ${f1(actual)}, not ${f1(claimed)}. At its share of the recipe, that move is already inside the should-cost.`;
    return `${cap(n)} moved ${f1(actual)}, which matches. At its share of the recipe, it is already carried in the should-cost. Worth conceding out loud: it costs nothing and buys credibility on the rest.`;
  }
  // Exposed for tests/story.test.mjs-style checks in the console.
  window.CAClaim = { check };

  CA.ready.then(() => {
    const st = CA.story;
    const sel = CA.$('#claimSel'), pctIn = CA.$('#claimPct');
    if (!st || !sel || !pctIn) return;
    const move = Object.fromEntries(st.recipe.map((l) => [l.key, l.chg12m * 100]));
    const byId = Object.fromEntries(st.claims.map((c) => [c.id, c]));
    const vEl = CA.$('#claimVerdict'), nEl = CA.$('#claimNote'), sEl = CA.$('#claimSaid'), aEl = CA.$('#claimActual');

    function run(fromUser) {
      const c = byId[sel.value];
      const claimed = pctIn.value === '' ? null : Number(pctIn.value);
      const actual = move[c.line];
      const v = check(claimed, actual);
      vEl.className = 'verdict ' + CLS[v];
      vEl.textContent = LABELS[v];
      nEl.textContent = note(v, c.line, claimed, actual);
      sEl.textContent = claimed == null ? '—' : f1(claimed);
      aEl.textContent = f1(actual);
      CA.$$('.claim-all button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cid === c.id)));
      if (fromUser) CA.track('claim_check', { claim: c.id });
    }
    function pick(id) { sel.value = id; pctIn.value = byId[id].claimed; run(true); }
    sel.addEventListener('change', () => pick(sel.value));
    pctIn.addEventListener('input', () => run(true));
    CA.$$('.claim-all button').forEach((b) => b.addEventListener('click', () => pick(b.dataset.cid)));
    run(false);
  });
})();
