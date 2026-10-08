import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, { formatApiError } from '../../../api';
import {
  EmptyState, ErrorState, LoadingPanel, KpiTile, useApi, GEMSTONES, gemstoneColor, fmt,
} from '../../../components/intel';
import { useToast } from '../../../components/Toast';
import { useConfirm } from '../../../components/ConfirmDialog';
import ActionForm from './ActionForm';
import ActionTimeline, { actionWindow } from './ActionTimeline';
import OppDialog from './OppDialog';
import {
  ACTION_STATUSES, countActions, isOverdue, statusTone, strategyUrl, todayIso, useTeamMembers, plural,
} from './OppShared';
import '../../../styles/strategy-opps.css';

/* Strategy › category › Actions Management.
 * The team's actions on this category's opportunities, grouped gemstone →
 * opportunity like the mockup, as a table or a timeline. Status and %
 * complete edit in place; the rest through the add / edit dialog. */

const enc = encodeURIComponent;
const NO_FILTERS = { status: '', assignee: '', gem: '' };
const NONE = 'none';
const leverKeyOf = (a) => (a.lever_id != null ? `a${a.lever_id}` : a.custom_lever_id ? `c${a.custom_lever_id}` : NONE);

function PctCell({ value, onCommit, label, disabled }) {
  const [draft, setDraft] = useState(null);
  const commit = () => {
    if (draft === null) return;
    const n = Math.max(0, Math.min(100, Math.round(Number(draft))));
    setDraft(null);
    if (Number.isFinite(n) && n !== value) onCommit(n);
  };
  return (
    <span className="so-pct">
      <span className="so-pct-track" aria-hidden><span className="so-pct-fill" style={{ width: `${value || 0}%` }} /></span>
      <input type="number" min={0} max={100} step={5} className="ix-input so-pct-input" aria-label={label}
        disabled={disabled}
        value={draft ?? value ?? 0} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
          if (e.key === 'Escape') setDraft(null);
        }} />
      <span className="so-pct-unit">%</span>
    </span>
  );
}

function ActionTable({ actions, today, onPatch, onEdit, onDelete, readOnly }) {
  return (
    <div className="ix-table-wrap">
      <table className="ix-table so-atable">
        <colgroup>
          <col />
          <col className="so-c-assignee" />
          <col className="so-c-date" />
          <col className="so-c-due" />
          <col className="so-c-status" />
          <col className="so-c-pct" />
          <col className="so-c-ops" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Title</th>
            <th scope="col">Assignee</th>
            <th scope="col">Start</th>
            <th scope="col">Due date</th>
            <th scope="col">Status</th>
            <th scope="col">% complete</th>
            <th scope="col"><span className="ca-sr-only">Edit or delete</span></th>
          </tr>
        </thead>
        <tbody>
          {actions.map((a) => {
            const overdue = isOverdue(a, today);
            return (
              <tr key={a.id} className={overdue ? 'is-overdue' : undefined}>
                <td>
                  <div className="so-a-title">{a.title}</div>
                  {a.description && <div className="so-a-desc" title={a.description}>{a.description}</div>}
                </td>
                <td>{a.assignee?.name || <span className="ix-muted">Unassigned</span>}</td>
                <td className="ix-mono">{a.start_date ? fmt.date(a.start_date) : <span className="ix-muted">—</span>}</td>
                <td className={`ix-mono${overdue ? ' so-due-overdue' : ''}`}>
                  {a.due_date ? fmt.date(a.due_date) : <span className="ix-muted">—</span>}
                  {overdue && <span className="so-overdue-tag">Overdue</span>}
                </td>
                <td>
                  <select className={`ix-select so-status tone-${statusTone(a.status)}`} value={a.status}
                    disabled={readOnly} aria-label={`Status of ${a.title}`} onChange={(e) => onPatch(a, { status: e.target.value })}>
                    {ACTION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
                <td>
                  <PctCell value={a.pct_complete} label={`% complete of ${a.title}`} disabled={readOnly}
                    onCommit={(n) => onPatch(a, { pct_complete: n })} />
                </td>
                <td className="so-ops">
                  {!readOnly && (
                    <>
                      <button type="button" className="ca-btn-link so-small-link" onClick={() => onEdit(a)}>Edit</button>
                      <button type="button" className="ca-btn-link so-small-link so-danger-link" onClick={() => onDelete(a)}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ActionsTab({ slug, teamId }) {
  const { addToast } = useToast();
  const confirm = useConfirm();
  const params = useMemo(() => ({ team_id: teamId }), [teamId]);
  const ready = !!(slug && teamId);
  const res = useApi(ready ? strategyUrl(`/categories/${enc(slug)}/actions`) : null, params, { keepPrevious: true });
  const lv = useApi(ready ? strategyUrl(`/categories/${enc(slug)}/levers`) : null, params);
  const members = useTeamMembers(teamId);
  const reload = res.reload;
  // Writes wait until the team has started the strategy (the category page says so).
  const cat = useApi(ready ? strategyUrl(`/categories/${enc(slug)}`) : null, params);
  const readOnly = !!cat.data && cat.data.playbook_slug === slug && !cat.data.adopted;

  const [actions, setActions] = useState(null);
  useEffect(() => {
    if (res.data && res.data.playbook_slug === slug) setActions(res.data.actions || []);
  }, [res.data, slug]);
  useEffect(() => { setActions(null); }, [slug, teamId]);
  const today = res.data?.today || todayIso();

  const [filters, setFilters] = useState(NO_FILTERS);
  const [view, setView] = useState('table');
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [dialog, setDialog] = useState(null);
  const seq = useRef({});

  const counts = useMemo(() => countActions(actions || [], today), [actions, today]);
  const filtering = Object.values(filters).some(Boolean);

  const assignees = useMemo(() => {
    const m = new Map();
    (actions || []).forEach((a) => { if (a.assignee) m.set(a.assignee.id, a.assignee.name); });
    return [...m.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [actions]);
  const hasUnassigned = (actions || []).some((a) => !a.assignee);
  const hasUnlinked = (actions || []).some((a) => !a.lever);

  const groups = useMemo(() => {
    if (!actions) return [];
    const list = actions.filter((a) => {
      if (filters.status === 'overdue') { if (!isOverdue(a, today)) return false; }
      else if (filters.status && a.status !== filters.status) return false;
      if (filters.assignee === NONE) { if (a.assignee) return false; }
      else if (filters.assignee && a.assignee?.id !== filters.assignee) return false;
      if (filters.gem && (a.lever?.gemstone || NONE) !== filters.gem) return false;
      return true;
    });
    const buckets = [...GEMSTONES.map((g) => ({ code: g.code, name: g.name })), { code: NONE, name: 'Not linked to an opportunity' }];
    return buckets.map((g) => {
      const items = list.filter((a) => (a.lever?.gemstone || NONE) === g.code);
      const byLever = new Map();
      items.forEach((a) => {
        const k = leverKeyOf(a);
        if (!byLever.has(k)) byLever.set(k, { key: k, lever: a.lever, items: [] });
        byLever.get(k).items.push(a);
      });
      const levers = [...byLever.values()].sort((x, y) => (x.lever?.number ?? 9999) - (y.lever?.number ?? 9999));
      return { ...g, items, levers, overdue: items.filter((a) => isOverdue(a, today)).length };
    }).filter((g) => g.items.length);
  }, [actions, filters, today]);

  const win = useMemo(() => actionWindow(actions || [], today), [actions, today]);

  const toggle = (k) => setCollapsed((c) => {
    const n = new Set(c);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });

  const patchAction = useCallback(async (a, patch) => {
    const prev = Object.fromEntries(Object.keys(patch).map((k) => [k, a[k]]));
    const apply = (x, p) => ({ ...x, ...p });
    setActions((list) => list.map((x) => (x.id === a.id ? apply(x, patch) : x)));
    // One sequence per action and field set: a late answer never overwrites a newer edit.
    const sk = `${a.id}|${Object.keys(patch).sort().join(',')}`;
    const mine = (seq.current[sk] || 0) + 1;
    seq.current[sk] = mine;
    try {
      const { data } = await api.put(strategyUrl(`/actions/${a.id}`), patch, { params });
      if (seq.current[sk] === mine) {
        const fields = [...Object.keys(patch), 'overdue', 'updated_at'];
        setActions((list) => list.map((x) => (
          x.id === a.id ? { ...x, ...Object.fromEntries(fields.map((k) => [k, data[k]])) } : x)));
      }
    } catch (err) {
      setActions((list) => list.map((x) => {
        if (x.id !== a.id) return x;
        const still = Object.keys(patch).every((k) => x[k] === patch[k]);
        return still ? apply(x, prev) : x;
      }));
      addToast(`Change not saved: ${formatApiError(err)}`, 'error');
    }
  }, [params, addToast]);

  const deleteAction = useCallback(async (a) => {
    const ok = await confirm({
      title: 'Delete this action?',
      message: `"${a.title}" is deleted for the whole team. This cannot be undone.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(strategyUrl(`/actions/${a.id}`), { params });
      setActions((list) => list.filter((x) => x.id !== a.id));
      addToast('Action deleted', 'success');
    } catch (err) {
      addToast(`Delete failed: ${formatApiError(err)}`, 'error');
    }
  }, [confirm, params, addToast]);

  const submitDialog = async (body) => {
    try {
      if (dialog.mode === 'edit') {
        await api.put(strategyUrl(`/actions/${dialog.action.id}`), body, { params });
      } else {
        await api.post(strategyUrl(`/categories/${enc(slug)}/actions`), body, { params });
      }
    } catch (err) {
      throw formatApiError(err);
    }
    addToast(dialog.mode === 'edit' ? 'Action saved' : 'Action added', 'success');
    setDialog(null);
    reload();
  };

  const onEdit = useCallback((a) => setDialog({ mode: 'edit', action: a }), []);

  if (!teamId) return <EmptyState title="No team selected" body="Pick a team to see its actions." />;

  const renderList = (items) => (view === 'table' ? (
    <ActionTable actions={items} today={today} onPatch={patchAction} onEdit={onEdit} onDelete={deleteAction}
      readOnly={readOnly} />
  ) : (
    <ActionTimeline actions={items} win={win} today={today} onEdit={onEdit} onDelete={deleteAction}
      readOnly={readOnly} />
  ));

  return (
    <div className="so-tab ix-stack">
      {actions && (
        <div className="ix-kpis so-kpis" aria-label="Action counts">
          <KpiTile label="Actions" value={counts.total} />
          <KpiTile label="Not started" value={counts.not_started} />
          <KpiTile label="In progress" value={counts.in_progress} tone={counts.in_progress ? 'info' : undefined} />
          <KpiTile label="Blocked" value={counts.blocked} tone={counts.blocked ? 'bad' : undefined} />
          <KpiTile label="Done" value={counts.done} tone={counts.done ? 'good' : undefined} />
          <KpiTile label="Overdue" value={counts.overdue} tone={counts.overdue ? 'bad' : undefined}
            sub={`Due before ${fmt.date(today)}`} />
        </div>
      )}

      <div className="so-toolbar" role="group" aria-label="Filter actions">
        <div className="so-seg" role="radiogroup" aria-label="View">
          {[['table', 'Table'], ['timeline', 'Gantt']].map(([id, label]) => (
            <button key={id} type="button" role="radio" aria-checked={view === id}
              className={`so-seg-btn${view === id ? ' is-on' : ''}`} onClick={() => setView(id)}>{label}</button>
          ))}
        </div>
        <select className="ix-select" aria-label="Status" value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}>
          <option value="">Status: all</option>
          {ACTION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          <option value="overdue">Overdue</option>
        </select>
        <select className="ix-select" aria-label="Assignee" value={filters.assignee}
          onChange={(e) => setFilters((f) => ({ ...f, assignee: e.target.value }))}>
          <option value="">Assignee: all</option>
          {assignees.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          {hasUnassigned && <option value={NONE}>Unassigned</option>}
        </select>
        <select className="ix-select" aria-label="Gemstone category" value={filters.gem}
          onChange={(e) => setFilters((f) => ({ ...f, gem: e.target.value }))}>
          <option value="">Gemstone: all</option>
          {GEMSTONES.map((g) => <option key={g.code} value={g.code}>{g.name}</option>)}
          {hasUnlinked && <option value={NONE}>Not linked</option>}
        </select>
        {filtering && (
          <button type="button" className="ca-btn-link so-small-link" onClick={() => setFilters(NO_FILTERS)}>
            Clear filters
          </button>
        )}
        <span className="ix-spacer" />
        {!readOnly && (
          <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setDialog({ mode: 'add' })}
            disabled={!actions || !cat.data}>
            + Add action
          </button>
        )}
      </div>

      {res.error && !actions ? (
        <ErrorState title="Could not load the actions" error={res.error} onRetry={reload} />
      ) : !actions ? (
        <LoadingPanel lines={5} />
      ) : !actions.length ? (
        <EmptyState
          title="No actions yet"
          body={readOnly
            ? 'Actions turn opportunities into work: an owner, dates and progress. Start the strategy to add them.'
            : 'Actions turn opportunities into work: an owner, dates and progress. Add one here, or from an opportunity on the Opportunities tab.'}
          action={readOnly ? null : (
            <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setDialog({ mode: 'add' })}>+ Add action</button>
          )}
        />
      ) : !groups.length ? (
        <EmptyState
          variant="compact"
          title="No actions match these filters"
          action={<button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>}
        />
      ) : groups.map((g) => {
        const open = !collapsed.has(`g:${g.code}`);
        const nOpps = g.levers.filter((x) => x.key !== NONE).length;
        return (
          <section key={g.code} className="so-group" style={{ '--gem': g.code === NONE ? 'var(--muted)' : gemstoneColor(g.code) }}
            aria-label={g.name}>
            <button type="button" className="so-group-head" aria-expanded={open} onClick={() => toggle(`g:${g.code}`)}>
              <span className="so-gem-dot" aria-hidden />
              <span className="so-group-name">{g.name}</span>
              <span className="so-group-count">
                {'— '}
                {g.code !== NONE && `${plural(nOpps, 'opportunity', 'opportunities')} · `}
                {plural(g.items.length, 'action')}
                {g.overdue > 0 && <span className="so-overdue-text">{` · ${g.overdue} overdue`}</span>}
              </span>
              <span className="ix-spacer" />
              <span className={`so-chev${open ? ' is-open' : ''}`} aria-hidden>▾</span>
            </button>
            {open && (
              <div className="so-group-body so-nested">
                {g.code === NONE ? (
                  <div className="so-apanel">{renderList(g.items)}</div>
                ) : g.levers.map((lg) => {
                  const lopen = !collapsed.has(`l:${lg.key}`);
                  const lOverdue = lg.items.filter((a) => isOverdue(a, today)).length;
                  return (
                    <div key={lg.key} className="so-lgroup">
                      <button type="button" className="so-lgroup-head" aria-expanded={lopen}
                        onClick={() => toggle(`l:${lg.key}`)}>
                        <span className="ix-num-bubble so-lever-num" aria-hidden>{lg.lever?.number}</span>
                        <span className="so-lgroup-title">{lg.lever?.title}</span>
                        {lg.lever?.custom && <span className="so-flag is-custom">Custom</span>}
                        <span className="so-group-count">
                          {`— ${plural(lg.items.length, 'action')}`}
                          {lOverdue > 0 && <span className="so-overdue-text">{` · ${lOverdue} overdue`}</span>}
                        </span>
                        <span className="ix-spacer" />
                        <span className={`so-chev${lopen ? ' is-open' : ''}`} aria-hidden>▾</span>
                      </button>
                      {lopen && <div className="so-apanel">{renderList(lg.items)}</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}

      {dialog && (
        <OppDialog title={dialog.mode === 'edit' ? 'Edit action' : 'Add action'} onClose={() => setDialog(null)}>
          <ActionForm
            levers={lv.data?.levers || []}
            members={members}
            initial={dialog.mode === 'edit' ? dialog.action : undefined}
            submitLabel={dialog.mode === 'edit' ? 'Save changes' : 'Add action'}
            onCancel={() => setDialog(null)}
            onSubmit={submitDialog}
          />
        </OppDialog>
      )}
    </div>
  );
}
