import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api';
import { useAuth } from '../AuthContext';

/* Scrum 16 — self-serve onboarding checklist, upgraded into a guided tour.
 * Mounted once in App.jsx next to ImpersonationBar (same pattern: self-fetches
 * its own visibility, returns null when not applicable). Reads
 * GET /api/teams/{id}/onboarding-status — four real progress signals computed
 * server-side from existing data, no new schema. Dismiss state persists
 * per-team in localStorage (mirrors ca_active_team).
 *
 * The "tour" half: once a step is known to be next, this looks up the user's
 * own real products/cost-models (the same GET /api/cost-models + /api/products
 * PortfolioArea.jsx already calls) to deep-link straight at the real page and
 * record a real button click would reach — not a shallow "/portfolio" every
 * time. There's no spotlight/overlay-with-cutout here (nothing like that
 * exists anywhere in this codebase to build on) — instead the CTA does what a
 * spotlight would only point at: it navigates to the exact target and, where
 * the target itself supports it (PortfolioArea's add-product modal), fires
 * the same action a manual click would via route state. */

const STEPS = [
  { key: 'has_product', label: 'Add a product' },
  { key: 'has_priced_model', label: 'Build a should-cost' },
  { key: 'has_actual_price', label: 'Load an actual price' },
  { key: 'has_brief', label: 'Generate a negotiation brief' },
];

const dismissedKey = (teamId) => `ca_onboarding_dismissed_${teamId}`;

export default function OnboardingChecklist() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  // Only fetched once we actually need a deep-link target, to avoid two extra
  // requests on every authenticated page load for users who are already done.
  const [catalog, setCatalog] = useState(null);

  useEffect(() => {
    if (!activeTeamId) return;
    setDismissed(localStorage.getItem(dismissedKey(activeTeamId)) === '1');
    api.get(`/api/teams/${activeTeamId}/onboarding-status`)
      .then(({ data }) => setStatus(data))
      .catch(() => setStatus(null));
  }, [activeTeamId]);

  const allDone = status && STEPS.every(s => status[s.key]);

  useEffect(() => {
    if (!activeTeamId || !status || dismissed || allDone) return;
    Promise.all([
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
    ])
      .then(([{ data: products }, { data: costModels }]) => setCatalog({ products, costModels }))
      .catch(() => setCatalog({ products: [], costModels: [] }));
  }, [activeTeamId, status, dismissed, allDone]);

  if (!status || dismissed || allDone) return null;

  const dismiss = () => {
    localStorage.setItem(dismissedKey(activeTeamId), '1');
    setDismissed(true);
  };

  const done = STEPS.filter(s => status[s.key]).length;
  const nextStep = STEPS.find(s => !status[s.key]);

  // Real deep-link target for the current next step, computed from the
  // user's own data once `catalog` has loaded — never a placeholder record.
  const nextTarget = (() => {
    if (!catalog) return null;
    const { products, costModels } = catalog;
    if (nextStep.key === 'has_product') {
      return { path: '/portfolio', state: { autoOpenAddProduct: true }, cta: 'Add your first product' };
    }
    if (nextStep.key === 'has_priced_model') {
      const draftProduct = products.find(p => !costModels.some(cm => cm.product_id === p.id));
      if (draftProduct) {
        return { path: '/cost-models/new', state: { productId: draftProduct.id }, cta: `Build a should-cost for ${draftProduct.name}` };
      }
      const unpriced = costModels.find(cm => !cm.formula_versions || cm.formula_versions.length === 0);
      if (unpriced) {
        return { path: `/cost-models/${unpriced.id}`, cta: `Build a should-cost for ${unpriced.product_name || 'your product'}` };
      }
      return { path: '/portfolio', cta: 'Go to Portfolio' };
    }
    if (nextStep.key === 'has_actual_price') {
      const priced = costModels.find(cm => cm.formula_versions && cm.formula_versions.length > 0);
      if (priced) {
        return { path: `/cost-models/${priced.id}/pricing`, cta: `Add an actual price for ${priced.product_name || 'your product'}` };
      }
      return { path: '/portfolio', cta: 'Go to Portfolio' };
    }
    // has_brief — landing on /negotiate/:id alone fires the brief request,
    // satisfying this signal with no further click needed.
    if (costModels.length > 0) {
      const cm = costModels[0];
      return { path: `/negotiate/${cm.id}`, cta: `Generate a brief for ${cm.product_name || 'your product'}` };
    }
    return { path: '/negotiate', cta: 'Go to Negotiate' };
  })();

  const goToNext = () => {
    if (!nextTarget) return;
    navigate(nextTarget.path, nextTarget.state ? { state: nextTarget.state } : undefined);
  };

  return (
    <div style={{
      position: 'fixed', bottom: 20, right: 20, zIndex: 9998,
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '12px 16px', width: 280,
      boxShadow: 'var(--shadow-bar)', fontSize: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <strong style={{ fontFamily: "'Syne', sans-serif" }}>Getting started</strong>
        <button onClick={dismiss} title="Dismiss"
          style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 14, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>{done} of {STEPS.length} complete</div>
      {STEPS.map(s => (
        <div key={s.key}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
          <span style={{
            width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
            border: `1px solid ${status[s.key] ? 'var(--accent)' : 'var(--border)'}`,
            background: status[s.key] ? 'var(--accent)' : 'transparent',
            color: 'var(--on-accent, #fff)', fontSize: 9, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {status[s.key] ? '✓' : ''}
          </span>
          <span style={{
            color: status[s.key] ? 'var(--muted)' : (s.key === nextStep.key ? 'var(--text)' : 'var(--text-secondary)'),
            textDecoration: status[s.key] ? 'line-through' : 'none',
            fontWeight: s.key === nextStep.key ? 600 : 400,
          }}>
            {s.label}
          </span>
        </div>
      ))}
      <button
        className="ca-btn ca-btn-primary ca-btn-sm"
        style={{ width: '100%', marginTop: 10 }}
        disabled={!nextTarget}
        onClick={goToNext}
      >
        {nextTarget ? `${nextTarget.cta} →` : 'Loading…'}
      </button>
    </div>
  );
}
