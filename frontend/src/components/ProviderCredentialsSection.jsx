import { useState, useEffect } from 'react';
import api, { formatApiError } from '../api';
import { useConfirm } from './ConfirmDialog';

const STATUS_BADGE = {
  ok: { bg: 'var(--success-bg)', color: 'var(--accent)', label: 'OK' },
  unverified: { bg: 'var(--surface2)', color: 'var(--muted)', label: 'UNVERIFIED' },
  expired: { bg: 'var(--warn-bg)', color: 'var(--accent3)', label: 'EXPIRED' },
  rejected: { bg: 'var(--danger-bg)', color: 'var(--accent2)', label: 'REJECTED' },
  error: { bg: 'var(--danger-bg)', color: 'var(--accent2)', label: 'ERROR' },
};

function StatusBadge({ status }) {
  const s = STATUS_BADGE[status] || STATUS_BADGE.unverified;
  return (
    <span className="ca-badge" style={{ background: s.bg, color: s.color, fontWeight: 600 }}>
      {s.label}
    </span>
  );
}

export default function ProviderCredentialsSection({ teamId, userRole }) {
  const confirm = useConfirm();
  const canManage = userRole === 'owner' || userRole === 'admin';

  const [credentials, setCredentials] = useState([]);
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [addProvider, setAddProvider] = useState('');
  const [addApiKey, setAddApiKey] = useState('');
  const [adding, setAdding] = useState(false);

  const [rotatingId, setRotatingId] = useState(null);
  const [rotateApiKey, setRotateApiKey] = useState('');
  const [rotating, setRotating] = useState(false);

  const fetchAll = () => {
    setLoading(true);
    Promise.all([
      api.get('/api/indexes/provider-credentials', { params: { team_id: teamId } }),
      api.get('/api/indexes/provider-credentials/providers'),
    ])
      .then(([credRes, provRes]) => {
        setCredentials(credRes.data);
        setProviders(provRes.data);
      })
      .catch(e => setError(formatApiError(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchAll(); }, [teamId]);

  const availableProviders = providers.filter(p => p.adapter_available);
  const configuredProviderKeys = new Set(credentials.map(c => c.provider));
  const addableProviders = availableProviders.filter(p => !configuredProviderKeys.has(p.key));

  const addCredential = async () => {
    if (!addProvider || !addApiKey.trim()) {
      setError('Select a provider and enter an API key.');
      return;
    }
    setAdding(true);
    try {
      await api.post('/api/indexes/provider-credentials', {
        team_id: teamId, provider: addProvider, credential: { api_key: addApiKey.trim() },
      });
      setShowAddForm(false);
      setAddProvider('');
      setAddApiKey('');
      fetchAll();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setAdding(false);
    }
  };

  const rotateCredential = async (cred) => {
    if (!rotateApiKey.trim()) {
      setError('Enter a new API key.');
      return;
    }
    setRotating(true);
    try {
      await api.post('/api/indexes/provider-credentials', {
        team_id: teamId, provider: cred.provider, credential: { api_key: rotateApiKey.trim() },
      });
      setRotatingId(null);
      setRotateApiKey('');
      fetchAll();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRotating(false);
    }
  };

  const verifyCredential = async (cred) => {
    setBusyId(cred.id);
    try {
      const { data } = await api.post(`/api/indexes/provider-credentials/${cred.id}/verify`);
      setCredentials(cs => cs.map(c => (c.id === data.id ? data : c)));
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusyId(null);
    }
  };

  const deleteCredential = async (cred) => {
    const ok = await confirm({
      title: `Delete ${cred.provider} credential?`,
      message: 'Index sources pointed at this provider will stop being able to fetch new data.',
      confirmLabel: 'Delete credential',
      danger: true,
    });
    if (!ok) return;
    setBusyId(cred.id);
    try {
      await api.delete(`/api/indexes/provider-credentials/${cred.id}`);
      fetchAll();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>;

  return (
    <div className="ca-card">
      {error && (
        <div style={{ padding: '10px 16px', marginBottom: 12, borderRadius: 8, fontSize: 12, background: 'var(--accent2-dim)', color: 'var(--accent2)', border: '1px solid var(--danger-bg-strong)', display: 'flex', justifyContent: 'space-between' }}>
          {error}
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent2)', fontWeight: 700 }}>×</button>
        </div>
      )}

      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Provider Credentials</div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 14 }}>
        Connect a licensed index provider (e.g. Fastmarkets) so your team's indexes can pull data straight from it instead of the free public feeds.
      </div>

      <table className="ca-table" style={{ marginBottom: 12 }}>
        <thead>
          <tr>
            <th>Provider</th>
            <th className="center">Status</th>
            <th>Last Verified</th>
            <th>Last Error</th>
            {canManage && <th className="center">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {credentials.length === 0 && (
            <tr><td colSpan={canManage ? 5 : 4} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No provider credentials configured.</td></tr>
          )}
          {credentials.map(c => (
            <tr key={c.id}>
              <td style={{ fontWeight: 600, fontSize: 13, textTransform: 'capitalize' }}>{c.provider}</td>
              <td className="center"><StatusBadge status={c.status} /></td>
              <td style={{ fontSize: 11, color: 'var(--muted)' }}>
                {c.last_verified_at ? new Date(c.last_verified_at).toLocaleString() : '—'}
              </td>
              <td style={{ fontSize: 11, color: 'var(--accent2)' }}>{c.last_error || '—'}</td>
              {canManage && (
                <td className="center">
                  {rotatingId === c.id ? (
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center', justifyContent: 'center' }}>
                      <input
                        type="password"
                        className="ca-input"
                        style={{ width: 140, padding: '4px 8px', fontSize: 11 }}
                        placeholder="New API key"
                        value={rotateApiKey}
                        onChange={e => setRotateApiKey(e.target.value)}
                      />
                      <button className="ca-btn ca-btn-primary ca-btn-sm" disabled={rotating} onClick={() => rotateCredential(c)}>Save</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => { setRotatingId(null); setRotateApiKey(''); }}>Cancel</button>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" disabled={busyId === c.id} onClick={() => verifyCredential(c)}>Verify</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => { setRotatingId(c.id); setRotateApiKey(''); }}>Rotate</button>
                      <button className="ca-btn ca-btn-danger ca-btn-sm" disabled={busyId === c.id} onClick={() => deleteCredential(c)}>Delete</button>
                    </div>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      {!canManage ? null : showAddForm ? (
        <div style={{ padding: 16, background: 'var(--surface2)', borderRadius: 8, borderLeft: '3px solid var(--accent)' }}>
          <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 12 }}>Add Provider Credential</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ minWidth: 160 }}>
              <label className="ca-label">Provider</label>
              <select className="ca-select" value={addProvider} onChange={e => setAddProvider(e.target.value)}>
                <option value="">Select a provider…</option>
                {addableProviders.map(p => (
                  <option key={p.key} value={p.key}>{p.label}</option>
                ))}
              </select>
            </div>
            <div style={{ minWidth: 200 }}>
              <label className="ca-label">API key</label>
              <input
                type="password"
                className="ca-input"
                value={addApiKey}
                onChange={e => setAddApiKey(e.target.value)}
                placeholder="Paste the provider's API key"
              />
            </div>
            <button className="ca-btn ca-btn-primary ca-btn-sm" disabled={adding} onClick={addCredential}>
              {adding ? 'Saving…' : 'Save'}
            </button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => { setShowAddForm(false); setAddProvider(''); setAddApiKey(''); }}>Cancel</button>
          </div>
          {addableProviders.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>
              Every available provider is already configured for this team.
            </div>
          )}
        </div>
      ) : (
        <button
          className="ca-btn ca-btn-ghost ca-btn-sm"
          disabled={addableProviders.length === 0}
          onClick={() => setShowAddForm(true)}
          title={addableProviders.length === 0 ? 'Every available provider is already configured' : undefined}
        >
          + Add credential
        </button>
      )}
    </div>
  );
}
