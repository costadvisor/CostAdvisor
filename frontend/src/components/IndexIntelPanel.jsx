import { useState, useEffect } from 'react';
import api from '../api';

/**
 * The Wave-3 index intelligence panels: dossier (DB-7), seasonality (SCRUM-69)
 * and volatility (DB-7), for one price series.
 *
 * Three rules here are not cosmetic:
 *
 *  - The seasonality note and its twelve factors come from **one** response.
 *    Fetching prose and numbers separately is exactly how the source drop ended
 *    up with season notes asserting a history their series did not have.
 *  - A volatility percentile always names the calibration behind it. The ladder
 *    is regenerated, not imported, so "90th percentile" means nothing without
 *    which ladder said so and when.
 *  - A producer share of zero means *not disclosed* on 99% of the source rows,
 *    so `share_disclosed: false` renders "not disclosed" and never "0%".
 *
 * A missing profile or dossier is a 404 carrying a reason. The reason is what
 * gets rendered — a flat curve would present "not enough history to tell" as
 * "no seasonality", which is a different answer.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const TIER_COLOR = { low: 'var(--muted)', modest: 'var(--accent4)', meaningful: 'var(--accent3)' };

const SIGNAL_COLOR = {
  dominant: 'var(--accent2)', strong: 'var(--accent2)',
  medium: 'var(--accent3)', moderate: 'var(--accent3)',
  weak: 'var(--muted)', low: 'var(--muted)',
};

function reasonOf(err) {
  const d = err?.response?.data?.detail;
  return typeof d === 'string' ? d : null;
}

function Empty({ children }) {
  return (
    <div style={{ fontSize: 11, color: 'var(--muted)', padding: '10px 2px', lineHeight: 1.55 }}>
      {children}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', gap: 10, fontSize: 11, padding: '4px 0' }}>
      <span style={{ color: 'var(--muted)', minWidth: 110 }}>{label}</span>
      <span style={{ flex: 1 }}>{children}</span>
    </div>
  );
}

/* ── Seasonality ──────────────────────────────────────────────────────────── */

function SeasonBars({ factors, peak, trough }) {
  // Deviation from 100 in both directions off a shared centre line, so a 3-point
  // month and a 30-point month are not drawn at the same height.
  const max = Math.max(6, ...factors.map(f => Math.abs(f - 100)));
  return (
    <div role="img" aria-label={`Seasonal factors by month, peak ${MONTHS[peak - 1]}, trough ${MONTHS[trough - 1]}`}
      style={{ display: 'flex', gap: 3, alignItems: 'stretch', height: 96, marginTop: 8 }}>
      {factors.map((f, i) => {
        const dev = f - 100;
        const frac = Math.abs(dev) / max;
        const isPeak = i + 1 === peak;
        const isTrough = i + 1 === trough;
        const color = isPeak ? 'var(--accent2)' : isTrough ? 'var(--accent)' : 'var(--accent4)';
        return (
          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}
            title={`${MONTHS[i]} · ${f.toFixed(1)} (${dev >= 0 ? '+' : ''}${dev.toFixed(1)})`}>
            <div style={{ flex: 1, width: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
              {dev > 0 && <div style={{ height: `${frac * 100}%`, background: color, borderRadius: '2px 2px 0 0' }} />}
            </div>
            <div style={{ height: 1, width: '100%', background: 'var(--border)' }} />
            <div style={{ flex: 1, width: '100%' }}>
              {dev < 0 && <div style={{ height: `${frac * 100}%`, background: color, borderRadius: '0 0 2px 2px' }} />}
            </div>
            <span style={{
              fontSize: 8, color: isPeak || isTrough ? 'var(--text)' : 'var(--muted)',
              fontFamily: "'JetBrains Mono', monospace", marginTop: 3,
            }}>{MONTHS[i][0]}</span>
          </div>
        );
      })}
    </div>
  );
}

function SeasonalityCard({ commodityId, region }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    if (!commodityId) return;
    let cancelled = false;
    setState({ loading: true });
    api.get(`/api/seasonality/series/${commodityId}`, { params: region ? { region } : {} })
      .then(({ data }) => { if (!cancelled) setState({ loading: false, profile: data }); })
      .catch(err => {
        if (!cancelled) setState({ loading: false, reason: reasonOf(err) || 'No seasonal profile for this series.' });
      });
    return () => { cancelled = true; };
  }, [commodityId, region]);

  const { loading, profile, reason } = state;
  return (
    <div className="ca-card" style={{ marginBottom: 16, padding: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>
        Seasonality
        {profile && (
          <span style={{ fontWeight: 400, fontSize: 11, color: TIER_COLOR[profile.tier] || 'var(--muted)' }}>
            {' '}· {profile.tier} · spread {profile.spread.toFixed(1)} pts
          </span>
        )}
      </div>
      {loading && <Empty>Loading…</Empty>}
      {!loading && !profile && <Empty>{reason}</Empty>}
      {profile && (
        <>
          {/* The note is rendered from these same twelve numbers server-side, so
              the prose and the chart cannot disagree. */}
          <div style={{ fontSize: 11, lineHeight: 1.6, color: 'var(--text-secondary)' }}>{profile.note}</div>
          <SeasonBars factors={profile.factors} peak={profile.peak_month} trough={profile.trough_month} />
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8, fontFamily: "'JetBrains Mono', monospace" }}>
            {profile.method} · fitted over {profile.window_months} months
            {profile.region ? ` · ${profile.region}` : ''}
          </div>
        </>
      )}
    </div>
  );
}

/* ── Volatility ───────────────────────────────────────────────────────────── */

function VolatilityCard({ commodityId }) {
  const [vol, setVol] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!commodityId) return;
    let cancelled = false;
    setLoading(true);
    api.get(`/api/dossiers/series/${commodityId}/volatility`)
      .then(({ data }) => { if (!cancelled) setVol(data); })
      .catch(() => { if (!cancelled) setVol(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [commodityId]);

  if (loading || !vol) return null;

  const pct = vol.percentile;
  const measured = pct !== null && pct !== undefined;

  return (
    <div className="ca-card" style={{ marginBottom: 16, padding: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>Volatility</div>
      {!measured ? (
        /* "not measurable" is not "calm" — say which one this is. */
        <Empty>{vol.reason || 'Not measurable for this series.'}</Empty>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 26, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
              {pct}<span style={{ fontSize: 13, color: 'var(--muted)' }}>th</span>
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              percentile against the platform library
            </span>
          </div>
          <div style={{ height: 6, background: 'var(--surface2)', borderRadius: 3, marginTop: 10, overflow: 'hidden' }}>
            <div style={{
              width: `${pct}%`, height: '100%',
              background: pct >= 70 ? 'var(--accent2)' : pct >= 40 ? 'var(--accent3)' : 'var(--accent)',
            }} />
          </div>
          <Row label="Dispersion">
            <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{vol.dispersion?.toFixed(2)}</span>
          </Row>
          {/* A percentile with no ladder behind it is unfalsifiable. */}
          <Row label="Calibration">
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>
              {String(vol.calibration_id || '').slice(0, 8)}
            </span>
            <span style={{ color: 'var(--muted)' }}>
              {' '}· {vol.method} over {vol.n_series} series
              {vol.calibration_computed_at
                ? ` · ${new Date(vol.calibration_computed_at).toISOString().slice(0, 10)}` : ''}
            </span>
          </Row>
        </>
      )}
    </div>
  );
}

/* ── Dossier ──────────────────────────────────────────────────────────────── */

function Drivers({ drivers }) {
  if (!drivers.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, letterSpacing: 0.4 }}>DRIVERS</div>
      <table className="ca-table" style={{ fontSize: 11 }}>
        <thead>
          <tr>
            <th>Driver</th>
            <th style={{ width: 70 }}>Corr.</th>
            <th style={{ width: 110 }}>Lag</th>
            <th>Signal</th>
          </tr>
        </thead>
        <tbody>
          {drivers.map((d, i) => (
            <tr key={i}>
              <td>{d.provider || d.category || '—'}</td>
              <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                {d.correlation === null || d.correlation === undefined
                  ? <span style={{ color: 'var(--muted)' }}>—</span>
                  : d.correlation.toFixed(2)}
              </td>
              {/* The raw lag string is authoritative; the parsed bounds are null
                  rather than guessed when it will not parse, and an unparsed lag
                  must not read as "arrives immediately". */}
              <td style={{ color: d.lag_raw ? 'var(--text-secondary)' : 'var(--muted)' }}
                title={d.lag_days_min !== null && d.lag_days_min !== undefined
                  ? `${d.lag_days_min}–${d.lag_days_max} days` : 'Not parseable to a day range'}>
                {d.lag_raw || 'not stated'}
              </td>
              <td style={{ color: SIGNAL_COLOR[d.signal_strength] || 'var(--text-secondary)' }}>
                {d.signal_raw || '—'}
                {d.move_raw && <span style={{ color: 'var(--muted)' }}> · {d.move_raw}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Chain({ chain }) {
  if (!chain.length) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6, letterSpacing: 0.4 }}>
        PRICE TRANSMISSION CHAIN
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
        {chain.map((n, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span className="ca-badge" title={n.detail || n.node_type}
              style={{ background: 'var(--surface2)', color: 'var(--text-secondary)', fontWeight: 500 }}>
              {n.label}
            </span>
            {i < chain.length - 1 && <span style={{ color: 'var(--muted)', fontSize: 11 }}>→</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

function Splits({ splits }) {
  if (!splits.length) return null;
  const groups = {};
  for (const s of splits) (groups[s.split_type] ||= []).push(s);
  return (
    <div style={{ marginTop: 14 }}>
      {Object.entries(groups).map(([kind, rows]) => (
        <div key={kind} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, letterSpacing: 0.4 }}>
            {kind.toUpperCase()}
          </div>
          {rows.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, padding: '2px 0' }}>
              <span style={{ minWidth: 150 }}>{s.label}</span>
              <div style={{ flex: 1, height: 5, background: 'var(--surface2)', borderRadius: 3, overflow: 'hidden' }}>
                {s.pct !== null && s.pct !== undefined && (
                  <div style={{ width: `${Math.min(100, s.pct)}%`, height: '100%', background: 'var(--accent4)' }} />
                )}
              </div>
              <span style={{ width: 46, textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                {s.pct === null || s.pct === undefined
                  ? <span style={{ color: 'var(--muted)' }}>—</span> : `${s.pct}%`}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function Producers({ roles }) {
  if (!roles.length) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, letterSpacing: 0.4 }}>PRODUCERS</div>
      <table className="ca-table" style={{ fontSize: 11 }}>
        <thead>
          <tr>
            <th>Producer</th>
            <th style={{ width: 110 }}>Role</th>
            <th style={{ width: 110 }}>Share</th>
            <th style={{ width: 120 }}>Location</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((r, i) => (
            <tr key={i}>
              <td title={r.raw_name && r.raw_name !== r.producer_name ? `Source says: ${r.raw_name}` : undefined}>
                {r.producer_name || r.raw_name || '—'}
              </td>
              <td style={{ color: 'var(--text-secondary)' }}>{r.role}</td>
              {/* A share of 0 means "not disclosed" on virtually every source
                  row, so publishing "0%" would state something the data never
                  said. */}
              <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                {r.share_disclosed && r.share_pct !== null && r.share_pct !== undefined
                  ? `${r.share_pct}%`
                  : <span style={{ color: 'var(--muted)', fontFamily: 'inherit' }}>not disclosed</span>}
              </td>
              <td style={{ color: 'var(--text-secondary)' }}>{r.location || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DossierCard({ commodityId, region }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    if (!commodityId) return;
    let cancelled = false;
    setState({ loading: true });
    api.get(`/api/dossiers/series/${commodityId}`, { params: region ? { region } : {} })
      .then(({ data }) => { if (!cancelled) setState({ loading: false, dossier: data }); })
      .catch(err => {
        if (!cancelled) setState({ loading: false, reason: reasonOf(err) || 'No dossier stored for this series.' });
      });
    return () => { cancelled = true; };
  }, [commodityId, region]);

  const { loading, dossier, reason } = state;
  if (loading) return null;

  if (!dossier) {
    return (
      <div className="ca-card" style={{ marginBottom: 16, padding: 16 }}>
        <div className="ca-card-title" style={{ marginBottom: 4 }}>Dossier</div>
        <Empty>{reason}</Empty>
      </div>
    );
  }

  const flags = dossier.flags || [];
  return (
    <div className="ca-card" style={{ marginBottom: 16, padding: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>
        Dossier
        <span style={{ fontWeight: 400, fontSize: 10, color: 'var(--muted)' }}>
          {' '}· {dossier.resolved_from === 'region'
            ? `region-specific (${dossier.region})` : 'series-wide'}
        </span>
      </div>

      {flags.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {flags.map((f, i) => (
            <span key={i} className="ca-badge" title={f.detail || f.flag_kind}
              style={{
                background: f.severity === 'high' ? 'var(--danger-bg)'
                  : f.severity === 'medium' ? 'var(--warn-bg)' : 'var(--surface2)',
                color: f.severity === 'high' ? 'var(--accent2)'
                  : f.severity === 'medium' ? 'var(--accent3)' : 'var(--text-secondary)',
              }}>
              {f.label}
            </span>
          ))}
        </div>
      )}

      <Drivers drivers={dossier.drivers || []} />
      <Chain chain={dossier.chain || []} />
      <Splits splits={dossier.splits || []} />
      <Producers roles={dossier.producer_roles || []} />

      {(dossier.pointers || []).length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, letterSpacing: 0.4 }}>
            NEGOTIATION POINTERS
          </div>
          {dossier.pointers.map((p, i) => (
            <div key={i} style={{ fontSize: 11, padding: '4px 0', lineHeight: 1.55 }}>
              <strong>{p.title}</strong>
              {p.body && <span style={{ color: 'var(--text-secondary)' }}> — {p.body}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Stated by the endpoint itself so nobody waits for a field that lives
          somewhere else on purpose. */}
      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
        Computed elsewhere, not stored on the dossier: {(dossier.computed_elsewhere || []).join(', ')}
      </div>
    </div>
  );
}

export default function IndexIntelPanel({ commodityId, region }) {
  // FX pair rows carry no commodity_id — there is no series to look up.
  if (!commodityId) return null;
  return (
    <>
      <VolatilityCard commodityId={commodityId} />
      <SeasonalityCard commodityId={commodityId} region={region} />
      <DossierCard commodityId={commodityId} region={region} />
    </>
  );
}
