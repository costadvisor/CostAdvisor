import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import RegionSelect from '../components/RegionSelect';
import exportCsv from '../utils/exportCsv';

/**
 * The faceted explorer and producer directory (Wave 3, SCRUM-77 / INT-3).
 *
 * Four rules the API was shaped around, which this must not undo:
 *
 *  - **`functionality` and `functionality_family` are two disjoint schemes** —
 *    two naming systems for the same idea, with essentially no overlap. Merging
 *    them into one facet gives you two halves and no way to tell which half a
 *    filter is acting on, so they render as separate rails.
 *  - **Two grains, same component.** `platform` answers "which formulas carry
 *    this" (what the Intelligence library renders); `team` answers "which of MY
 *    products carry this" (Portfolio, and the audit use case). A single
 *    "products" framing loses one of them.
 *  - **Every hit carries its audit trail** — the alias that matched, the raw
 *    value the source said, the region, and platform-vs-team scope. A bare list
 *    of names cannot be checked by the person who has to act on it.
 *  - **A region filter still admits the "every region" claims.** They are shown
 *    and labelled, because an EU query that quietly dropped them would look like
 *    it had lost every global assertion.
 *
 * On the producer side: one raw string can name several companies, so the alias
 * list is shown; and a share of zero means *not disclosed* on virtually every
 * source row, so it never renders as 0%.
 */

const KIND_LABEL = {
  functionality: 'Functionality',
  functionality_family: 'Functionality (family scheme)',
  industry: 'Industry',
  compliance_flag: 'Compliance flag',
  supply_region: 'Supply region',
  substitution_risk: 'Substitution risk',
};

const KIND_NOTE = {
  functionality: 'The 41-term controlled taxonomy.',
  functionality_family: 'A separate 22-term scheme with essentially no overlap with the taxonomy above — kept apart on purpose, because a crosswalk between them is a judgement call nobody has made yet.',
  industry: 'Most raw industry strings need an analyst mapping; the unresolved ones are in Curation.',
  compliance_flag: 'Terms come from analyst decisions, not from the raw labels — many of those are whole sentences, which is not a facet.',
  supply_region: 'Where the material is produced, as asserted by the source.',
  substitution_risk: 'How replaceable the material is.',
};

/* ── Facet explorer ───────────────────────────────────────────────────────── */

function Explorer({ teamId }) {
  const navigate = useNavigate();
  const [kinds, setKinds] = useState([]);
  const [kind, setKind] = useState(null);
  const [terms, setTerms] = useState([]);
  const [term, setTerm] = useState(null);
  const [grain, setGrain] = useState('platform');
  const [region, setRegion] = useState('');
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!teamId) return;
    api.get('/api/dimensions/kinds', { params: { team_id: teamId } })
      .then(({ data }) => {
        const populated = (data.kinds || []).filter(k => k.terms > 0);
        setKinds(data.kinds || []);
        if (populated.length) setKind(populated[0].kind);
      })
      .catch(e => setErr(formatApiError(e) || 'Could not load facets.'));
  }, [teamId]);

  useEffect(() => {
    if (!teamId || !kind) return;
    setTerms([]); setTerm(null); setResult(null);
    api.get('/api/dimensions/terms', { params: { team_id: teamId, kind } })
      .then(({ data }) => setTerms(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load terms.'));
  }, [teamId, kind]);

  const run = useCallback(() => {
    if (!teamId || !kind || !term) return;
    setLoading(true); setErr(null); setResult(null);
    api.get('/api/dimensions/query', {
      params: { team_id: teamId, kind, code: term.code, grain, ...(region ? { region } : {}) },
    })
      .then(({ data }) => setResult(data))
      .catch(e => setErr(formatApiError(e) || 'Query failed.'))
      .finally(() => setLoading(false));
  }, [teamId, kind, term, grain, region]);

  useEffect(run, [run]);

  const hits = result?.hits || [];
  // Shown separately rather than filtered out: an EU query that dropped the
  // "every region" claims would look like it had lost every global assertion.
  const globalHits = region ? hits.filter(h => h.region === null || h.region === undefined) : [];
  // An assertion with no recorded alias cannot be checked: nothing says how the
  // source's wording became this term. Counted and surfaced rather than mixed
  // silently into the total, because a facet is only as good as its mapping.
  const unverified = hits.filter(h => !h.matched_alias);

  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      {/* Facet rail */}
      <div style={{ width: 250, flexShrink: 0 }}>
        <div className="ca-card" style={{ padding: 12 }}>
          <div className="ca-card-title" style={{ fontSize: 11, marginBottom: 8 }}>Facets</div>
          {kinds.map(k => (
            <button key={k.kind} onClick={() => setKind(k.kind)}
              disabled={k.terms === 0}
              title={KIND_NOTE[k.kind]}
              style={{
                display: 'flex', justifyContent: 'space-between', width: '100%',
                background: kind === k.kind ? 'var(--surface3)' : 'none',
                border: 0, borderRadius: 5, padding: '6px 8px', marginBottom: 2,
                fontSize: 11, textAlign: 'left',
                cursor: k.terms === 0 ? 'not-allowed' : 'pointer',
                color: k.terms === 0 ? 'var(--muted)'
                  : kind === k.kind ? 'var(--text)' : 'var(--text-secondary)',
                fontWeight: kind === k.kind ? 600 : 400,
              }}>
              <span>{KIND_LABEL[k.kind] || k.kind}</span>
              <span style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                {k.terms}
              </span>
            </button>
          ))}
        </div>

        {kind && (
          <div className="ca-card" style={{ padding: 12, marginTop: 12 }}>
            <div className="ca-card-title" style={{ fontSize: 11, marginBottom: 4 }}>
              {KIND_LABEL[kind] || kind}
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5, marginBottom: 8 }}>
              {KIND_NOTE[kind]}
            </div>
            <div style={{ maxHeight: 380, overflowY: 'auto' }}>
              {terms.length === 0 && (
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>No terms in this facet.</div>
              )}
              {terms.map(t => (
                <button key={t.id} onClick={() => setTerm(t)}
                  title={t.description || t.code}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    background: term?.id === t.id ? 'var(--surface3)' : 'none',
                    border: 0, borderRadius: 5, padding: '5px 8px', marginBottom: 1,
                    fontSize: 11, cursor: 'pointer',
                    color: term?.id === t.id ? 'var(--text)' : 'var(--text-secondary)',
                    fontWeight: term?.id === t.id ? 600 : 400,
                  }}>
                  {t.label}
                  {t.team_id && (
                    <span className="ca-badge" title="Your team's own term, not the platform vocabulary"
                      style={{ marginLeft: 5, background: 'var(--surface2)', color: 'var(--muted)', fontSize: 9 }}>
                      team
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      <div style={{ flex: 1, minWidth: 380 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
          {/* Two grains, one component. */}
          {[['platform', 'Platform formulas'], ['team', 'My products']].map(([g, l]) => (
            <button key={g} onClick={() => setGrain(g)}
              className={`ca-btn ca-btn-sm ${grain === g ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
              title={g === 'platform'
                ? 'Which formulas in the shared library carry this term'
                : "Which of your own products and cost models carry it"}>
              {l}
            </button>
          ))}
          <div style={{ width: 180 }}>
            <RegionSelect value={region} onChange={setRegion} includeEmpty
              emptyLabel="All regions" />
          </div>
        </div>

        {!term && (
          <div className="ca-card" style={{ padding: 32, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
            Pick a term on the left to see what carries it.
          </div>
        )}
        {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
        {loading && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

        {term && result && (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{term.label}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                {result.code} · {result.total} {grain === 'team' ? 'product rows' : 'formulas'}
              </span>
              {hits.length > 0 && (
                <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ marginLeft: 'auto', fontSize: 10 }}
                  onClick={() => exportCsv(
                    `dimension_${result.kind}_${result.code}_${grain}.csv`,
                    grain === 'team'
                      ? ['Product', 'Cost model region', 'Region of claim', 'Applies here',
                         'Term', 'Matched alias', 'Source said', 'Scope', 'Source']
                      : ['Formula', 'Subject code', 'Region of claim', 'Term',
                         'Matched alias', 'Source said', 'Scope', 'Source'],
                    hits.map(h => grain === 'team'
                      ? [h.product_name || '', h.cost_model_region || '', h.region || 'all regions',
                         h.region_applies ? 'Yes' : 'No', h.term_label, h.matched_alias || '',
                         h.raw_value || '', h.scope, h.source]
                      : [h.template_name || '', h.subject_code, h.region || 'all regions',
                         h.term_label, h.matched_alias || '', h.raw_value || '', h.scope, h.source]),
                  )}>Export CSV</button>
              )}
            </div>

            {unverified.length > 0 && (
              <div style={{
                fontSize: 10, color: 'var(--accent3)', marginBottom: 8, lineHeight: 1.5,
                padding: '6px 10px', background: 'var(--warn-bg)', borderRadius: 6,
              }}>
                <strong>{unverified.length}</strong> of {hits.length} carry no recorded alias —
                nothing states how the source's wording was mapped to this term. Check the raw
                value in the “Matched on” column: where it plainly is not this term, the mapping
                behind it no longer exists and the hit should not be trusted. Unmapped values
                are worked in <button onClick={() => navigate('/curation')}
                  style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer',
                           color: 'var(--accent4)', textDecoration: 'underline', font: 'inherit' }}>
                  Curation</button>.
              </div>
            )}

            {region && (
              /* Stated, not implied. */
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8, lineHeight: 1.5 }}>
                Filtered to <strong>{region}</strong>. This still includes{' '}
                <strong>{globalHits.length}</strong> claim{globalHits.length === 1 ? '' : 's'} that
                apply to every region — dropping them would make an EU query look like it had
                lost every global assertion.
              </div>
            )}

            {hits.length === 0 ? (
              <div className="ca-card" style={{ padding: 28, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
                Nothing carries this term{region ? ` in ${region}` : ''}
                {grain === 'team' ? ' among your products' : ' in the platform library'}.
              </div>
            ) : (
              <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
                <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
                  <thead>
                    <tr>
                      <th>{grain === 'team' ? 'Product' : 'Formula'}</th>
                      <th style={{ width: 110 }}>Region</th>
                      {/* The audit trail. Without it a reader has a list of names
                          they cannot check. */}
                      <th>Matched on</th>
                      <th style={{ width: 90 }}>Scope</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hits.map((h, i) => (
                      <tr key={i}
                        onClick={() => {
                          if (grain === 'team' && h.cost_model_id) navigate(`/portfolio/${h.cost_model_id}`);
                        }}
                        style={{ cursor: grain === 'team' && h.cost_model_id ? 'pointer' : 'default' }}>
                        <td>
                          {grain === 'team' ? (h.product_name || '—')
                            : (h.template_name || h.subject_code)}
                          <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                            {h.subject_code}
                          </div>
                        </td>
                        <td>
                          {h.region ? (
                            <span style={{ fontSize: 11 }}>{h.region}</span>
                          ) : (
                            <span style={{ fontSize: 10, color: 'var(--muted)' }}
                              title="This claim carries no region, so it applies everywhere">
                              all regions
                            </span>
                          )}
                          {grain === 'team' && h.region_applies === false && (
                            <span className="ca-badge" style={{
                              marginLeft: 4, background: 'var(--warn-bg)', color: 'var(--accent3)', fontSize: 9,
                            }} title={`Asserted for ${h.region}; this cost model is ${h.cost_model_region}. A region-specific claim does not carry across regions.`}>
                              not here
                            </span>
                          )}
                        </td>
                        <td style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
                          {h.matched_alias ? (
                            <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{h.matched_alias}</span>
                          ) : (
                            /* NOT "direct" — a missing alias is an absent audit
                               trail, not a clean match. Some are harmless (a
                               loader path that never recorded one); some are
                               rows whose mapping no longer exists at all. The
                               raw value beside the term is what lets a reader
                               tell which, so it is always shown. */
                            <span style={{ color: 'var(--accent3)' }}
                              title="No alias recorded for this assertion — nothing states how the source's wording was mapped to this term.">
                              no alias
                            </span>
                          )}
                          {h.raw_value && h.raw_value !== h.matched_alias && (
                            <span style={{ color: 'var(--muted)' }} title="What the source actually said">
                              {' '}← {h.raw_value}
                            </span>
                          )}
                        </td>
                        <td>
                          <span className="ca-badge" style={{
                            background: 'var(--surface2)',
                            color: h.scope === 'team' ? 'var(--accent4)' : 'var(--muted)',
                          }} title={h.scope === 'team'
                            ? "Your team's own assertion"
                            : 'From the shared platform library'}>
                            {h.scope}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Producer directory ───────────────────────────────────────────────────── */

function ProducerDetail({ producerId, teamId, onClose }) {
  const [p, setP] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setP(null); setErr(null);
    api.get(`/api/dimensions/producers/${producerId}`, { params: { team_id: teamId } })
      .then(({ data }) => setP(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load this producer.'));
  }, [producerId, teamId]);

  return (
    <div className="ca-card" style={{ marginTop: 12, padding: 14 }}>
      {err && <div style={{ color: 'var(--accent2)', fontSize: 12 }}>{err}</div>}
      {!p && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}
      {p && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div>
              <div className="ca-card-title" style={{ marginBottom: 2 }}>{p.name}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                {p.hq_country || 'HQ not stated'} · {p.source}
                {p.source === 'minted' && ' (no alias mapping — created from the raw name)'}
              </div>
            </div>
            <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={onClose}>Close</button>
          </div>

          {/* One raw string can name several companies, and a fifth of names have
              no alias mapping at all — so the spellings behind a producer are
              part of the record, not trivia. */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: 0.4, marginBottom: 4 }}>
              KNOWN AS ({p.aliases.length})
            </div>
            {p.aliases.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>No recorded spellings.</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {p.aliases.map(a => (
                  <span key={a} className="ca-badge" style={{
                    background: 'var(--surface2)', color: 'var(--text-secondary)', fontWeight: 500,
                  }}>{a}</span>
                ))}
              </div>
            )}
          </div>

          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: 0.4, marginBottom: 4 }}>
              MAKES ({p.portfolio.length})
            </div>
            {p.portfolio.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>No products recorded.</div>
            ) : (
              <table className="ca-table" style={{ fontSize: 11 }}>
                <thead>
                  <tr>
                    <th>Formula</th>
                    <th style={{ width: 100 }}>Region</th>
                    <th style={{ width: 120 }}>Share</th>
                  </tr>
                </thead>
                <tbody>
                  {p.portfolio.map((f, i) => (
                    <tr key={i}>
                      <td style={{ fontFamily: "'JetBrains Mono', monospace" }}
                        title={f.raw_name && f.raw_name !== p.name ? `Source says: ${f.raw_name}` : undefined}>
                        {f.subject_code}
                      </td>
                      <td style={{ color: 'var(--text-secondary)' }}>
                        {f.region || (f.regions_raw || []).join(', ') || '—'}
                      </td>
                      {/* A zero share means undisclosed on virtually every source
                          row, so it must never render as "0%". */}
                      <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                        {f.share_disclosed && f.share_pct !== null && f.share_pct !== undefined
                          ? `${f.share_pct}%`
                          : <span style={{ color: 'var(--muted)', fontFamily: 'inherit' }}>not disclosed</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Producers({ teamId }) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    if (!teamId) return;
    const handle = setTimeout(() => {
      setRows(null); setErr(null);
      api.get('/api/dimensions/producers', {
        params: { team_id: teamId, limit: 200, ...(q.trim() ? { q: q.trim() } : {}) },
      })
        .then(({ data }) => setRows(data))
        .catch(e => setErr(formatApiError(e) || 'Could not load producers.'));
    }, 250);
    return () => clearTimeout(handle);
  }, [teamId, q]);

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        The platform company master — a producer exists independently of any buying team,
        which is why it is not a supplier record. This is what pays off the caveat every
        supplier trust score carries today: scores are still keyed on a raw supplier name,
        so one company's history can split across several spellings.
      </p>

      <input className="ca-input" style={{ maxWidth: 320, marginBottom: 12 }}
        placeholder="Search producers…" value={q} onChange={e => setQ(e.target.value)} />

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
      {!rows && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

      {rows && rows.length === 0 && (
        <div className="ca-card" style={{ padding: 28, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
          No producers{q ? ` matching "${q}"` : ''}.
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
            <thead>
              <tr>
                <th>Producer</th>
                <th style={{ width: 120 }}>HQ</th>
                <th style={{ width: 100, textAlign: 'right' }}>Spellings</th>
                <th style={{ width: 110 }}>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(p => (
                <tr key={p.id} onClick={() => setOpenId(openId === p.id ? null : p.id)}
                  tabIndex={0} style={{ cursor: 'pointer' }}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenId(openId === p.id ? null : p.id); } }}>
                  <td>{p.name}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{p.hq_country || '—'}</td>
                  <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                    {p.alias_count}
                  </td>
                  <td>
                    <span className="ca-badge" style={{
                      background: 'var(--surface2)',
                      color: p.source === 'minted' ? 'var(--accent3)' : 'var(--muted)',
                    }} title={p.source === 'minted'
                      ? 'No alias mapping covered this name, so a producer was created from it rather than dropping the data'
                      : 'Came from the supplied alias map'}>
                      {p.source}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openId && <ProducerDetail producerId={openId} teamId={teamId} onClose={() => setOpenId(null)} />}
    </>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function Dimensions() {
  const { activeTeamId } = useAuth();
  const [tab, setTab] = useState('explore');

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Dimensions</div>
      <p className="ca-subtitle">
        What the library is made of, sliced by facet — and who actually makes it.
      </p>

      <div style={{ display: 'flex', gap: 6, margin: '14px 0', flexWrap: 'wrap' }}>
        {[['explore', 'Explore'], ['producers', 'Producers']].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`ca-btn ca-btn-sm ${tab === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'explore' && <Explorer teamId={activeTeamId} />}
      {tab === 'producers' && <Producers teamId={activeTeamId} />}
    </div>
  );
}
