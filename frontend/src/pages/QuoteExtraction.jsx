import { useEffect, useRef, useState } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import RegionSelect from '../components/RegionSelect';

/* Scrum 31b — Quote & price-list extraction. Upload a supplier quote PDF,
 * review the extracted lines (each field carries a confidence + a locator
 * back into the source document), confirm or reject per line — a document
 * can be multi-product/multi-tier, so review is line-grained. Confirming
 * writes into the permanent quote record; nothing lands there before that.
 * A confirmed line can then be compared against a catalog combo via the
 * existing negotiation-position engine (Scrum 30b) with no re-typing. */

const FIELD_LABELS = {
  product_reference: 'Product', price: 'Price', currency: 'Currency', unit: 'Unit',
  volume_tier: 'Volume tier', incoterm: 'Incoterm', named_place: 'Named place',
  quote_date: 'Quote date', valid_from: 'Valid from', valid_until: 'Valid until',
};
const CONFIRM_FIELDS = Object.keys(FIELD_LABELS);

function confidenceColor(c) {
  if (c >= 0.9) return 'var(--accent)';
  if (c >= 0.6) return 'var(--accent3)';
  return 'var(--accent2)';
}

function ExtractedLineRow({ line, onConfirmed, onRejected }) {
  const { addToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [overrides, setOverrides] = useState(() => {
    const init = {};
    for (const f of CONFIRM_FIELDS) init[f] = line.fields[f]?.value ?? '';
    return init;
  });
  const [saving, setSaving] = useState(false);

  const setField = (name, val) => setOverrides(prev => ({ ...prev, [name]: val }));

  const confirm = async () => {
    setSaving(true);
    try {
      const payload = {};
      for (const f of CONFIRM_FIELDS) {
        const v = overrides[f];
        payload[f] = v === '' ? null : (f === 'price' ? Number(v) : v);
      }
      const { data } = await api.post(`/api/quotes/lines/${line.id}/confirm`, payload);
      addToast('Confirmed — landed in the quote record', 'success');
      onConfirmed(data);
    } catch (err) {
      addToast(formatApiError(err), 'error');
    } finally {
      setSaving(false);
    }
  };

  const reject = async () => {
    try {
      await api.post(`/api/quotes/lines/${line.id}/reject`);
      addToast('Rejected', 'info');
      onRejected(line.id);
    } catch (err) {
      addToast(formatApiError(err), 'error');
    }
  };

  if (line.status !== 'pending') {
    return (
      <tr>
        <td colSpan={3} style={{ color: 'var(--muted)', fontSize: 12 }}>
          Line {line.line_index + 1} — {line.status}
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td style={{ verticalAlign: 'top', padding: '10px 8px', width: 40 }}>{line.line_index + 1}</td>
      <td style={{ verticalAlign: 'top', padding: '10px 8px' }}>
        {editing ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, maxWidth: 620 }}>
            {CONFIRM_FIELDS.map(f => (
              <div key={f}>
                <label className="ca-label" style={{ fontSize: 9 }}>{FIELD_LABELS[f]}</label>
                <input className="ca-input" style={{ fontSize: 11, padding: '5px 6px' }}
                  value={overrides[f]} onChange={e => setField(f, e.target.value)} />
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
            {CONFIRM_FIELDS.filter(f => line.fields[f]).map(f => {
              const entry = line.fields[f];
              return (
                <span key={f} title={`p.${entry.locator.page}: "${entry.locator.snippet}"`}
                  style={{ fontSize: 11, padding: '3px 8px', borderRadius: 12, background: 'var(--bg)', cursor: 'help' }}>
                  <span style={{ color: 'var(--muted)' }}>{FIELD_LABELS[f]}:</span>{' '}
                  <strong>{String(entry.value)}</strong>{' '}
                  <span style={{
                    display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
                    background: confidenceColor(entry.confidence), marginLeft: 3,
                  }} />
                </span>
              );
            })}
            {CONFIRM_FIELDS.filter(f => !line.fields[f]).length > 0 && (
              <span style={{ fontSize: 10, color: 'var(--muted)', fontStyle: 'italic' }}>
                not found: {CONFIRM_FIELDS.filter(f => !line.fields[f]).map(f => FIELD_LABELS[f]).join(', ')}
              </span>
            )}
          </div>
        )}
      </td>
      <td style={{ verticalAlign: 'top', padding: '10px 8px', whiteSpace: 'nowrap' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditing(v => !v)}>
            {editing ? 'Cancel edit' : 'Edit'}
          </button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={confirm} disabled={saving}>
            {saving ? 'Confirming…' : 'Confirm'}
          </button>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)' }} onClick={reject}>
            Reject
          </button>
        </div>
      </td>
    </tr>
  );
}

function PositionPreview({ quoteLine, templates }) {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const [templateId, setTemplateId] = useState('');
  const [region, setRegion] = useState('Europe');
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [quarter, setQuarter] = useState(Math.floor(now.getMonth() / 3) + 1);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const compute = async () => {
    if (!templateId) { addToast('Pick a catalog formula first', 'error'); return; }
    setLoading(true);
    setResult(null);
    try {
      const { data } = await api.get(`/api/formulas/${templateId}/negotiation-position`, {
        params: { team_id: activeTeamId, region, year, quarter, quote_line_id: quoteLine.id },
      });
      setResult(data);
    } catch (err) {
      addToast(formatApiError(err), 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginTop: 6, background: 'var(--bg)' }}>
      <div style={{ fontSize: 10, fontWeight: 600, marginBottom: 6, color: 'var(--muted)', textTransform: 'uppercase' }}>
        Negotiation position for this line
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ca-select" style={{ fontSize: 11, minWidth: 160 }} value={templateId} onChange={e => setTemplateId(e.target.value)}>
          <option value="">Catalog formula…</option>
          {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <div style={{ width: 130 }}><RegionSelect value={region} onChange={setRegion} /></div>
        <input className="ca-input" style={{ width: 70, fontSize: 11 }} type="number" value={year} onChange={e => setYear(+e.target.value)} />
        <select className="ca-select" style={{ fontSize: 11, width: 70 }} value={quarter} onChange={e => setQuarter(+e.target.value)}>
          {[1, 2, 3, 4].map(q => <option key={q} value={q}>Q{q}</option>)}
        </select>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={compute} disabled={loading}>
          {loading ? 'Computing…' : 'Compute'}
        </button>
      </div>
      {result && (
        <div style={{ display: 'flex', gap: 18, marginTop: 10, fontSize: 12 }}>
          <div>
            <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Target</div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>
              {result.target.should_cost != null ? result.target.should_cost.toFixed(2) : `${result.target.index_level_pct}% (index only)`}
            </div>
          </div>
          {result.position.insufficient ? (
            <div style={{ color: 'var(--muted)', fontStyle: 'italic' }}>{result.position.reason}</div>
          ) : (
            <>
              <div>
                <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Ask</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>{result.position.ask.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Unexplained</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, color: 'var(--accent2)' }}>
                  {result.position.unexplained_remainder.toFixed(2)}
                </div>
              </div>
            </>
          )}
        </div>
      )}
      {result?.normalization?.notes?.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 10, color: 'var(--muted)' }}>
          {result.normalization.notes.map((n, i) => <div key={i}>· {n}</div>)}
        </div>
      )}
    </div>
  );
}

export default function QuoteExtraction() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const fileInputRef = useRef(null);

  const [uploading, setUploading] = useState(false);
  const [run, setRun] = useState(null);
  const [records, setRecords] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [confirmedPreview, setConfirmedPreview] = useState(null);

  const loadRecords = () => {
    if (!activeTeamId) return;
    api.get('/api/quotes/records', { params: { team_id: activeTeamId } })
      .then(({ data }) => setRecords(data))
      .catch(() => {});
  };

  useEffect(() => {
    if (!activeTeamId) return;
    loadRecords();
    api.get('/api/formulas/', { params: { team_id: activeTeamId } })
      .then(({ data }) => setTemplates(data))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId]);

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setRun(null);
    setConfirmedPreview(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const { data } = await api.post('/api/quotes/extract', form, { params: { team_id: activeTeamId } });
      setRun(data);
      addToast(`Extracted ${data.lines.length} line(s) — review below`, 'success');
    } catch (err) {
      addToast(formatApiError(err), 'error');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleLineConfirmed = (recordLine, lineId) => {
    setRun(prev => ({
      ...prev,
      lines: prev.lines.map(l => l.id === lineId ? { ...l, status: 'confirmed' } : l),
    }));
    setConfirmedPreview(recordLine);
    loadRecords();
  };

  const handleLineRejected = (lineId) => {
    setRun(prev => ({
      ...prev,
      lines: prev.lines.map(l => l.id === lineId ? { ...l, status: 'rejected' } : l),
    }));
  };

  const pendingCount = run?.lines?.filter(l => l.status === 'pending').length ?? 0;

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1" style={{ marginBottom: 4 }}>Quote Extraction</div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
        Upload a supplier quote or price list (PDF). Every extracted field carries a confidence
        and a locator back into the document — nothing lands in the quote record until you confirm it.
      </div>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
          {uploading ? 'Extracting…' : 'Upload Quote (PDF)'}
        </button>
        <input ref={fileInputRef} type="file" accept=".pdf" style={{ display: 'none' }} onChange={handleFile} />

        {run && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
              {run.filename} — {run.lines.length} line(s), {pendingCount} pending review
            </div>
            <div className="ca-scroll-x">
              <table className="ca-table" style={{ width: '100%' }}>
                <thead>
                  <tr><th style={{ width: 40 }}>#</th><th>Extracted fields</th><th style={{ width: 180 }}>Actions</th></tr>
                </thead>
                <tbody>
                  {run.lines.map(line => (
                    <ExtractedLineRow
                      key={line.id}
                      line={line}
                      onConfirmed={(recordLine) => handleLineConfirmed(recordLine, line.id)}
                      onRejected={() => handleLineRejected(line.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {confirmedPreview && (
        <div className="ca-card" style={{ marginBottom: 16 }}>
          <div className="ca-card-title">Just confirmed</div>
          <PositionPreview quoteLine={confirmedPreview} templates={templates} />
        </div>
      )}

      {records.length > 0 && (
        <div className="ca-card">
          <div className="ca-card-title">Confirmed quote records</div>
          {records.map(rec => (
            <div key={rec.id} style={{ marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>{rec.filename}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{new Date(rec.created_at).toLocaleString()}</div>
              {rec.lines.map(l => (
                <div key={l.id} style={{ fontSize: 11, marginTop: 4 }}>
                  {l.product_reference || 'Unnamed line'} — {l.price != null ? `${l.price} ${l.currency || ''}` : 'no price'}
                  {l.incoterm ? ` · ${l.incoterm}${l.named_place ? ' ' + l.named_place : ''}` : ''}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
