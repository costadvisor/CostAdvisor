import { useLayoutEffect, useRef, useState, memo } from 'react';
import { gemstoneColor } from '../../../components/intel';
import ActionForm from './ActionForm';
import { LEVER_STATUSES, SCORES, statusTone, plural } from './OppShared';

/* One opportunity (lever) of the Opportunities tab: number, title, guidance,
 * the team's scoring controls, the objectives it serves and its actions. */

const FIELD_LABEL = { applies: 'Applies', ease: 'Ease', savings_score: 'Impact', status: 'Status', notes: 'Notes' };

function fmtDefault(field, v) {
  if (field === 'applies') return v ? 'applies' : 'does not apply';
  if (v === null || v === undefined || v === '') return 'empty';
  return String(v);
}

/* Commit-on-blur text / number input (Enter commits, Escape reverts). */
function DraftInput({ value, onCommit, type = 'text', className, ...rest }) {
  const shown = value ?? '';
  const [draft, setDraft] = useState(null);
  const commit = () => {
    if (draft === null) return;
    const next = draft;
    setDraft(null);
    if (String(next) !== String(shown)) onCommit(next);
  };
  return (
    <input
      type={type}
      className={className}
      value={draft ?? shown}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        // Enter commits through blur, so a value is never sent twice.
        if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
        if (e.key === 'Escape') { setDraft(null); }
      }}
      {...rest}
    />
  );
}

function Guidance({ text }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    setClamped(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  if (!text) return null;
  return (
    <div className="so-guidance-wrap">
      <p ref={ref} className={`so-guidance${open ? ' is-open' : ''}`}>{text}</p>
      {(clamped || open) && (
        <button type="button" className="ca-btn-link so-small-link" aria-expanded={open}
          onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : 'Show full guidance'}
        </button>
      )}
    </div>
  );
}

function LeverRow({
  lever: l, matches, selected, objectives, members, onPatch, onReset, onDelete, onAddAction, onViewActions,
  readOnly = false,
}) {
  const [adding, setAdding] = useState(false);
  const d = l.defaults;
  const changed = (f) => !!d && (l[f] ?? null) !== (d[f] ?? null) && !(f === 'notes' && !l[f] && !d[f]);
  const hint = (f) => (changed(f) ? `Changed from the playbook (${fmtDefault(f, d[f])})` : undefined);
  const fieldCls = (f) => `so-ctl${changed(f) ? ' is-changed' : ''}`;
  const servedCodes = new Set((l.objectives || []).map((o) => o.code));
  const num = l.number;

  const scoreSelect = (field) => (
    <label className={fieldCls(field)} title={hint(field)}>
      <span className="so-ctl-lbl">{FIELD_LABEL[field]}</span>
      <select className="ix-select so-score" value={l[field] ?? ''}
        onChange={(e) => onPatch(l, field, e.target.value === '' ? null : Number(e.target.value))}>
        {l[field] == null && <option value="">—</option>}
        {SCORES.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  );

  return (
    <article
      id={`so-lever-${l.lever_id ?? l.custom_lever_id}`}
      className={`so-lever${l.applies ? '' : ' is-off'}${matches ? ' is-match' : ''}${selected ? ' is-selected' : ''}`}
      style={{ '--gem': gemstoneColor(l.gemstone) }}
      aria-label={`Opportunity ${num}: ${l.title}`}
    >
      <div className="so-lever-top">
        <span className="ix-num-bubble so-lever-num" aria-hidden>{num}</span>
        <h3 className="so-lever-title">{l.title}</h3>
        <div className="so-lever-flags">
          {l.custom && <span className="so-flag is-custom">Custom</span>}
          {l.overridden && (
            <span className="so-flag is-edited" title="The team has changed this opportunity from the playbook">
              Edited
              {!readOnly && (
                <button type="button" className="so-flag-btn" onClick={() => onReset(l)}
                  title="Put every field back to the playbook values">Reset</button>
              )}
            </span>
          )}
          {l.actions_count > 0 && (
            <button type="button" className="so-flag is-actions" onClick={() => onViewActions(l)}
              title="Open the Actions Management tab">
              {plural(l.actions_count, 'action')}
            </button>
          )}
        </div>
      </div>

      <Guidance text={l.guidance} />

      <fieldset className="so-controls" disabled={readOnly}>
        <legend className="ca-sr-only">{`Team scoring for opportunity ${num}`}</legend>
        <label className={`${fieldCls('applies')} so-applies`} title={hint('applies')}>
          <input type="checkbox" className="so-check" checked={!!l.applies}
            onChange={(e) => onPatch(l, 'applies', e.target.checked)} />
          <span>Applies</span>
        </label>
        {scoreSelect('ease')}
        {scoreSelect('savings_score')}
        <label className="so-ctl">
          <span className="so-ctl-lbl">Value</span>
          <DraftInput type="number" className="ix-input so-value" placeholder="optional" min={0} step="any"
            value={l.savings_value ?? ''} aria-label="Savings value (optional)"
            onCommit={(v) => onPatch(l, 'savings_value', v === '' ? null : Number(v))} />
        </label>
        <label className={fieldCls('status')} title={hint('status')}>
          <span className="so-ctl-lbl">Status</span>
          <select className={`ix-select so-status tone-${statusTone(l.status)}`} value={l.status || 'Identified'}
            onChange={(e) => onPatch(l, 'status', e.target.value)}>
            {LEVER_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className={`${fieldCls('notes')} so-notes`} title={hint('notes')}>
          <span className="so-ctl-lbl">Notes</span>
          <DraftInput className="ix-input so-wide" placeholder="Notes…" value={l.notes ?? ''}
            aria-label="Notes" onCommit={(v) => onPatch(l, 'notes', v)} />
        </label>
      </fieldset>

      <div className="so-lever-foot">
        <span className="so-foot-lbl">Serves objectives</span>
        {l.custom ? (
          objectives.map((o) => {
            const on = servedCodes.has(o.code);
            return (
              <button key={o.code} type="button" className={`so-obj-pill${on ? ' is-on' : ''}`} aria-pressed={on}
                title={o.desc} disabled={readOnly}
                onClick={() => {
                  const next = objectives.map((x) => x.code)
                    .filter((c) => (c === o.code ? !on : servedCodes.has(c)));
                  onPatch(l, 'objectives', next);
                }}>
                {o.type}
              </button>
            );
          })
        ) : (
          (l.objectives || []).length
            ? l.objectives.map((o) => <span key={o.code} className="so-obj-pill is-on is-static">{o.name}</span>)
            : <span className="ix-muted ix-small">None authored</span>
        )}
        <span className="ix-spacer" />
        {l.custom && !readOnly && (
          <button type="button" className="ca-btn-link so-danger-link" onClick={() => onDelete(l)}>Delete</button>
        )}
        {!adding && !readOnly && (
          <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm so-add-action" onClick={() => setAdding(true)}>
            + Add action
          </button>
        )}
      </div>

      {adding && (
        <div className="so-inline-form">
          <div className="so-inline-form-title">New action on #{num}</div>
          <ActionForm
            variant="inline"
            fixedLever={l}
            members={members}
            onCancel={() => setAdding(false)}
            onSubmit={async (body) => { await onAddAction(l, body); setAdding(false); }}
          />
        </div>
      )}
    </article>
  );
}

export default memo(LeverRow);
