import { Fragment, useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from './Toast';

/**
 * The trigger radar (Wave 3, SCRUM-79 / MON-1).
 *
 * The output is a time-bounded **window**, not a point event: "Brent moved 8%"
 * is a fact, "notice is due on the 14th and the driver is running against you"
 * is something to act on. So a row is an open, a close, a close *basis*, the
 * drivers that justify it, and the products it covers.
 *
 * Four things here are load-bearing and must not be smoothed over:
 *
 *  - **`close_basis: "unknown"` is a real answer.** A forward-looking close needs
 *    forecast storage we do not have, so it renders as a sentence saying so.
 *    A synthesised date would be worse than no date.
 *  - **Coverage is tri-valued and has to look it.** `unknown` is amber with the
 *    unresolved type-codes named, never a quiet grey — a buyer told "no signal"
 *    on a product whose biggest cost line has never had a price is worse off
 *    than one told nothing at all.
 *  - **The negotiation state is a suggestion.** The radar never writes it
 *    (`trigger_radar.SUGGESTS_NOT_SETS`); this renders it as a one-click
 *    proposal against the existing flag endpoint, and says whose decision it is.
 *  - **Windows group at the resolved-series grain.** One move on a series that a
 *    large share of the library resolves to is ONE window carrying every
 *    affected product, not one near-identical alert per product.
 */

const DRIVERS = {
  clause_deadline: {
    label: 'Contract notice',
    blurb: 'Notice can no longer be given after this date.',
    color: 'var(--accent2)',
  },
  index_move: {
    label: 'Index move',
    blurb: 'One price series moved past the threshold — grouped by series, not by product.',
    color: 'var(--accent3)',
  },
  gap: {
    label: 'Price gap',
    blurb: 'The supplier price has drifted away from the live should-cost.',
    color: 'var(--accent3)',
  },
  buy_window: {
    label: 'Buy window',
    blurb: 'Should-cost sits away from its own trailing average.',
    color: 'var(--accent4)',
  },
  market_signal: {
    label: 'Market signal',
    blurb: 'An analyst-entered or imported market event.',
    color: 'var(--accent4)',
  },
};

const COVERAGE = {
  covered: {
    label: 'Covered', color: 'var(--accent)', bg: 'var(--success-bg)',
    title: 'Every index cost line resolves to a price series (or the recipe is all fixed cost — nothing to move).',
  },
  partial: {
    label: 'Partial', color: 'var(--accent3)', bg: 'var(--warn-bg)',
    title: 'Some cost lines resolve, some do not — the signal is real but incomplete.',
  },
  unknown: {
    label: 'Blind spot', color: 'var(--accent2)', bg: 'var(--danger-bg)',
    title: 'No cost line here resolves to a price series. "No signal" on this product means "we cannot tell", not "nothing moved".',
  },
};

const CLOSE_BASIS_COPY = {
  clause_deadline: 'Closes on the contract notice deadline.',
  forecast_turn: 'Closes where the forecast turns.',
  quarter_end: 'Closes at quarter end.',
  signal_expiry: 'Closes when the market signal expires.',
  unknown: 'No close date — a forward-looking close needs forecast storage, and a synthesised date would be worse than none.',
};

const NEG_STATE_LABEL = {
  none: 'No flag', in_negotiation: 'In negotiation',
  under_review: 'Under review', agreed: 'Agreed',
};

const SIGNAL_TYPES = [
  ['supplier_announcement', 'Supplier announcement'],
  ['disruption', 'Disruption / force majeure'],
  ['policy', 'Policy or regulation'],
  ['capacity', 'Capacity change'],
  ['other', 'Other'],
];

function CoverageChip({ coverage, codes = [], small }) {
  const c = COVERAGE[coverage] || COVERAGE.unknown;
  const named = codes.length
    ? `${c.title}\nUnresolved: ${codes.join(', ')}`
    : c.title;
  return (
    <span className="ca-badge" title={named}
      style={{ background: c.bg, color: c.color, fontWeight: 600, fontSize: small ? 9 : 10 }}>
      {c.label}
    </span>
  );
}

function DaysChip({ days, basis }) {
  if (days === null || days === undefined) {
    return (
      <span style={{ fontSize: 11, color: 'var(--muted)' }}
        title={CLOSE_BASIS_COPY[basis] || CLOSE_BASIS_COPY.unknown}>
        no close date
      </span>
    );
  }
  const urgent = days <= 14;
  return (
    <span style={{
      fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
      color: urgent ? 'var(--accent2)' : days <= 45 ? 'var(--accent3)' : 'var(--text-secondary)',
      fontWeight: urgent ? 700 : 500,
    }} title={CLOSE_BASIS_COPY[basis] || basis}>
      {days <= 0 ? 'closes today' : `${days}d`}
    </span>
  );
}

/* ── Inspection drawer ────────────────────────────────────────────────────── */

function EvidenceRows({ evidence }) {
  // Rendered generically: each driver writes its own evidence keys, and a
  // hand-maintained per-driver field list would silently drop whatever a new
  // feed adds. Keys the panels below already render on their own are skipped.
  const SKIP = new Set([
    'suggested_negotiation_state', 'resolution_path', 'unresolved_type_codes',
    'type_codes_resolving_here', 'body',
  ]);
  const rows = Object.entries(evidence || {})
    .filter(([k, v]) => !SKIP.has(k) && v !== null && v !== undefined && v !== '')
    .filter(([, v]) => typeof v !== 'object');
  if (!rows.length) return null;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 12px', fontSize: 11 }}>
      {rows.map(([k, v]) => (
        <Fragment key={k}>
          <span style={{ color: 'var(--muted)' }}>{k.replace(/_/g, ' ')}</span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            {typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v)}
          </span>
        </Fragment>
      ))}
    </div>
  );
}

function WindowDrawer({ windowId, onClose, onDismissed, onFlagged }) {
  const [win, setWin] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const { addToast } = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    setWin(null); setErr(null);
    api.get(`/api/radar/windows/${windowId}`)
      .then(({ data }) => setWin(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load this window.'));
  }, [windowId]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const dismiss = async () => {
    setBusy(true);
    try {
      await api.post(`/api/radar/windows/${windowId}/dismiss`);
      addToast('Window dismissed — a later radar run will not reopen it.', 'success');
      onDismissed();
      onClose();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not dismiss', 'error');
    } finally { setBusy(false); }
  };

  // The radar suggests; a person decides. This writes the flag the radar
  // deliberately never touches, through the endpoint that already audits it.
  const applyFlag = async (costModelId) => {
    setBusy(true);
    try {
      await api.put(`/api/cost-models/${costModelId}/flag`,
        { negotiation_state: win.suggested_negotiation_state });
      addToast(`Flagged as "${NEG_STATE_LABEL[win.suggested_negotiation_state]}".`, 'success');
      const { data } = await api.get(`/api/radar/windows/${windowId}`);
      setWin(data);
      onFlagged?.();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not set the flag', 'error');
    } finally { setBusy(false); }
  };

  const d = win ? (DRIVERS[win.driver] || { label: win.driver, color: 'var(--muted)' }) : null;
  const ev = win?.evidence || {};
  const path = ev.resolution_path || [];
  const unresolved = ev.unresolved_type_codes || [];
  const codesHere = ev.type_codes_resolving_here || [];

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ maxWidth: 660, width: '95vw' }}
        onClick={e => e.stopPropagation()} role="dialog" aria-modal="true"
        aria-label="Negotiation window detail">
        <div className="ca-modal-header" style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 2 }}>
          <div>
            {d && (
              <span className="ca-badge" style={{ background: 'var(--surface2)', color: d.color, fontWeight: 600 }}>
                {d.label}
              </span>
            )}
            <div style={{ fontWeight: 600, fontSize: 13, marginTop: 6 }}>
              {win?.headline || 'Loading…'}
            </div>
          </div>
          <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={onClose}>Close</button>
        </div>

        <div style={{ padding: 16 }}>
          {err && <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>}
          {!win && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

          {win && (
            <>
              {/* Open / close, with the basis spelled out rather than an empty cell. */}
              <div className="ca-card" style={{ padding: 14, marginBottom: 14 }}>
                <div style={{ display: 'flex', gap: 20, fontSize: 11, flexWrap: 'wrap' }}>
                  <span><span style={{ color: 'var(--muted)' }}>Opened</span> {win.opens_on}</span>
                  <span>
                    <span style={{ color: 'var(--muted)' }}>Closes</span>{' '}
                    {win.closes_on || <span style={{ color: 'var(--muted)' }}>—</span>}
                  </span>
                  <span><DaysChip days={win.closes_in_days} basis={win.close_basis} /></span>
                  <CoverageChip coverage={win.coverage} codes={unresolved} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.55 }}>
                  {CLOSE_BASIS_COPY[win.close_basis] || win.close_basis}
                </div>
                {/* A threshold without its unit cannot be read: an index level is
                    base 100, where nothing is money. */}
                {/* The other half of the cross-link: the contract page lists the
                    windows it drives, and a window points back at its contract. */}
                {win.scope_contract_id && (
                  <div style={{ marginTop: 8 }}>
                    <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ fontSize: 10 }}
                      onClick={() => navigate('/contracts')}>
                      Open the contract
                    </button>
                  </div>
                )}
                {win.threshold_value !== null && win.threshold_value !== undefined && (
                  <div style={{ fontSize: 11, marginTop: 6 }}>
                    <span style={{ color: 'var(--muted)' }}>Threshold applied </span>
                    <strong style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                      {win.threshold_value}{win.threshold_unit === 'pct' ? '%' : ` ${win.threshold_unit}`}
                    </strong>
                  </div>
                )}
              </div>

              <div className="ca-card" style={{ padding: 14, marginBottom: 14 }}>
                <div className="ca-card-title" style={{ marginBottom: 8 }}>Evidence</div>
                <EvidenceRows evidence={ev} />
                {ev.body && (
                  <div style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                    {ev.body}
                  </div>
                )}
                {codesHere.length > 0 && (
                  <div style={{ marginTop: 10 }}>
                    {/* The grain that makes one move one window: many labels, one series. */}
                    <div style={{ fontSize: 10, color: 'var(--muted)', letterSpacing: 0.4, marginBottom: 4 }}>
                      {codesHere.length} TYPE CODE{codesHere.length === 1 ? '' : 'S'} RESOLVE TO THIS SERIES
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {codesHere.map(c => (
                        <span key={c} className="ca-badge" style={{
                          background: 'var(--surface2)', color: 'var(--text-secondary)',
                          fontFamily: "'JetBrains Mono', monospace", fontWeight: 500,
                        }}>{c}</span>
                      ))}
                    </div>
                  </div>
                )}
                {unresolved.length > 0 && (
                  <div style={{ marginTop: 10, fontSize: 11, color: 'var(--accent3)' }}>
                    Unresolved on this window: {unresolved.join(', ')}
                  </div>
                )}
              </div>

              {/* Affected products, with the resolution path's proxy state where
                  the feed supplied one. */}
              <div className="ca-card" style={{ padding: 14, marginBottom: 14 }}>
                <div className="ca-card-title" style={{ marginBottom: 8 }}>
                  Affected products ({win.products.length})
                </div>
                {win.products.length === 0 ? (
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                    Portfolio-wide — no single product carries this window.
                  </div>
                ) : (
                  <table className="ca-table" style={{ fontSize: 11 }}>
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th style={{ width: 90, textAlign: 'right' }}>Exposure</th>
                        <th style={{ width: 80 }}>Priced via</th>
                        <th style={{ width: 130 }}>Your flag</th>
                      </tr>
                    </thead>
                    <tbody>
                      {win.products.map(p => {
                        const current = win.current_negotiation_states?.[p.cost_model_id] || 'none';
                        const suggestion = win.suggested_negotiation_state;
                        const alreadyThere = current === suggestion;
                        const fromPath = path.find(r => r.cost_model_id === p.cost_model_id);
                        const viaProxy = p.via_proxy ?? fromPath?.via_proxy;
                        return (
                          <tr key={p.cost_model_id}>
                            <td>
                              <button
                                onClick={() => navigate(`/portfolio/${p.cost_model_id}`)}
                                style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer', color: 'var(--accent4)' }}>
                                {p.product || p.cost_model_id.slice(0, 8)}
                              </button>
                            </td>
                            <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                              {p.exposure_pct === null || p.exposure_pct === undefined
                                ? <span style={{ color: 'var(--muted)' }}>—</span>
                                : `${p.exposure_pct}%`}
                            </td>
                            <td>
                              {viaProxy === true
                                ? <span className="ca-badge" title="Read from the type-code registry, the authoritative side when the two proxy columns disagree"
                                    style={{ background: 'var(--warn-bg)', color: 'var(--accent3)' }}>PROXY</span>
                                : viaProxy === false
                                  ? <span style={{ color: 'var(--muted)' }}>direct</span>
                                  : <span style={{ color: 'var(--muted)' }}>—</span>}
                            </td>
                            <td>
                              <span style={{ color: current === 'none' ? 'var(--muted)' : 'var(--text)' }}>
                                {NEG_STATE_LABEL[current]}
                              </span>
                              {suggestion && !alreadyThere && (
                                <button className="ca-btn ca-btn-sm ca-btn-ghost" disabled={busy}
                                  style={{ marginLeft: 6, fontSize: 10 }}
                                  title={`The radar suggests "${NEG_STATE_LABEL[suggestion]}" — it never sets it. This applies it.`}
                                  onClick={() => applyFlag(p.cost_model_id)}>
                                  → {NEG_STATE_LABEL[suggestion]}
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
                {win.suggested_negotiation_state && (
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8, lineHeight: 1.5 }}>
                    The radar <strong>suggests</strong> “{NEG_STATE_LABEL[win.suggested_negotiation_state]}”
                    and never sets it. Applying it is your decision and is recorded in the audit log.
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                {win.state === 'open' && (
                  <button className="ca-btn ca-btn-sm ca-btn-ghost" disabled={busy}
                    style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                    onClick={dismiss}
                    title="A later radar run will not reopen a dismissed window">
                    Dismiss
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Windows ──────────────────────────────────────────────────────────────── */

function Windows({ teamId, onInspect, reloadKey }) {
  const [windows, setWindows] = useState(null);
  const [err, setErr] = useState(null);
  const [state, setState] = useState('open');

  useEffect(() => {
    if (!teamId) return;
    let cancelled = false;
    setWindows(null); setErr(null);
    api.get('/api/radar/windows', { params: { team_id: teamId, state } })
      .then(({ data }) => { if (!cancelled) setWindows(data); })
      .catch(e => { if (!cancelled) setErr(formatApiError(e) || 'Could not load windows.'); });
    return () => { cancelled = true; };
  }, [teamId, state, reloadKey]);

  // Grouped by driver, each group sorted by how soon it closes. A window with no
  // close date sorts last rather than first — it is not the most urgent thing.
  const groups = useMemo(() => {
    const map = new Map();
    for (const w of windows || []) {
      if (!map.has(w.driver)) map.set(w.driver, []);
      map.get(w.driver).push(w);
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const ad = a.closes_in_days, bd = b.closes_in_days;
        if (ad === null || ad === undefined) return (bd === null || bd === undefined) ? 0 : 1;
        if (bd === null || bd === undefined) return -1;
        return ad - bd;
      });
    }
    return [...map.entries()].sort(
      (a, b) => Object.keys(DRIVERS).indexOf(a[0]) - Object.keys(DRIVERS).indexOf(b[0]));
  }, [windows]);

  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {['open', 'closed', 'dismissed'].map(s => (
          <button key={s} onClick={() => setState(s)}
            className={`ca-btn ca-btn-sm ${state === s ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {s[0].toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}
      {!windows && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}

      {windows && windows.length === 0 && (
        <div className="ca-card" style={{ padding: 32, textAlign: 'center' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 6 }}>
            No {state} windows.
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.6, maxWidth: 460, margin: '0 auto' }}>
            {state === 'open'
              ? 'Nothing is running against you right now — but check Coverage before reading that as calm: a product whose cost lines never resolve cannot produce a window at all. Recording a contract term and notice period is the one feed you can fill in directly.'
              : 'Nothing here yet.'}
          </div>
        </div>
      )}

      {groups.map(([driver, list]) => {
        const d = DRIVERS[driver] || { label: driver, blurb: '', color: 'var(--muted)' };
        return (
          <div key={driver} style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: 12, color: d.color }}>{d.label}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>{list.length}</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6 }}>{d.blurb}</div>
            <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
              <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
                <thead>
                  <tr>
                    <th>Window</th>
                    <th style={{ width: 90 }}>Closes</th>
                    <th style={{ width: 80, textAlign: 'right' }}>Products</th>
                    <th style={{ width: 90 }}>Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map(w => (
                    <tr key={w.id} onClick={() => onInspect(w.id)} style={{ cursor: 'pointer' }}
                      tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onInspect(w.id); } }}>
                      <td>{w.headline}</td>
                      <td><DaysChip days={w.closes_in_days} basis={w.close_basis} /></td>
                      <td style={{ textAlign: 'right' }}>
                        {w.products.length || <span style={{ color: 'var(--muted)' }}>portfolio</span>}
                      </td>
                      <td><CoverageChip coverage={w.coverage} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </>
  );
}

/* ── Coverage ─────────────────────────────────────────────────────────────── */

function Coverage({ teamId }) {
  const [report, setReport] = useState(null);
  const [err, setErr] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!teamId) return;
    api.get('/api/radar/coverage', { params: { team_id: teamId } })
      .then(({ data }) => setReport(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the coverage report.'));
  }, [teamId]);

  if (err) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>;
  if (!report) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const counts = report.counts || {};
  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Which products the radar can actually see. This is reachable with no window open,
        because that is exactly when a blind spot is invisible — a product whose cost lines
        never resolve stays silent forever, and silence reads like calm.
      </p>
      <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        {['covered', 'partial', 'unknown'].map(k => (
          <div key={k} className="ca-metric" style={{ flex: 1, minWidth: 140 }}>
            <div className="ca-metric-lbl">{COVERAGE[k].label}</div>
            <div className="ca-metric-val" style={{ color: COVERAGE[k].color }}>{counts[k] || 0}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.4 }}>{COVERAGE[k].title}</div>
          </div>
        ))}
      </div>
      {report.models.length === 0 ? (
        <div className="ca-card" style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
          No products with a formula yet.
        </div>
      ) : (
        <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
            <thead>
              <tr>
                <th>Product</th>
                <th style={{ width: 90 }}>Coverage</th>
                <th style={{ width: 110, textAlign: 'right' }}>Lines resolved</th>
                <th>Unresolved</th>
              </tr>
            </thead>
            <tbody>
              {report.models
                .slice()
                .sort((a, b) => ['unknown', 'partial', 'covered'].indexOf(a.coverage)
                              - ['unknown', 'partial', 'covered'].indexOf(b.coverage))
                .map(m => (
                  <tr key={m.cost_model_id} style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/portfolio/${m.cost_model_id}`)}>
                    <td>{m.product || m.cost_model_id.slice(0, 8)}</td>
                    <td><CoverageChip coverage={m.coverage} codes={m.unresolved_type_codes} /></td>
                    <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                      {m.total_index_lines === 0
                        ? <span style={{ color: 'var(--muted)', fontFamily: 'inherit' }}>all fixed</span>
                        : `${m.resolved_lines}/${m.total_index_lines}`}
                    </td>
                    <td style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>
                      {m.unresolved_type_codes.join(', ') || (m.fallback_reason || '—')}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ── Market signals ───────────────────────────────────────────────────────── */

function Signals({ teamId, onChanged }) {
  const [signals, setSignals] = useState([]);
  const [err, setErr] = useState(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const { addToast } = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({
    signal_type: 'disruption', headline: '', body: '',
    as_of_date: today, expires_at: '', source_url: '',
  });

  const load = useCallback(() => {
    if (!teamId) return;
    api.get('/api/radar/signals', { params: { team_id: teamId } })
      .then(({ data }) => setSignals(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load signals.'));
  }, [teamId]);

  useEffect(load, [load]);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post('/api/radar/signals', {
        ...form,
        expires_at: form.expires_at || null,
        source_url: form.source_url || null,
        body: form.body || null,
      }, { params: { team_id: teamId } });
      addToast('Signal added — it will be picked up on the next radar run.', 'success');
      setForm({ ...form, headline: '', body: '', source_url: '', expires_at: '' });
      setAdding(false);
      load();
      onChanged?.();
    } catch (e2) {
      addToast(formatApiError(e2) || 'Could not add the signal', 'error');
    } finally { setBusy(false); }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/api/radar/signals/${id}`);
      load(); onChanged?.();
    } catch (e2) {
      addToast(formatApiError(e2) || 'Could not delete', 'error');
    }
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0, flex: 1, minWidth: 300 }}>
          {/* Manual entry is the day-one path on purpose: there is no automated
              producer for this feed, and an analyst who hears about a force
              majeure should not need a deploy to put it on the radar. */}
          Supplier announcements, disruptions and policy changes an analyst wants on the
          radar. There is no automated feed behind this yet, so manual entry is the path —
          a signal added here is picked up by the next radar run.
        </p>
        <button className="ca-btn ca-btn-sm ca-btn-primary" onClick={() => setAdding(a => !a)}>
          {adding ? 'Cancel' : '+ Add signal'}
        </button>
      </div>

      {adding && (
        <form className="ca-card" style={{ padding: 14, marginBottom: 14 }} onSubmit={submit}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
            <label style={{ fontSize: 11 }}>
              <div style={{ color: 'var(--muted)', marginBottom: 3 }}>Type</div>
              <select className="ca-input" value={form.signal_type}
                onChange={e => setForm({ ...form, signal_type: e.target.value })}>
                {SIGNAL_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 11 }}>
              <div style={{ color: 'var(--muted)', marginBottom: 3 }}>As of</div>
              <input className="ca-input" type="date" required value={form.as_of_date}
                onChange={e => setForm({ ...form, as_of_date: e.target.value })} />
            </label>
            <label style={{ fontSize: 11 }}>
              <div style={{ color: 'var(--muted)', marginBottom: 3 }}>Expires (optional)</div>
              <input className="ca-input" type="date" value={form.expires_at}
                onChange={e => setForm({ ...form, expires_at: e.target.value })} />
            </label>
          </div>
          <label style={{ fontSize: 11, display: 'block', marginTop: 10 }}>
            <div style={{ color: 'var(--muted)', marginBottom: 3 }}>Headline</div>
            <input className="ca-input" required maxLength={200} value={form.headline}
              placeholder="e.g. Force majeure declared at a European cracker"
              onChange={e => setForm({ ...form, headline: e.target.value })} />
          </label>
          <label style={{ fontSize: 11, display: 'block', marginTop: 10 }}>
            <div style={{ color: 'var(--muted)', marginBottom: 3 }}>Detail (optional)</div>
            <textarea className="ca-input" rows={3} value={form.body}
              onChange={e => setForm({ ...form, body: e.target.value })} />
          </label>
          <label style={{ fontSize: 11, display: 'block', marginTop: 10 }}>
            <div style={{ color: 'var(--muted)', marginBottom: 3 }}>Source URL (optional)</div>
            <input className="ca-input" type="url" value={form.source_url}
              onChange={e => setForm({ ...form, source_url: e.target.value })} />
          </label>
          <div style={{ marginTop: 12, textAlign: 'right' }}>
            <button className="ca-btn ca-btn-sm ca-btn-primary" type="submit" disabled={busy}>
              {busy ? 'Saving…' : 'Add signal'}
            </button>
          </div>
        </form>
      )}

      {err && <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>}

      {signals.length === 0 ? (
        <div className="ca-card" style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--muted)' }}>
          No market signals on the radar.
        </div>
      ) : (
        <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="ca-table" style={{ fontSize: 11, marginBottom: 0 }}>
            <thead>
              <tr>
                <th>Signal</th>
                <th style={{ width: 130 }}>Type</th>
                <th style={{ width: 130 }}>As of</th>
                <th style={{ width: 110 }}>Origin</th>
                <th style={{ width: 60 }} />
              </tr>
            </thead>
            <tbody>
              {signals.map(s => (
                <tr key={s.id}>
                  <td title={s.body || undefined}>
                    {s.headline}
                    {s.source_url && (
                      <a href={s.source_url} target="_blank" rel="noopener noreferrer"
                        style={{ marginLeft: 6, fontSize: 10, color: 'var(--accent4)' }}>source ↗</a>
                    )}
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>
                    {(SIGNAL_TYPES.find(t => t[0] === s.signal_type) || [, s.signal_type])[1]}
                  </td>
                  <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    {s.as_of_date}
                    {/* Imported editorial carries no authored date — the vantage
                        date lives only in prose, so it is synthesised and that
                        has to be visible rather than passed off as authored. */}
                    {s.as_of_inferred && (
                      <span title="This date was inferred, not authored — the source has no date field"
                        style={{ color: 'var(--accent3)', marginLeft: 4, fontFamily: 'inherit' }}>~</span>
                    )}
                  </td>
                  <td>
                    <span className="ca-badge" style={{
                      background: 'var(--surface2)', color: 'var(--text-secondary)', fontWeight: 500,
                    }} title={s.team_id ? 'Entered by your team' : 'Platform-wide signal'}>
                      {s.origin.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td>
                    {s.team_id && (
                      <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ fontSize: 10 }}
                        onClick={() => remove(s.id)}>Remove</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ── Shell ────────────────────────────────────────────────────────────────── */

const TABS = [
  { key: 'windows', label: 'Windows' },
  { key: 'coverage', label: 'Coverage' },
  { key: 'signals', label: 'Market signals' },
];

export default function RadarView() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
  // Same per-feature probe the nav uses: contracts sit behind their own
  // permission, so the link is hidden rather than leading to a 403.
  const [canSeeContracts, setCanSeeContracts] = useState(false);
  const [tab, setTab] = useState('windows');
  const [inspecting, setInspecting] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [running, setRunning] = useState(false);

  const reload = () => setReloadKey(k => k + 1);

  useEffect(() => {
    if (!activeTeamId) return;
    let cancelled = false;
    api.get('/api/contracts/can-access', { params: { team_id: activeTeamId } })
      .then(({ data }) => { if (!cancelled) setCanSeeContracts(!!data.can_view); })
      .catch(() => { if (!cancelled) setCanSeeContracts(false); });
    return () => { cancelled = true; };
  }, [activeTeamId]);

  const runRadar = async () => {
    setRunning(true);
    try {
      const { data } = await api.post('/api/radar/run', null, { params: { team_id: activeTeamId } });
      addToast(
        `Radar run — ${data.opened} opened, ${data.refreshed} refreshed, ${data.closed} closed (${data.windows_open} open).`,
        'success');
      reload();
    } catch (e) {
      addToast(formatApiError(e) || 'Radar run failed', 'error');
    } finally { setRunning(false); }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '14px 0', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`ca-btn ca-btn-sm ${tab === t.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {t.label}
          </button>
        ))}
        {canSeeContracts && (
          <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ marginLeft: 'auto' }}
            onClick={() => navigate('/contracts')}
            title="Contract terms and notice periods — the notice deadline is the one hard future date the radar has">
            Contracts
          </button>
        )}
        <button className="ca-btn ca-btn-sm ca-btn-ghost"
          style={canSeeContracts ? undefined : { marginLeft: 'auto' }}
          onClick={runRadar} disabled={running}
          title="Re-evaluate every feed now. A standing window is refreshed rather than duplicated, and a dismissed one is never reopened (owner/admin).">
          {running ? 'Running…' : '⟳ Run radar'}
        </button>
      </div>

      {tab === 'windows' && (
        <Windows teamId={activeTeamId} reloadKey={reloadKey} onInspect={setInspecting} />
      )}
      {tab === 'coverage' && <Coverage teamId={activeTeamId} key={reloadKey} />}
      {tab === 'signals' && <Signals teamId={activeTeamId} onChanged={reload} />}

      {inspecting && (
        <WindowDrawer
          windowId={inspecting}
          onClose={() => setInspecting(null)}
          onDismissed={reload}
          onFlagged={reload}
        />
      )}
    </>
  );
}
