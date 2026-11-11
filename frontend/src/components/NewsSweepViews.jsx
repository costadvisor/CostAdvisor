import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import { useToast } from './Toast';
import { useConfirm } from './ConfirmDialog';

/**
 * The monthly news sweep (Wave 4, SCRUM-214 / NEWS-2), as two views of the
 * News profiles tab:
 *
 *  - Runs:    the run log — every run with what it searched, found, kept and
 *             cost; "Run now" for one family or every active profile; and,
 *             per run, the per-profile breakdown, the errors and the items stored.
 *  - Sources: the free feeds read on every run (RSS/Atom, SEC EDGAR).
 *
 * Items are stored "unsorted": sorting (SCRUM-215) and the editor queue
 * (SCRUM-216) come next, so for now this is where editors see what was found.
 */

const mono = { fontFamily: "'JetBrains Mono', monospace" };

const RUN_STATUS = {
  running: { label: 'Running…', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  completed: { label: 'Completed', color: 'var(--accent)', bg: 'var(--success-bg)' },
  stopped_at_cap: { label: 'Stopped at cap', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  failed: { label: 'Failed', color: 'var(--accent2)', bg: 'var(--danger-bg)' },
};

const DROP_LABEL = {
  excluded_term: 'excluded term', blocked_site: 'blocked site', outside_time_window: 'too old',
  event_type_not_wanted: 'event type', link_not_in_search_results: 'link not in results',
  duplicate: 'duplicate', incomplete: 'incomplete',
};

const ORIGIN_LABEL = { web_search: 'Search', feed: 'Feed', sec: 'SEC' };

const money = (v) => `$${(v || 0).toFixed(2)}`;
const when = (v) => (v ? new Date(v).toLocaleString() : '—');

function StatusBadge({ status }) {
  const m = RUN_STATUS[status] || { label: status, color: 'var(--muted)', bg: 'var(--surface2)' };
  return <span className="ca-badge" style={{ background: m.bg, color: m.color, fontWeight: 600 }}>{m.label}</span>;
}

function useFamilies() {
  const [families, setFamilies] = useState([]);
  useEffect(() => {
    api.get('/api/news/profiles').then(({ data }) => setFamilies(data)).catch(() => {});
  }, []);
  return families;
}

/* ── Runs ─────────────────────────────────────────────────────────────────── */

function RunDetail({ runId, onBack }) {
  const [run, setRun] = useState(null);
  const [items, setItems] = useState([]);
  const [err, setErr] = useState(null);

  const load = useCallback(() => Promise.all([
    api.get(`/api/news/runs/${runId}`),
    api.get('/api/news/items', { params: { run_id: runId, limit: 500 } }),
  ]).then(([r, i]) => { setRun(r.data); setItems(i.data); })
    .catch(e => setErr(formatApiError(e) || 'Could not load the run.')), [runId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (run?.status !== 'running') return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [run?.status, load]);

  if (err) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>;
  if (!run) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const metrics = [
    ['Profiles run', `${run.profiles_run} / ${run.profiles_planned}`],
    ['Searches', run.searches],
    ['Feed entries read', run.feed_entries_read],
    ['New items', run.items_new],
    ['Duplicates skipped', run.items_duplicate],
    ['Filtered out', run.items_dropped],
    ['Cost', `${money(run.cost_usd)} of ${money(run.spend_cap_usd)} cap`],
    ['Tokens', (run.input_tokens + run.output_tokens).toLocaleString()],
  ];
  return (
    <>
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onBack}>← All runs</button>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', margin: '10px 0' }}>
        <div className="ca-h2" style={{ margin: 0 }}>
          {run.family_name ? run.family_name : 'All active profiles'} · {run.period}
        </div>
        <StatusBadge status={run.status} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12 }}>
        {run.trigger === 'manual' ? `Started by ${run.started_by_name || 'someone'}` : 'Scheduled'} · {when(run.started_at)}
        {run.finished_at && ` · finished ${when(run.finished_at)}`}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        {metrics.map(([l, v]) => (
          <div key={l} className="ca-metric" style={{ flex: '1 1 150px' }}>
            <div className="ca-metric-lbl">{l}</div>
            <div className="ca-metric-val" style={{ fontSize: 16 }}>{v}</div>
          </div>
        ))}
      </div>

      {run.errors.length > 0 && (
        <div className="ca-card" style={{ marginBottom: 14, borderColor: 'var(--accent3)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>What went wrong ({run.errors.length})</div>
          {run.errors.map((e, n) => (
            <div key={n} style={{ fontSize: 11, marginBottom: 4 }}>
              <b>{e.label}:</b> <span style={{ color: 'var(--text-secondary)' }}>{e.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="ca-card ca-scroll-x" style={{ padding: 0, marginBottom: 14 }}>
        <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
          <thead><tr>
            <th>Profile</th><th style={{ textAlign: 'right' }}>Searches</th><th style={{ textAlign: 'right' }}>Found</th>
            <th style={{ textAlign: 'right' }}>New</th><th style={{ textAlign: 'right' }}>Duplicate</th>
            <th>Filtered out</th><th style={{ textAlign: 'right' }}>Cost</th>
          </tr></thead>
          <tbody>
            {run.details.map((d, n) => (
              <tr key={n}>
                <td>
                  {d.label}
                  {d.note && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{d.note}</div>}
                </td>
                <td style={{ ...mono, textAlign: 'right' }}>{d.searches}</td>
                <td style={{ ...mono, textAlign: 'right' }}>{d.found}</td>
                <td style={{ ...mono, textAlign: 'right' }}>{d.new}</td>
                <td style={{ ...mono, textAlign: 'right' }}>{d.duplicate}</td>
                <td style={{ color: 'var(--text-secondary)' }}>
                  {Object.entries(d.dropped || {}).map(([k, v]) => `${v} ${DROP_LABEL[k] || k}`).join(' · ') || '—'}
                </td>
                <td style={{ ...mono, textAlign: 'right' }}>{money(d.cost_usd)}</td>
              </tr>
            ))}
            {run.details.length === 0 && (
              <tr><td colSpan={7} style={{ color: 'var(--muted)' }}>
                {run.status === 'running' ? 'Working… this page refreshes by itself.' : 'No profiles were run.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Items stored by this run ({items.length})</div>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>
        Stored as unsorted. Sorting and the editor queue come next (SCRUM-215/216).
      </div>
      {items.map(i => (
        <div key={i.id} className="ca-card" style={{ padding: 10, marginBottom: 6 }}>
          <div style={{ display: 'flex', gap: 8, fontSize: 10, flexWrap: 'wrap', marginBottom: 3 }}>
            <span className="ca-badge">{ORIGIN_LABEL[i.origin] || i.origin}</span>
            {i.search_relevance !== null && <span style={mono}>relevance {i.search_relevance.toFixed(2)}</span>}
            <span style={{ color: 'var(--muted)' }}>{i.published_date || 'date unknown'} · {i.source || '—'}</span>
            {i.product_line_name && <span style={{ color: 'var(--muted)' }}>· {i.product_line_name}</span>}
          </div>
          <a href={i.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
            {i.headline}
          </a>
          {i.summary && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3 }}>{i.summary}</div>}
        </div>
      ))}
    </>
  );
}

export function RunsView() {
  const { addToast } = useToast();
  const confirm = useConfirm();
  const families = useFamilies();
  const [runs, setRuns] = useState(null);
  const [open, setOpen] = useState(null);
  const [familyId, setFamilyId] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.get('/api/news/runs').then(({ data }) => setRuns(data))
    .catch(e => addToast(formatApiError(e) || 'Could not load the runs.', 'error')), [addToast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!runs?.some(r => r.status === 'running')) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [runs, load]);

  const start = async () => {
    const fam = families.find(f => String(f.family_id) === familyId);
    const ok = await confirm({
      title: fam ? `Run the news search for ${fam.family_name} now?` : 'Run every active profile now?',
      message: 'This searches the web with Claude and costs money (a few cents per profile). '
        + 'New items are stored; nothing is published.',
      confirmLabel: 'Run now',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const { data } = await api.post('/api/news/runs', { family_id: fam ? fam.family_id : null });
      addToast('Run started — it takes a minute or two per profile.', 'success');
      await load();
      setOpen(data.id);
    } catch (e) {
      const status = e.response?.status;
      addToast(status === 409 ? 'Another run is still going. Wait for it to finish.'
        : status === 429 ? 'Run limit reached for this hour.'
          : formatApiError(e) || 'Could not start the run.', 'error');
    } finally { setBusy(false); }
  };

  if (open) return <RunDetail runId={open} onBack={() => { setOpen(null); load(); }} />;
  const withProfile = families.filter(f => f.has_profile);
  return (
    <>
      <div className="ca-card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Run now</span>
        <select className="ca-select" style={{ fontSize: 12, width: 'auto', minWidth: 240 }}
          value={familyId} onChange={e => setFamilyId(e.target.value)}>
          <option value="">Every active profile</option>
          {withProfile.map(f => (
            <option key={f.family_id} value={f.family_id}>{f.family_name}{f.is_active ? '' : ' (not active)'}</option>
          ))}
        </select>
        <button className="ca-btn ca-btn-primary ca-btn-sm" disabled={busy} onClick={start}>
          {busy ? 'Starting…' : 'Run'}
        </button>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          The monthly run happens on the 1st by itself once switched on for this environment.
        </span>
      </div>
      {!runs && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}
      {runs && runs.length === 0 && (
        <div className="ca-card" style={{ fontSize: 12, color: 'var(--muted)' }}>No runs yet. Try “Run now” on one family.</div>
      )}
      {runs && runs.length > 0 && (
        <div className="ca-card ca-scroll-x" style={{ padding: 0 }}>
          <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
            <thead><tr>
              <th>Started</th><th>What</th><th>Status</th><th style={{ textAlign: 'right' }}>Profiles</th>
              <th style={{ textAlign: 'right' }}>Searches</th><th style={{ textAlign: 'right' }}>New</th>
              <th style={{ textAlign: 'right' }}>Duplicates</th><th style={{ textAlign: 'right' }}>Cost</th><th>Issues</th>
            </tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id} onClick={() => setOpen(r.id)} style={{ cursor: 'pointer' }}>
                  <td style={{ color: 'var(--text-secondary)' }}>{when(r.started_at)}</td>
                  <td>{r.family_name || 'All active profiles'}
                    <span style={{ color: 'var(--muted)' }}> · {r.trigger === 'manual' ? 'manual' : 'scheduled'}</span></td>
                  <td><StatusBadge status={r.status} /></td>
                  <td style={{ ...mono, textAlign: 'right' }}>{r.profiles_run}/{r.profiles_planned}</td>
                  <td style={{ ...mono, textAlign: 'right' }}>{r.searches}</td>
                  <td style={{ ...mono, textAlign: 'right' }}>{r.items_new}</td>
                  <td style={{ ...mono, textAlign: 'right' }}>{r.items_duplicate}</td>
                  <td style={{ ...mono, textAlign: 'right' }}>{money(r.cost_usd)}</td>
                  <td style={{ color: r.errors.length ? 'var(--accent3)' : 'var(--muted)' }}>{r.errors.length || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ── Sources ──────────────────────────────────────────────────────────────── */

const EMPTY_FEED = { name: '', url: '', kind: 'rss', family_id: '', producer: null };

export function SourcesView() {
  const { addToast } = useToast();
  const confirm = useConfirm();
  const families = useFamilies();
  const [feeds, setFeeds] = useState(null);
  const [form, setForm] = useState(EMPTY_FEED);
  const [q, setQ] = useState('');
  const [producers, setProducers] = useState([]);

  const load = useCallback(() => api.get('/api/news/feeds').then(({ data }) => setFeeds(data))
    .catch(e => addToast(formatApiError(e) || 'Could not load the sources.', 'error')), [addToast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (form.kind !== 'sec' || q.trim().length < 2) { setProducers([]); return undefined; }
    const t = setTimeout(() => api.get('/api/news/producers', { params: { q } })
      .then(({ data }) => setProducers(data)).catch(() => setProducers([])), 250);
    return () => clearTimeout(t);
  }, [q, form.kind]);

  const payload = (f) => ({
    name: f.name, url: f.url, kind: f.kind, enabled: f.enabled ?? true,
    family_id: f.family_id === '' || f.family_id === null ? null : Number(f.family_id),
    producer_id: f.producer ? f.producer.id : (f.producer_id || null),
  });

  const add = async () => {
    try {
      await api.post('/api/news/feeds', payload(form));
      setForm(EMPTY_FEED); setQ('');
      addToast('Source added. It will be read on the next run.', 'success');
      load();
    } catch (e) { addToast(formatApiError(e) || 'Could not add the source.', 'error'); }
  };
  const toggle = async (f) => {
    try { await api.put(`/api/news/feeds/${f.id}`, payload({ ...f, enabled: !f.enabled })); load(); } catch (e) {
      addToast(formatApiError(e) || 'Could not update the source.', 'error');
    }
  };
  const remove = async (f) => {
    if (!(await confirm({ title: `Remove ${f.name}?`, message: 'Items already found stay.', confirmLabel: 'Remove', danger: true }))) return;
    try { await api.delete(`/api/news/feeds/${f.id}`); load(); } catch (e) {
      addToast(formatApiError(e) || 'Could not remove the source.', 'error');
    }
  };

  return (
    <>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 0 }}>
        Free sources read on every run, on top of Claude&apos;s web search. Trade-press and company-press RSS feeds
        are matched against each profile&apos;s search terms; an SEC feed carries one producer&apos;s filings and counts
        as news for every profile that watches that producer.
      </p>
      <div className="ca-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
          <div><label className="ca-label">Name</label>
            <input className="ca-input" style={{ fontSize: 12 }} value={form.name} placeholder="CompositesWorld news"
              onChange={e => setForm({ ...form, name: e.target.value })} /></div>
          <div><label className="ca-label">Feed address</label>
            <input className="ca-input" style={{ fontSize: 12 }} value={form.url} placeholder="https://…/rss"
              onChange={e => setForm({ ...form, url: e.target.value })} /></div>
          <div><label className="ca-label">Kind</label>
            <select className="ca-select" style={{ fontSize: 12 }} value={form.kind}
              onChange={e => setForm({ ...form, kind: e.target.value, producer: null })}>
              <option value="rss">RSS / Atom (trade or company press)</option>
              <option value="sec">SEC filings (one producer)</option>
            </select></div>
          <div><label className="ca-label">Family</label>
            <select className="ca-select" style={{ fontSize: 12 }} value={form.family_id}
              onChange={e => setForm({ ...form, family_id: e.target.value })}>
              <option value="">Every family</option>
              {families.map(f => <option key={f.family_id} value={f.family_id}>{f.family_name}</option>)}
            </select></div>
        </div>
        {form.kind === 'sec' && (
          <div style={{ marginTop: 10, position: 'relative', maxWidth: 420 }}>
            <label className="ca-label">Producer</label>
            {form.producer
              ? <span className="ca-tag" style={{ color: 'var(--text)' }}>{form.producer.name}{' '}
                <button type="button" onClick={() => setForm({ ...form, producer: null })}
                  style={{ background: 'none', border: 0, color: 'var(--muted)', cursor: 'pointer' }}>×</button></span>
              : <input className="ca-input" style={{ fontSize: 12 }} value={q} placeholder="Search producers…"
                onChange={e => setQ(e.target.value)} />}
            {!form.producer && producers.length > 0 && (
              <div className="ca-card" style={{ position: 'absolute', zIndex: 5, left: 0, right: 0, padding: 4 }}>
                {producers.map(p => (
                  <button key={p.id} type="button" className="ca-btn ca-btn-ghost ca-btn-sm"
                    style={{ display: 'block', width: '100%', textAlign: 'left' }}
                    onClick={() => { setForm({ ...form, producer: p }); setQ(''); setProducers([]); }}>{p.name}</button>
                ))}
              </div>
            )}
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              EDGAR address: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=…&amp;type=8-K&amp;output=atom
            </div>
          </div>
        )}
        <button className="ca-btn ca-btn-primary ca-btn-sm" style={{ marginTop: 12 }}
          disabled={!form.name.trim() || !form.url.trim() || (form.kind === 'sec' && !form.producer)} onClick={add}>
          Add source
        </button>
      </div>

      {feeds && feeds.length === 0 && (
        <div className="ca-card" style={{ fontSize: 12, color: 'var(--muted)' }}>No sources yet: runs use Claude&apos;s web search only.</div>
      )}
      {feeds && feeds.length > 0 && (
        <div className="ca-card ca-scroll-x" style={{ padding: 0 }}>
          <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
            <thead><tr><th>Name</th><th>Kind</th><th>Family</th><th>Last read</th><th>Problem</th><th>On</th><th /></tr></thead>
            <tbody>
              {feeds.map(f => (
                <tr key={f.id}>
                  <td><a href={f.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--text)' }}>{f.name}</a></td>
                  <td>{f.kind === 'sec' ? `SEC · ${f.producer_name || '?'}` : 'RSS'}</td>
                  <td>{f.family_name || 'Every family'}</td>
                  <td style={{ color: 'var(--muted)' }}>{when(f.last_fetched_at)}</td>
                  <td style={{ color: f.last_error ? 'var(--accent2)' : 'var(--muted)' }}>{f.last_error || '—'}</td>
                  <td><input type="checkbox" checked={f.enabled} onChange={() => toggle(f)} aria-label={`Read ${f.name}`} /></td>
                  <td><button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => remove(f)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
