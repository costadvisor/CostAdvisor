import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import { qLabel } from '../utils/quarters';

// Supplier price-list import (Scrum 30).
//
// Not the same page as /quotes, and the difference is the destination: that one
// reads a one-off quote into a quote record for a negotiation position, this one
// reads a recurring price list into ActualPrice — the number every gap in
// Monitor is measured against. So the review step here is about matching, not
// about parsing.
//
// Bespoke rather than FileUpload.jsx: that component's contract is a dry-run
// double-POST to one endpoint, and this flow persists a real reviewable draft
// on the first call. Same reasoning that made SheetRoundTripPanel bespoke.

const MATCH = {
  exact: { label: 'Exact', color: 'var(--accent)', bg: 'var(--accent-dim)',
    help: 'The product name or code matched one of your cost models exactly.' },
  fuzzy: { label: 'Fuzzy', color: 'var(--accent3)', bg: 'var(--accent3-dim)',
    help: 'A close match — worth a glance before you commit it.' },
  ambiguous: { label: 'Ambiguous', color: 'var(--accent2)', bg: 'var(--accent2-dim)',
    help: 'Several cost models fit equally well. Pick one; the document cannot tell them apart.' },
  unmatched: { label: 'No match', color: 'var(--accent2)', bg: 'var(--accent2-dim)',
    help: 'Nothing close enough. Choose a cost model or skip the row.' },
};

const FIELDS = [
  ['product_reference', 'Product'],
  ['price', 'Price'],
  ['currency', 'Cur'],
  ['unit', 'Unit'],
  ['incoterm', 'Incoterm'],
];

function confColor(conf) {
  if (conf == null) return 'var(--muted)';
  if (conf >= 0.9) return 'var(--accent)';
  if (conf >= 0.7) return 'var(--accent3)';
  return 'var(--accent2)';
}

function FieldCell({ entry }) {
  // An absent key means the parser did not find the field. That is not the
  // same as found-and-empty, and the two must not render identically.
  if (!entry || entry.value === null || entry.value === undefined) {
    return <td><span style={{ fontSize: 11, color: 'var(--accent2)' }}>not found</span></td>;
  }
  const v = typeof entry.value === 'number' ? entry.value.toFixed(2) : String(entry.value);
  const snippet = entry.locator?.snippet;
  return (
    <td title={snippet ? `Read from: "${snippet}"` : undefined}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span aria-hidden="true" style={{
          width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
          background: confColor(entry.confidence),
        }} />
        <span>{v}</span>
        {entry.confidence != null && (
          <span className="ca-sr-only">confidence {Math.round(entry.confidence * 100)} percent</span>
        )}
      </span>
    </td>
  );
}

function PeriodInput({ row, override, onChange }) {
  const year = override.year ?? row.period_year ?? '';
  const quarter = override.quarter ?? row.period_quarter ?? '';
  const derived = row.period_year != null && override.year == null && override.quarter == null;
  return (
    <td>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <input
          className="ca-input" type="number" value={year} placeholder="year"
          aria-label={`Year for row ${row.line_index + 1}`}
          onChange={e => onChange({ year: e.target.value === '' ? null : Number(e.target.value) })}
          style={{ width: 66 }}
        />
        <input
          className="ca-input" type="number" min={1} max={4} value={quarter} placeholder="Q"
          aria-label={`Quarter for row ${row.line_index + 1}`}
          onChange={e => onChange({ quarter: e.target.value === '' ? null : Number(e.target.value) })}
          style={{ width: 46 }}
        />
      </div>
      <div style={{ fontSize: 10, color: derived ? 'var(--muted)' : 'var(--accent3)', marginTop: 2 }}>
        {derived
          ? `read from the document (${qLabel(row.period_year, row.period_quarter)})`
          : row.period_year == null ? 'not stated — enter one' : 'overridden'}
      </div>
    </td>
  );
}

export default function PriceListImport() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const fileRef = useRef(null);

  const [suppliers, setSuppliers] = useState([]);
  const [supplierId, setSupplierId] = useState('');
  const [run, setRun] = useState(null);
  const [overrides, setOverrides] = useState({}); // row_id -> {cost_model_id, year, quarter}
  const [selected, setSelected] = useState(new Set());
  const [recent, setRecent] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [results, setResults] = useState(null);

  const loadRecent = useCallback(() => {
    if (!activeTeamId) return;
    api.get('/api/price-lists/runs', { params: { team_id: activeTeamId, limit: 10 } })
      .then(({ data }) => setRecent(data))
      .catch(() => setRecent([]));
  }, [activeTeamId]);

  useEffect(() => {
    if (!activeTeamId) return;
    api.get('/api/suppliers', { params: { team_id: activeTeamId } })
      .then(({ data }) => setSuppliers(data))
      .catch(() => setSuppliers([]));
    loadRecent();
  }, [activeTeamId, loadRecent]);

  const applyRun = (data) => {
    setRun(data);
    setResults(null);
    setOverrides({});
    // Pre-select only what can actually go in. An ambiguous or unmatched row
    // ticked by default is how a price lands on the wrong product.
    setSelected(new Set(
      data.rows.filter(r => r.status === 'pending' && r.matched_cost_model_id && r.period_year != null)
        .map(r => r.id),
    ));
  };

  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setErr(null);
    const form = new FormData();
    form.append('file', file);
    if (supplierId) form.append('supplier_id', supplierId);
    try {
      const { data } = await api.post('/api/price-lists/extract', form,
        { params: { team_id: activeTeamId } });
      applyRun(data);
      loadRecent();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not read that file.');
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const openRun = async (id) => {
    setBusy(true); setErr(null);
    try {
      const { data } = await api.get(`/api/price-lists/runs/${id}`);
      applyRun(data);
    } catch (e) {
      setErr(formatApiError(e) || 'Could not open that run.');
    } finally {
      setBusy(false);
    }
  };

  const setOverride = (rowId, patch) =>
    setOverrides(o => ({ ...o, [rowId]: { ...o[rowId], ...patch } }));

  const toggle = (rowId) => setSelected(s => {
    const next = new Set(s);
    if (next.has(rowId)) next.delete(rowId); else next.add(rowId);
    return next;
  });

  const commit = async () => {
    const rows = [...selected].map(id => {
      const o = overrides[id] || {};
      return {
        row_id: id,
        ...(o.cost_model_id ? { cost_model_id: o.cost_model_id } : {}),
        ...(o.year != null ? { year: o.year } : {}),
        ...(o.quarter != null ? { quarter: o.quarter } : {}),
      };
    });
    if (!rows.length) return;
    setBusy(true); setErr(null);
    try {
      const { data } = await api.post(`/api/price-lists/runs/${run.id}/commit`, { rows });
      setResults(Object.fromEntries(data.results.map(r => [r.row_id, r])));
      addToast(
        data.failed
          ? `${data.committed} committed, ${data.failed} could not be — see the reasons in the table.`
          : `${data.committed} price${data.committed === 1 ? '' : 's'} committed.`,
        data.failed ? 'info' : 'success',
      );
      const { data: fresh } = await api.get(`/api/price-lists/runs/${run.id}`);
      setRun(fresh);
      setSelected(new Set());
      loadRecent();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not commit those rows.');
    } finally {
      setBusy(false);
    }
  };

  const skip = async (rowId) => {
    try {
      const { data } = await api.post(`/api/price-lists/rows/${rowId}/skip`);
      setRun(data);
      setSelected(s => { const n = new Set(s); n.delete(rowId); return n; });
      loadRecent();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not skip that row.', 'error');
    }
  };

  const pending = run ? run.rows.filter(r => r.status === 'pending') : [];
  const committedCount = run ? run.rows.filter(r => r.status === 'committed').length : 0;

  return (
    <div className="ca-page ca-fade-in">
      <h1 className="ca-h1">Supplier price list</h1>
      <p className="ca-subtitle">
        Read a supplier&apos;s PDF price list, check what it says and which product each line is about, then
        commit it as actual prices.
      </p>

      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 14 }}>{err}</div>}

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title" style={{ marginBottom: 12 }}>Upload</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 240px' }}>
            <label className="ca-label" htmlFor="pl-supplier">Supplier (optional)</label>
            <select id="pl-supplier" className="ca-select" value={supplierId}
                    onChange={e => setSupplierId(e.target.value)} style={{ width: '100%' }}>
              <option value="">Any supplier</option>
              {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf"
            aria-label="Price list PDF"
            onChange={e => upload(e.target.files?.[0])}
            disabled={busy || !activeTeamId}
            className="ca-input"
            style={{ flex: '2 1 280px' }}
          />
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 10 }}>
          Naming the supplier narrows matching to their cost models — which is usually what turns two
          same-named products into one answer. Tabular lists read best; a free-text letter still works, with
          lower confidence on everything except the price.
        </div>
      </div>

      {run && (
        <div className="ca-card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <div>
              <div style={{ fontWeight: 600 }}>{run.filename}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
                {run.rows.length} row{run.rows.length === 1 ? '' : 's'} · {pending.length} pending ·{' '}
                {committedCount} committed
              </div>
            </div>
            <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto',
                           display: 'inline-flex', alignItems: 'center', gap: 12 }}>
              <span><span aria-hidden="true" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', marginRight: 4 }} />labelled</span>
              <span><span aria-hidden="true" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--accent3)', marginRight: 4 }} />contextual</span>
              <span><span aria-hidden="true" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--accent2)', marginRight: 4 }} />weak</span>
            </span>
          </div>

          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">
                Rows read from the price list, with the cost model each one matched and the period it applies to.
              </caption>
              <thead>
                <tr>
                  <th scope="col" style={{ width: 30 }}><span className="ca-sr-only">Include</span></th>
                  {FIELDS.map(([k, label]) => <th key={k} scope="col">{label}</th>)}
                  <th scope="col">Period</th>
                  <th scope="col">Cost model</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {run.rows.map(row => {
                  const m = MATCH[row.match_confidence] || MATCH.unmatched;
                  const o = overrides[row.id] || {};
                  const result = results?.[row.id];
                  const done = row.status !== 'pending';
                  const chosen = o.cost_model_id || row.matched_cost_model_id || '';
                  return (
                    <tr key={row.id} style={{ opacity: done ? 0.55 : 1 }}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.has(row.id)}
                          disabled={done}
                          onChange={() => toggle(row.id)}
                          aria-label={`Include row ${row.line_index + 1}`}
                        />
                      </td>
                      {FIELDS.map(([k]) => <FieldCell key={k} entry={row.fields[k]} />)}
                      {done ? (
                        <td style={{ fontSize: 11, color: 'var(--muted)' }}>
                          {row.period_year ? qLabel(row.period_year, row.period_quarter) : '—'}
                        </td>
                      ) : (
                        <PeriodInput row={row} override={o} onChange={p => setOverride(row.id, p)} />
                      )}
                      <td style={{ minWidth: 220 }}>
                        <span className="ca-badge" style={{ background: m.bg, color: m.color, marginBottom: 4, display: 'inline-block' }}
                              title={m.help}>
                          {m.label}
                        </span>
                        {done ? (
                          <div style={{ fontSize: 11 }}>
                            {(row.match_candidates || []).find(c => c.cost_model_id === row.matched_cost_model_id)?.product_name || '—'}
                          </div>
                        ) : (
                          <select
                            className="ca-select"
                            value={chosen}
                            aria-label={`Cost model for row ${row.line_index + 1}`}
                            onChange={e => setOverride(row.id, { cost_model_id: e.target.value || null })}
                            style={{ width: '100%' }}
                          >
                            <option value="">Choose a cost model…</option>
                            {(row.match_candidates || []).map(c => (
                              <option key={c.cost_model_id} value={c.cost_model_id}>
                                {c.product_name}{c.supplier_name ? ` · ${c.supplier_name}` : ''} ({c.score})
                              </option>
                            ))}
                          </select>
                        )}
                        {result && !result.committed && (
                          <div style={{ fontSize: 10, color: 'var(--accent2)', marginTop: 4 }}>{result.error}</div>
                        )}
                      </td>
                      <td>
                        {done ? (
                          <span className="ca-badge" style={{
                            background: row.status === 'committed' ? 'var(--accent-dim)' : 'var(--neutral-bg)',
                            color: row.status === 'committed' ? 'var(--accent)' : 'var(--muted)',
                          }}>
                            {row.status}
                          </span>
                        ) : (
                          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => skip(row.id)}>
                            Skip
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={commit}
                    disabled={busy || selected.size === 0}>
              {busy ? 'Committing…' : `Commit ${selected.size} row${selected.size === 1 ? '' : 's'}`}
            </button>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>
              A row with no cost model or no period is held back rather than guessed at — a price on the wrong
              product moves a gap with nothing on screen to say so.
            </span>
          </div>
        </div>
      )}

      {recent.length > 0 && (
        <div className="ca-card">
          <div className="ca-card-title" style={{ marginBottom: 12 }}>Recent imports</div>
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">Previously uploaded price lists.</caption>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Uploaded</th>
                  <th scope="col">Status</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Rows</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Committed</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {recent.map(r => (
                  <tr key={r.id}>
                    <td>{r.filename}</td>
                    <td style={{ color: 'var(--muted)' }}>{new Date(r.created_at).toLocaleString()}</td>
                    <td>{r.status}</td>
                    <td style={{ textAlign: 'right' }}>{r.row_count}</td>
                    <td style={{ textAlign: 'right' }}>{r.committed_count}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => openRun(r.id)}>Open</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 18 }}>
        Looking for a one-off quote to build a negotiation position from, rather than a recurring list?{' '}
        <button className="ca-btn ca-btn-link ca-btn-sm" onClick={() => navigate('/quotes')}>
          Quote extraction
        </button>
      </p>
    </div>
  );
}
