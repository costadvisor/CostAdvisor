import { useCallback, useEffect, useState } from 'react';
import api, { formatApiError } from '../api';
import { useToast } from './Toast';
import exportCsv from '../utils/exportCsv';

/* Stale content — the LIVE-5 worklist (Curation tab).
 *
 * Every report section, panel, platform lever and block the daily check
 * marked out of date, with its reasons, most serious first. "Mark reviewed"
 * says a person read it again: its baseline is re-pinned to today's data and
 * the flag clears until the data moves further. Texts stale only by age are
 * counted, not listed — they all clear together when the content drop is
 * re-loaded. Needs the platform content.approve permission.
 */

const REASON = {
  data_moved: 'Data moved',
  typed_drifted: 'Typed numbers drifted',
  data_late: 'Data late',
  expired: 'Expired',
  old_text: 'Old text',
};
const KINDS = [['', 'All'], ['section', 'Report sections'], ['panel', 'Strategy panels'],
  ['block', 'Blocks'], ['lever', 'Levers']];

export default function StaleQueue() {
  const { addToast } = useToast();
  const [kind, setKind] = useState('');
  const [reason, setReason] = useState('');
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    setErr(null);
    api.get('/api/staleness/texts', { params: { kind: kind || undefined, reason: reason || undefined, limit: 500 } })
      .then(({ data: d }) => setData(d))
      .catch((e) => setErr(formatApiError(e) || 'Could not load stale content.'));
  }, [kind, reason]);

  useEffect(load, [load]);

  const review = async (item) => {
    setBusy(item.id);
    try {
      await api.post('/api/staleness/review', { kind: item.kind, id: item.id });
      addToast(`Marked reviewed: ${item.where}. It clears until the data moves further.`, 'success');
      load();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not mark reviewed (needs content.approve).', 'error');
    } finally { setBusy(null); }
  };

  if (err) return <div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div>;
  if (!data) return <div className="ca-skeleton" style={{ height: 120 }} />;

  const total = Object.values(data.counts).reduce((a, b) => a + b, 0);

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '4px 0 14px' }}>
        <div className="ca-metric" style={{ flex: '1 1 140px' }}>
          <div className="ca-metric-val" style={total ? { color: 'var(--accent3)' } : undefined}>{total}</div>
          <div className="ca-metric-lbl">Stale texts</div>
        </div>
        {Object.entries(data.by_reason).map(([code, n]) => (
          <div key={code} className="ca-metric" style={{ flex: '1 1 140px' }}>
            <div className="ca-metric-val">{n}</div>
            <div className="ca-metric-lbl">{REASON[code] || code}</div>
          </div>
        ))}
      </div>

      {data.age_only > 0 && (
        <div className="ca-card" style={{ marginBottom: 12, fontSize: 12 }}>
          {data.age_only} text(s) are stale only by age. They are not listed: re-loading the content drop
          refreshes them all at once.
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10, alignItems: 'center' }}>
        {KINDS.map(([k, label]) => (
          <button key={k || 'all'} className={`ca-btn ca-btn-sm ${kind === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            aria-pressed={kind === k} onClick={() => setKind(k)}>{label}</button>
        ))}
        <select className="ca-input" aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}
          style={{ marginLeft: 'auto', fontSize: 11 }}>
          <option value="">Any reason</option>
          {Object.entries(REASON).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
        <button className="ca-btn ca-btn-sm ca-btn-ghost" disabled={!data.items.length}
          onClick={() => exportCsv('stale_content.csv', ['Kind', 'Where', 'Reasons', 'Reader sees'],
            data.items.map((i) => [i.kind, i.where, i.reasons.map((r) => r.detail).join(' | '), i.reader_note || '']))}>
          Export CSV
        </button>
      </div>

      {data.items.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 24, fontSize: 12, color: 'var(--muted)' }}>
          Nothing stale here.
        </div>
      ) : (
        <div className="ca-scroll-x">
          <table className="ca-table" style={{ fontSize: 11 }}>
            <caption className="ca-sr-only">Stale texts, most serious first, with their reasons.</caption>
            <thead>
              <tr>
                <th scope="col">Text</th>
                <th scope="col">Why</th>
                <th scope="col">Readers see</th>
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((i) => (
                <tr key={`${i.kind}-${i.id}`}>
                  <td><span className="ca-tag">{i.kind}</span> {i.where}</td>
                  <td>
                    {i.reasons.map((r) => (
                      <div key={r.code}><strong>{REASON[r.code] || r.code}:</strong> {r.detail}</div>
                    ))}
                  </td>
                  <td style={{ color: 'var(--muted)' }}>{i.reader_note || '— (staff only)'}</td>
                  <td>
                    <button className="ca-btn ca-btn-sm ca-btn-ghost" disabled={busy === i.id} onClick={() => review(i)}
                      title="You read it again: re-pins its data and clears the flag until the data moves further">
                      Mark reviewed
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
