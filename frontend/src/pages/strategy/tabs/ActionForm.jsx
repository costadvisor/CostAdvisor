import { useState, useId } from 'react';
import { GEMSTONES } from '../../../components/intel/palette';
import { ACTION_STATUSES, leverKey } from './OppShared';

/* The action form, used inline under a lever (Opportunities tab: the lever is
 * fixed) and in the add / edit dialog (Actions tab: with a lever picker).
 *
 *   <ActionForm members={members} levers={levers} initial={action}
 *     onSubmit={(body) => api.post(...)} onCancel={close} submitLabel="Add action" />
 *
 * `onSubmit` receives the API body and returns a promise; the form stays open
 * (with the error shown) if it rejects. */

const EMPTY = {
  lever: '', title: '', description: '', assignee: '', start: '', due: '', status: 'Not started', pct: 0,
};

function fromAction(a, fixedLever) {
  if (!a) return { ...EMPTY, lever: fixedLever ? leverKey(fixedLever) : '' };
  return {
    lever: a.lever_id != null ? `a${a.lever_id}` : a.custom_lever_id ? `c${a.custom_lever_id}` : '',
    title: a.title || '',
    description: a.description || '',
    assignee: a.assignee_user_id || '',
    start: a.start_date || '',
    due: a.due_date || '',
    status: a.status || 'Not started',
    pct: a.pct_complete ?? 0,
  };
}

export function actionBody(v) {
  const lever = v.lever || '';
  return {
    title: v.title.trim(),
    description: v.description.trim() || null,
    lever_id: lever.startsWith('a') ? Number(lever.slice(1)) : null,
    custom_lever_id: lever.startsWith('c') ? lever.slice(1) : null,
    assignee_user_id: v.assignee || null,
    start_date: v.start || null,
    due_date: v.due || null,
    status: v.status,
    pct_complete: Math.max(0, Math.min(100, Math.round(Number(v.pct) || 0))),
  };
}

export default function ActionForm({
  levers, fixedLever, members = [], initial, onSubmit, onCancel, submitLabel = 'Add action',
  variant = 'dialog', autoFocus = true,
}) {
  const [v, setV] = useState(() => fromAction(initial, fixedLever));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const uid = useId();
  const set = (k) => (e) => setV((s) => ({ ...s, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    if (!v.title.trim()) { setError('Enter a title.'); return; }
    if (v.start && v.due && v.due < v.start) { setError('The due date is before the start date.'); return; }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(actionBody(v));
    } catch (err) {
      setError(typeof err === 'string' ? err : err?.message || 'Could not save the action.');
      setBusy(false);
    }
  };

  // Lever picker: grouped by gemstone, in the list's own order and numbering.
  // A lever that no longer applies is still offered when it is the current link.
  const groups = levers
    ? GEMSTONES.map((g) => ({
      ...g,
      items: levers.filter((l) => l.gemstone === g.code && (l.applies || leverKey(l) === v.lever)),
    })).filter((g) => g.items.length)
    : [];

  const id = (k) => `${uid}-${k}`;
  const inline = variant === 'inline';

  return (
    <form className={`so-aform${inline ? ' is-inline' : ''}`} onSubmit={submit} noValidate>
      {levers && (
        <div className="so-field so-span-2">
          <label htmlFor={id('lever')}>Opportunity</label>
          <select id={id('lever')} className="ix-select so-wide" value={v.lever} onChange={set('lever')}>
            <option value="">Not linked to an opportunity</option>
            {groups.map((g) => (
              <optgroup key={g.code} label={g.name}>
                {g.items.map((l) => (
                  <option key={leverKey(l)} value={leverKey(l)}>{`#${l.number} · ${l.title}`}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
      )}
      <div className={`so-field ${inline ? 'so-grow' : 'so-span-2'}`}>
        <label htmlFor={id('title')}>Title</label>
        <input id={id('title')} className="ix-input so-wide" value={v.title} onChange={set('title')}
          placeholder="e.g. Ask the supplier for an index-linked offer" autoFocus={autoFocus} maxLength={300} />
      </div>
      {!inline && (
        <div className="so-field so-span-2">
          <label htmlFor={id('desc')}>Description</label>
          <textarea id={id('desc')} className="ix-input so-wide" rows={3} value={v.description}
            onChange={set('description')} placeholder="Detail and next steps (optional)" />
        </div>
      )}
      <div className="so-field">
        <label htmlFor={id('assignee')}>Assignee</label>
        <select id={id('assignee')} className="ix-select so-wide" value={v.assignee} onChange={set('assignee')}>
          <option value="">Unassigned</option>
          {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </div>
      <div className="so-field">
        <label htmlFor={id('status')}>Status</label>
        <select id={id('status')} className="ix-select so-wide" value={v.status} onChange={set('status')}>
          {ACTION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div className="so-field">
        <label htmlFor={id('start')}>Start date</label>
        <input id={id('start')} type="date" className="ix-input so-wide" value={v.start} onChange={set('start')} />
      </div>
      <div className="so-field">
        <label htmlFor={id('due')}>Due date</label>
        <input id={id('due')} type="date" className="ix-input so-wide" value={v.due} min={v.start || undefined}
          onChange={set('due')} />
      </div>
      {!inline && (
        <div className="so-field">
          <label htmlFor={id('pct')}>% complete</label>
          <input id={id('pct')} type="number" min={0} max={100} step={5} className="ix-input so-wide"
            value={v.pct} onChange={set('pct')} />
        </div>
      )}
      <div className={`so-aform-foot${inline ? '' : ' so-span-2'}`}>
        {error && <span className="so-form-error" role="alert">{error}</span>}
        <span className="ix-spacer" />
        <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="ca-btn ca-btn-primary ca-btn-sm" disabled={busy}>
          {busy ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  );
}
