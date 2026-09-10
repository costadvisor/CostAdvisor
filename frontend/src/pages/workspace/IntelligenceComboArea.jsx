import { useState, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { Sparkline } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * The ID card, at formula x region combo grain (Wave 3, SCRUM-75 + 76 + 77).
 *
 * **Two calls, by design.** The derived half is one endpoint (series, drivers,
 * cycle, seasonality, volatility, trust); the composed editorial + dimensions
 * half is another. Neither is a copy of the other, and folding them into one
 * would put a second source of truth behind half the page.
 *
 * Three things the old page got wrong that this fixes:
 *   - it computed a cycle percentile over whatever history it happened to have
 *     while hardcoding the words "24-month". The window, the verdict and the
 *     label now all come from the payload, generated from one constant, and the
 *     client computes none of it.
 *   - it rendered an unconditional "not yet reviewed by an in-house chemistry
 *     expert". That caveats combos nobody questioned and vouches for none of
 *     them; the caveat now comes from the combo's own trust grade.
 *   - its second tab was a placeholder waiting for editorial persistence. That
 *     store exists now, so the tab renders real blocks — and says plainly when
 *     there are none, rather than implying the content was reviewed away.
 * ──────────────────────────────────────────────────────────────────── */

const GRADE = {
  high: { label: 'HIGH', color: 'var(--accent)', bg: 'var(--success-bg)' },
  medium: { label: 'MED', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  low: { label: 'LOW', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  blocked: { label: 'BLOCKED', color: 'var(--accent2)', bg: 'var(--danger-bg)' },
  unrated: { label: 'UNRATED', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
};

const CYCLE_COLOR = {
  near_the_top: 'var(--accent2)',
  mid_range: 'var(--text-secondary)',
  near_the_bottom: 'var(--accent)',
  flat: 'var(--muted)',
};

const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

const BLOCK_LABEL = {
  functionalities: 'Functionality',
  applications: 'Applications',
  suppliers: 'Suppliers',
  supplier_note: 'Supplier note',
  compliance: 'Compliance',
  macro_drivers: 'Macro drivers',
  substitution: 'Substitution',
  supply: 'Supply',
  demand: 'Demand',
  synthesis_route: 'Synthesis route',
  current_events: 'Current events',
  narrative: 'Narrative',
};

const KIND_LABEL = {
  functionality: 'Functionality',
  functionality_family: 'Functionality (family scheme)',
  industry: 'Industry',
  compliance_flag: 'Compliance',
  supply_region: 'Supply region',
  substitution_risk: 'Substitution risk',
};

function Card({ title, sub, children }) {
  return (
    <div className="ca-card" style={{ marginBottom: 16, padding: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: sub ? 2 : 10 }}>{title}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 10 }}>{sub}</div>}
      {children}
    </div>
  );
}

function SeasonBars({ factors }) {
  const max = Math.max(6, ...factors.map(f => Math.abs(f - 100)));
  return (
    <div style={{ display: 'flex', gap: 3, height: 74, marginTop: 6 }}
      role="img" aria-label="Seasonal factors by month">
      {factors.map((f, i) => {
        const dev = f - 100;
        return (
          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}
            title={`${MONTHS[i]} · ${f.toFixed(1)}`}>
            <div style={{ flex: 1, width: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
              {dev > 0 && <div style={{ height: `${(dev / max) * 100}%`, background: 'var(--accent4)', borderRadius: '2px 2px 0 0' }} />}
            </div>
            <div style={{ height: 1, width: '100%', background: 'var(--border)' }} />
            <div style={{ flex: 1, width: '100%' }}>
              {dev < 0 && <div style={{ height: `${(-dev / max) * 100}%`, background: 'var(--accent4)', borderRadius: '0 0 2px 2px' }} />}
            </div>
            <span style={{ fontSize: 8, color: 'var(--muted)', marginTop: 2 }}>{MONTHS[i]}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ── Tab 1: the derived half ──────────────────────────────────────────────── */

function DerivedTab({ d, onFixAnchor }) {
  const levels = (d.series || []).map(p => p.level);
  const g = GRADE[d.trust?.grade || 'unrated'];

  if (!d.evaluable) {
    return (
      <div className="ca-card" style={{ padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 13, marginBottom: 8 }}>Nothing to derive yet</div>
        <div style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 520, margin: '0 auto 16px', lineHeight: 1.6 }}>
          {d.reason}
        </div>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={onFixAnchor}>
          Set a base price on Formulas
        </button>
      </div>
    );
  }

  return (
    <>
      {/* The caveat comes from the grade that produced it — one field, not a
          sentence this screen decided to always show. */}
      {d.trust?.caveat && (
        <div style={{
          fontSize: 11, lineHeight: 1.6, padding: '8px 12px', borderRadius: 6,
          background: g.bg, color: g.color, marginBottom: 16,
        }}>
          <strong>{g.label}</strong> — {d.trust.caveat}
        </div>
      )}

      {/* Kept, but it now fires only if the two sides ever diverge again. The
          costing engine gained a monthly tier, so reading the drop's series is
          no longer a difference — the payload says so and this stays silent. */}
      {d.value_sources && d.value_sources.matches_costing_engine === false && (
        <div style={{ fontSize: 10, color: 'var(--accent3)', marginBottom: 12, lineHeight: 1.55 }}>
          {d.value_sources.note
            || 'Some values here come from a price store the costing engine does not read, so a should-cost elsewhere in the app may differ on this combo.'}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
        <div className="ca-metric">
          <div className="ca-metric-lbl">Index level</div>
          <div className="ca-metric-val">{levels.length ? levels[levels.length - 1].toFixed(1) : '—'}</div>
          <div style={{ fontSize: 10, color: 'var(--muted)' }}>
            base 100 at {d.base_year} Q{d.base_quarter}
          </div>
        </div>
        {d.change && (
          <div className="ca-metric">
            <div className="ca-metric-lbl">Change</div>
            <div className="ca-metric-val" style={{
              color: (d.change.short_pct ?? 0) > 0 ? 'var(--accent2)' : 'var(--accent)',
            }}>
              {d.change.short_pct === null || d.change.short_pct === undefined
                ? '—' : `${d.change.short_pct > 0 ? '+' : ''}${d.change.short_pct.toFixed(1)}%`}
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>
              over {d.change.short_window_quarters}Q
              {d.change.long_pct !== null && d.change.long_pct !== undefined
                && ` · ${d.change.long_pct > 0 ? '+' : ''}${d.change.long_pct.toFixed(1)}% over ${d.change.long_window_quarters}Q`}
            </div>
          </div>
        )}
        {d.base_price !== null && d.base_price !== undefined && (
          <div className="ca-metric">
            <div className="ca-metric-lbl">Should-cost</div>
            <div className="ca-metric-val">
              {(d.base_price * (levels.length ? levels[levels.length - 1] : 100) / 100).toFixed(2)}
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>
              {d.currency} · anchor {d.base_price}
            </div>
          </div>
        )}
      </div>

      {levels.length >= 2 && (
        <Card title="Index history"
          sub={`${d.series.length} quarters, rebased to 100 at the combo's base period — margin sits inside the recipe, so the level is exactly 100 there by construction.`}>
          <Sparkline data={levels} width={620} height={90}
            label={`Index level over ${levels.length} quarters`} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
            <span>{d.series[0].year} Q{d.series[0].quarter}</span>
            <span>{d.series[d.series.length - 1].year} Q{d.series[d.series.length - 1].quarter}</span>
          </div>
        </Card>
      )}

      {/* Window, verdict and wording all come from the payload. The client used
          to compute a percentile over whatever it had and label it with a
          window it never used. */}
      {d.cycle && (
        <Card title="Cycle position" sub={`${d.cycle.window_label} window · ${d.cycle.periods_used} periods`}>
          <div style={{ fontSize: 13, color: CYCLE_COLOR[d.cycle.verdict] || 'var(--text)', lineHeight: 1.6 }}>
            {d.cycle.sentence}
          </div>
          {d.cycle.verdict !== 'flat' && d.cycle.percentile !== null && d.cycle.percentile !== undefined && (
            <>
              <div style={{ height: 6, background: 'var(--surface2)', borderRadius: 3, marginTop: 10, overflow: 'hidden' }}>
                <div style={{ width: `${d.cycle.percentile}%`, height: '100%', background: CYCLE_COLOR[d.cycle.verdict] }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                <span>low {d.cycle.low?.toFixed(1)}</span>
                <span>high {d.cycle.high?.toFixed(1)}</span>
              </div>
            </>
          )}
        </Card>
      )}

      {d.components?.length > 0 && (
        <Card title="Cost lines"
          sub="Contributions sum to the index level. A line with no index value rides flat rather than being dropped — dropping it would inflate everything else.">
          <table className="ca-table" style={{ fontSize: 11 }}>
            <thead>
              <tr>
                <th>Line</th>
                <th style={{ width: 70, textAlign: 'right' }}>Weight</th>
                <th style={{ width: 70, textAlign: 'right' }}>Ratio</th>
                <th style={{ width: 90, textAlign: 'right' }}>Contribution</th>
              </tr>
            </thead>
            <tbody>
              {d.components.map((c, i) => (
                <tr key={i}>
                  <td>
                    {c.name}
                    {c.is_proxy && (
                      <span className="ca-badge" title="Priced through a stand-in index — directional, not the exact traded price"
                        style={{ marginLeft: 5, background: 'var(--warn-bg)', color: 'var(--accent3)', fontSize: 9 }}>
                        PROXY
                      </span>
                    )}
                    {c.depth > 0 && (
                      <span style={{ fontSize: 9, color: 'var(--muted)' }} title="Comes from a nested formula"> ·d{c.depth}</span>
                    )}
                    {!c.has_data && (
                      <span style={{ fontSize: 9, color: 'var(--accent3)' }} title="No index value found — this line rides flat"> · flat</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>{c.weight_pct}</td>
                  <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace",
                               color: c.ratio > 1 ? 'var(--accent2)' : c.ratio < 1 ? 'var(--accent)' : 'var(--muted)' }}>
                    {c.ratio.toFixed(3)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                    {c.contribution_pct.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        {d.seasonality && (
          <Card title="Seasonality"
            sub={`spread ${d.seasonality.spread.toFixed(1)} pts · ${d.seasonality.seasonal_weight_pct.toFixed(0)}% of the recipe carries a profile`}>
            <SeasonBars factors={d.seasonality.factors} />
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6, lineHeight: 1.5 }}>
              Lines without a profile contribute flat, which damps the amplitude — that
              damping is the signal, not a rounding error.
            </div>
          </Card>
        )}
        {d.volatility && (
          <Card title="Volatility">
            {d.volatility.percentile === null || d.volatility.percentile === undefined ? (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                {d.volatility.reason || 'Not measurable for this combo.'}
              </div>
            ) : (
              <>
                <div style={{ fontSize: 26, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
                  {d.volatility.percentile}<span style={{ fontSize: 13, color: 'var(--muted)' }}>th</span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  percentile against the platform library
                </div>
                {/* Which ladder said so. A bare percentile is unfalsifiable. */}
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8, lineHeight: 1.5 }}>
                  dispersion {d.volatility.dispersion?.toFixed(2)} · {d.volatility.method} ·
                  calibration {String(d.volatility.calibration_id || '').slice(0, 8)}
                  {d.volatility.calibration_computed_at
                    && ` (${String(d.volatility.calibration_computed_at).slice(0, 10)})`}
                </div>
              </>
            )}
          </Card>
        )}
      </div>

      {d.data_gaps?.length > 0 && (
        <Card title={`Data gaps (${d.data_gaps.length})`}
          sub="Named rather than silently absorbed — each of these rode flat at ratio 1.0.">
          {d.data_gaps.map((gap, i) => (
            <div key={i} style={{ fontSize: 11, padding: '2px 0' }}>
              <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{gap.line}</span>
              <span style={{ color: 'var(--muted)' }}> — {gap.reason}</span>
            </div>
          ))}
        </Card>
      )}
    </>
  );
}

/* ── Tab 2: the composed editorial + dimensions half ──────────────────────── */

function ContextTab({ card, dims, code }) {
  const blocks = Object.entries(card?.blocks || {});
  const dimEntries = Object.entries(dims?.dimensions || {})
    .filter(([, terms]) => (terms || []).length > 0);

  if (blocks.length === 0 && dimEntries.length === 0) {
    return (
      <div className="ca-card" style={{ padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 13, marginBottom: 8 }}>No context recorded for {code}</div>
        {/* Says which of the two halves is missing and why, rather than letting
            "nothing here" read as "reviewed and found empty". */}
        <div style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 540, margin: '0 auto', lineHeight: 1.6 }}>
          Editorial blocks and dimension assertions both have somewhere to live now, but
          nothing has been authored or asserted against this formula. An empty card means
          "not written yet", not "reviewed and found to have nothing".
        </div>
      </div>
    );
  }

  return (
    <>
      {dimEntries.length > 0 && (
        <Card title="Dimensions" sub="What this formula is tagged as, and where each tag came from.">
          {dimEntries.map(([kind, terms]) => (
            <div key={kind} style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: 0.4, marginBottom: 4 }}>
                {(KIND_LABEL[kind] || kind).toUpperCase()}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {terms.map((t, i) => (
                  <span key={i} className="ca-badge"
                    title={[t.raw_value && `Source said: ${t.raw_value}`,
                            t.matched_alias && `Matched alias: ${t.matched_alias}`,
                            t.region ? `Region: ${t.region}` : 'Applies to every region']
                            .filter(Boolean).join('\n')}
                    style={{ background: 'var(--surface2)', color: 'var(--text-secondary)', fontWeight: 500 }}>
                    {t.label || t.code}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </Card>
      )}

      {blocks.map(([blockType, b]) => (
        <Card key={blockType}
          title={BLOCK_LABEL[blockType] || blockType.replace(/_/g, ' ')}
          sub={card.resolved_from?.[blockType]}>
          {/* The provenance badge and its caveat ship together with the block. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <span className="ca-badge" style={{
              background: b.badge?.reviewed ? 'var(--success-bg)' : 'var(--neutral-bg)',
              color: b.badge?.reviewed ? 'var(--accent)' : 'var(--muted)',
            }}>
              {b.badge?.label || b.provenance}
            </span>
            {b.badge?.caveat && (
              <span style={{ fontSize: 10, color: 'var(--accent3)' }}>{b.badge.caveat}</span>
            )}
            <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
              v{b.current_version_no ?? '—'}
            </span>
          </div>
          {b.body_text && (
            <div style={{ fontSize: 12, lineHeight: 1.65, whiteSpace: 'pre-wrap' }}>{b.body_text}</div>
          )}
          {b.body_json && (
            <pre style={{
              fontSize: 10, background: 'var(--surface2)', padding: 10,
              borderRadius: 6, overflow: 'auto', maxHeight: 240,
            }}>{JSON.stringify(b.body_json, null, 2)}</pre>
          )}
        </Card>
      ))}
    </>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function IntelligenceComboArea() {
  const { templateId, region, costModelId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { activeTeamId } = useAuth();

  const [tab, setTab] = useState('derived');
  const [derived, setDerived] = useState(null);
  const [card, setCard] = useState(null);
  const [dims, setDims] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  // Call 1 — the derived half. A product route resolves to the same combo and
  // reports how it got there.
  useEffect(() => {
    if (!activeTeamId) return;
    setLoading(true); setError(null); setDerived(null);
    const url = costModelId
      ? `/api/intelligence/cost-models/${costModelId}`
      : `/api/intelligence/combos/${templateId}/${encodeURIComponent(region)}`;
    api.get(url, { params: { team_id: activeTeamId } })
      .then(({ data }) => setDerived(data))
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [activeTeamId, templateId, region, costModelId]);

  // Call 2 — the composed editorial + dimensions half, keyed on the formula code
  // the first call resolved. Deliberately separate: neither is a copy of the
  // other, and one endpoint returning both would put a second source of truth
  // behind half the page.
  const code = derived?.template_code;
  useEffect(() => {
    if (!activeTeamId || !code) return;
    const enc = encodeURIComponent(code);
    const p = { team_id: activeTeamId };
    api.get(`/api/editorial/cards/formula/${enc}`,
      { params: { ...p, ...(derived?.coverage_region ? { region: derived.coverage_region } : {}) } })
      .then(({ data }) => setCard(data)).catch(() => setCard(null));
    api.get(`/api/dimensions/subjects/formula/${enc}`, { params: p })
      .then(({ data }) => setDims(data)).catch(() => setDims(null));
  }, [activeTeamId, code, derived?.coverage_region]);

  const title = derived
    ? `${derived.template_code || 'Formula'} · ${derived.coverage_region || derived.region_requested}`
    : 'Intelligence';

  return (
    <div className="ca-page ca-fade-in">
      <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginBottom: 10 }}
        onClick={() => navigate(params.get('from') === 'portfolio' ? '/portfolio' : '/intelligence')}>
        ← Back
      </button>

      <div className="ca-h1">{title}</div>
      {derived && (
        <p className="ca-subtitle" style={{ marginBottom: 0 }}>
          {derived.template_id && <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{derived.template_code}</span>}
          {derived.resolved_via && (
            <span style={{ color: 'var(--muted)' }}> · reached via {derived.resolved_via}</span>
          )}
          {derived.coverage_region && derived.coverage_region !== derived.region_requested && (
            <span style={{ color: 'var(--accent3)' }}>
              {' '}· no combo priced for {derived.region_requested}, showing {derived.coverage_region}
            </span>
          )}
        </p>
      )}

      <div style={{ display: 'flex', gap: 6, margin: '14px 0' }}>
        <button className={`ca-btn ca-btn-sm ${tab === 'derived' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setTab('derived')}>Market &amp; Pricing</button>
        <button className={`ca-btn ca-btn-sm ${tab === 'context' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setTab('context')}>Product Intelligence</button>
      </div>

      {loading && <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>}
      {error && <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>}

      {derived && tab === 'derived' && (
        <DerivedTab d={derived} onFixAnchor={() => navigate('/formulas')} />
      )}
      {derived && tab === 'context' && (
        <ContextTab card={card} dims={dims} code={derived.template_code} />
      )}
    </div>
  );
}
