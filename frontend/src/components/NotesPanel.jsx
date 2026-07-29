import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';

/* Scrum 25 — threaded notes on a cost model. Any team member can post; @email
 * mentions a teammate (server emails them). Replies nest one level under their
 * parent. Author can delete their own note. */

const fmtTime = (iso) => {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
};

// Highlight @email mentions in the body.
const renderBody = (body) => {
  const parts = body.split(/(@[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g);
  return parts.map((p, i) =>
    /^@[A-Za-z0-9._%+-]+@/.test(p)
      ? <span key={i} style={{ color: 'var(--accent4)', fontWeight: 600 }}>{p}</span>
      : <span key={i}>{p}</span>);
};

export default function NotesPanel({ costModelId }) {
  const { user } = useAuth();
  const [notes, setNotes] = useState(null);
  const [error, setError] = useState(null);
  const [body, setBody] = useState('');
  const [replyTo, setReplyTo] = useState(null);   // { id, author_name }
  const [posting, setPosting] = useState(false);

  const load = useCallback(() => {
    if (!costModelId) return;
    api.get(`/api/cost-models/${costModelId}/notes`)
      .then(({ data }) => setNotes(data))
      .catch(err => setError(formatApiError(err)));
  }, [costModelId]);

  useEffect(() => { load(); }, [load]);

  const post = () => {
    if (!body.trim()) return;
    setPosting(true);
    api.post(`/api/cost-models/${costModelId}/notes`, { body: body.trim(), parent_note_id: replyTo?.id || null })
      .then(() => { setBody(''); setReplyTo(null); load(); })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setPosting(false));
  };

  const remove = (id) => {
    api.delete(`/api/cost-models/${costModelId}/notes/${id}`)
      .then(load)
      .catch(err => setError(formatApiError(err)));
  };

  const parents = (notes || []).filter(n => !n.parent_note_id);
  const repliesOf = (pid) => (notes || []).filter(n => n.parent_note_id === pid);

  const NoteRow = ({ n, reply }) => (
    <div style={{ padding: '8px 0', borderTop: reply ? 'none' : '1px solid var(--border)', marginLeft: reply ? 20 : 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 12 }}>{n.author_name || 'Unknown'}</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{fmtTime(n.created_at)}</span>
      </div>
      <div style={{ fontSize: 13, color: 'var(--text)', whiteSpace: 'pre-wrap', margin: '2px 0 4px' }}>{renderBody(n.body)}</div>
      <div style={{ display: 'flex', gap: 10 }}>
        {!reply && <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => setReplyTo({ id: n.id, author_name: n.author_name })}>Reply</button>}
        {user?.id === n.author_user_id && <button className="ca-btn-link" style={{ fontSize: 11, color: 'var(--accent2)' }} onClick={() => remove(n.id)}>Delete</button>}
      </div>
    </div>
  );

  return (
    <div className="ca-card">
      <div className="ca-card-title" style={{ marginBottom: 8 }}>Team notes</div>
      {error && <div style={{ color: 'var(--accent2)', fontSize: 12, marginBottom: 8 }}>{error}</div>}

      {notes === null ? (
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>
      ) : parents.length === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: '4px 0 10px' }}>
          No notes yet — leave the first one to share your read with the team. Use <code>@teammate@email</code> to notify someone.
        </div>
      ) : (
        <div style={{ marginBottom: 8 }}>
          {parents.map(p => (
            <div key={p.id}>
              <NoteRow n={p} />
              {repliesOf(p.id).map(r => <NoteRow key={r.id} n={r} reply />)}
            </div>
          ))}
        </div>
      )}

      {replyTo && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
          Replying to {replyTo.author_name} · <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => setReplyTo(null)}>cancel</button>
        </div>
      )}
      <textarea
        className="ca-input"
        style={{ width: '100%', minHeight: 60, resize: 'vertical' }}
        placeholder="Add a note… (@teammate@email to notify)"
        value={body}
        onChange={e => setBody(e.target.value)}
      />
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={post} disabled={posting || !body.trim()}>
          {posting ? 'Posting…' : replyTo ? 'Post reply' : 'Post note'}
        </button>
      </div>
    </div>
  );
}
