import { useState } from 'react';
import { GEMSTONES } from '../../../components/intel';
import OppDialog from './OppDialog';
import { LEVER_STATUSES, SCORES } from './OppShared';

/* "+ Add custom opportunity": the team's own lever, filed under a gemstone.
 * `onSubmit(body)` posts it and returns a promise (rejecting with a message). */
export default function OppCustomLeverDialog({ objectives = [], defaultGemstone, onSubmit, onClose }) {
  const [v, setV] = useState({
    gemstone: defaultGemstone || GEMSTONES[0].code,
    title: '', guidance: '', ease: '3', impact: '3', value: '', status: 'Identified', notes: '',
    applies: true, objectives: [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setV((s) => ({ ...s, [k]: e.target.value }));
  const toggleObj = (code) => setV((s) => ({
    ...s,
    objectives: s.objectives.includes(code) ? s.objectives.filter((c) => c !== code) : [...s.objectives, code],
  }));

  const submit = async (e) => {
    e.preventDefault();
    if (!v.title.trim()) { setError('Enter a title for the opportunity.'); return; }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        gemstone: v.gemstone,
        title: v.title.trim(),
        guidance: v.guidance.trim() || null,
        applies: v.applies,
        ease: v.ease ? Number(v.ease) : null,
        savings_score: v.impact ? Number(v.impact) : null,
        savings_value: v.value === '' ? null : Number(v.value),
        status: v.status,
        notes: v.notes.trim() || null,
        // Keep the API's objective order.
        objectives: objectives.map((o) => o.code).filter((c) => v.objectives.includes(c)),
      });
    } catch (err) {
      setError(typeof err === 'string' ? err : err?.message || 'Could not add the opportunity.');
      setBusy(false);
    }
  };

  return (
    <OppDialog title="Add custom opportunity" onClose={onClose} width={600}>
      <form className="so-aform" onSubmit={submit} noValidate>
        <div className="so-field so-span-2">
          <label htmlFor="so-cl-gem">Gemstone category</label>
          <select id="so-cl-gem" className="ix-select so-wide" value={v.gemstone} onChange={set('gemstone')}>
            {GEMSTONES.map((g) => <option key={g.code} value={g.code}>{g.name}</option>)}
          </select>
        </div>
        <div className="so-field so-span-2">
          <label htmlFor="so-cl-title">Title</label>
          <input id="so-cl-title" className="ix-input so-wide" value={v.title} onChange={set('title')} autoFocus
            maxLength={300} placeholder="e.g. Pool volumes with the neighbouring utility" />
        </div>
        <div className="so-field so-span-2">
          <label htmlFor="so-cl-guid">Guidance</label>
          <textarea id="so-cl-guid" className="ix-input so-wide" rows={3} value={v.guidance} onChange={set('guidance')}
            placeholder="What this opportunity involves and why it applies here" />
        </div>
        <div className="so-field">
          <label htmlFor="so-cl-ease">Ease (1–5)</label>
          <select id="so-cl-ease" className="ix-select so-wide" value={v.ease} onChange={set('ease')}>
            <option value="">Not scored</option>
            {SCORES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className="so-field">
          <label htmlFor="so-cl-imp">Impact (1–5)</label>
          <select id="so-cl-imp" className="ix-select so-wide" value={v.impact} onChange={set('impact')}>
            <option value="">Not scored</option>
            {SCORES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className="so-field">
          <label htmlFor="so-cl-val">Savings value (optional)</label>
          <input id="so-cl-val" type="number" min={0} step="any" className="ix-input so-wide" value={v.value}
            onChange={set('value')} placeholder="e.g. 50000" />
        </div>
        <div className="so-field">
          <label htmlFor="so-cl-status">Status</label>
          <select id="so-cl-status" className="ix-select so-wide" value={v.status} onChange={set('status')}>
            {LEVER_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="so-field so-span-2">
          <label htmlFor="so-cl-notes">Notes</label>
          <input id="so-cl-notes" className="ix-input so-wide" value={v.notes} onChange={set('notes')}
            placeholder="Optional note" />
        </div>
        <label className="so-span-2 so-check-line">
          <input type="checkbox" className="so-check" checked={v.applies}
            onChange={(e) => setV((s) => ({ ...s, applies: e.target.checked }))} />
          Applies to this category
        </label>
        <div className="so-field so-span-2">
          <span className="so-field-lbl">Serves objectives</span>
          <div className="so-pill-row">
            {objectives.map((o) => {
              const on = v.objectives.includes(o.code);
              return (
                <button key={o.code} type="button" className={`so-obj-pill${on ? ' is-on' : ''}`} aria-pressed={on}
                  title={o.desc} onClick={() => toggleObj(o.code)}>
                  {o.type}
                </button>
              );
            })}
          </div>
        </div>
        <div className="so-aform-foot so-span-2">
          {error && <span className="so-form-error" role="alert">{error}</span>}
          <span className="ix-spacer" />
          <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="ca-btn ca-btn-primary ca-btn-sm" disabled={busy}>
            {busy ? 'Adding…' : 'Add opportunity'}
          </button>
        </div>
      </form>
    </OppDialog>
  );
}
