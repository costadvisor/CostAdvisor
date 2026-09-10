import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import SheetRoundTripPanel from '../components/SheetRoundTripPanel';
import exportCsv from '../utils/exportCsv';

/**
 * The curation console (Wave 3, W3-I).
 *
 * Named as the consumer by three tickets — SCRUM-76 (editorial), 77 (dimensions)
 * and 78 (trust) — and it is the one screen that makes the data *get better*
 * rather than stay as it is. Three queues, one page:
 *
 *  1. **Trust review** — combos whose inputs do not resolve cleanly, ranked by
 *     severity, with the reasons naming the type-codes so a reviewer knows what
 *     to go and look at rather than being handed an ungraded verdict.
 *  2. **Editorial approvals** — the four-state provenance ladder. Only
 *     `human_approved` clears the caveat, and an edit clears the approval, so a
 *     block can never keep reading as signed off after its text changed.
 *  3. **Dimension decisions** — the unresolved register, ranked by how many
 *     source assertions each value blocked, plus the export/decide/reimport flow
 *     on the mechanism that already ships.
 *
 * Approving is a different permission from editing (`content.approve`): before
 * that split, whoever authored a set of weights could vouch for their own work.
 */

const GRADE = {
  blocked: { label: 'BLOCKED', color: 'var(--accent2)', bg: 'var(--danger-bg)' },
  low: { label: 'LOW', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  unrated: { label: 'UNRATED', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
  medium: { label: 'MED', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  high: { label: 'HIGH', color: 'var(--accent)', bg: 'var(--success-bg)' },
};

// Every reason states what a reviewer would actually do about it. An ungraded
// "low" is exactly the thing the derivation stores its reasons to avoid.
const REASON = {
  type_code_resolves_to_no_series: 'No price series behind',
  type_code_is_ambiguous: 'Ambiguous type code',
  line_has_no_type_code_link: 'Lines with no type-code link',
  priced_through_a_proxy: 'Priced through a stand-in',
  line_and_type_code_disagree_on_proxy_status: 'Line and registry disagree on stand-in status',
  weight_set_does_not_close: 'Weights do not sum to 100',
  combo_has_no_cost_lines: 'No cost lines',
};

const PROVENANCE = {
  imported: { label: 'Reference data', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
  ai_draft: { label: 'AI draft', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  human_edited: { label: 'Analyst edited', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  human_approved: { label: 'Analyst approved', color: 'var(--accent)', bg: 'var(--success-bg)' },
};

const TABS = [
  { key: 'trust', label: 'Trust review' },
  { key: 'editorial', label: 'Editorial approvals' },
  { key: 'dimensions', label: 'Dimension decisions' },
];

function Badge({ map, value }) {
  const m = map[value] || { label: value || '—', color: 'var(--muted)', bg: 'var(--surface2)' };
  return (
    <span className="ca-badge" style={{ background: m.bg, color: m.color, fontWeight: 600 }}>
      {m.label}
    </span>
  );
}

/* ── 1. Trust review ──────────────────────────────────────────────────────── */

function ReasonList({ inputs }) {
  const reasons = inputs?.reasons || [];
  if (!reasons.length) return <span style={{ color: 'var(--muted)' }}>—</span>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {reasons.map((r, i) => (
        <div key={i} style={{ fontSize: 10, lineHeight: 1.45 }}>
          <span style={{ color: 'var(--text-secondary)' }}>{REASON[r.reason] || r.reason}</span>
          {r.subjects?.length > 0 && (
            <span style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
              {' '}{r.subjects.join(', ')}
              {r.weight_share_pct !== null && r.weight_share_pct !== undefined
                && ` · ${r.weight_share_pct}% of weight`}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function TrustQueue({ teamId }) {
  const { addToast } = useToast();
  const navigate = useNavigate();
  const [grades, setGrades] = useState(['blocked', 'low']);
  const [pending, setPending] = useState(true);   // needs_review filter
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    if (!teamId) return;
    setData(null); setErr(null);
    api.get('/api/formulas/review-queue', {
      params: {
        team_id: teamId, grade: grades, needs_review: pending,
        order_by: 'severity', limit: 200,
      },
      // FastAPI reads a repeated query param as a list; the default axios
      // serializer would send grade[]=… and the filter would match nothing.
      paramsSerializer: { indexes: null },
    })
      .then(({ data: d }) => setData(d))
      .catch(e => setErr(formatApiError(e) || 'Could not load the review queue.'));
  }, [teamId, grades, pending]);

  useEffect(load, [load]);

  const signOff = async (row) => {
    const key = `${row.template_id}:${row.region}`;
    setBusy(key);
    try {
      // No team_id: the endpoint resolves the tier from the template itself
      // (platform vs a team's fork) and gates each on the right side.
      await api.post(
        `/api/formulas/${row.template_id}/coverage/${encodeURIComponent(row.region)}/review`);
      addToast(`Signed off ${row.template_code || row.template_name} @ ${row.region}.`, 'success');
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not sign off — approving needs content.approve', 'error');
    } finally { setBusy(null); }
  };

  const toggleGrade = (g) => setGrades(gs =>
    gs.includes(g) ? gs.filter(x => x !== g) : [...gs, g]);

  // In the "signed off / not queued" view the endpoint's severity order buries
  // the handful of rows a person actually signed among hundreds that simply
  // never queued. Those are the rows being looked for, so they come first.
  const rows = (data?.rows || []).slice().sort((a, b) => {
    if (pending) return 0;
    return (b.reviewed_at ? 1 : 0) - (a.reviewed_at ? 1 : 0);
  });

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Combos whose inputs do not resolve cleanly, worst first. Ordering by region or name
        would have a reviewer reading alphabetically through something whose whole point is
        triage. <strong>A sign-off is pinned to the exact line set it was given for</strong> —
        change a weight and the combo comes back here; reorder the same lines and it does not,
        because order is presentation.
      </p>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {['blocked', 'low', 'unrated', 'medium', 'high'].map(g => (
          <button key={g} onClick={() => toggleGrade(g)}
            className={`ca-btn ca-btn-sm ${grades.includes(g) ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            style={{ fontSize: 10 }}>
            {GRADE[g].label}
          </button>
        ))}
        <div style={{ width: 1, height: 22, background: 'var(--border)', margin: '0 4px' }} />
        <button onClick={() => setPending(true)}
          className={`ca-btn ca-btn-sm ${pending ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          style={{ fontSize: 10 }}>Awaiting review</button>
        <button onClick={() => setPending(false)}
          className={`ca-btn ca-btn-sm ${!pending ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          style={{ fontSize: 10 }}
          title="Combos already signed off, and combos whose grade does not queue them">
          Signed off / not queued
        </button>
        {rows.length > 0 && (
          <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ marginLeft: 'auto', fontSize: 10 }}
            onClick={() => exportCsv('trust_review_queue.csv',
              ['Code', 'Formula', 'Region', 'Scope', 'Grade', 'Needs review',
               'Coverage tier', 'Proxy density', 'Reasons', 'Reviewed by', 'Reviewed at'],
              rows.map(r => [
                r.template_code || '', r.template_name || '', r.region, r.scope,
                r.trust_grade || '', r.needs_review ? 'Yes' : 'No',
                r.coverage_tier || '', r.proxy_density_tier || '',
                (r.trust_inputs?.reasons || [])
                  .map(x => `${REASON[x.reason] || x.reason}${x.subjects?.length ? `: ${x.subjects.join('/')}` : ''}`)
                  .join(' | '),
                r.reviewed_by_name || '', r.reviewed_at || '',
              ]))}>Export CSV</button>
        )}
      </div>

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
      {!data && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

      {data && rows.length === 0 && (
        <div className="ca-card" style={{ padding: 28, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
          {grades.length === 0
            ? 'Select at least one grade.'
            : pending
              ? 'Nothing awaiting review at these grades.'
              : 'Nothing signed off at these grades yet.'}
        </div>
      )}

      {data && rows.length > 0 && (
        <>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6 }}>
            {rows.length} of {data.total} · ordered by {data.order_by}
          </div>
          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Code</th>
                  <th>Formula</th>
                  <th style={{ width: 80 }}>Region</th>
                  <th style={{ width: 80 }}>Grade</th>
                  <th>Why</th>
                  <th style={{ width: 150 }}>{pending ? '' : 'Signed off'}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const key = `${r.template_id}:${r.region}`;
                  return (
                    <tr key={`${key}:${r.variant}`}>
                      <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                        {r.template_code || '—'}
                        {r.scope === 'team' && (
                          <span className="ca-badge" style={{
                            marginLeft: 4, background: 'var(--surface2)',
                            color: 'var(--muted)', fontSize: 9,
                          }}>fork</span>
                        )}
                      </td>
                      <td>
                        <button onClick={() => navigate('/formulas')}
                          style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer',
                                   color: 'var(--accent4)', textAlign: 'left' }}>
                          {r.template_name || '—'}
                        </button>
                      </td>
                      <td>{r.region}</td>
                      <td><Badge map={GRADE} value={r.trust_grade} /></td>
                      <td><ReasonList inputs={r.trust_inputs} /></td>
                      <td>
                        {r.needs_review ? (
                          <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ fontSize: 10 }}
                            disabled={busy === key} onClick={() => signOff(r)}
                            title="Records that an expert has vouched for this combo's inputs. Pinned to the current line set.">
                            {busy === key ? 'Signing…' : 'Sign off'}
                          </button>
                        ) : r.reviewed_at ? (
                          <div style={{ fontSize: 10, lineHeight: 1.4 }}>
                            <div>{r.reviewed_by_name || 'signed off'}</div>
                            <div style={{ color: 'var(--muted)' }}>
                              {String(r.reviewed_at).slice(0, 10)}
                              {r.review_fingerprint && (
                                <span title={`Pinned to line set ${r.review_fingerprint}. Editing a weight returns this combo to the queue.`}
                                  style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                                  {' '}· {r.review_fingerprint.slice(0, 8)}
                                </span>
                              )}
                            </div>
                          </div>
                        ) : (
                          <span style={{ fontSize: 10, color: 'var(--muted)' }}>not queued</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

/* ── 2. Editorial approvals ───────────────────────────────────────────────── */

function VersionHistory({ blockId }) {
  const [versions, setVersions] = useState(null);
  useEffect(() => {
    api.get(`/api/editorial/blocks/${blockId}/versions`)
      .then(({ data }) => setVersions(data))
      .catch(() => setVersions([]));
  }, [blockId]);
  if (!versions) return <div style={{ fontSize: 10, color: 'var(--muted)' }}>Loading history…</div>;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: 0.4, marginBottom: 4 }}>
        VERSION HISTORY
      </div>
      {versions.map(v => (
        <div key={v.id} style={{ fontSize: 10, padding: '3px 0', borderTop: '1px solid var(--border)' }}>
          <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>v{v.version_no}</span>
          {' · '}<Badge map={PROVENANCE} value={v.provenance} />
          {' '}<span style={{ color: 'var(--muted)' }}>{String(v.created_at).slice(0, 10)}</span>
          {v.change_note && <span style={{ color: 'var(--text-secondary)' }}> — {v.change_note}</span>}
        </div>
      ))}
    </div>
  );
}

function EditorialQueue({ teamId }) {
  const { addToast } = useToast();
  const [states, setStates] = useState(['imported', 'ai_draft', 'human_edited']);
  const [blocks, setBlocks] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!teamId) return;
    setBlocks(null); setErr(null);
    api.get('/api/editorial/blocks', {
      params: { team_id: teamId, provenance: states, limit: 300 },
      paramsSerializer: { indexes: null },
    })
      .then(({ data }) => setBlocks(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load editorial blocks.'));
  }, [teamId, states]);

  useEffect(load, [load]);

  const toggleState = (s) => setStates(ss =>
    ss.includes(s) ? ss.filter(x => x !== s) : [...ss, s]);

  const approve = async (b) => {
    setBusy(true);
    try {
      await api.post(`/api/editorial/blocks/${b.id}/approve`, null, { params: { team_id: teamId } });
      addToast('Approved — the caveat is cleared for this block.', 'success');
      setOpen(null); load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not approve — this needs content.approve', 'error');
    } finally { setBusy(false); }
  };

  const saveEdit = async (b) => {
    setBusy(true);
    try {
      await api.put(`/api/editorial/blocks/${b.id}`,
        { body_text: draft, body_format: 'text', provenance: 'human_edited' },
        { params: { team_id: teamId } });
      addToast('Saved as a new version — this clears any prior approval.', 'success');
      setOpen(null); load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not save', 'error');
    } finally { setBusy(false); }
  };

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Four states, and only <strong>Analyst approved</strong> clears the customer-facing
        caveat. An edit appends a version and <strong>drops the approval</strong> — a block
        must never keep reading as signed off after its text changed. A bulk import is
        neither AI-drafted nor approved, which is why two states would not do.
      </p>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {Object.keys(PROVENANCE).map(s => (
          <button key={s} onClick={() => toggleState(s)}
            className={`ca-btn ca-btn-sm ${states.includes(s) ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            style={{ fontSize: 10 }}>
            {PROVENANCE[s].label}
          </button>
        ))}
      </div>

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
      {!blocks && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

      {blocks && blocks.length === 0 && (
        <div className="ca-card" style={{ padding: 28, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
            {states.length === 0 ? 'Select at least one state.' : 'No editorial blocks in these states.'}
          </div>
          {states.length > 0 && (
            /* Say why it is empty rather than implying everything is approved.
               The storage and the review workflow ship; the bulk loader that
               fills them from the reference drop does not exist yet. */
            <div style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 520, margin: '0 auto', lineHeight: 1.6 }}>
              The store and this review workflow are in place, but the bulk loader that
              fills them from the reference library has not been built — so an empty queue
              here means "nothing authored yet", not "everything approved".
            </div>
          )}
        </div>
      )}

      {blocks && blocks.length > 0 && (
        <>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6 }}>{blocks.length} blocks</div>
          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ width: 90 }}>Subject</th>
                  <th style={{ width: 170 }}>Code</th>
                  <th style={{ width: 130 }}>Block</th>
                  <th style={{ width: 130 }}>State</th>
                  <th style={{ width: 60 }}>Ver</th>
                  <th style={{ width: 70 }} />
                </tr>
              </thead>
              <tbody>
                {blocks.map(b => (
                  <tr key={b.id}>
                    <td style={{ color: 'var(--text-secondary)' }}>{b.subject_type}</td>
                    <td style={{ fontFamily: "'JetBrains Mono', monospace" }} title={b.subject_code}>
                      {b.subject_code}
                    </td>
                    <td>{b.block_type}</td>
                    <td>
                      <Badge map={PROVENANCE} value={b.provenance} />
                      {b.team_id === null && (
                        <span style={{ fontSize: 9, color: 'var(--muted)' }} title="Platform library"> plat</span>
                      )}
                    </td>
                    <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                      {b.current_version_no ?? '—'}
                    </td>
                    <td>
                      <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ fontSize: 10 }}
                        onClick={() => {
                          setOpen(open?.id === b.id ? null : b);
                          setDraft(b.body_text || '');
                        }}>
                        {open?.id === b.id ? 'Close' : 'Open'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {open && (
        <div className="ca-card" style={{ marginTop: 12, padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div>
              <div className="ca-card-title" style={{ marginBottom: 2 }}>
                {open.block_type} · {open.subject_code}
              </div>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                {open.subject_type}{open.region ? ` · ${open.region}` : ' · all regions'}
                {' · '}v{open.current_version_no ?? '—'}
              </div>
            </div>
            <Badge map={PROVENANCE} value={open.provenance} />
          </div>

          {/* The badge's own caveat, shipped with the state machine rather than
              re-invented per screen. */}
          {open.badge?.caveat && (
            <div style={{
              fontSize: 11, color: 'var(--accent3)', marginTop: 8,
              padding: '6px 10px', background: 'var(--warn-bg)', borderRadius: 6,
            }}>
              {open.badge.caveat}
            </div>
          )}

          {open.body_json ? (
            <pre style={{
              fontSize: 10, marginTop: 10, maxHeight: 220, overflow: 'auto',
              background: 'var(--surface2)', padding: 10, borderRadius: 6,
            }}>{JSON.stringify(open.body_json, null, 2)}</pre>
          ) : (
            <textarea className="ca-input" rows={6} style={{ marginTop: 10, fontSize: 12 }}
              value={draft} onChange={e => setDraft(e.target.value)} />
          )}

          {open.internal_note && (
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>
              {/* Author-to-self backlog text; deliberately never rendered to a customer. */}
              Internal note (not customer-facing): {open.internal_note}
            </div>
          )}

          <VersionHistory blockId={open.id} />

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
            {!open.body_json && (
              <button className="ca-btn ca-btn-sm ca-btn-ghost" disabled={busy || draft === (open.body_text || '')}
                onClick={() => saveEdit(open)}
                title="Appends a new version and clears any approval — the text changed, so the sign-off no longer applies to it">
                Save as new version
              </button>
            )}
            {open.provenance !== 'human_approved' && (
              <button className="ca-btn ca-btn-sm ca-btn-primary" disabled={busy}
                onClick={() => approve(open)}
                title="Needs content.approve — separate from content.edit so an author cannot vouch for their own text">
                Approve
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/* ── 3. Dimension decisions ───────────────────────────────────────────────── */

const KIND_LABEL = {
  functionality: 'Functionality',
  functionality_family: 'Functionality (family scheme)',
  industry: 'Industry',
  compliance_flag: 'Compliance flag',
  supply_region: 'Supply region',
  substitution_risk: 'Substitution risk',
};

function DimensionQueue({ teamId }) {
  const [kind, setKind] = useState('');
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!teamId) return;
    setRows(null); setErr(null);
    api.get('/api/dimensions/unresolved', {
      params: { team_id: teamId, ...(kind ? { kind } : {}) },
    })
      .then(({ data }) => setRows(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the unresolved register.'));
  }, [teamId, kind]);

  const kinds = [...new Set((rows || []).map(r => r.kind))];

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Every raw value the load could not resolve, ranked by how many source assertions it
        blocked. Nothing here was guessed and nothing was dropped — a value that cannot be
        mapped stays visible until somebody decides what it means. The register is
        <strong> rebuilt on every load</strong>, so a value resolved by yesterday's decision
        stops appearing; a queue that only grows is one nobody trusts.
      </p>

      {/* The decision itself goes through the shipped round-trip mechanism:
          export the queue, fill in one column offline, reimport, read the diff,
          apply. Applying creates the alias, which is what makes the next load
          resolve it — the decision is expressed as data, not as loader branches. */}
      <SheetRoundTripPanel
        payloadKey="dimension_decision"
        title="Decide unresolved values offline"
        blurb="Export the queue, fill in the one editable column with the term each raw value maps to, reimport to see the diff, then apply. Applying creates the alias, so the next load resolves it."
        exportFilename="dimension_decisions.xlsx"
        rowKeyLabel={(k) => `${k.kind} · ${k.raw_value}`}
        filterParams={() => (kind ? { kind } : {})}
        renderFilters={() => (
          <select className="ca-select" style={{ fontSize: 11 }}
            value={kind} onChange={e => setKind(e.target.value)}>
            <option value="">All facets</option>
            {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        )}
      />

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
      {!rows && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

      {rows && rows.length === 0 && (
        <div className="ca-card" style={{ padding: 28, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
          Nothing unresolved{kind ? ` for ${KIND_LABEL[kind] || kind}` : ''}.
        </div>
      )}

      {rows && rows.length > 0 && (
        <>
          <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
            {kinds.map(k => {
              const forKind = rows.filter(r => r.kind === k);
              return (
                <div key={k} className="ca-metric" style={{ flex: 1, minWidth: 150 }}>
                  <div className="ca-metric-lbl">{KIND_LABEL[k] || k}</div>
                  <div className="ca-metric-val">{forKind.length}</div>
                  <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                    {forKind.reduce((n, r) => n + r.occurrences, 0)} assertions blocked
                  </div>
                </div>
              );
            })}
          </div>

          <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
              <thead>
                <tr>
                  <th style={{ width: 150 }}>Facet</th>
                  <th>Raw value</th>
                  <th style={{ width: 90, textAlign: 'right' }}>Blocked</th>
                  <th>Examples</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.kind}:${r.raw_value}:${i}`}>
                    <td style={{ color: 'var(--text-secondary)' }}>{KIND_LABEL[r.kind] || r.kind}</td>
                    <td title={r.reason || undefined}>{r.raw_value}</td>
                    <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                      {r.occurrences}
                    </td>
                    <td style={{ color: 'var(--muted)', fontSize: 10 }}>
                      {(r.sample_subjects || []).slice(0, 3).join(', ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function Curation() {
  const { activeTeamId } = useAuth();
  const [tab, setTab] = useState('trust');

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Curation</div>
      <p className="ca-subtitle">
        Three queues that make the library get better instead of staying as it is: which
        combos an expert should look at, which text has been vouched for, and which raw
        values nobody has decided the meaning of yet.
      </p>

      <div style={{ display: 'flex', gap: 6, margin: '14px 0', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`ca-btn ca-btn-sm ${tab === t.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'trust' && <TrustQueue teamId={activeTeamId} />}
      {tab === 'editorial' && <EditorialQueue teamId={activeTeamId} />}
      {tab === 'dimensions' && <DimensionQueue teamId={activeTeamId} />}
    </div>
  );
}
