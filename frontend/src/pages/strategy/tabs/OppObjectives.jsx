import { useEffect, useRef, useState } from 'react';
import api, { formatApiError } from '../../../api';
import { Panel } from '../../../components/intel';
import { useToast } from '../../../components/Toast';
import { PRIORITIES, strategyUrl } from './OppShared';

/* Key strategic objectives: the 7 rows, pre-filled from the playbook's
 * authored objectives until the team saves its own. Every change is saved
 * with PUT …/objectives after a short pause (only the rows that changed). */

// Ticking an objective with no priority takes the playbook's authored one,
// else this (the mockup's default). Until then an unset priority reads '—'.
const DEFAULT_PRIORITY = 'Medium';

function differsFromAuthored(r) {
  if (!r.authored) return false;
  return !r.selected
    || (r.priority || null) !== (r.authored.priority || null)
    || (r.note || '') !== (r.authored.note || '');
}

export default function OppObjectives({ slug, teamId, objectives, onSelectedChange, readOnly = false }) {
  const { addToast } = useToast();
  const [rows, setRows] = useState(objectives || []);
  const [save, setSave] = useState({ state: 'idle' });
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const dirty = useRef(new Set());
  const timer = useRef(null);
  const seq = useRef(0);
  const url = strategyUrl(`/categories/${encodeURIComponent(slug)}/objectives`);

  useEffect(() => { setRows(objectives || []); }, [objectives]);

  useEffect(() => {
    onSelectedChange?.(rows.filter((r) => r.selected).map((r) => r.code));
  }, [rows, onSelectedChange]);

  const bodyFor = (codes) => rowsRef.current
    .filter((r) => codes.includes(r.code))
    .map((r) => ({
      code: r.code,
      selected: !!r.selected,
      priority: r.priority || null,
      note: r.note && r.note.trim() ? r.note.trim() : null,
    }));

  const flush = async () => {
    clearTimeout(timer.current);
    const codes = [...dirty.current];
    if (!codes.length) return;
    dirty.current.clear();
    const mine = ++seq.current;
    setSave({ state: 'saving' });
    try {
      const { data } = await api.put(url, bodyFor(codes), { params: { team_id: teamId } });
      if (mine !== seq.current) return;
      // Take the server's overlay (overridden / authored) unless the user has
      // typed again meanwhile — then keep the local values and save again.
      if (!dirty.current.size && Array.isArray(data?.objectives)) {
        setRows((rs) => rs.map((r) => {
          const s = data.objectives.find((o) => o.code === r.code);
          return s ? { ...r, overridden: s.overridden, authored: s.authored } : r;
        }));
      }
      setSave({ state: 'saved', at: new Date() });
    } catch (err) {
      codes.forEach((c) => dirty.current.add(c));
      if (mine !== seq.current) return;
      const msg = formatApiError(err);
      setSave({ state: 'error', error: msg });
      addToast(`Objectives not saved: ${msg}`, 'error');
    }
  };
  const flushRef = useRef(flush);
  flushRef.current = flush;

  // Leaving the tab with an edit still pending: send it now.
  useEffect(() => () => {
    clearTimeout(timer.current);
    const codes = [...dirty.current];
    if (codes.length) {
      api.put(url, bodyFor(codes), { params: { team_id: teamId } }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, teamId]);

  const edit = (code, patch, delay = 600) => {
    setRows((rs) => rs.map((r) => (r.code === code ? { ...r, ...patch } : r)));
    dirty.current.add(code);
    setSave((s) => (s.state === 'saving' ? s : { state: 'pending' }));
    clearTimeout(timer.current);
    timer.current = setTimeout(() => flushRef.current(), delay);
  };

  const resetToPlaybook = (r) => edit(r.code, {
    selected: true, priority: r.authored.priority || null, note: r.authored.note || '',
  }, 0);

  const anyOverridden = rows.some((r) => r.overridden);
  let status;
  if (readOnly) status = <span className="so-save">Read only until the strategy is started</span>;
  else if (save.state === 'saving' || save.state === 'pending') status = <span className="so-save is-busy">Saving…</span>;
  else if (save.state === 'error') {
    status = (
      <span className="so-save is-error">
        Not saved
        <button type="button" className="ca-btn-link" onClick={() => flushRef.current()}>Retry</button>
      </span>
    );
  } else if (save.state === 'saved') {
    const t = save.at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    status = <span className="so-save is-ok">✓ Saved {t}</span>;
  } else {
    status = <span className="so-save">{anyOverridden ? '✓ Saved' : 'Pre-filled from the playbook'}</span>;
  }

  return (
    <Panel
      title="Key strategic objectives"
      caption="Bridges into the levers below — highlights the opportunities that serve them"
      actions={<span aria-live="polite">{status}</span>}
      className="so-objectives"
    >
      <fieldset className="so-obj-list" disabled={readOnly}>
        <legend className="ca-sr-only">Key strategic objectives</legend>
        {rows.map((r) => {
          const pid = `so-obj-${r.code}`;
          return (
            <div key={r.code} className={`so-obj-row${r.selected ? ' is-on' : ''}`}>
              <input id={pid} type="checkbox" className="so-check" checked={!!r.selected}
                onChange={(e) => edit(r.code, {
                  selected: e.target.checked,
                  priority: e.target.checked
                    ? (r.priority || r.authored?.priority || DEFAULT_PRIORITY)
                    : r.priority,
                }, 300)} />
              <label htmlFor={pid} className="so-obj-name">
                <span className="so-obj-type">{r.type}</span>
                <span className="so-obj-desc">{r.desc}</span>
              </label>
              <select className="ix-select so-obj-pri" aria-label={`${r.type} priority`}
                value={r.priority || ''}
                onChange={(e) => edit(r.code, { priority: e.target.value || null }, 300)}>
                {!r.priority && <option value="">—</option>}
                {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input className="ix-input so-obj-note" aria-label={`${r.type} note`} placeholder="Optional note…"
                value={r.note || ''} title={r.note || undefined}
                onChange={(e) => edit(r.code, { note: e.target.value }, 900)}
                onBlur={() => { if (dirty.current.size) flushRef.current(); }} />
              <span className="so-obj-reset">
                {!readOnly && differsFromAuthored(r) && (
                  <button type="button" className="ca-btn-link so-small-link"
                    title={`Playbook: ${r.authored.priority || 'no'} priority${r.authored.note ? ` — ${r.authored.note}` : ''}`}
                    onClick={() => resetToPlaybook(r)}>
                    Use playbook
                  </button>
                )}
              </span>
            </div>
          );
        })}
      </fieldset>
    </Panel>
  );
}
