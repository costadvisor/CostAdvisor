import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useToast } from '../../components/Toast';

/* Guided negotiation prep (Scrum 29).
 *
 * The app does not predict the supplier's counter — it holds no supplier-cost
 * data, and a fabricated counter-proposal playbook was deleted once for
 * pretending otherwise. So their position is an INPUT on this page: you record
 * what was actually said, and the output is the evidence answering it.
 *
 * The claim is stored; the verdict is not. It is recomputed from the live brief
 * every load, because the driver's real movement changes as index data lands
 * and a stored verdict would quietly become a different answer from the one the
 * numbers now support — which a buyer would then carry into a room.
 *
 * Print reuses the app-wide window.print() + title-swap from Brief.jsx and the
 * shared .ca-print-page / .ca-no-print classes. No second export path. */

const VERDICT_STYLE = {
  overstated: { color: 'var(--accent2)', bg: 'var(--accent2-dim)' },
  contradicted: { color: 'var(--accent2)', bg: 'var(--accent2-dim)' },
  already_priced: { color: 'var(--accent3)', bg: 'var(--accent3-dim)' },
  no_movement: { color: 'var(--muted)', bg: 'var(--neutral-bg)' },
  out_of_scope: { color: 'var(--muted)', bg: 'var(--neutral-bg)' },
  unmapped: { color: 'var(--accent4)', bg: 'var(--accent4-dim)' },
};

const money = (v, cur, unit) =>
  v == null ? '—' : `${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}/${unit}`;

const slug = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

function Ladder({ ladder }) {
  const { currency: cur, unit, floor, should_cost: target, current_price: ask } = ladder;
  const lo = Math.min(...[floor, target, ask].filter(v => v != null));
  const hi = Math.max(...[floor, target, ask].filter(v => v != null));
  const span = hi - lo || 1;
  const rows = [
    ['Their price', ask, 'var(--accent2)'],
    ['Your target — should-cost', target, 'var(--accent)'],
    ['Floor — indexed cost before margin', floor, 'var(--accent4)'],
  ];
  return (
    <div>
      {rows.map(([label, value, color]) => (
        <div key={label} style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4, gap: 10 }}>
            <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
            <span style={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{money(value, cur, unit)}</span>
          </div>
          <div style={{ background: 'var(--surface2)', height: 6, borderRadius: 3 }}>
            <div style={{
              width: value == null ? 0 : `${Math.max(2, ((value - lo) / span) * 100)}%`,
              background: color, height: '100%', borderRadius: 3,
            }} />
          </div>
        </div>
      ))}
      {ladder.unexplained != null && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 14 }}>
          Unexplained portion of their price:{' '}
          <strong style={{ color: 'var(--accent2)' }}>{money(ladder.unexplained, cur, unit)}</strong>
          {ladder.gap_pct != null && ` (${ladder.gap_pct > 0 ? '+' : ''}${ladder.gap_pct.toFixed(1)}%)`}
          . The should-cost has already consumed every verified index movement, so nothing they cite can
          re-explain this.
        </div>
      )}
    </div>
  );
}

export default function NegotiationPrepArea() {
  const { costModelId } = useParams();
  const { addToast } = useToast();
  const [prep, setPrep] = useState(null);
  const [err, setErr] = useState(null);
  const [said, setSaid] = useState('');
  const [driver, setDriver] = useState('');
  const [claimedPct, setClaimedPct] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setErr(null);
    api.get(`/api/negotiation-prep/${costModelId}/prep`)
      .then(({ data }) => setPrep(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the prep.'));
  }, [costModelId]);

  useEffect(() => { load(); }, [load]);

  const addClaim = async () => {
    if (!said.trim()) return;
    setBusy(true);
    try {
      // The period comes from the brief the prep was built at, so a claim is
      // always checked against the window it was made about.
      const [, q, yy] = prep.period_label.match(/Q(\d)-(\d{2})/) || [];
      await api.post(`/api/negotiation-prep/${costModelId}/claims`, {
        said: said.trim(),
        year: yy ? 2000 + Number(yy) : new Date().getFullYear(),
        quarter: q ? Number(q) : Math.floor(new Date().getMonth() / 3) + 1,
        driver_label: driver || null,
        claimed_change_pct: claimedPct === '' ? null : Number(claimedPct),
      });
      setSaid(''); setDriver(''); setClaimedPct('');
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not save that claim.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const patch = async (claimId, body) => {
    try {
      await api.put(`/api/negotiation-prep/claims/${claimId}`, body);
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not update that claim.', 'error');
    }
  };

  const remove = async (claimId) => {
    try {
      await api.delete(`/api/negotiation-prep/claims/${claimId}`);
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not delete that claim.', 'error');
    }
  };

  const print = () => {
    const prev = document.title;
    document.title = `negotiation-prep-${slug(prep.product_name)}-${slug(prep.supplier_name)}-${prep.period_label}`;
    window.print();
    setTimeout(() => { document.title = prev; }, 500);
  };

  if (err) return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div></div>;
  if (!prep) return <div className="ca-page ca-fade-in"><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;

  return (
    <div className="ca-page ca-fade-in ca-print-page">
      <nav className="ca-no-print" style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, display: 'flex', gap: 6 }}>
        <Link to="/negotiate" style={{ color: 'var(--muted)' }}>Negotiate</Link>
        <span>/</span>
        <Link to={`/negotiate/${costModelId}`} style={{ color: 'var(--muted)' }}>{prep.product_name}</Link>
        <span>/</span>
        <span>Prepare</span>
      </nav>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <h1 className="ca-h1">Prepare · {prep.product_name}</h1>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            {prep.supplier_name || 'No supplier'} · {prep.period_label} — record what they claimed, see what
            the indices actually did.
          </p>
        </div>
        <button className="ca-btn ca-btn-ghost ca-no-print" onClick={print}>↓ Export PDF</button>
      </div>

      {prep.data_gaps.length > 0 && (
        <div className="ca-card" style={{ margin: '18px 0', borderColor: 'var(--accent3)', background: 'var(--warn-bg)' }}>
          <div className="ca-card-title" style={{ marginBottom: 6 }}>Check before the call</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            {prep.data_gaps.length} cost line{prep.data_gaps.length === 1 ? '' : 's'} have no index data and
            are riding flat, so the target is conservative. Do not quote them in the room.
            <ul style={{ margin: '6px 0 0 16px' }}>
              {prep.data_gaps.slice(0, 5).map((g, i) => <li key={i}>{g}</li>)}
            </ul>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start', margin: '20px 0' }}>
        <div className="ca-card" style={{ flex: '1 1 300px', minWidth: 280 }}>
          <div className="ca-card-title" style={{ marginBottom: 14 }}>Your position</div>
          <Ladder ladder={prep.ladder} />
        </div>

        <div className="ca-card" style={{ flex: '1 1 380px', minWidth: 300 }}>
          <div className="ca-card-title" style={{ marginBottom: 8 }}>What did they claim?</div>
          <p className="ca-no-print" style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
            Type what the supplier actually told you. Attach it to one of this product&apos;s own cost lines and
            it gets checked against what that index really did.
          </p>
          <div className="ca-no-print">
            <input
              className="ca-input"
              placeholder='e.g. "Acrylate feedstock is up 30% on the year"'
              value={said}
              aria-label="What the supplier said"
              onChange={e => setSaid(e.target.value)}
              style={{ width: '100%', marginBottom: 8 }}
            />
            <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
              <select className="ca-select" value={driver} aria-label="Cost line"
                      onChange={e => setDriver(e.target.value)} style={{ flex: '2 1 180px' }}>
                <option value="">Which cost line? (optional)</option>
                {prep.drivers.map(d => (
                  <option key={d.label} value={d.index_name || d.label}>
                    {d.label}{d.index_name ? ` · ${d.index_name}` : ''} ({d.change_pct > 0 ? '+' : ''}{d.change_pct.toFixed(1)}%)
                  </option>
                ))}
              </select>
              <input className="ca-input" type="number" placeholder="they said %"
                     aria-label="Percentage the supplier claimed"
                     value={claimedPct} onChange={e => setClaimedPct(e.target.value)}
                     style={{ flex: '1 1 110px' }} />
              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={addClaim}
                      disabled={busy || !said.trim()}>
                Check
              </button>
            </div>
          </div>

          {prep.claims.length === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
              Nothing logged yet. Their argument goes here; the rebuttal comes from your own recipe.
            </div>
          ) : prep.claims.map(c => {
            const st = VERDICT_STYLE[c.verdict] || VERDICT_STYLE.unmapped;
            return (
              <div key={c.claim_id} className="ca-card"
                   style={{ padding: 12, marginBottom: 8, background: 'var(--bg)',
                            opacity: c.include_in_script ? 1 : 0.55 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                  <span className="ca-badge" style={{ background: st.bg, color: st.color, fontWeight: 600 }}>
                    {c.verdict_label}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>&ldquo;{c.said}&rdquo;</span>
                  <span className="ca-no-print" style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm"
                            aria-pressed={c.include_in_script}
                            onClick={() => patch(c.claim_id, { include_in_script: !c.include_in_script })}>
                      {c.include_in_script ? 'In script' : 'Excluded'}
                    </button>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" aria-label="Delete claim"
                            onClick={() => remove(c.claim_id)}>×</button>
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{c.note}</div>
                {c.verdict === 'unmapped' && (
                  <select
                    className="ca-select ca-no-print"
                    value=""
                    aria-label={`Attach a cost line to "${c.said}"`}
                    onChange={e => e.target.value && patch(c.claim_id, { driver_label: e.target.value })}
                    style={{ width: '100%', marginTop: 8 }}
                  >
                    <option value="">Attach a cost line…</option>
                    {prep.drivers.map(d => (
                      <option key={d.label} value={d.index_name || d.label}>
                        {d.label}{d.index_name ? ` · ${d.index_name}` : ''}
                      </option>
                    ))}
                  </select>
                )}
                {c.actual_change_pct != null && (
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
                    {c.driver_label}
                    {c.weight_pct != null && ` · ${c.weight_pct}% of the recipe`}
                    {' · actual '}
                    <span style={{ color: c.actual_change_pct >= 0 ? 'var(--accent2)' : 'var(--accent)' }}>
                      {c.actual_change_pct > 0 ? '+' : ''}{c.actual_change_pct.toFixed(1)}%
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="ca-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <div className="ca-card-title" style={{ marginBottom: 0 }}>Call script</div>
          <span className="ca-tag">
            {prep.claims.filter(c => c.include_in_script).length} of {prep.claims.length} claims included
          </span>
        </div>
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: 12, lineHeight: 1.9, color: 'var(--text-secondary)' }}>
          {prep.script.map((line, i) => <li key={i} style={{ marginBottom: 6 }}>{line}</li>)}
        </ol>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 14 }}>
          Every figure here comes from the same engine as the brief, not from a language model. Template first,
          numbers from the engine — an LLM may smooth the prose, never supply a number.
        </div>
      </div>
    </div>
  );
}
