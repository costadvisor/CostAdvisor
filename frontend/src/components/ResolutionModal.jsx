import { useState, useEffect } from 'react';
import api, { formatApiError } from '../api';
import Modal from './Modal';
import exportCsv from '../utils/exportCsv';

/**
 * The resolution layer, made visible (Wave 3, SCRUM-74 / SCRUM-80).
 *
 * The headline this exists for: a cost breakdown that *looks* diversified can be
 * one commodity reached through dozens of labels. Concentration is the view that
 * shows it — dozens of type codes resolving to one series, carrying a large share
 * of all indexed cost weight.
 *
 * Two rules the backend already enforces and the UI must not undo:
 *
 *  - **Unpriceable is grouped by reason and never totalled.** "No series behind
 *    it", "ambiguous code" and "resolves but has no history" need three different
 *    actions — buy a feed, decide what the code means, run a scrape. One combined
 *    number would tell a buyer to do nothing in particular.
 *  - **The swap backlog is ranked by the cost weight actually behind a code**, and
 *    `swap_priority` is a sourcing rank, not an accuracy score: A means a better
 *    index exists and buying it improves the number overnight, C means the
 *    stand-in already is the right index.
 */

const TABS = [
  { key: 'concentration', label: 'Concentration' },
  { key: 'unpriceable', label: 'Unpriceable' },
  { key: 'backlog', label: 'Swap backlog' },
];

// What a reader is actually being told to do about each blocker. The three are
// deliberately different sentences, because they are three different jobs.
const BLOCKER_COPY = {
  no_series: {
    label: 'No price series behind the code',
    action: 'Source a feed. Nothing can price these lines until one is bought or found.',
    color: 'var(--accent2)',
  },
  ambiguous: {
    label: 'Code is ambiguous',
    action: 'An analyst has to decide what the code means before it can resolve at all.',
    color: 'var(--accent3)',
  },
  resolved_but_no_history: {
    label: 'Resolves, but the series has no values',
    action: 'The series exists — run or wire up a scrape for it.',
    color: 'var(--accent4)',
  },
};

const RANK_TITLE = {
  A: 'A — a better index exists; buying it improves the number overnight',
  B: 'B — a defensible upstream stand-in',
  C: 'C — permanent by design (the stand-in already is the right index)',
};

function pct(v) {
  return v === null || v === undefined ? '—' : `${v.toFixed(2)}%`;
}

function Bar({ share, color }) {
  return (
    <div style={{ height: 5, background: 'var(--surface2)', borderRadius: 3, overflow: 'hidden', minWidth: 60 }}>
      <div style={{ width: `${Math.min(100, share || 0)}%`, height: '100%', background: color }} />
    </div>
  );
}

/* ── Concentration ────────────────────────────────────────────────────────── */

function Concentration({ onOpenSeries }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.get('/api/resolution/concentration', { params: { limit: 40 } })
      .then(({ data }) => setData(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load concentration.'));
  }, []);

  if (err) return <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>;
  if (!data) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  const series = data.series || [];
  const top = series[0];

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Every type code the catalog references, grouped by the price series it actually
        resolves to. A recipe that names ten different inputs may be standing on far
        fewer real series than it looks like.
        {top && (
          <>
            {' '}Right now <strong>{top.type_code_count} codes</strong> resolve to{' '}
            <strong>{top.commodity_key || `series ${top.commodity_id}`}</strong>, carrying{' '}
            <strong>{pct(top.weight_share_of_library_pct)}</strong> of all indexed cost weight.
          </>
        )}
      </p>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8, fontFamily: "'JetBrains Mono', monospace" }}>
        Library total indexed weight: {data.library_total_weight?.toLocaleString()}
      </div>
      {/* .ca-grid-scroll: without it, a table wider than the modal (long
          commodity keys, the share bar + percentage) bleeds text out past
          the card's border instead of scrolling — the same failure mode
          documented on that class, this component just never adopted it. */}
      <div className="ca-grid-scroll" style={{ maxHeight: 420 }}>
      <table className="ca-table" style={{ fontSize: 11, tableLayout: 'fixed', width: '100%' }}>
        <thead>
          <tr>
            <th>Series</th>
            <th style={{ width: 70, textAlign: 'right' }}>Codes</th>
            <th style={{ width: 90, textAlign: 'right' }}>Weight</th>
            <th style={{ width: 180 }}>Share of library</th>
          </tr>
        </thead>
        <tbody>
          {series.map(s => (
            <tr key={s.commodity_id}
              onClick={() => onOpenSeries?.(s.commodity_id)}
              style={{ cursor: onOpenSeries ? 'pointer' : 'default' }}>
              <td style={{ fontFamily: "'JetBrains Mono', monospace", overflowWrap: 'break-word' }}>
                {s.commodity_key || `#${s.commodity_id}`}
              </td>
              <td style={{ textAlign: 'right' }}>{s.type_code_count}</td>
              <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                {Math.round(s.source_total_weight).toLocaleString()}
              </td>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Bar share={s.weight_share_of_library_pct} color="var(--accent4)" />
                  <span style={{ width: 52, textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                    {pct(s.weight_share_of_library_pct)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}

/* ── Unpriceable ──────────────────────────────────────────────────────────── */

function Unpriceable() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.get('/api/resolution/unpriceable')
      .then(({ data }) => setData(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load blockers.'));
  }, []);

  if (err) return <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>;
  if (!data) return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>;

  // A reason with nothing under it is not a finding. The endpoint always
  // returns all three groups so a consumer can rely on the keys; an empty one
  // renders as nothing rather than as a card saying zero.
  const groups = Object.entries(data.blockers || {}).filter(([, g]) => g.code_count > 0);
  if (!groups.length) {
    return <div style={{ fontSize: 12, color: 'var(--muted)' }}>Every referenced type code resolves to a series with values.</div>;
  }

  return (
    <>
      {/* Deliberately not summed. Three reasons, three different jobs. */}
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Grouped by why, not totalled — each group needs a different action, so one
        combined count would not tell anyone what to do.
      </p>
      {groups.map(([reason, g]) => {
        const copy = BLOCKER_COPY[reason] || { label: reason, action: '', color: 'var(--muted)' };
        return (
          <div key={reason} className="ca-card" style={{ marginBottom: 12, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, fontSize: 12, color: copy.color }}>{copy.label}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                {g.code_count} code{g.code_count === 1 ? '' : 's'} · {pct(g.weight_share_of_library_pct)} of library weight
              </span>
            </div>
            {copy.action && (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{copy.action}</div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
              {(g.codes || []).map((c, i) => (
                <span key={i} className="ca-badge"
                  title={[c.label, c.ideal_index && `Wants: ${c.ideal_index}`,
                          c.swap_priority && `Rank ${c.swap_priority}`]
                          .filter(Boolean).join(' · ') || undefined}
                  style={{
                    background: 'var(--surface2)', color: 'var(--text-secondary)',
                    fontFamily: "'JetBrains Mono', monospace", fontWeight: 500,
                  }}>
                  {c.code}
                  {c.source_total_weight ? (
                    <span style={{ color: 'var(--muted)' }}> {Math.round(c.source_total_weight)}</span>
                  ) : null}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

/* ── Swap backlog ─────────────────────────────────────────────────────────── */

function SwapBacklog() {
  const [rank, setRank] = useState('all');
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setData(null);
    api.get('/api/resolution/swap-backlog', {
      params: { limit: 200, ...(rank === 'all' ? {} : { priority: rank }) },
    })
      .then(({ data }) => setData(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load the backlog.'));
  }, [rank]);

  const entries = data?.entries || [];

  const handleExport = () => exportCsv(
    `swap_backlog${rank === 'all' ? '' : `_${rank}`}.csv`,
    ['Type code', 'Label', 'Rank', 'Resolution', 'Stand-in status', 'Wanted index',
     'Catalog weight', 'Cost lines', 'Priceable'],
    entries.map(e => [e.code, e.label || '', e.swap_priority || '', e.resolution,
      e.proxy_status || '', e.ideal_index || '', Math.round(e.catalog_weight),
      e.line_count, e.priceable ? 'Yes' : 'No']),
  );

  return (
    <>
      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 0 }}>
        Sourcing candidates ranked by the cost weight actually behind them, so an A
        carrying a lot of weight sorts above an A carrying almost none. Each row with a
        wanted index <em>is</em> the purchase instruction.
      </p>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        {['all', 'A', 'B', 'C'].map(r => (
          <button key={r} onClick={() => setRank(r)}
            title={RANK_TITLE[r]}
            className={`ca-btn ca-btn-sm ${rank === r ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {r === 'all' ? 'All ranks' : r}
          </button>
        ))}
        <button className="ca-btn ca-btn-sm ca-btn-ghost" style={{ marginLeft: 'auto' }}
          onClick={handleExport} disabled={!entries.length}>Export CSV</button>
      </div>
      {err && <div style={{ fontSize: 12, color: 'var(--accent2)' }}>{err}</div>}
      {!data && !err && <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>}
      {data && (
        <>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6, fontFamily: "'JetBrains Mono', monospace" }}>
            {entries.length} codes · library indexed weight {data.total_catalog_weight?.toLocaleString()}
          </div>
          <div className="ca-grid-scroll" style={{ maxHeight: 420 }}>
          <table className="ca-table" style={{ fontSize: 11, tableLayout: 'fixed', width: '100%' }}>
            <thead>
              <tr>
                <th style={{ width: 110 }}>Code</th>
                <th style={{ width: 44 }}>Rank</th>
                <th>Wanted index</th>
                <th style={{ width: 80, textAlign: 'right' }}>Weight</th>
                <th style={{ width: 60, textAlign: 'right' }}>Lines</th>
                <th style={{ width: 90 }}>State</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.code}>
                  <td style={{ fontFamily: "'JetBrains Mono', monospace", overflowWrap: 'break-word' }} title={e.label || undefined}>
                    {e.code}
                  </td>
                  <td title={RANK_TITLE[e.swap_priority]}
                    style={{ fontWeight: 600, color: e.swap_priority === 'A' ? 'var(--accent2)' : 'var(--text-secondary)' }}>
                    {e.swap_priority || '—'}
                  </td>
                  <td style={{ color: e.ideal_index ? 'var(--text-secondary)' : 'var(--muted)', overflowWrap: 'break-word' }}>
                    {e.ideal_index || 'not stated'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                    {Math.round(e.catalog_weight).toLocaleString()}
                  </td>
                  <td style={{ textAlign: 'right' }}>{e.line_count}</td>
                  <td style={{ overflowWrap: 'break-word' }}>
                    {e.priceable
                      ? <span style={{ color: 'var(--muted)' }}>{e.proxy_status || 'priceable'}</span>
                      : <span style={{ color: 'var(--accent2)' }}>{e.resolution}</span>}
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

export default function ResolutionModal({ onClose, onOpenSeries }) {
  const [tab, setTab] = useState('concentration');
  return (
    <Modal isOpen onClose={onClose} title="Resolution" width={860}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`ca-btn ca-btn-sm ${tab === t.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'concentration' && <Concentration onOpenSeries={onOpenSeries} />}
      {tab === 'unpriceable' && <Unpriceable />}
      {tab === 'backlog' && <SwapBacklog />}
    </Modal>
  );
}
