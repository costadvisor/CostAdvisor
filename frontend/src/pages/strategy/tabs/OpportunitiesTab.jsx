import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import api, { formatApiError } from '../../../api';
import {
  Panel, EmptyState, ErrorState, LoadingPanel, useApi, GEMSTONES, gemstoneColor,
} from '../../../components/intel';
import { BubbleQuadrant } from '../../../components/charts';
import { useToast } from '../../../components/Toast';
import { useConfirm } from '../../../components/ConfirmDialog';
import OppObjectives from './OppObjectives';
import LeverRow from './LeverRow';
import OppCustomLeverDialog from './OppCustomLeverDialog';
import {
  LEVER_STATUSES, leverKey, isPlotted, strategyUrl, useTeamMembers, plural,
} from './OppShared';
import '../../../styles/strategy-opps.css';

/* Strategy › category › Opportunities.
 * Objectives (pre-filled from the playbook) → filters → Impact vs Ease chart →
 * the levers grouped by the 8 gemstones. Every edit is the team's overlay on
 * the playbook: saved at once, shown at once, rolled back if the save fails. */

const NO_FILTERS = { gem: '', applies: '', status: '', objective: '' };
const enc = encodeURIComponent;
const sameCodes = (a, b) => a.length === b.length && a.every((c, i) => c === b[i]);

function withField(l, field, value, objNames) {
  const next = { ...l };
  if (field === 'objectives') {
    next.objectives = value.map((code) => ({ code, name: objNames[code] || code }));
  } else {
    next[field] = value;
  }
  next.plotted = isPlotted(next);
  return next;
}

// An authored lever's field set back to the playbook value is sent as null, so
// the API drops the override instead of storing a copy of the default.
function sameAsDefault(l, field, value) {
  const d = l.defaults;
  if (!d || !(field in d)) return value === null || value === undefined;
  if (field === 'notes') return (value || '') === (d.notes || '');
  return (value ?? null) === (d[field] ?? null);
}

export default function OpportunitiesTab({ slug, teamId }) {
  const { addToast } = useToast();
  const confirm = useConfirm();
  const [, setSearchParams] = useSearchParams();
  const params = useMemo(() => ({ team_id: teamId }), [teamId]);
  const ready = !!(slug && teamId);
  const cat = useApi(ready ? strategyUrl(`/categories/${enc(slug)}`) : null, params);
  const lv = useApi(ready ? strategyUrl(`/categories/${enc(slug)}/levers`) : null, params, { keepPrevious: true });
  const members = useTeamMembers(teamId);
  const reloadLevers = lv.reload;

  const [levers, setLevers] = useState(null);
  useEffect(() => {
    if (lv.data && lv.data.playbook_slug === slug) setLevers(lv.data.levers || []);
  }, [lv.data, slug]);
  useEffect(() => { setLevers(null); }, [slug, teamId]);

  const objectives = cat.data?.objectives;
  // The category page says a strategy must be started before the team's
  // values are saved; until then the playbook reads as authored.
  const readOnly = !!cat.data && cat.data.playbook_slug === slug && !cat.data.adopted;
  const objList = useMemo(() => (objectives || []).map((o) => ({ code: o.code, type: o.type, desc: o.desc })), [objectives]);
  const objNames = useMemo(() => Object.fromEntries(objList.map((o) => [o.code, o.type])), [objList]);

  const [selObj, setSelObj] = useState([]);
  const onSelectedChange = useCallback((codes) => setSelObj((p) => (sameCodes(p, codes) ? p : codes)), []);
  const [filters, setFilters] = useState(NO_FILTERS);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [selKey, setSelKey] = useState(null);
  const [scrollTo, setScrollTo] = useState(null);
  const [adding, setAdding] = useState(false);
  const pendingSelect = useRef(null);
  const seq = useRef({});

  const selSet = useMemo(() => new Set(selObj), [selObj]);
  const matchesObjectives = useCallback(
    (l) => selSet.size > 0 && (l.objectives || []).some((o) => selSet.has(o.code)),
    [selSet],
  );
  const passes = useCallback((l) => {
    if (filters.gem && l.gemstone !== filters.gem) return false;
    if (filters.applies === 'yes' && !l.applies) return false;
    if (filters.applies === 'no' && l.applies) return false;
    if (filters.status && l.status !== filters.status) return false;
    if (filters.objective && !(l.objectives || []).some((o) => o.code === filters.objective)) return false;
    return true;
  }, [filters]);
  const filtering = Object.values(filters).some(Boolean);

  const gemMeta = useMemo(() => {
    const api8 = lv.data?.gemstones || [];
    return GEMSTONES.map((g) => ({ code: g.code, name: api8.find((x) => x.code === g.code)?.name || g.name }));
  }, [lv.data]);

  const groups = useMemo(() => {
    if (!levers) return [];
    return gemMeta.map((g) => {
      const all = levers.filter((l) => l.gemstone === g.code);
      const items = all.filter(passes);
      return { ...g, all, items, match: items.some(matchesObjectives) };
    }).filter((g) => g.items.length);
  }, [levers, gemMeta, passes, matchesObjectives]);

  // Scroll a lever into view once it is rendered (bubble click, new custom lever).
  useEffect(() => {
    if (!scrollTo) return undefined;
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(`so-lever-${scrollTo}`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setScrollTo(null);
    });
    return () => cancelAnimationFrame(raf);
  }, [scrollTo, groups]);

  const focusLever = useCallback((l) => {
    const key = leverKey(l);
    setSelKey(key);
    if (!passes(l)) setFilters(NO_FILTERS);
    setCollapsed((c) => {
      if (!c.has(l.gemstone)) return c;
      const n = new Set(c);
      n.delete(l.gemstone);
      return n;
    });
    setScrollTo(String(l.lever_id ?? l.custom_lever_id));
  }, [passes]);

  // After a reload that follows "add custom opportunity", select the new lever.
  useEffect(() => {
    if (!levers || !pendingSelect.current) return;
    const l = levers.find((x) => x.custom_lever_id === pendingSelect.current);
    if (l) { pendingSelect.current = null; focusLever(l); }
  }, [levers, focusLever]);

  const patchLever = useCallback(async (lever, field, value) => {
    const key = leverKey(lever);
    const prevVal = field === 'objectives' ? (lever.objectives || []).map((o) => o.code) : lever[field];
    setLevers((ls) => ls.map((l) => (leverKey(l) === key ? withField(l, field, value, objNames) : l)));
    // One sequence per lever *and field*: a late answer never overwrites a
    // newer edit of the same field, and edits of other fields stay as typed.
    const sk = `${key}|${field}`;
    const mine = (seq.current[sk] || 0) + 1;
    seq.current[sk] = mine;
    const body = lever.custom
      ? { [field]: value }
      : { [field]: sameAsDefault(lever, field, value) ? null : value };
    const url = lever.custom
      ? strategyUrl(`/custom-levers/${lever.custom_lever_id}`)
      : strategyUrl(`/levers/${lever.lever_id}`);
    try {
      const { data } = await api.put(url, body, { params });
      if (seq.current[sk] === mine) {
        setLevers((ls) => ls.map((l) => {
          if (leverKey(l) !== key) return l;
          const merged = {
            ...l,
            [field]: data[field],
            overridden: data.overridden,
            actions_count: data.actions_count,
            number: data.number,
          };
          merged.plotted = isPlotted(merged);
          return merged;
        }));
      }
    } catch (err) {
      setLevers((ls) => ls.map((l) => {
        if (leverKey(l) !== key) return l;
        const cur = field === 'objectives' ? (l.objectives || []).map((o) => o.code) : l[field];
        return JSON.stringify(cur) === JSON.stringify(value) ? withField(l, field, prevVal, objNames) : l;
      }));
      addToast(`Change not saved: ${formatApiError(err)}`, 'error');
    }
  }, [params, objNames, addToast]);

  const resetLever = useCallback(async (lever) => {
    const ok = await confirm({
      title: 'Reset to the playbook?',
      message: `#${lever.number} goes back to the playbook's applies, ease, impact, status and notes. Your value and notes on it are cleared.`,
      confirmLabel: 'Reset',
    });
    if (!ok) return;
    const key = leverKey(lever);
    Object.keys(seq.current).forEach((k) => { if (k.startsWith(`${key}|`)) seq.current[k] += 1; });
    try {
      const { data } = await api.put(strategyUrl(`/levers/${lever.lever_id}`), {
        applies: null, ease: null, savings_score: null, savings_value: null, status: null, notes: null,
      }, { params });
      setLevers((ls) => ls.map((l) => (leverKey(l) === key ? data : l)));
      addToast(`#${lever.number} reset to the playbook`, 'success');
    } catch (err) {
      addToast(`Reset failed: ${formatApiError(err)}`, 'error');
    }
  }, [confirm, params, addToast]);

  const deleteLever = useCallback(async (lever) => {
    const ok = await confirm({
      title: 'Delete this custom opportunity?',
      message: `"${lever.title}" is removed. Actions linked to it stay, unlinked.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(strategyUrl(`/custom-levers/${lever.custom_lever_id}`), { params });
      addToast('Custom opportunity deleted', 'success');
      reloadLevers();
    } catch (err) {
      addToast(`Delete failed: ${formatApiError(err)}`, 'error');
    }
  }, [confirm, params, addToast, reloadLevers]);

  const addAction = useCallback(async (lever, body) => {
    try {
      await api.post(strategyUrl(`/categories/${enc(slug)}/actions`), body, { params });
    } catch (err) {
      throw formatApiError(err);
    }
    const key = leverKey(lever);
    setLevers((ls) => ls.map((l) => (leverKey(l) === key ? { ...l, actions_count: (l.actions_count || 0) + 1 } : l)));
    addToast(`Action added to #${lever.number}. It is listed under Actions Management.`, 'success');
  }, [slug, params, addToast]);

  const viewActions = useCallback(() => {
    setSearchParams((p) => {
      const n = new URLSearchParams(p);
      n.set('tab', 'actions');
      return n;
    }, { replace: true });
    window.scrollTo({ top: 0 });
  }, [setSearchParams]);

  const addCustom = async (body) => {
    let data;
    try {
      ({ data } = await api.post(strategyUrl(`/categories/${enc(slug)}/levers`), body, { params }));
    } catch (err) {
      throw formatApiError(err);
    }
    setAdding(false);
    pendingSelect.current = data.custom_lever_id;
    addToast(`Custom opportunity #${data.number} added`, 'success');
    reloadLevers();
  };

  const toggleGroup = (code) => setCollapsed((c) => {
    const n = new Set(c);
    if (n.has(code)) n.delete(code); else n.add(code);
    return n;
  });

  if (!teamId) return <EmptyState title="No team selected" body="Pick a team to see its opportunities." />;

  const plotted = (levers || []).filter(isPlotted);
  const visibleKeys = new Set(groups.flatMap((g) => g.items.map(leverKey)));
  const points = plotted.map((l) => ({
    id: leverKey(l), number: l.number, x: l.ease, y: l.savings_score, gemstone: l.gemstone, title: l.title,
  }));
  const dimIds = filtering ? plotted.map(leverKey).filter((k) => !visibleKeys.has(k)) : undefined;
  const highlightIds = selSet.size ? plotted.filter(matchesObjectives).map(leverKey) : undefined;
  const leverByKey = new Map((levers || []).map((l) => [leverKey(l), l]));
  const shown = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="so-tab ix-stack">
      {cat.error ? (
        <ErrorState title="Could not load the objectives" error={cat.error} onRetry={cat.reload} />
      ) : !objectives ? (
        <LoadingPanel lines={6} />
      ) : (
        <OppObjectives slug={slug} teamId={teamId} objectives={objectives} onSelectedChange={onSelectedChange}
          readOnly={readOnly} />
      )}

      <div className="so-toolbar" role="group" aria-label="Filter opportunities">
        <select className="ix-select" aria-label="Gemstone category" value={filters.gem}
          onChange={(e) => setFilters((f) => ({ ...f, gem: e.target.value }))}>
          <option value="">All Gemstone categories</option>
          {gemMeta.map((g) => <option key={g.code} value={g.code}>{g.name}</option>)}
        </select>
        <select className="ix-select" aria-label="Applies" value={filters.applies}
          onChange={(e) => setFilters((f) => ({ ...f, applies: e.target.value }))}>
          <option value="">Applies: all</option>
          <option value="yes">Applies: yes</option>
          <option value="no">Applies: no</option>
        </select>
        <select className="ix-select" aria-label="Status" value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}>
          <option value="">Status: all</option>
          {LEVER_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="ix-select" aria-label="Objective" value={filters.objective}
          onChange={(e) => setFilters((f) => ({ ...f, objective: e.target.value }))}>
          <option value="">Objective: all</option>
          {objList.map((o) => <option key={o.code} value={o.code}>{o.type}</option>)}
        </select>
        {filtering && (
          <button type="button" className="ca-btn-link so-small-link" onClick={() => setFilters(NO_FILTERS)}>
            Clear filters
          </button>
        )}
        <span className="ix-spacer" />
        {levers && filtering && (
          <span className="ix-muted ix-small">{`${shown} of ${levers.length} opportunities`}</span>
        )}
        {!readOnly && (
          <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setAdding(true)}
            disabled={!levers || !cat.data}>
            + Add custom opportunity
          </button>
        )}
      </div>

      {lv.error && !levers ? (
        <ErrorState title="Could not load the opportunities" error={lv.error} onRetry={lv.reload} />
      ) : !levers ? (
        <>
          <LoadingPanel chart height={300} />
          <LoadingPanel lines={4} />
        </>
      ) : !levers.length ? (
        <EmptyState
          title="No opportunities authored for this category"
          body="The playbook carries no levers. Add the team's own with “+ Add custom opportunity”."
        />
      ) : (
        <>
          <Panel
            title="Impact vs. ease"
            caption={`${plotted.length} plotted (opportunities that don't apply are not plotted) · numbers match the list below`}
          >
            <div className="so-chart">
              <BubbleQuadrant
                points={points}
                selectedId={selKey}
                highlightIds={highlightIds}
                dimIds={dimIds}
                onSelect={(id) => { const l = leverByKey.get(id); if (l) focusLever(l); }}
              />
            </div>
            {(highlightIds?.length > 0 || filtering) && (
              <p className="so-chart-note">
                {highlightIds?.length > 0 && 'Dashed ring: serves a selected objective. '}
                {filtering && 'Faded: hidden by the filters. '}
                Click a bubble to open its opportunity.
              </p>
            )}
          </Panel>

          {groups.length === 0 ? (
            <EmptyState
              variant="compact"
              title="No opportunities match these filters"
              action={<button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>}
            />
          ) : groups.map((g) => {
            const open = !collapsed.has(g.code);
            const applying = g.items.filter((l) => l.applies).length;
            return (
              <section key={g.code} className="so-group" style={{ '--gem': gemstoneColor(g.code) }}
                aria-label={g.name}>
                <button type="button" className={`so-group-head${g.match ? ' is-match' : ''}`} aria-expanded={open}
                  onClick={() => toggleGroup(g.code)}>
                  <span className="so-gem-dot" aria-hidden />
                  <span className="so-group-name">{g.name}</span>
                  <span className="so-group-count">
                    {`— ${plural(g.items.length, 'lever')}`}
                    {g.items.length !== g.all.length && ` of ${g.all.length}`}
                    {` (${applying} applying)`}
                  </span>
                  {g.match && <span className="so-group-match">· matches your objectives</span>}
                  <span className="ix-spacer" />
                  <span className={`so-chev${open ? ' is-open' : ''}`} aria-hidden>▾</span>
                </button>
                {open && (
                  <div className="so-group-body">
                    {g.items.map((l) => (
                      <LeverRow
                        key={leverKey(l)}
                        lever={l}
                        matches={matchesObjectives(l)}
                        selected={selKey === leverKey(l)}
                        objectives={objList}
                        members={members}
                        onPatch={patchLever}
                        onReset={resetLever}
                        onDelete={deleteLever}
                        onAddAction={addAction}
                        onViewActions={viewActions}
                        readOnly={readOnly}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </>
      )}

      {adding && (
        <OppCustomLeverDialog
          objectives={objList}
          defaultGemstone={filters.gem || undefined}
          onSubmit={addCustom}
          onClose={() => setAdding(false)}
        />
      )}
    </div>
  );
}
