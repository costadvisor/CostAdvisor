import { useCallback, useEffect, useMemo, useState } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import exportCsv from '../utils/exportCsv';

// Index data-quality validation console (Scrum 33).
//
// The backend has been shipped and tested since Wave 3 and nothing called it —
// 1,554 stored findings that no screen read. This is that screen.
//
// Platform-grain deliberately, matching the API: these are facts about the
// shared index library, not about anyone's tenancy, so there is no team
// selector. Reading is open to any authenticated user; **running** is
// super-admin, because a run writes a customer-visible judgement about the
// library. That split is the API's, not this page's — the Run button is simply
// absent for everyone else rather than present and 403-ing.

const SEVERITY = {
  contradiction: {
    label: 'Contradiction', color: 'var(--accent2)', bg: 'var(--accent2-dim)',
    help: 'Two sources state different things about the same subject. Somebody has to decide which is right.',
  },
  gap: {
    label: 'Gap', color: 'var(--accent3)', bg: 'var(--accent3-dim)',
    help: 'Something the library wants and does not have — usually a feed to buy or a scrape to run.',
  },
  note: {
    label: 'Note', color: 'var(--accent4)', bg: 'var(--accent4-dim)',
    help: 'Worth knowing, not worth blocking on.',
  },
};

// Plain-language gloss per check. The API returns a code; a reviewer should not
// have to read the service to know what a row is telling them.
const CHECK_HELP = {
  proxy_status_contradiction: 'The type-code registry and the cost line naming it disagree about whether this is a proxy.',
  resolution_ambiguous: 'This code does not resolve to a series — nobody has decided what it means.',
  card_series_provenance: 'A card and the series it sits on disagree about where the data came from.',
  sibling_card_disagreement: 'Two cards on one series declare different frequency or category.',
  duplicate_default_region: 'Several cards on this slug each claim to be the default region.',
  forecast_only_series: 'Every value on this series is a forecast. It charts and has a latest value, but nothing was ever observed.',
  unverified_agency: 'The publishing agency behind this series has not been verified.',
  frequency_outside_vocabulary: 'The stated cadence is not one of the values the vocabulary allows.',
  region_rebadged: 'A card filed under one region sits on a series that is specific to another.',
  drop_issue: 'Carried through verbatim from the data drop’s own issue register — not re-derived here.',
};

const fmtDate = (s) => (s ? new Date(s).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

function Stat({ value, label, color }) {
  return (
    <div className="ca-metric" style={{ flex: '1 1 150px' }}>
      <div className="ca-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ca-metric-lbl">{label}</div>
    </div>
  );
}

function FindingRow({ f, expanded, onToggle }) {
  const sev = SEVERITY[f.severity] || SEVERITY.note;
  // Only the sides that actually carry a label. Plenty of findings are
  // one-sided — a declared drop issue states a problem without a counterpart —
  // and rendering an empty second card next to it reads as missing data rather
  // than as "there is no other side".
  const sides = [
    { key: 'left', label: f.left_label, value: f.left_value },
    { key: 'right', label: f.right_label, value: f.right_value },
  ].filter(s => s.label);
  return (
    <>
      <tr
        onClick={onToggle}
        style={{ cursor: 'pointer', opacity: f.resolved_at ? 0.55 : 1 }}
      >
        <td>
          <span className="ca-badge" style={{ background: sev.bg, color: sev.color, fontWeight: 600 }}>
            {sev.label}
          </span>
        </td>
        <td>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span>{f.check_code}</span>
            {f.origin === 'declared' && (
              <span className="ca-tag" title="Carried through from the drop's own issue register, not re-derived">
                declared
              </span>
            )}
            {f.resolved_at && (
              <span className="ca-badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                resolved
              </span>
            )}
          </div>
        </td>
        <td>
          <div>{f.subject_key}</div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>
            {f.subject_table}{f.subject_column ? ` · ${f.subject_column}` : ''}
          </div>
        </td>
        <td style={{ maxWidth: 420 }}>{f.summary}</td>
        <td style={{ textAlign: 'right', fontSize: 10, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
          {fmtDate(f.last_seen_at)}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} style={{ background: 'var(--bg)' }}>
            <div style={{ padding: '10px 4px 14px 4px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>
                {CHECK_HELP[f.check_code] || 'No description registered for this check.'}
              </div>
              {sides.length > 0 ? (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
                  {sides.map(s => (
                    <div
                      key={s.key}
                      style={{
                        flex: '1 1 220px', minWidth: 0, padding: 10,
                        border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                      }}
                    >
                      <div className="ca-card-title" style={{ marginBottom: 4 }}>{s.label}</div>
                      <div style={{ fontSize: 13, fontWeight: 600, wordBreak: 'break-word' }}>
                        {s.value === null || s.value === undefined || s.value === '' ? '—' : s.value}
                      </div>
                    </div>
                  ))}
                  {sides.length === 1 && (
                    <div style={{ flex: '1 1 220px', minWidth: 0, padding: 10, fontSize: 11, color: 'var(--muted)' }}>
                      One-sided — this finding states a problem rather than a disagreement between two sources.
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12 }}>
                  This check reports a subject rather than two conflicting values.
                </div>
              )}
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                First seen {fmtDate(f.first_seen_at)} · last seen {fmtDate(f.last_seen_at)}
                {f.resolved_at && ` · resolved ${fmtDate(f.resolved_at)}`}
              </div>
              {f.detail && Object.keys(f.detail).length > 0 && (
                <pre style={{
                  marginTop: 10, fontSize: 10, color: 'var(--text-secondary)',
                  background: 'var(--surface2)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)', padding: 10, overflowX: 'auto',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {JSON.stringify(f.detail, null, 2)}
                </pre>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

const PAGE_SIZE = 200;

export default function Validation() {
  const { user } = useAuth();
  const { addToast } = useToast();

  const [runs, setRuns] = useState(null);
  const [findings, setFindings] = useState(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState(null);
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [offset, setOffset] = useState(0);

  const [filters, setFilters] = useState({
    severity: '', origin: '', check_code: '', subject_key: '', open_only: true,
  });

  const loadRuns = useCallback(() => {
    api.get('/api/validation/runs', { params: { limit: 10 } })
      .then(({ data }) => setRuns(data.runs))
      .catch(e => setErr(formatApiError(e) || 'Could not load validation runs.'));
  }, []);

  const loadFindings = useCallback(() => {
    const params = { limit: PAGE_SIZE, offset, open_only: filters.open_only };
    if (filters.severity) params.severity = filters.severity;
    if (filters.origin) params.origin = filters.origin;
    if (filters.check_code) params.check_code = filters.check_code;
    if (filters.subject_key.trim()) params.subject_key = filters.subject_key.trim();
    setFindings(null);
    api.get('/api/validation/findings', { params })
      .then(({ data }) => { setFindings(data.findings); setTotal(data.total); })
      .catch(e => setErr(formatApiError(e) || 'Could not load findings.'));
  }, [filters, offset]);

  useEffect(() => { loadRuns(); }, [loadRuns]);
  useEffect(() => { loadFindings(); }, [loadFindings]);

  const latest = runs && runs.length ? runs[0] : null;

  // Per-check counts come off the latest run rather than being recomputed from
  // the page of findings on screen — a page is 200 rows and the counts are
  // about the whole library.
  const checkCodes = useMemo(() => {
    const fromRun = latest?.checks ? Object.keys(latest.checks) : [];
    const fromRows = findings ? findings.map(f => f.check_code) : [];
    return [...new Set([...fromRun, ...fromRows])].sort();
  }, [latest, findings]);

  const setFilter = (k, v) => { setOffset(0); setFilters(f => ({ ...f, [k]: v })); };

  const runNow = async () => {
    setRunning(true);
    setErr(null);
    try {
      const { data } = await api.post('/api/validation/runs', { include_declared: true });
      addToast(
        `Validation complete — ${data.n_findings} open, ${data.n_new} new, ${data.n_resolved} resolved.`,
        data.n_new > 0 ? 'info' : 'success',
      );
      loadRuns();
      loadFindings();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not run validation.');
    } finally {
      setRunning(false);
    }
  };

  const downloadCsv = () => {
    if (!findings || findings.length === 0) return;
    exportCsv(
      `index-validation-${new Date().toISOString().slice(0, 10)}.csv`,
      ['Severity', 'Check', 'Origin', 'Table', 'Key', 'Column',
        'Left label', 'Left value', 'Right label', 'Right value',
        'Summary', 'First seen', 'Last seen', 'Resolved'],
      findings.map(f => [
        f.severity, f.check_code, f.origin, f.subject_table, f.subject_key,
        f.subject_column, f.left_label, f.left_value, f.right_label, f.right_value,
        f.summary, f.first_seen_at, f.last_seen_at, f.resolved_at,
      ]),
    );
  };

  const filtersActive = filters.severity || filters.origin || filters.check_code
    || filters.subject_key.trim() || !filters.open_only;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <div style={{ minWidth: 0 }}>
          <h1 className="ca-h1">Index data quality</h1>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            What the index library contradicts itself about, what it is missing, and whether a fix landed.
          </p>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={downloadCsv}
                  disabled={!findings || findings.length === 0}>
            Export CSV
          </button>
          {user?.is_super_admin && (
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={runNow} disabled={running}>
              {running ? 'Running…' : 'Run validation'}
            </button>
          )}
        </div>
      </div>

      {err && (
        <div style={{ fontSize: 12, color: 'var(--accent2)', margin: '16px 0' }}>{err}</div>
      )}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '28px 0 16px' }}>
        <Stat value={latest ? latest.n_findings : '—'} label="Open findings" />
        <Stat value={latest ? latest.n_new : '—'} label="New in last run" color="var(--accent3)" />
        <Stat value={latest ? latest.n_resolved : '—'} label="Resolved in last run" color="var(--accent)" />
        <Stat value={latest ? Object.keys(latest.checks || {}).length : '—'} label="Checks run" />
        <div className="ca-metric" style={{ flex: '2 1 220px' }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{latest ? fmtDate(latest.finished_at || latest.started_at) : 'Never run'}</div>
          <div className="ca-metric-lbl">Last run</div>
        </div>
      </div>

      {latest === null && runs !== null && (
        <div className="ca-card" style={{ textAlign: 'center', padding: '36px 20px', marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>No validation has been run yet</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>
            A run walks every check over the index library and records what it finds. Findings persist between
            runs, so one that stops being observed is stamped resolved rather than quietly disappearing.
          </div>
          {user?.is_super_admin ? (
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={runNow} disabled={running}>
              {running ? 'Running…' : 'Run the first validation'}
            </button>
          ) : (
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>A super admin has to start the first run.</div>
          )}
        </div>
      )}

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 150px' }}>
            <label className="ca-label" htmlFor="v-sev">Severity</label>
            <select id="v-sev" className="ca-select" value={filters.severity}
                    onChange={e => setFilter('severity', e.target.value)} style={{ width: '100%' }}>
              <option value="">All</option>
              {Object.entries(SEVERITY).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
            </select>
          </div>
          <div style={{ flex: '1 1 150px' }}>
            <label className="ca-label" htmlFor="v-origin">Origin</label>
            <select id="v-origin" className="ca-select" value={filters.origin}
                    onChange={e => setFilter('origin', e.target.value)} style={{ width: '100%' }}>
              <option value="">All</option>
              <option value="derived">Derived by a check</option>
              <option value="declared">Declared by the drop</option>
            </select>
          </div>
          <div style={{ flex: '1 1 200px' }}>
            <label className="ca-label" htmlFor="v-check">Check</label>
            <select id="v-check" className="ca-select" value={filters.check_code}
                    onChange={e => setFilter('check_code', e.target.value)} style={{ width: '100%' }}>
              <option value="">All</option>
              {checkCodes.map(c => (
                <option key={c} value={c}>
                  {c}{latest?.checks?.[c] !== undefined ? ` (${latest.checks[c]})` : ''}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: '1 1 180px' }}>
            <label className="ca-label" htmlFor="v-subject">Subject key</label>
            <input id="v-subject" className="ca-input" value={filters.subject_key}
                   placeholder="e.g. ELEC-EU"
                   onChange={e => setFilter('subject_key', e.target.value)} style={{ width: '100%' }} />
          </div>
          <button
            className={`ca-btn ca-btn-sm ${filters.open_only ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            aria-pressed={filters.open_only}
            onClick={() => setFilter('open_only', !filters.open_only)}
          >
            Open only
          </button>
          {filtersActive && (
            <button
              className="ca-btn ca-btn-ghost ca-btn-sm"
              onClick={() => { setOffset(0); setFilters({ severity: '', origin: '', check_code: '', subject_key: '', open_only: true }); }}
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
          <div className="ca-card-title" style={{ marginBottom: 0 }}>
            Findings{findings ? ` — ${total.toLocaleString()}` : ''}
          </div>
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>
            A row expands to show the two conflicting values, labelled.
          </span>
        </div>

        {findings === null ? (
          <div>
            {[0, 1, 2, 3, 4, 5].map(i => <div key={i} className="ca-skeleton" style={{ height: 30, marginBottom: 6 }} />)}
          </div>
        ) : findings.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '28px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
              {filtersActive ? 'Nothing matches those filters' : 'No open findings'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
              {filtersActive
                ? 'Widen the filters, or clear them to see everything open.'
                : 'Either the library is clean or no run has recorded anything yet.'}
            </div>
          </div>
        ) : (
          <>
            <div className="ca-scroll-x">
              <table className="ca-table">
                <caption className="ca-sr-only">
                  Index data-quality findings. Each row expands to show the conflicting values behind it.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Check</th>
                    <th scope="col">Subject</th>
                    <th scope="col">Summary</th>
                    <th scope="col" style={{ textAlign: 'right' }}>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.map(f => (
                    <FindingRow
                      key={f.id}
                      f={f}
                      expanded={expanded === f.id}
                      onToggle={() => setExpanded(expanded === f.id ? null : f.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {total > PAGE_SIZE && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
                <button className="ca-btn ca-btn-ghost ca-btn-sm" disabled={offset === 0}
                        onClick={() => { setExpanded(null); setOffset(Math.max(0, offset - PAGE_SIZE)); }}>
                  Previous
                </button>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total.toLocaleString()}
                </span>
                <button className="ca-btn ca-btn-ghost ca-btn-sm" disabled={offset + PAGE_SIZE >= total}
                        onClick={() => { setExpanded(null); setOffset(offset + PAGE_SIZE); }}>
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {runs && runs.length > 0 && (
        <div className="ca-card">
          <div className="ca-card-title" style={{ marginBottom: 12 }}>Recent runs</div>
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">Validation run history.</caption>
              <thead>
                <tr>
                  <th scope="col">Started</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Open</th>
                  <th scope="col" style={{ textAlign: 'right' }}>New</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Resolved</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.started_at)}</td>
                    <td style={{ textAlign: 'right' }}>{r.n_findings}</td>
                    <td style={{ textAlign: 'right', color: r.n_new > 0 ? 'var(--accent3)' : 'var(--muted)' }}>
                      {r.n_new}
                    </td>
                    <td style={{ textAlign: 'right', color: r.n_resolved > 0 ? 'var(--accent)' : 'var(--muted)' }}>
                      {r.n_resolved}
                    </td>
                    <td style={{ color: 'var(--muted)' }}>{r.note || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 10 }}>
            A finding that a later run stops observing is stamped resolved, never deleted — which is what makes
            &ldquo;did my fix land?&rdquo; answerable. One that comes back un-resolves rather than appearing new.
          </div>
        </div>
      )}
    </div>
  );
}
