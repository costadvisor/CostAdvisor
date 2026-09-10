import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';

const STATUS_LABEL = { open: 'Open', pending: 'Awaiting you', resolved: 'Resolved', closed: 'Closed' };

function fmtTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function FAQPanel() {
  const [faqs, setFaqs] = useState(null);
  const [err, setErr] = useState(null);
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    api.get('/api/support/faq').then(({ data }) => setFaqs(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the FAQ.'));
  }, []);

  if (err) return <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>;
  if (!faqs) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;
  if (!faqs.length) return null;

  const byCategory = {};
  for (const f of faqs) (byCategory[f.category] ||= []).push(f);

  return (
    <div className="ca-card" style={{ marginBottom: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>Frequently asked</div>
      {Object.entries(byCategory).map(([cat, items]) => (
        <div key={cat} style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase',
                        letterSpacing: 0.6, marginBottom: 6 }}>{cat}</div>
          {items.map(f => (
            <div key={f.id} style={{ borderBottom: '1px solid var(--border)', padding: '8px 0' }}>
              <div
                role="button" tabIndex={0} style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}
                onClick={() => setOpenId(o => (o === f.id ? null : f.id))}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenId(o => (o === f.id ? null : f.id)); } }}
              >
                {openId === f.id ? '▾' : '▸'} {f.question}
              </div>
              {openId === f.id && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, whiteSpace: 'pre-wrap' }}>
                  {f.answer}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function ThreadView({ thread, onBack, onChanged, isStaff }) {
  const [detail, setDetail] = useState(null);
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    api.get(`/api/support/threads/${thread.id}`).then(({ data }) => setDetail(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load this thread.'));
  }, [thread.id]);

  useEffect(() => { load(); }, [load]);

  const send = async () => {
    if (!body.trim()) return;
    setBusy(true);
    try {
      await api.post(`/api/support/threads/${thread.id}/messages`, { body });
      setBody('');
      load();
      onChanged();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not send that.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onBack}>← Back</button>
        <div style={{ fontWeight: 700, fontSize: 14 }}>{thread.subject}</div>
        <span className="ca-badge" style={{ marginLeft: 'auto' }}>{STATUS_LABEL[thread.status] || thread.status}</span>
      </div>
      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 8 }}>{err}</div>}
      {!detail ? (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
          {detail.messages.map(m => (
            <div key={m.id} style={{
              alignSelf: m.is_staff ? 'flex-start' : 'flex-end', maxWidth: '80%',
              background: m.is_staff ? 'var(--surface2)' : 'var(--accent-dim, var(--surface2))',
              border: '1px solid var(--border)', borderRadius: 10, padding: '8px 12px',
            }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
                {m.is_staff ? `${m.author_name || 'Support'} · Support` : m.author_name} · {fmtTime(m.created_at)}
              </div>
              <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{m.body}</div>
            </div>
          ))}
        </div>
      )}
      <textarea
        className="ca-input" rows={3} placeholder="Write a reply…" value={body}
        onChange={e => setBody(e.target.value)} style={{ width: '100%', marginBottom: 8 }}
      />
      <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={send} disabled={busy || !body.trim()}>
        {busy ? 'Sending…' : 'Send'}
      </button>
    </div>
  );
}

function NewThreadForm({ teamId, onCreated }) {
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [scope, setScope] = useState('person');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const submit = async () => {
    if (!subject.trim() || !body.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const { data } = await api.post(`/api/support/threads?team_id=${teamId}`, { subject, body, scope });
      setSubject(''); setBody('');
      onCreated(data);
    } catch (e) {
      setErr(formatApiError(e) || 'Could not send that.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ca-card" style={{ marginBottom: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>New message to support</div>
      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 8 }}>{err}</div>}
      <input className="ca-input" placeholder="Subject" value={subject}
             onChange={e => setSubject(e.target.value)} style={{ width: '100%', marginBottom: 8 }} />
      <textarea className="ca-input" rows={4} placeholder="How can we help?" value={body}
                onChange={e => setBody(e.target.value)} style={{ width: '100%', marginBottom: 8 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="radio" checked={scope === 'person'} onChange={() => setScope('person')} />
          Just me
        </label>
        <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}
               title="Everyone on your team can see and reply to this thread">
          <input type="radio" checked={scope === 'team'} onChange={() => setScope('team')} />
          My whole team
        </label>
      </div>
      <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={submit} disabled={busy || !subject.trim() || !body.trim()}>
        {busy ? 'Sending…' : 'Send to support'}
      </button>
    </div>
  );
}

export default function Support() {
  const { activeTeamId } = useAuth();
  const [threads, setThreads] = useState(null);
  const [selected, setSelected] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    if (!activeTeamId) return;
    api.get('/api/support/threads', { params: { team_id: activeTeamId } })
      .then(({ data }) => setThreads(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load your support threads.'));
  }, [activeTeamId]);

  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 16px' }}>
      <h2 style={{ marginBottom: 16 }}>Support</h2>
      <FAQPanel />
      {selected ? (
        <ThreadView
          thread={selected}
          isStaff={false}
          onBack={() => { setSelected(null); load(); }}
          onChanged={load}
        />
      ) : (
        <>
          <NewThreadForm teamId={activeTeamId} onCreated={t => { load(); setSelected(t); }} />
          <div className="ca-card">
            <div className="ca-card-title" style={{ marginBottom: 8 }}>Your messages</div>
            {err && <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>}
            {!threads ? (
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>
            ) : threads.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                Nothing yet — send a message above and it'll show up here.
              </div>
            ) : (
              threads.map(t => (
                <div
                  key={t.id} role="button" tabIndex={0} onClick={() => setSelected(t)}
                  onKeyDown={e => { if (e.key === 'Enter') setSelected(t); }}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '10px 4px', borderBottom: '1px solid var(--border)', cursor: 'pointer',
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{t.subject}</div>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                      {t.scope === 'team' ? 'Team thread' : 'Private'} · {fmtTime(t.updated_at)}
                    </div>
                  </div>
                  <span className="ca-badge">{STATUS_LABEL[t.status] || t.status}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
