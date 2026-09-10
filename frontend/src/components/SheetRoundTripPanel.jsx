import { useEffect, useMemo, useRef, useState } from 'react';
import api, { formatApiError } from '../api';
import { useToast } from './Toast';

/* Scrum 27b — sheet round-trip: export a filtered slice, edit offline, reimport
 * as a reviewable diff, apply separately.
 *
 * Payload-agnostic, matching the backend: `sheet_roundtrip` is a mechanism plus
 * registered per-payload specs, so a second payload is a spec and one registry
 * line, not new machinery. This component takes the same shape — the payload
 * key, its filter form and how to label a row key are props, and the diff /
 * apply / run-history half is identical for every payload. The two callers are
 * catalog combo prices (Formulas) and dimension decisions (the curation
 * console).
 *
 * The two rules the backend enforces and this must not undo: importing only
 * ever computes a diff (applying is a separate, explicit call), and rejected
 * or invalid rows are shown rather than silently absorbed. */

const KIND_LABEL = {
  change: { label: 'Change', color: 'var(--accent)' },
  rejected_readonly_edit: { label: 'Read-only edited — ignored', color: 'var(--accent2)' },
  invalid_value: { label: 'Invalid value — ignored', color: 'var(--accent2)' },
  unmatched_key: { label: 'Row not found — ignored', color: 'var(--muted)' },
};

export default function SheetRoundTripPanel({
  // Catalog combo prices stay the default so the existing Formulas usage is
  // unchanged by the generalisation.
  payloadKey = 'formula_coverage_price',
  title = 'Review & apply price changes',
  blurb = 'Export a slice, edit prices offline, reimport to see exactly what changed before applying anything.',
  exportFilename = 'formula_coverage_prices.xlsx',
  // Passed by a caller with its own filters; when absent the built-in
  // subfamily / needs-review form below is used.
  renderFilters,
  filterParams: filterParamsProp,
  rowKeyLabel = (k) => `${k.code} · ${k.region}`,
  catalogRows = [],
}) {
  const { addToast } = useToast();
  const fileInputRef = useRef(null);

  const [subfamilyId, setSubfamilyId] = useState('');
  const [needsReviewOnly, setNeedsReviewOnly] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [run, setRun] = useState(null);
  const [pastRuns, setPastRuns] = useState([]);

  const subfamilyOptions = useMemo(() => {
    const map = new Map();
    for (const t of catalogRows) {
      if (t.subfamily_id != null) {
        map.set(t.subfamily_id, `${t.family_name || 'Uncategorised'} / ${t.subfamily_name || '—'}`);
      }
    }
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [catalogRows]);

  const filterParams = () => {
    if (filterParamsProp) return filterParamsProp();
    const p = {};
    if (subfamilyId) p.subfamily_id = subfamilyId;
    if (needsReviewOnly) p.needs_review = true;
    return p;
  };

  const loadPastRuns = () => {
    api.get('/api/sheets/import-runs', { params: { payload_key: payloadKey } })
      .then(({ data }) => setPastRuns(data))
      .catch(() => {});
  };
  useEffect(() => { loadPastRuns(); }, [payloadKey]);

  const handleExport = async () => {
    setExporting(true);
    try {
      const { data } = await api.get(`/api/sheets/${payloadKey}/export`, {
        params: filterParams(), responseType: 'blob',
      });
      const url = window.URL.createObjectURL(data);
      const a = document.createElement('a');
      a.href = url;
      a.download = exportFilename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      addToast(formatApiError(err) || 'Export failed', 'error');
    } finally {
      setExporting(false);
    }
  };

  const handleImportFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setRun(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const { data } = await api.post(`/api/sheets/${payloadKey}/import`, form, {
        params: filterParams(),
      });
      setRun(data);
      addToast(
        data.diffs.length ? `${data.diffs.length} row(s) affected — review below.` : 'No changes — the sheet matches what is already saved.',
        data.diffs.length ? 'info' : 'success',
      );
    } catch (err) {
      addToast(formatApiError(err) || 'Import failed', 'error');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleApply = async () => {
    if (!run) return;
    setApplying(true);
    try {
      const { data } = await api.post(`/api/sheets/import-runs/${run.id}/apply`);
      setRun(data.run);
      const skipped = data.skipped_stale.length;
      addToast(
        `Applied ${data.applied.length} change(s)` + (skipped ? `, ${skipped} skipped (changed since diff)` : '.'),
        'success',
      );
      loadPastRuns();
    } catch (err) {
      addToast(formatApiError(err) || 'Apply failed', 'error');
    } finally {
      setApplying(false);
    }
  };

  const pendingChanges = run?.diffs?.filter(d => d.kind === 'change' && !d.applied) ?? [];
  const otherEntries = run?.diffs?.filter(d => d.kind !== 'change') ?? [];

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px',
      marginBottom: 12, background: 'var(--surface)',
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>{blurb}</div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        {renderFilters ? renderFilters() : (
          <>
            <select className="ca-select" style={{ fontSize: 11 }} value={subfamilyId} onChange={e => setSubfamilyId(e.target.value)}>
              <option value="">All subfamilies</option>
              {subfamilyOptions.map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
            <label style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
              <input type="checkbox" checked={needsReviewOnly} onChange={e => setNeedsReviewOnly(e.target.checked)} />
              Needs review only
            </label>
          </>
        )}
        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }} onClick={handleExport} disabled={exporting}>
          {exporting ? 'Exporting…' : '↓ Export slice'}
        </button>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
          onClick={() => fileInputRef.current?.click()} disabled={importing}>
          {importing ? 'Diffing…' : '↑ Reimport & diff'}
        </button>
        <input ref={fileInputRef} type="file" accept=".xlsx" style={{ display: 'none' }} onChange={handleImportFile} />
      </div>

      {run && (
        <div style={{ marginTop: 8 }}>
          <div className="ca-scroll-x">
            <table className="ca-table" style={{ width: '100%', fontSize: 11 }}>
              <thead>
                <tr>
                  <th>Row</th><th>Column</th><th>Old</th><th>New</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {[...pendingChanges, ...otherEntries].map(d => {
                  const k = KIND_LABEL[d.kind] || { label: d.kind, color: 'var(--muted)' };
                  return (
                    <tr key={d.id}>
                      <td style={{ fontFamily: "'JetBrains Mono', monospace" }}>{rowKeyLabel(d.row_key)}</td>
                      <td>{d.column}</td>
                      <td>{d.old_value ?? '—'}</td>
                      <td>{d.new_value ?? '—'}</td>
                      <td style={{ color: k.color }}>{d.applied ? 'Applied' : k.label}</td>
                    </tr>
                  );
                })}
                {run.diffs.length === 0 && (
                  <tr><td colSpan={5} style={{ color: 'var(--muted)', textAlign: 'center', padding: 12 }}>No changes.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {pendingChanges.length > 0 && (
            <button className="ca-btn ca-btn-primary ca-btn-sm" style={{ marginTop: 8 }}
              onClick={handleApply} disabled={applying}>
              {applying ? 'Applying…' : `Apply ${pendingChanges.length} change(s)`}
            </button>
          )}
        </div>
      )}

      {pastRuns.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 10, color: 'var(--muted)' }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Past runs</div>
          {pastRuns.slice(0, 5).map(r => (
            <div key={r.id}>
              {new Date(r.created_at).toLocaleString()} — {r.status} ({r.diffs.length} row{r.diffs.length === 1 ? '' : 's'})
              {r.applied_at ? ` · applied ${new Date(r.applied_at).toLocaleString()}` : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
