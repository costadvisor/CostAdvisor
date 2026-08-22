import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api';
import { useAuth } from '../AuthContext';

/* Scrum 16 — self-serve onboarding checklist. Mounted once in App.jsx next to
 * ImpersonationBar (same pattern: self-fetches its own visibility, returns null
 * when not applicable). Reads GET /api/teams/{id}/onboarding-status — four real
 * progress signals computed server-side from existing data, no new schema.
 * Dismiss state persists per-team in localStorage (mirrors ca_active_team). */

const STEPS = [
  { key: 'has_product', label: 'Add a product', path: '/portfolio' },
  { key: 'has_priced_model', label: 'Build a should-cost', path: '/portfolio' },
  { key: 'has_actual_price', label: 'Load an actual price', path: '/portfolio' },
  { key: 'has_brief', label: 'Generate a negotiation brief', path: '/negotiate' },
];

const dismissedKey = (teamId) => `ca_onboarding_dismissed_${teamId}`;

export default function OnboardingChecklist() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!activeTeamId) return;
    setDismissed(localStorage.getItem(dismissedKey(activeTeamId)) === '1');
    api.get(`/api/teams/${activeTeamId}/onboarding-status`)
      .then(({ data }) => setStatus(data))
      .catch(() => setStatus(null));
  }, [activeTeamId]);

  if (!status || dismissed) return null;
  const done = STEPS.filter(s => status[s.key]).length;
  if (done === STEPS.length) return null;  // all steps complete — disappear automatically

  const dismiss = () => {
    localStorage.setItem(dismissedKey(activeTeamId), '1');
    setDismissed(true);
  };

  return (
    <div style={{
      position: 'fixed', bottom: 20, right: 20, zIndex: 9998,
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '12px 16px', width: 260,
      boxShadow: 'var(--shadow-bar)', fontSize: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <strong style={{ fontFamily: "'Syne', sans-serif" }}>Getting started</strong>
        <button onClick={dismiss} title="Dismiss"
          style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 14, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>{done} of {STEPS.length} complete</div>
      {STEPS.map(s => (
        <div key={s.key} onClick={() => navigate(s.path)}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', cursor: 'pointer' }}>
          <span style={{
            width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
            border: `1px solid ${status[s.key] ? 'var(--accent)' : 'var(--border)'}`,
            background: status[s.key] ? 'var(--accent)' : 'transparent',
            color: 'var(--on-accent, #fff)', fontSize: 9, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {status[s.key] ? '✓' : ''}
          </span>
          <span style={{ color: status[s.key] ? 'var(--muted)' : 'var(--text)', textDecoration: status[s.key] ? 'line-through' : 'none' }}>
            {s.label}
          </span>
        </div>
      ))}
    </div>
  );
}
