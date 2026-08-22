import { useState, useEffect } from 'react';
import api from '../api';
import { useAuth } from '../AuthContext';
import { useConfirm } from './ConfirmDialog';

export default function ImpersonationBar() {
  const { user, refreshUser } = useAuth();
  const confirm = useConfirm();
  const [active, setActive] = useState(false);

  useEffect(() => {
    setActive(document.cookie.split(';').some(c => c.trim().startsWith('ca_impersonating=')));
  }, [user]);

  if (!active) return null;

  const stop = async () => {
    const ok = await confirm({
      title: 'Stop impersonation?',
      message: `You will return to your own admin session and be redirected to the admin console.`,
      confirmLabel: 'Stop Impersonation',
    });
    if (!ok) return;
    try {
      await api.post('/api/admin/stop-impersonate');
      await refreshUser();
      window.location.href = '/admin';
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div style={{
      position: 'fixed', bottom: 20, left: '50%', transform: 'translateX(-50%)',
      zIndex: 9999,
      background: 'var(--surface)', border: '1px solid var(--accent2)',
      borderRadius: 10, padding: '8px 16px',
      display: 'flex', alignItems: 'center', gap: 12,
      boxShadow: 'var(--shadow-bar)',
      fontSize: 11,
    }}>
      <div style={{
        width: 6, height: 6, borderRadius: '50%', background: 'var(--accent2)',
        animation: 'pulse 1.5s infinite',
      }} />
      <span style={{ color: 'var(--text-secondary)' }}>
        Viewing as <strong style={{ color: 'var(--text)' }}>{user?.display_name || user?.email}</strong>
      </span>
      <button
        onClick={stop}
        style={{
          background: 'var(--accent2)', color: 'var(--on-danger)', border: 'none',
          borderRadius: 6, padding: '4px 12px', fontSize: 10, fontWeight: 600,
          cursor: 'pointer', fontFamily: "'JetBrains Mono', monospace",
          textTransform: 'uppercase', letterSpacing: 0.5,
        }}
      >
        Stop
      </button>
    </div>
  );
}
