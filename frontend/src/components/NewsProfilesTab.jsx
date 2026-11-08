import { useState, useEffect, useCallback, useMemo } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from './Toast';
import { useConfirm } from './ConfirmDialog';
import { RunsView, SourcesView } from './NewsSweepViews';

/**
 * News search profiles (Wave 4, SCRUM-213 / NEWS-1) — a Curation tab.
 *
 * A profile is the recipe the monthly news search uses for one family: the
 * names it goes by, producers to watch, regions, event types, trusted and
 * blocked sites, and words that mark noise. Product lines can override it; an
 * override only stores its differences (lists add to the family's, single
 * values replace them, blanks inherit).
 *
 * Two Claude actions, neither of which saves anything:
 *  - "Draft with Claude" fills the form from what the platform already knows.
 *  - "Test this profile" shows what the *current form* would find right now.
 * The editor saves explicitly, which records the profile as theirs.
 */

const PROVENANCE = {
  ai_draft: { label: 'AI draft', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  human_edited: { label: 'Analyst edited', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  human_approved: { label: 'Analyst approved', color: 'var(--accent)', bg: 'var(--success-bg)' },
  imported: { label: 'Imported', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
};

const EVENT_TYPES = [
  ['new_plant', 'New plant'],
  ['capacity_change', 'Capacity change'],
  ['new_supplier', 'New supplier'],
  ['technology', 'Technology'],
  ['closure', 'Closure'],
  ['regulation', 'Regulation'],
  ['m_and_a', 'M&A'],
];
const EVENT_LABEL = Object.fromEntries([...EVENT_TYPES, ['other', 'Other']]);

const DROP_REASON = {
  link_not_in_search_results: 'Link not in search results',
  blocked_site: 'Blocked site',
  excluded_term: 'Excluded term',
  outside_time_window: 'Too old',
  event_type_not_wanted: 'Event type not wanted',
  duplicate: 'Duplicate',
  incomplete: 'Incomplete',
};

const DATE_SOURCE = {
  reported: null,
  search_result: 'date from search result',
};

const LIST_FIELDS = ['terms', 'synonyms', 'cas_numbers', 'exclude_terms', 'preferred_sites', 'blocked_sites'];
const DEFAULTS = { language: 'en', min_relevance: 0.6, lookback_days: 31 };
const mono = { fontFamily: "'JetBrains Mono', monospace" };

function emptyForm() {
  return {
    terms: [], synonyms: [], cas_numbers: [], exclude_terms: [], preferred_sites: [], blocked_sites: [],
    event_types: [], producers: [], regions: [], language: '', min_relevance: '', lookback_days: '',
    is_active: false, owner_editor_id: null, notes: '',
  };
}

function formFromProfile(p) {
  if (!p) return emptyForm();
  return {
    ...emptyForm(),
    ...Object.fromEntries(LIST_FIELDS.map(f => [f, p[f] || []])),
    event_types: p.event_types || [],
    producers: p.producers || [],
    regions: p.regions || [],
    language: p.language || '',
    min_relevance: p.min_relevance ?? '',
    lookback_days: p.lookback_days ?? '',
    is_active: !!p.is_active,
    owner_editor_id: p.owner_editor_id || null,
    notes: p.notes || '',
  };
}

function payloadFromForm(f) {
  const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v));
  return {
    ...Object.fromEntries(LIST_FIELDS.map(k => [k, f[k]])),
    event_types: f.event_types,
    producer_ids: f.producers.map(p => p.id),
    region_codes: f.regions.map(r => r.code),
    language: f.language || null,
    min_relevance: num(f.min_relevance),
    lookback_days: num(f.lookback_days),
    is_active: f.is_active,
    owner_editor_id: f.owner_editor_id,
    notes: f.notes,
  };
}

function Badge({ value }) {
  const m = PROVENANCE[value] || { label: 'No profile', color: 'var(--muted)', bg: 'var(--surface2)' };
  return <span className="ca-badge" style={{ background: m.bg, color: m.color, fontWeight: 600 }}>{m.label}</span>;
}

/* ── Small inputs ─────────────────────────────────────────────────────────── */

function TagInput({ value, onChange, placeholder, inherited }) {
  const [text, setText] = useState('');
  const add = (raw) => {
    const parts = raw.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    const seen = new Set(value.map(v => v.toLowerCase()));
    const next = [...value];
    parts.forEach(p => { if (!seen.has(p.toLowerCase())) { seen.add(p.toLowerCase()); next.push(p); } });
    onChange(next);
    setText('');
  };
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: value.length ? 6 : 0 }}>
        {value.map(v => (
          <span key={v} className="ca-tag" style={{ color: 'var(--text)', display: 'inline-flex', gap: 6 }}>
            {v}
            <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter(x => x !== v))}
              style={{ background: 'none', border: 0, color: 'var(--muted)', cursor: 'pointer', padding: 0 }}>×</button>
          </span>
        ))}
      </div>
      <input className="ca-input" value={text} placeholder={placeholder} style={{ fontSize: 12 }}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => { if ((e.key === 'Enter' || e.key === ',') && text.trim()) { e.preventDefault(); add(text); } }}
        onPaste={e => { const t = e.clipboardData.getData('text'); if (/[,\n]/.test(t)) { e.preventDefault(); add(t); } }}
        onBlur={() => text.trim() && add(text)} />
      {inherited?.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
          From the family: {inherited.join(', ')}
        </div>
      )}
    </div>
  );
}

function ProducerPicker({ value, onChange, inherited }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return undefined; }
    const t = setTimeout(() => {
      api.get('/api/news/producers', { params: { q } })
        .then(({ data }) => setResults(data.filter(p => !value.some(v => v.id === p.id))))
        .catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, value]);
  return (
    <div style={{ position: 'relative' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: value.length ? 6 : 0 }}>
        {value.map(p => (
          <span key={p.id} className="ca-tag" style={{ color: 'var(--text)', display: 'inline-flex', gap: 6 }}>
            {p.name}
            <button type="button" aria-label={`Remove ${p.name}`} onClick={() => onChange(value.filter(x => x.id !== p.id))}
              style={{ background: 'none', border: 0, color: 'var(--muted)', cursor: 'pointer', padding: 0 }}>×</button>
          </span>
        ))}
      </div>
      <input className="ca-input" value={q} placeholder="Search producers…" style={{ fontSize: 12 }}
        onChange={e => setQ(e.target.value)} />
      {results.length > 0 && (
        <div className="ca-card" style={{ position: 'absolute', zIndex: 5, left: 0, right: 0, marginTop: 4,
          padding: 4, maxHeight: 220, overflowY: 'auto' }}>
          {results.map(p => (
            <button key={p.id} type="button" className="ca-btn ca-btn-ghost ca-btn-sm"
              style={{ display: 'block', width: '100%', textAlign: 'left' }}
              onClick={() => { onChange([...value, p]); setQ(''); setResults([]); }}>
              {p.name}
            </button>
          ))}
        </div>
      )}
      {inherited?.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
          From the family: {inherited.map(p => p.name).join(', ')}
        </div>
      )}
    </div>
  );
}

function RegionPicker({ value, onChange, allRegions, inherited }) {
  const available = allRegions.filter(r => !value.some(v => v.code === r.code));
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: value.length ? 6 : 0 }}>
        {value.map(r => (
          <span key={r.code} className="ca-tag" style={{ color: 'var(--text)', display: 'inline-flex', gap: 6 }}>
            {r.name} <span style={{ ...mono, color: 'var(--muted)' }}>{r.code}</span>
            <button type="button" aria-label={`Remove ${r.name}`} onClick={() => onChange(value.filter(x => x.code !== r.code))}
              style={{ background: 'none', border: 0, color: 'var(--muted)', cursor: 'pointer', padding: 0 }}>×</button>
          </span>
        ))}
      </div>
      <select className="ca-select" style={{ fontSize: 12 }} value=""
        onChange={e => { const r = allRegions.find(x => x.code === e.target.value); if (r) onChange([...value, r]); }}>
        <option value="">Add a region…</option>
        {available.map(r => <option key={r.code} value={r.code}>{r.name} ({r.code})</option>)}
      </select>
      {inherited?.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
          From the family: {inherited.map(r => r.name).join(', ')}
        </div>
      )}
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label className="ca-label">{label}</label>
      {children}
      {hint && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

/* ── Test results ─────────────────────────────────────────────────────────── */

function ItemCard({ item, dim }) {
  return (
    <div className="ca-card" style={{ padding: 12, marginBottom: 8, opacity: dim ? 0.75 : 1 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 10, marginBottom: 4 }}>
        <span className="ca-badge" style={{ fontWeight: 600 }}>{EVENT_LABEL[item.event_type] || item.event_type}</span>
        <span style={mono}>relevance {item.relevance.toFixed(2)}</span>
        <span style={{ color: 'var(--muted)' }}>
          {item.published_date || 'date unknown — check before using'}
          {item.published_date && DATE_SOURCE[item.date_source] ? ` (${DATE_SOURCE[item.date_source]})` : ''}
          {' · '}{item.source}
        </span>
        {item.drop_reason && (
          <span className="ca-badge" style={{ background: 'var(--danger-bg)', color: 'var(--accent2)' }}>
            {DROP_REASON[item.drop_reason] || item.drop_reason}
          </span>
        )}
      </div>
      <a href={item.url} target="_blank" rel="noopener noreferrer"
        style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{item.headline}</a>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{item.summary}</div>
    </div>
  );
}

function TestResults({ result }) {
  const [show, setShow] = useState(null);
  if (!result) return null;
  return (
    <div style={{ marginTop: 18 }}>
      <div className="ca-h2" style={{ marginBottom: 6 }}>Test run — nothing was saved</div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
        News since {result.window_start} · {result.searches} searches · {result.input_tokens + result.output_tokens} tokens
        · cost ${result.cost_usd.toFixed(3)}
      </div>
      {result.error && <div className="ca-card" style={{ color: 'var(--accent2)', marginBottom: 10 }}>{result.error}</div>}
      {result.queries.length > 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 10 }}>
          Searched: {result.queries.map(q => <span key={q} className="ca-tag" style={{ marginRight: 4 }}>{q}</span>)}
        </div>
      )}
      <div style={{ fontSize: 12, fontWeight: 600, margin: '8px 0' }}>
        Would reach the editor queue ({result.kept.length})
      </div>
      {result.kept.length === 0 && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Nothing above the relevance threshold.</div>}
      {result.kept.map(i => <ItemCard key={i.url} item={i} />)}

      <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
        <button className={`ca-btn ca-btn-sm ${show === 'below' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setShow(show === 'below' ? null : 'below')}>
          Below threshold ({result.below_threshold.length})
        </button>
        <button className={`ca-btn ca-btn-sm ${show === 'dropped' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setShow(show === 'dropped' ? null : 'dropped')}>
          Filtered out ({result.dropped.length})
        </button>
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', margin: '6px 0' }}>
        Hidden items are useful for tuning: noise here suggests an exclusion or blocked site to add.
      </div>
      {show === 'below' && result.below_threshold.map(i => <ItemCard key={i.url} item={i} dim />)}
      {show === 'dropped' && result.dropped.map((i, n) => <ItemCard key={`${i.url}:${n}`} item={i} dim />)}
    </div>
  );
}

/* ── The editor for one family ────────────────────────────────────────────── */

function ProfileEditor({ familyId, onBack }) {
  const { addToast } = useToast();
  const { user } = useAuth();
  const confirm = useConfirm();
  const [detail, setDetail] = useState(null);
  const [editors, setEditors] = useState([]);
  const [allRegions, setAllRegions] = useState([]);
  const [scope, setScope] = useState('family');      // 'family' or a product line id
  const [form, setForm] = useState(emptyForm());
  const [loaded, setLoaded] = useState(emptyForm());
  const [busy, setBusy] = useState(null);
  const [draftInfo, setDraftInfo] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const isOverride = scope !== 'family';
  const base = detail?.profile || null;
  const current = !detail ? null : isOverride
    ? detail.overrides.find(o => o.product_line_id === scope) || null
    : base;

  const load = useCallback(() => {
    setErr(null);
    return api.get(`/api/news/profiles/${familyId}`)
      .then(({ data }) => setDetail(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the profile.'));
  }, [familyId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.get('/api/regions/').then(({ data }) => setAllRegions(data)).catch(() => {}); }, []);
  useEffect(() => { api.get('/api/news/editors').then(({ data }) => setEditors(data)).catch(() => {}); }, []);
  useEffect(() => {
    const f = formFromProfile(current);
    setForm(f); setLoaded(f); setDraftInfo(null); setResult(null);
  }, [detail, scope]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(loaded), [form, loaded]);
  const set = (k) => (v) => setForm(f => ({ ...f, [k]: v }));

  const discardOk = async () => !dirty || confirm({
    title: 'Discard unsaved changes?', message: 'Your edits to this profile have not been saved.',
    confirmLabel: 'Discard', danger: true,
  });

  const switchScope = async (next) => {
    if (!(await discardOk())) return;
    setScope(next);
  };

  const save = async () => {
    setBusy('save');
    try {
      const url = isOverride
        ? `/api/news/profiles/${familyId}/lines/${scope}`
        : `/api/news/profiles/${familyId}`;
      const { data } = await api.put(url, payloadFromForm(form));
      setDetail(data);
      addToast('Profile saved.', 'success');
    } catch (e) {
      addToast(formatApiError(e) || 'Could not save the profile.', 'error');
    } finally { setBusy(null); }
  };

  const removeOverride = async () => {
    const ok = await confirm({
      title: `Remove the override for ${lineName}?`,
      message: 'This product line will use the family profile as it is.',
      confirmLabel: 'Remove', danger: true,
    });
    if (!ok) return;
    setBusy('remove');
    try {
      const { data } = await api.delete(`/api/news/profiles/${familyId}/lines/${scope}`);
      setDetail(data);
      addToast('Override removed.', 'success');
    } catch (e) {
      addToast(formatApiError(e) || 'Could not remove the override.', 'error');
    } finally { setBusy(null); }
  };

  const draft = async () => {
    if (dirty && !(await confirm({
      title: 'Replace the form with a Claude draft?', message: 'Your unsaved changes will be lost.',
      confirmLabel: 'Replace', danger: true,
    }))) return;
    setBusy('draft');
    try {
      const { data } = await api.post(`/api/news/profiles/${familyId}/draft`);
      setForm(f => ({
        ...f,
        ...Object.fromEntries(LIST_FIELDS.map(k => [k, data.fields[k] || []])),
        event_types: data.fields.event_types || [],
        producers: data.producers, regions: data.regions,
        language: data.fields.language || '',
        min_relevance: data.fields.min_relevance ?? '',
        lookback_days: data.fields.lookback_days ?? '',
      }));
      setDraftInfo(data);
      addToast('Draft filled in — review it, then save.', 'success');
    } catch (e) {
      addToast(e.response?.status === 429
        ? 'Draft limit reached for this hour. Try again later.'
        : formatApiError(e) || 'Claude could not draft this profile.', 'error');
    } finally { setBusy(null); }
  };

  const test = async () => {
    setBusy('test'); setResult(null);
    try {
      const p = payloadFromForm(form);
      const fields = Object.fromEntries(Object.entries(p).filter(
        ([k]) => !['is_active', 'owner_editor_id', 'notes'].includes(k)));
      const { data } = await api.post(`/api/news/profiles/${familyId}/test`, {
        product_line_id: isOverride ? scope : null, fields,
      });
      setResult(data);
    } catch (e) {
      addToast(e.response?.status === 429
        ? 'Test limit reached for this hour — each test costs money. Try again later.'
        : formatApiError(e) || 'The test run failed.', 'error');
    } finally { setBusy(null); }
  };

  if (err) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>;
  if (!detail) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const inherit = (k) => (isOverride && base ? base[k] : undefined);
  const placeholderFor = (k) => (isOverride && base
    ? `Inherit (${base[k] ?? DEFAULTS[k]})` : `Default: ${DEFAULTS[k]}`);
  const lineName = isOverride ? detail.product_lines.find(l => l.id === scope)?.name : null;
  const hasTerms = form.terms.length + form.synonyms.length + form.cas_numbers.length > 0;
  const canOverride = !!base;

  return (
    <>
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={async () => { if (await discardOk()) onBack(); }}>
        ← All families
      </button>
      <div className="ca-h2" style={{ margin: '10px 0 4px' }}>
        {detail.family_code && <span style={{ ...mono, color: 'var(--muted)', marginRight: 8 }}>{detail.family_code}</span>}
        {detail.family_name}
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '10px 0 16px' }}>
        <button className={`ca-btn ca-btn-sm ${!isOverride ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => switchScope('family')}>Family profile</button>
        {detail.product_lines.length > 0 && (
          <select className="ca-select" style={{ fontSize: 12, width: 'auto', minWidth: 260 }}
            disabled={!canOverride} value={isOverride ? String(scope) : ''}
            title={canOverride ? undefined : 'Save the family profile first'}
            onChange={e => e.target.value && switchScope(Number(e.target.value))}>
            <option value="">{canOverride ? 'Product line override…' : 'Save the family profile to override a line'}</option>
            {Object.entries(detail.product_lines.reduce((groups, l) => {
              const g = l.subfamily_name || 'Other lines';
              (groups[g] = groups[g] || []).push(l);
              return groups;
            }, {})).map(([group, lines]) => (
              <optgroup key={group} label={group}>
                {lines.map(l => (
                  <option key={l.id} value={l.id}>{l.name}{l.has_override ? '  • overridden' : ''}</option>
                ))}
              </optgroup>
            ))}
          </select>
        )}
        {detail.product_lines.length === 0 && (
          <span style={{ fontSize: 11, color: 'var(--muted)', alignSelf: 'center' }}>This family has no product lines to override.</span>
        )}
      </div>

      {current && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, marginBottom: 12 }}>
          <Badge value={current.provenance} />
          {current.updated_at && <span style={{ color: 'var(--muted)' }}>updated {new Date(current.updated_at).toLocaleDateString()}</span>}
          {!isOverride && current.owner_editor_name && <span style={{ color: 'var(--muted)' }}>· owner {current.owner_editor_name}</span>}
        </div>
      )}
      {isOverride && (
        <div className="ca-card" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
          Overriding <b>{lineName}</b>. Only store what differs: lists are <b>added</b> to the family&apos;s,
          numbers <b>replace</b> them, and anything left blank is inherited.
          {!current && ' This product line has no override yet — it uses the family profile as it is.'}
        </div>
      )}
      {draftInfo && (
        <div className="ca-card" style={{ fontSize: 11, marginBottom: 12, borderColor: 'var(--accent3)' }}>
          <b>Claude draft — not saved yet.</b> {draftInfo.rationale}
          {draftInfo.dropped.length > 0 && (
            <ul style={{ margin: '6px 0 0 16px', color: 'var(--muted)' }}>
              {draftInfo.dropped.map(d => <li key={d}>{d}</li>)}
            </ul>
          )}
          <div style={{ color: 'var(--muted)', marginTop: 4 }}>Cost ${draftInfo.cost_usd.toFixed(3)}</div>
        </div>
      )}

      <div className="ca-card" style={{ padding: 18 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '0 24px' }}>
          <div>
            <Field label="Search for" hint="The main names. Press Enter or comma to add.">
              <TagInput value={form.terms} onChange={set('terms')} placeholder="fiberglass" inherited={inherit('terms')} />
            </Field>
            <Field label="Other names">
              <TagInput value={form.synonyms} onChange={set('synonyms')} placeholder="E-glass" inherited={inherit('synonyms')} />
            </Field>
            <Field label="CAS numbers" hint="Checked against the CAS check digit on save.">
              <TagInput value={form.cas_numbers} onChange={set('cas_numbers')} placeholder="65997-17-3" inherited={inherit('cas_numbers')} />
            </Field>
            <Field label="Ignore results about" hint="Results mentioning these are filtered out.">
              <TagInput value={form.exclude_terms} onChange={set('exclude_terms')} placeholder="boat" inherited={inherit('exclude_terms')} />
            </Field>
            <Field label="Producers to watch">
              <ProducerPicker value={form.producers} onChange={set('producers')} inherited={inherit('producers')} />
            </Field>
          </div>
          <div>
            <Field label="Regions">
              <RegionPicker value={form.regions} onChange={set('regions')} allRegions={allRegions} inherited={inherit('regions')} />
            </Field>
            <Field label="Event types to keep"
              hint={isOverride ? 'None ticked = same as the family.' : 'None ticked = all types.'}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px' }}>
                {EVENT_TYPES.map(([k, l]) => (
                  <label key={k} style={{ fontSize: 12, display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                    <input type="checkbox" checked={form.event_types.includes(k)}
                      onChange={e => set('event_types')(e.target.checked
                        ? [...form.event_types, k] : form.event_types.filter(x => x !== k))} />
                    {l}
                  </label>
                ))}
              </div>
            </Field>
            <Field label="Prefer these sites">
              <TagInput value={form.preferred_sites} onChange={set('preferred_sites')} placeholder="compositesworld.com" inherited={inherit('preferred_sites')} />
            </Field>
            <Field label="Never use these sites">
              <TagInput value={form.blocked_sites} onChange={set('blocked_sites')} placeholder="example.com" inherited={inherit('blocked_sites')} />
            </Field>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              <Field label="Min relevance">
                <input className="ca-input" type="number" min="0" max="1" step="0.05" style={{ fontSize: 12 }}
                  value={form.min_relevance} placeholder={placeholderFor('min_relevance')}
                  onChange={e => set('min_relevance')(e.target.value)} />
              </Field>
              <Field label="Look back (days)">
                <input className="ca-input" type="number" min="1" max="366" style={{ fontSize: 12 }}
                  value={form.lookback_days} placeholder={placeholderFor('lookback_days')}
                  onChange={e => set('lookback_days')(e.target.value)} />
              </Field>
              <Field label="Language">
                <input className="ca-input" style={{ fontSize: 12 }} maxLength={8}
                  value={form.language} placeholder={placeholderFor('language')}
                  onChange={e => set('language')(e.target.value)} />
              </Field>
            </div>
          </div>
        </div>

        {!isOverride && (
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap', margin: '4px 0 14px', fontSize: 12 }}>
            <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={form.is_active} onChange={e => set('is_active')(e.target.checked)} />
              Active — include in the monthly news run
            </label>
            <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              Owner
              <select className="ca-select" style={{ fontSize: 12, width: 'auto', minWidth: 180 }}
                value={form.owner_editor_id || ''} onChange={e => set('owner_editor_id')(e.target.value || null)}>
                <option value="">Nobody yet</option>
                {editors.map(ed => (
                  <option key={ed.id} value={ed.id}>{ed.name}{ed.id === user?.id ? ' (me)' : ''}</option>
                ))}
              </select>
            </label>
          </div>
        )}
        <Field label="Notes for editors">
          <textarea className="ca-input" rows={2} style={{ fontSize: 12 }} value={form.notes}
            onChange={e => set('notes')(e.target.value)} />
        </Field>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="ca-btn ca-btn-primary" disabled={!dirty || !!busy || (!isOverride && !hasTerms)}
            title={!isOverride && !hasTerms ? 'Add at least one search term, other name or CAS number' : undefined}
            onClick={save}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </button>
          <button className="ca-btn ca-btn-ghost" disabled={!!busy || (!isOverride && !hasTerms)} onClick={test}
            title="Searches with the form as it is now. Nothing is saved.">
            {busy === 'test' ? 'Searching… (up to a minute)' : 'Test this profile'}
          </button>
          {!isOverride && (
            <button className="ca-btn ca-btn-ghost" disabled={!!busy} onClick={draft}>
              {busy === 'draft' ? 'Drafting…' : 'Draft with Claude'}
            </button>
          )}
          {isOverride && current && (
            <button className="ca-btn ca-btn-danger" disabled={!!busy} onClick={removeOverride}>Remove override</button>
          )}
          {dirty && <span style={{ fontSize: 11, color: 'var(--accent3)' }}>Unsaved changes</span>}
          {!isOverride && !hasTerms && (
            <span style={{ fontSize: 11, color: 'var(--accent2)' }}>Add something to search for first.</span>
          )}
        </div>
      </div>

      <TestResults result={result} />
    </>
  );
}

/* ── The tab: list of families, or one family's editor ────────────────────── */

const VIEWS = [
  ['profiles', 'Profiles'],
  ['runs', 'Runs'],
  ['sources', 'Sources'],
];

export default function NewsProfilesTab() {
  const [view, setView] = useState('profiles');
  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        {VIEWS.map(([k, l]) => (
          <button key={k} className={`ca-btn ca-btn-sm ${view === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            onClick={() => setView(k)}>{l}</button>
        ))}
      </div>
      {view === 'profiles' && <ProfilesView />}
      {view === 'runs' && <RunsView />}
      {view === 'sources' && <SourcesView />}
    </>
  );
}

function ProfilesView() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null);

  const load = useCallback(() => {
    setErr(null);
    api.get('/api/news/profiles')
      .then(({ data }) => setRows(data))
      .catch(e => setErr(e.response?.status === 403
        ? 'News profiles need the Content Editor role.'
        : formatApiError(e) || 'Could not load the news profiles.'));
  }, []);
  useEffect(load, [load]);

  if (open !== null) return <ProfileEditor familyId={open} onBack={() => { setOpen(null); load(); }} />;
  if (err) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>;
  if (!rows) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const withProfile = rows.filter(r => r.has_profile).length;
  const active = rows.filter(r => r.is_active).length;
  return (
    <>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 0 }}>
        What the monthly news search looks for in each family. A good profile is the difference between
        &ldquo;new furnace in Poland&rdquo; and boat adverts.
      </p>
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <div className="ca-metric" style={{ flex: 1, minWidth: 150 }}>
          <div className="ca-metric-lbl">Families with a profile</div>
          <div className="ca-metric-val">{withProfile} / {rows.length}</div>
        </div>
        <div className="ca-metric" style={{ flex: 1, minWidth: 150 }}>
          <div className="ca-metric-lbl">Active in the monthly run</div>
          <div className="ca-metric-val">{active}</div>
        </div>
      </div>
      <div className="ca-card ca-scroll-x" style={{ padding: 0 }}>
        <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
          <thead>
            <tr>
              <th style={{ width: 70 }}>Code</th><th>Family</th><th>Profile</th><th>Active</th>
              <th style={{ textAlign: 'right' }}>Overrides</th><th>Owner</th><th>Needs attention</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.family_id} onClick={() => setOpen(r.family_id)} style={{ cursor: 'pointer' }}>
                <td style={{ ...mono, color: 'var(--muted)' }}>{r.family_code || '—'}</td>
                <td>{r.family_name}</td>
                <td><Badge value={r.has_profile ? r.provenance : null} /></td>
                <td>{r.is_active ? 'Yes' : <span style={{ color: 'var(--muted)' }}>No</span>}</td>
                <td style={{ ...mono, textAlign: 'right' }}>{r.override_count || '—'}</td>
                <td style={{ color: 'var(--text-secondary)' }}>{r.owner_editor_name || '—'}</td>
                <td style={{ color: r.warnings.length ? 'var(--accent3)' : 'var(--muted)', fontSize: 10 }}>
                  {r.has_profile ? (r.warnings.join(' · ') || '—') : '—'}
                </td>
                <td style={{ color: 'var(--muted)' }}>{r.updated_at ? new Date(r.updated_at).toLocaleDateString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
