import { useState } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { THEMES } from '../utils/theme';

export default function Profile() {
  const { user, refreshUser, setTheme } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [company, setCompany] = useState(user?.company || '');
  const [savingName, setSavingName] = useState(false);
  const [message, setMessage] = useState(null);
  const [showDelete, setShowDelete] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState('');

  const isImpersonating = document.cookie.split(';').some(c => c.trim().startsWith('ca_impersonating='));

  const nameDirty = (displayName || '').trim() !== (user?.display_name || '').trim();
  const companyDirty = (company || '').trim() !== (user?.company || '').trim();
  const profileDirty = nameDirty || companyDirty;

  const handleSaveName = async () => {
    const nextName = (displayName || '').trim();
    if (!nextName) {
      setMessage({ type: 'error', text: 'Display name cannot be empty.' });
      return;
    }
    setSavingName(true);
    try {
      const payload = { display_name: nextName };
      if (companyDirty) payload.company = (company || '').trim() || null;
      await api.put('/auth/me', payload);
      await refreshUser();
      setMessage({ type: 'success', text: 'Profile updated.' });
    } catch (err) {
      setMessage({ type: 'error', text: formatApiError(err) });
    } finally {
      setSavingName(false);
    }
  };

  const handleDeleteAccount = async () => {
    if (deleteConfirm !== user?.email) {
      setMessage({ type: 'error', text: 'Type your email exactly to confirm.' });
      return;
    }
    try {
      await api.delete('/api/account');
      window.location.href = '/login';
    } catch (err) {
      setMessage({ type: 'error', text: formatApiError(err) });
    }
  };

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Profile</div>
      <p className="ca-subtitle">Your personal preferences. Team-level settings live on the Team page.</p>

      {isImpersonating && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 12,
          background: 'var(--accent2-dim)', color: 'var(--accent2)',
          border: '1px solid var(--danger-bg-strong)',
        }}>
          You are currently impersonating another user. Profile edits are disabled until you stop impersonation.
        </div>
      )}

      {message && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16, fontSize: 12,
          background: message.type === 'success' ? 'var(--accent-dim)' : 'var(--accent2-dim)',
          color: message.type === 'success' ? 'var(--accent)' : 'var(--accent2)',
          border: `1px solid ${message.type === 'success' ? 'var(--success-bg-strong)' : 'var(--danger-bg-strong)'}`,
        }}>
          {message.text}
        </div>
      )}

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div className="ca-card-title">Profile</div>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 16 }}>
          {user?.avatar_url && (
            <img
              src={user.avatar_url}
              alt=""
              style={{ width: 56, height: 56, borderRadius: '50%' }}
            />
          )}
          <div>
            <div style={{ fontSize: 12, color: 'var(--text)' }}>{user?.email}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
              Signed in via Google. Avatar and email are managed by your Google account.
            </div>
          </div>
        </div>

        <label className="ca-label">Display name</label>
        <div style={{ display: 'flex', gap: 8, maxWidth: 480, marginBottom: 12 }}>
          <input
            className="ca-input"
            value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && profileDirty && !isImpersonating && handleSaveName()}
            placeholder="Your name"
            disabled={isImpersonating}
          />
        </div>
        <label className="ca-label">Company</label>
        <div style={{ display: 'flex', gap: 8, maxWidth: 480 }}>
          <input
            className="ca-input"
            value={company}
            onChange={e => setCompany(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && profileDirty && !isImpersonating && handleSaveName()}
            placeholder="Your company name"
            disabled={isImpersonating}
          />
          <button
            className="ca-btn ca-btn-primary ca-btn-sm"
            onClick={handleSaveName}
            disabled={!profileDirty || savingName || isImpersonating}
          >
            {savingName ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      <div className="ca-card" style={{ marginBottom: 20 }}>
        <div className="ca-card-title">Appearance</div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 18, marginTop: -8 }}>
          Picks a color palette for your account. Saved on your user, follows you across devices.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
          {THEMES.map(t => (
            <ThemePreview
              key={t.id}
              theme={t}
              active={(user?.theme || 'default') === t.id}
              onSelect={() => setTheme(t.id)}
            />
          ))}
        </div>
      </div>

      <div className="ca-card" style={{ borderColor: 'var(--danger-bg-strong)' }}>
        <div className="ca-card-title" style={{ color: 'var(--accent2)' }}>Delete account</div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 12 }}>
          Teams where you are the sole member are permanently deleted along with all their data.
          For teams with other members, only your membership is removed. This cannot be undone.
        </p>
        {!showDelete ? (
          <button className="ca-btn-danger" onClick={() => setShowDelete(true)} disabled={isImpersonating}>
            Delete my account
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              className="ca-input"
              placeholder={`Type ${user?.email} to confirm`}
              value={deleteConfirm}
              onChange={e => setDeleteConfirm(e.target.value)}
              style={{ minWidth: 280 }}
            />
            <button className="ca-btn-danger" onClick={handleDeleteAccount}>
              Confirm delete
            </button>
            <button
              className="ca-btn ca-btn-sm"
              onClick={() => { setShowDelete(false); setDeleteConfirm(''); }}
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ThemePreview({ theme, active, onSelect }) {
  return (
    <div data-theme={theme.id} style={{
      background: 'var(--surface)',
      border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
      borderRadius: 'var(--radius-lg)',
      padding: 18,
      transition: 'border-color .2s',
    }}>
      <div style={{
        background: 'var(--bg)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: 14,
        marginBottom: 14,
      }}>
        <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
          <span style={{ width: 18, height: 18, borderRadius: 4, background: 'var(--accent)' }} />
          <span style={{ width: 18, height: 18, borderRadius: 4, background: 'var(--accent2)' }} />
          <span style={{ width: 18, height: 18, borderRadius: 4, background: 'var(--accent3)' }} />
          <span style={{ width: 18, height: 18, borderRadius: 4, background: 'var(--accent4)' }} />
        </div>
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '8px 10px', marginBottom: 10,
        }}>
          <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 2 }}>
            Sample card
          </div>
          <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 14, color: 'var(--text)' }}>
            $12,480
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <span style={{
            background: 'var(--accent)', color: 'var(--on-accent)',
            fontSize: 9, fontWeight: 600, padding: '4px 10px',
            borderRadius: 6, textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            Primary
          </span>
          <span style={{
            background: 'var(--success-bg)', color: 'var(--accent)',
            fontSize: 9, fontWeight: 600, padding: '4px 10px',
            borderRadius: 6, textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            OK
          </span>
          <span style={{
            background: 'var(--danger-bg)', color: 'var(--accent2)',
            fontSize: 9, fontWeight: 600, padding: '4px 10px',
            borderRadius: 6, textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            DRIFT
          </span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 14 }}>
            {theme.label}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
            {theme.description}
          </div>
        </div>
        {active ? (
          <span className="ca-badge" style={{ background: 'var(--accent)', color: 'var(--on-accent)', alignSelf: 'center' }}>
            Current
          </span>
        ) : (
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onSelect} style={{ alignSelf: 'center' }}>
            Use
          </button>
        )}
      </div>
    </div>
  );
}
