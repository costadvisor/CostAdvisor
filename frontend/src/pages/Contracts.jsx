import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import { useConfirm } from '../components/ConfirmDialog';
import exportCsv from '../utils/exportCsv';

/**
 * Contracts and their clauses (Wave 3, SCRUM-79 / MON-1).
 *
 * The list is ordered by notice deadline ascending, because "which contracts are
 * approaching notice" is the entire reason that date is stored and indexed
 * rather than recomputed per request.
 *
 * Two rules carried straight from the API contract:
 *
 *  - **`notice_deadline` is read-only.** It is derived from `term_end −
 *    notice_days` and recomputed on every write; accepting it as an input would
 *    let the stored value and its own inputs disagree, and then neither could be
 *    trusted. The form shows the computed result live and never submits it.
 *  - **Contracts are gated on their own `contracts.*` category**, not on
 *    `costing.view`. A price and a notice date are more sensitive than a
 *    should-cost curve, so a role without contract access should not reach this
 *    page — the nav entry is hidden for them too.
 */

const CLAUSE_TYPES = [
  ['notice', 'Notice'],
  ['price_review', 'Price review'],
  ['indexation', 'Indexation'],
  ['renewal', 'Renewal'],
  ['volume', 'Volume'],
  ['penalty', 'Penalty'],
  ['other', 'Other'],
];

const CADENCES = [
  ['', '—'],
  ['none', 'None'],
  ['monthly', 'Monthly'],
  ['quarterly', 'Quarterly'],
  ['semiannual', 'Semi-annual'],
  ['annual', 'Annual'],
  ['on_request', 'On request'],
];

const label = (pairs, k) => (pairs.find(p => p[0] === k) || [, k || '—'])[1];

const EMPTY = {
  supplier_id: '', reference: '', term_start: '', term_end: '',
  auto_renew: false, notice_days: '', price_review_cadence: '',
  currency: '', notes: '', cost_model_ids: [], clauses: [],
};

/* The same derivation the server runs, shown live in the form so the read-only
 * field is explained rather than merely locked. Both sides read `term_end -
 * notice_days`; there is no second rule here to drift from it. */
function previewDeadline(termEnd, noticeDays) {
  if (!termEnd || noticeDays === '' || noticeDays === null) return null;
  const n = Number(noticeDays);
  if (!Number.isFinite(n)) return null;
  const d = new Date(`${termEnd}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function NoticeChip({ days, deadline }) {
  if (!deadline) {
    return (
      <span style={{ fontSize: 11, color: 'var(--muted)' }}
        title="No notice deadline — it needs both a term end and a notice period. An absent deadline is a real answer, not a zero.">
        not set
      </span>
    );
  }
  const past = days !== null && days !== undefined && days < 0;
  const urgent = days !== null && days !== undefined && days >= 0 && days <= 30;
  return (
    <span style={{
      fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
      color: past ? 'var(--muted)' : urgent ? 'var(--accent2)' : days <= 90 ? 'var(--accent3)' : 'var(--text-secondary)',
      fontWeight: urgent ? 700 : 500,
    }} title={past ? 'The notice window has already passed' : `Notice must be given by ${deadline}`}>
      {deadline}
      {days !== null && days !== undefined && (
        <span style={{ color: 'var(--muted)', fontWeight: 400 }}>
          {' '}· {past ? `${Math.abs(days)}d ago` : `${days}d`}
        </span>
      )}
    </span>
  );
}

/* ── Editor ───────────────────────────────────────────────────────────────── */

function ContractForm({ initial, suppliers, costModels, onCancel, onSaved }) {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const deadline = previewDeadline(form.term_end, form.notice_days);

  const addClause = () => set('clauses', [...form.clauses, {
    clause_type: 'notice', label: '', body: '',
    effective_date: '', deadline_date: '', sort_order: form.clauses.length,
  }]);
  const setClause = (i, k, v) => set('clauses',
    form.clauses.map((c, j) => (j === i ? { ...c, [k]: v } : c)));
  const dropClause = (i) => set('clauses', form.clauses.filter((_, j) => j !== i));

  const toggleModel = (id) => set('cost_model_ids',
    form.cost_model_ids.includes(id)
      ? form.cost_model_ids.filter(x => x !== id)
      : [...form.cost_model_ids, id]);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    // notice_deadline is deliberately absent from this body — the server derives it.
    const body = {
      supplier_id: form.supplier_id ? Number(form.supplier_id) : null,
      reference: form.reference || null,
      term_start: form.term_start || null,
      term_end: form.term_end || null,
      auto_renew: !!form.auto_renew,
      notice_days: form.notice_days === '' ? null : Number(form.notice_days),
      price_review_cadence: form.price_review_cadence || null,
      currency: form.currency || null,
      notes: form.notes || null,
      cost_model_ids: form.cost_model_ids,
      clauses: form.clauses.map((c, i) => ({
        clause_type: c.clause_type,
        label: c.label || null,
        body: c.body || null,
        effective_date: c.effective_date || null,
        deadline_date: c.deadline_date || null,
        sort_order: i,
      })),
    };
    try {
      if (initial.id) {
        await api.put(`/api/contracts/${initial.id}`, body);
        addToast('Contract updated', 'success');
      } else {
        await api.post('/api/contracts', body, { params: { team_id: activeTeamId } });
        addToast('Contract created', 'success');
      }
      onSaved();
    } catch (err) {
      addToast(formatApiError(err) || 'Could not save the contract', 'error');
    } finally { setBusy(false); }
  };

  return (
    <form className="ca-card" style={{ marginTop: 14 }} onSubmit={save}>
      <div className="ca-card-title">{initial.id ? 'Edit contract' : 'New contract'}</div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
        <div>
          <label className="ca-label">Reference</label>
          <input className="ca-input" maxLength={120} value={form.reference}
            placeholder="e.g. SAS-2026-01" onChange={e => set('reference', e.target.value)} />
        </div>
        <div>
          <label className="ca-label">Supplier</label>
          <select className="ca-input" value={form.supplier_id}
            onChange={e => set('supplier_id', e.target.value)}>
            <option value="">—</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div>
          <label className="ca-label">Currency</label>
          <input className="ca-input" maxLength={3} value={form.currency}
            placeholder="EUR" onChange={e => set('currency', e.target.value.toUpperCase())} />
        </div>
        <div>
          <label className="ca-label">Term start</label>
          <input className="ca-input" type="date" value={form.term_start}
            onChange={e => set('term_start', e.target.value)} />
        </div>
        <div>
          <label className="ca-label">Term end</label>
          <input className="ca-input" type="date" value={form.term_end}
            onChange={e => set('term_end', e.target.value)} />
        </div>
        <div>
          <label className="ca-label">Notice period (days)</label>
          <input className="ca-input" type="number" min="0" max="3650" value={form.notice_days}
            onChange={e => set('notice_days', e.target.value)} />
        </div>
        <div>
          {/* Computed, never typed. Locking it is the point: a stored deadline
              that disagrees with the term end and notice period beside it is
              worse than no deadline at all. */}
          <label className="ca-label">Notice deadline</label>
          <input className="ca-input" readOnly disabled
            value={deadline || 'needs a term end + notice period'}
            title="Derived from term end − notice period. Computed by the server on every save; never an input."
            style={{ color: deadline ? 'var(--text)' : 'var(--muted)', cursor: 'not-allowed' }} />
        </div>
        <div>
          <label className="ca-label">Price review</label>
          <select className="ca-input" value={form.price_review_cadence}
            onChange={e => set('price_review_cadence', e.target.value)}>
            {CADENCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 6 }}>
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={form.auto_renew}
              onChange={e => set('auto_renew', e.target.checked)} />
            Auto-renews
          </label>
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <label className="ca-label">Notes</label>
        <textarea className="ca-input" rows={2} value={form.notes}
          onChange={e => set('notes', e.target.value)} />
      </div>

      {/* Covered products. Replace-as-a-block on save, the same convention as
          the weighted-lines editor. */}
      <div style={{ marginTop: 16 }}>
        <div className="ca-card-title" style={{ fontSize: 12 }}>
          Covered products
          <span style={{ fontWeight: 400, color: 'var(--muted)', fontSize: 11 }}>
            {' '}· {form.cost_model_ids.length} selected
          </span>
        </div>
        {costModels.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>No products with a cost model yet.</div>
        ) : (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 6,
            maxHeight: 140, overflowY: 'auto', padding: 2,
          }}>
            {costModels.map(cm => {
              const on = form.cost_model_ids.includes(cm.id);
              return (
                <button key={cm.id} type="button" onClick={() => toggleModel(cm.id)}
                  className={`ca-btn ca-btn-sm ${on ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                  style={{ fontSize: 10 }}>
                  {cm.product_name}{cm.supplier_name ? ` · ${cm.supplier_name}` : ''}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="ca-card-title" style={{ fontSize: 12, margin: 0 }}>Clauses</div>
          <button type="button" className="ca-btn ca-btn-sm ca-btn-ghost" onClick={addClause}>
            + Add clause
          </button>
        </div>
        {form.clauses.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6 }}>
            No clauses recorded. The notice period above is what drives the radar; clauses
            are the supporting detail a negotiator reads.
          </div>
        ) : form.clauses.map((c, i) => (
          <div key={i} className="ca-card" style={{ padding: 12, marginTop: 8, background: 'var(--surface2)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
              <div>
                <label className="ca-label">Type</label>
                <select className="ca-input" value={c.clause_type}
                  onChange={e => setClause(i, 'clause_type', e.target.value)}>
                  {CLAUSE_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </div>
              <div>
                <label className="ca-label">Label</label>
                <input className="ca-input" value={c.label || ''}
                  onChange={e => setClause(i, 'label', e.target.value)} />
              </div>
              <div>
                <label className="ca-label">Effective</label>
                <input className="ca-input" type="date" value={c.effective_date || ''}
                  onChange={e => setClause(i, 'effective_date', e.target.value)} />
              </div>
              <div>
                <label className="ca-label">Deadline</label>
                <input className="ca-input" type="date" value={c.deadline_date || ''}
                  onChange={e => setClause(i, 'deadline_date', e.target.value)} />
              </div>
            </div>
            <div style={{ marginTop: 8 }}>
              <label className="ca-label">Text</label>
              <textarea className="ca-input" rows={2} value={c.body || ''}
                onChange={e => setClause(i, 'body', e.target.value)} />
            </div>
            <div style={{ textAlign: 'right', marginTop: 6 }}>
              <button type="button" className="ca-btn ca-btn-sm ca-btn-ghost"
                style={{ color: 'var(--accent2)', fontSize: 10 }}
                onClick={() => dropClause(i)}>Remove</button>
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="ca-btn ca-btn-primary ca-btn-sm" disabled={busy}>
          {busy ? 'Saving…' : initial.id ? 'Save changes' : 'Create contract'}
        </button>
      </div>
    </form>
  );
}

/* ── Detail ───────────────────────────────────────────────────────────────── */

function ContractDetail({ contract, windows, onEdit, onDelete, canEdit, canDelete, onClose }) {
  const navigate = useNavigate();
  const c = contract;
  return (
    <div className="ca-card" style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
        <div>
          <div className="ca-card-title" style={{ marginBottom: 2 }}>
            {c.reference || 'Untitled contract'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            {c.supplier_name || 'No supplier'}{c.currency ? ` · ${c.currency}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {canEdit && <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={onEdit}>Edit</button>}
          {canDelete && (
            <button className="ca-btn ca-btn-sm ca-btn-ghost"
              style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
              onClick={onDelete}>Delete</button>
          )}
          <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 12, marginTop: 14,
      }}>
        {[
          ['Term', c.term_start || '—', c.term_end ? `to ${c.term_end}` : 'no end date'],
          ['Notice period', c.notice_days === null || c.notice_days === undefined ? '—' : `${c.notice_days}d`,
            c.auto_renew ? 'auto-renews' : 'does not auto-renew'],
          ['Price review', label(CADENCES, c.price_review_cadence), ''],
        ].map(([lbl, val, sub]) => (
          <div key={lbl} className="ca-metric">
            <div className="ca-metric-lbl">{lbl}</div>
            <div className="ca-metric-val" style={{ fontSize: 16 }}>{val}</div>
            {sub && <div style={{ fontSize: 10, color: 'var(--muted)' }}>{sub}</div>}
          </div>
        ))}
        <div className="ca-metric">
          <div className="ca-metric-lbl">Notice deadline</div>
          <div className="ca-metric-val" style={{ fontSize: 16 }}>
            <NoticeChip days={c.days_to_notice} deadline={c.notice_deadline} />
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>term end − notice period</div>
        </div>
      </div>

      {c.notes && (
        <div style={{ fontSize: 11, marginTop: 14, lineHeight: 1.6, color: 'var(--text-secondary)' }}>
          {c.notes}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <div className="ca-card-title" style={{ fontSize: 12 }}>
          Covered products ({c.covered.length})
        </div>
        {c.covered.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            No products linked — the radar cannot attach a notice window to anything.
          </div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {c.covered.map(m => (
              <button key={m.cost_model_id} className="ca-btn ca-btn-sm ca-btn-ghost"
                style={{ fontSize: 10 }}
                onClick={() => navigate(`/portfolio/${m.cost_model_id}`)}>
                {m.product || m.cost_model_id.slice(0, 8)}
                {m.share_pct !== null && m.share_pct !== undefined && (
                  <span style={{ color: 'var(--muted)' }}> · {m.share_pct}%</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {c.clauses.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="ca-card-title" style={{ fontSize: 12 }}>Clauses ({c.clauses.length})</div>
          <table className="ca-table" style={{ fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ width: 110 }}>Type</th>
                <th>Label</th>
                <th style={{ width: 100 }}>Effective</th>
                <th style={{ width: 100 }}>Deadline</th>
              </tr>
            </thead>
            <tbody>
              {c.clauses.map(cl => (
                <tr key={cl.id}>
                  <td style={{ color: 'var(--text-secondary)' }}>{label(CLAUSE_TYPES, cl.clause_type)}</td>
                  <td title={cl.body || undefined}>{cl.label || <span style={{ color: 'var(--muted)' }}>—</span>}</td>
                  <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>{cl.effective_date || '—'}</td>
                  <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>{cl.deadline_date || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* The other half of the cross-link: a clause_deadline window points at
          its contract, and a contract points at the windows it drives. */}
      <div style={{ marginTop: 16 }}>
        <div className="ca-card-title" style={{ fontSize: 12 }}>Windows this contract drives</div>
        {windows.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            None open. A notice window opens when the deadline comes into range on a radar run.
          </div>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11, lineHeight: 1.8 }}>
            {windows.map(w => (
              <li key={w.id}>
                {w.headline}
                <span style={{ color: 'var(--muted)' }}>
                  {' '}· closes {w.closes_on || 'no date'}
                  {w.closes_in_days !== null && w.closes_in_days !== undefined && ` (${w.closes_in_days}d)`}
                </span>
              </li>
            ))}
          </ul>
        )}
        <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ marginTop: 8, fontSize: 10 }}
          onClick={() => navigate('/monitor')}>Open Monitor → Radar</button>
      </div>
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function Contracts() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();

  const [access, setAccess] = useState(null);
  const [contracts, setContracts] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [costModels, setCostModels] = useState([]);
  const [windows, setWindows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!activeTeamId) return;
    setLoading(true);
    const p = { team_id: activeTeamId };
    api.get('/api/contracts/can-access', { params: p })
      .then(({ data: acc }) => {
        setAccess(acc);
        if (!acc.can_view) { setLoading(false); return null; }
        return Promise.all([
          api.get('/api/contracts', { params: p }),
          api.get('/api/suppliers', { params: p }).catch(() => ({ data: [] })),
          api.get('/api/cost-models', { params: p }).catch(() => ({ data: [] })),
          api.get('/api/radar/windows', { params: { ...p, driver: 'clause_deadline' } })
            .catch(() => ({ data: [] })),
        ]).then(([c, s, cm, w]) => {
          setContracts(c.data); setSuppliers(s.data);
          setCostModels(cm.data); setWindows(w.data);
          setLoading(false);
        });
      })
      .catch(err => { addToast(formatApiError(err), 'error'); setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId]);

  useEffect(() => { load(); }, [load]);

  const remove = async (c) => {
    if (!(await confirm({
      title: 'Delete this contract?',
      message: 'Its clauses and product links go with it. Any window it drives closes on the next radar run.',
      confirmLabel: 'Delete', danger: true,
    }))) return;
    try {
      await api.delete(`/api/contracts/${c.id}`);
      addToast('Contract deleted', 'success');
      setSelected(null); load();
    } catch (err) {
      addToast(formatApiError(err) || 'Could not delete', 'error');
    }
  };

  const startEdit = (c) => setEditing({
    id: c.id,
    supplier_id: c.supplier_id ?? '',
    reference: c.reference || '',
    term_start: c.term_start || '',
    term_end: c.term_end || '',
    auto_renew: !!c.auto_renew,
    notice_days: c.notice_days ?? '',
    price_review_cadence: c.price_review_cadence || '',
    currency: c.currency || '',
    notes: c.notes || '',
    cost_model_ids: c.covered.map(m => m.cost_model_id),
    clauses: c.clauses.map(cl => ({ ...cl })),
  });

  if (loading) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Contracts</div>
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      </div>
    );
  }

  // Reached by URL rather than the nav, which hides the entry for these users.
  if (access && !access.can_view) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Contracts</div>
        <div className="ca-card" style={{ padding: 32, textAlign: 'center' }}>
          <div style={{ fontSize: 13, marginBottom: 6 }}>You don't have access to contracts.</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 460, margin: '0 auto', lineHeight: 1.6 }}>
            Contract prices and notice dates sit behind their own permission, separate from
            costing — ask a team owner or admin for the <code>contracts.view</code> permission.
          </div>
        </div>
      </div>
    );
  }

  const windowsFor = (id) => windows.filter(w => w.scope_contract_id === id);

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div className="ca-h1">Contracts</div>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            Ordered by notice deadline — the date after which you can no longer give notice
            is the one hard deadline the radar has, so the soonest sits first.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {contracts.length > 0 && (
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => exportCsv(
              'contracts.csv',
              ['Reference', 'Supplier', 'Term start', 'Term end', 'Auto-renew', 'Notice days',
               'Notice deadline', 'Days to notice', 'Price review', 'Currency', 'Products', 'Clauses'],
              contracts.map(c => [
                c.reference || '', c.supplier_name || '', c.term_start || '', c.term_end || '',
                c.auto_renew ? 'Yes' : 'No', c.notice_days ?? '', c.notice_deadline || '',
                c.days_to_notice ?? '', c.price_review_cadence || '', c.currency || '',
                c.covered.length, c.clauses.length,
              ]),
            )}>Export CSV</button>
          )}
          {access?.can_edit && (
            <button className="ca-btn ca-btn-primary ca-btn-sm"
              onClick={() => { setSelected(null); setEditing({ ...EMPTY }); }}>
              + New contract
            </button>
          )}
        </div>
      </div>

      {editing && (
        <ContractForm
          initial={editing}
          suppliers={suppliers}
          costModels={costModels}
          onCancel={() => setEditing(null)}
          onSaved={() => { setEditing(null); setSelected(null); load(); }}
        />
      )}

      {selected && !editing && (
        <ContractDetail
          contract={selected}
          windows={windowsFor(selected.id)}
          canEdit={!!access?.can_edit}
          canDelete={!!access?.can_delete}
          onEdit={() => startEdit(selected)}
          onDelete={() => remove(selected)}
          onClose={() => setSelected(null)}
        />
      )}

      {contracts.length === 0 && !editing ? (
        <div className="ca-card" style={{ marginTop: 14, padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 13, marginBottom: 8 }}>No contracts yet.</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 520, margin: '0 auto 16px', lineHeight: 1.6 }}>
            A contract is what turns "this price looks high" into "notice is due on the 14th".
            Record the term and notice period and the radar will open a window as the deadline
            comes into range.
          </div>
          {access?.can_edit && (
            <button className="ca-btn ca-btn-primary" onClick={() => setEditing({ ...EMPTY })}>
              Add your first contract
            </button>
          )}
        </div>
      ) : contracts.length > 0 && (
        <div className="ca-card" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
          <table className="ca-table" style={{ marginBottom: 0 }}>
            <thead>
              <tr>
                <th>Reference</th>
                <th>Supplier</th>
                <th style={{ width: 170 }}>Notice deadline</th>
                <th style={{ width: 150 }}>Term</th>
                <th style={{ width: 90, textAlign: 'right' }}>Products</th>
                <th style={{ width: 80, textAlign: 'right' }}>Clauses</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map(c => (
                <tr key={c.id} onClick={() => { setEditing(null); setSelected(c); }}
                  tabIndex={0} style={{ cursor: 'pointer' }}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(null); setSelected(c); } }}>
                  <td>
                    {c.reference || <span style={{ color: 'var(--muted)' }}>untitled</span>}
                    {c.auto_renew && (
                      <span className="ca-badge" title="Renews automatically unless notice is given"
                        style={{ marginLeft: 6, background: 'var(--surface2)', color: 'var(--text-secondary)' }}>
                        auto
                      </span>
                    )}
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>{c.supplier_name || '—'}</td>
                  <td><NoticeChip days={c.days_to_notice} deadline={c.notice_deadline} /></td>
                  <td style={{ fontSize: 11, color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                    {c.term_start || '?'} → {c.term_end || '?'}
                  </td>
                  <td style={{ textAlign: 'right' }}>{c.covered.length}</td>
                  <td style={{ textAlign: 'right' }}>{c.clauses.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
