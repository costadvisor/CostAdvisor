import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';

const STATUS_LABEL = { open: 'Open', pending: 'Awaiting user', resolved: 'Resolved', closed: 'Closed' };

function fmtTime(iso) {
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function ThreadsPanel({ canned }) {
  const [threads, setThreads] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [body, setBody] = useState('');
  const [cannedId, setCannedId] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const loadThreads = useCallback(() => {
    api.get('/api/support/threads').then(({ data }) => setThreads(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load threads.'));
  }, []);
  useEffect(() => { loadThreads(); }, [loadThreads]);

  const openThread = (t) => {
    setSelected(t);
    setDetail(null);
    api.get(`/api/support/threads/${t.id}`).then(({ data }) => setDetail(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load this thread.'));
  };

  const insertCanned = (id) => {
    setCannedId(id);
    const c = canned.find(x => x.id === id);
    if (c) setBody(c.body);
  };

  const reply = async () => {
    if (!body.trim() || !selected) return;
    setBusy(true);
    try {
      await api.post(`/api/support/threads/${selected.id}/messages`, {
        body, canned_response_id: cannedId || null,
      });
      setBody(''); setCannedId('');
      openThread(selected);
      loadThreads();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not send that.');
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (status) => {
    if (!selected) return;
    try {
      const { data } = await api.put(`/api/support/threads/${selected.id}/status`, { status });
      setSelected(data);
      loadThreads();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not update status.');
    }
  };

  if (err) return <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 10 }}>{err}</div>;
  if (!threads) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const visible = statusFilter === 'all' ? threads : threads.filter(t => t.status === statusFilter);

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <div style={{ width: 320, flexShrink: 0 }}>
        <select className="ca-select" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                style={{ marginBottom: 10, width: '100%' }}>
          <option value="all">All statuses</option>
          {Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {visible.length === 0 && <div style={{ fontSize: 12, color: 'var(--muted)' }}>No threads.</div>}
        {visible.map(t => (
          <div
            key={t.id} role="button" tabIndex={0} onClick={() => openThread(t)}
            onKeyDown={e => { if (e.key === 'Enter') openThread(t); }}
            style={{
              padding: '8px 10px', borderRadius: 8, cursor: 'pointer', marginBottom: 4,
              background: selected?.id === t.id ? 'var(--surface2)' : 'transparent',
              border: '1px solid var(--border)',
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 600 }}>{t.subject}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>
              {t.team_name} · {t.user_name} · {t.scope} · {fmtTime(t.updated_at)}
            </div>
            <span className="ca-badge" style={{ marginTop: 4, display: 'inline-block' }}>
              {STATUS_LABEL[t.status] || t.status}
            </span>
          </div>
        ))}
      </div>

      <div style={{ flex: 1 }}>
        {!selected ? (
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Select a thread.</div>
        ) : (
          <div className="ca-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <div style={{ fontWeight: 700 }}>{selected.subject}</div>
              <select className="ca-select" value={selected.status} onChange={e => setStatus(e.target.value)}
                      style={{ marginLeft: 'auto' }}>
                {Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
            {!detail ? (
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12,
                            maxHeight: 360, overflowY: 'auto' }}>
                {detail.messages.map(m => (
                  <div key={m.id} style={{
                    alignSelf: m.is_staff ? 'flex-end' : 'flex-start', maxWidth: '80%',
                    background: 'var(--surface2)', border: '1px solid var(--border)',
                    borderRadius: 10, padding: '8px 12px',
                  }}>
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
                      {m.author_name || (m.is_staff ? 'Support' : 'User')} · {fmtTime(m.created_at)}
                    </div>
                    <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{m.body}</div>
                  </div>
                ))}
              </div>
            )}
            <select className="ca-select" value={cannedId} onChange={e => insertCanned(e.target.value)}
                    style={{ width: '100%', marginBottom: 8 }}>
              <option value="">Insert a canned response…</option>
              {canned.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
            <textarea className="ca-input" rows={4} value={body} onChange={e => setBody(e.target.value)}
                      placeholder="Reply…" style={{ width: '100%', marginBottom: 8 }} />
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={reply} disabled={busy || !body.trim()}>
              {busy ? 'Sending…' : 'Reply'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function CannedResponsesPanel({ canned, onRefresh }) {
  const [editing, setEditing] = useState(null); // {id?, title, body, category}
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      if (editing.id) await api.put(`/api/support/canned-responses/${editing.id}`, editing);
      else await api.post('/api/support/canned-responses', editing);
      setEditing(null);
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    await api.delete(`/api/support/canned-responses/${id}`);
    onRefresh();
  };

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
        <div className="ca-card-title">Canned responses</div>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
                onClick={() => setEditing({ title: '', body: '', category: '' })}>
          + Add
        </button>
      </div>
      {editing && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginBottom: 10 }}>
          <input className="ca-input" placeholder="Title" value={editing.title}
                 onChange={e => setEditing({ ...editing, title: e.target.value })}
                 style={{ width: '100%', marginBottom: 6 }} />
          <input className="ca-input" placeholder="Category (optional)" value={editing.category || ''}
                 onChange={e => setEditing({ ...editing, category: e.target.value })}
                 style={{ width: '100%', marginBottom: 6 }} />
          <textarea className="ca-input" rows={4} placeholder="Body" value={editing.body}
                    onChange={e => setEditing({ ...editing, body: e.target.value })}
                    style={{ width: '100%', marginBottom: 6 }} />
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={save} disabled={busy}>Save</button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}
      {canned.map(c => (
        <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                  padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{c.title}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>{c.category || 'uncategorized'}</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(c)}>Edit</button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => remove(c.id)}>Delete</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function FAQPanel() {
  const [faqs, setFaqs] = useState(null);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get('/api/support/faq/admin').then(({ data }) => setFaqs(data));
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      if (editing.id) await api.put(`/api/support/faq/${editing.id}`, editing);
      else await api.post('/api/support/faq', editing);
      setEditing(null);
      load();
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => { await api.delete(`/api/support/faq/${id}`); load(); };

  if (!faqs) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
        <div className="ca-card-title">Knowledge base (onboarding, team creation, etc.)</div>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
                onClick={() => setEditing({ category: 'onboarding', question: '', answer: '', sort_order: 0, published: true })}>
          + Add
        </button>
      </div>
      {editing && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginBottom: 10 }}>
          <input className="ca-input" placeholder="Category (e.g. onboarding, team_creation)" value={editing.category}
                 onChange={e => setEditing({ ...editing, category: e.target.value })}
                 style={{ width: '100%', marginBottom: 6 }} />
          <input className="ca-input" placeholder="Question" value={editing.question}
                 onChange={e => setEditing({ ...editing, question: e.target.value })}
                 style={{ width: '100%', marginBottom: 6 }} />
          <textarea className="ca-input" rows={4} placeholder="Answer" value={editing.answer}
                    onChange={e => setEditing({ ...editing, answer: e.target.value })}
                    style={{ width: '100%', marginBottom: 6 }} />
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
            <input type="checkbox" checked={editing.published}
                   onChange={e => setEditing({ ...editing, published: e.target.checked })} />
            Published
          </label>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={save} disabled={busy}>Save</button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}
      {faqs.map(f => (
        <div key={f.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                  padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              {f.question} {!f.published && <span style={{ color: 'var(--muted)', fontWeight: 400 }}>(unpublished)</span>}
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>{f.category}</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(f)}>Edit</button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => remove(f.id)}>Delete</button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SupportAdminTab() {
  const [sub, setSub] = useState('threads');
  const [canned, setCanned] = useState([]);

  const loadCanned = useCallback(() => {
    api.get('/api/support/canned-responses').then(({ data }) => setCanned(data)).catch(() => setCanned([]));
  }, []);
  useEffect(() => { loadCanned(); }, [loadCanned]);

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        {[['threads', 'Threads'], ['canned', 'Canned Responses'], ['faq', 'Knowledge Base']].map(([k, l]) => (
          <button key={k} className={`ca-btn ca-btn-sm ${sub === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                  onClick={() => setSub(k)}>{l}</button>
        ))}
      </div>
      {sub === 'threads' && <ThreadsPanel canned={canned} />}
      {sub === 'canned' && <CannedResponsesPanel canned={canned} onRefresh={loadCanned} />}
      {sub === 'faq' && <FAQPanel />}
    </div>
  );
}
