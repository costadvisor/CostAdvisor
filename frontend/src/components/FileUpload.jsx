import { useState, useRef } from 'react';
import api, { formatApiError } from '../api';

export default function FileUpload({ endpoint, onSuccess, accept = '.csv,.xlsx,.xls', label = 'Upload File' }) {
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState(null); // { filename, rows_processed, errors }
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const pendingFileRef = useRef(null);

  const handleFileSelect = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setUploading(true);
    setError(null);
    setResult(null);
    setPreview(null);
    pendingFileRef.current = file;

    const formData = new FormData();
    formData.append('file', file);

    try {
      const sep = endpoint.includes('?') ? '&' : '?';
      const { data } = await api.post(`${endpoint}${sep}dry_run=true`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setPreview({ filename: data.filename || file.name, rows_processed: data.rows_processed, errors: data.errors || [] });
    } catch (err) {
      setError(formatApiError(err));
      pendingFileRef.current = null;
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleConfirm = async () => {
    const file = pendingFileRef.current;
    if (!file) return;

    setConfirming(true);
    setError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const { data } = await api.post(endpoint, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setResult(data);
      setPreview(null);
      pendingFileRef.current = null;
      if (onSuccess) onSuccess(data);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setConfirming(false);
    }
  };

  const handleCancel = () => {
    setPreview(null);
    pendingFileRef.current = null;
    setError(null);
  };

  return (
    <div>
      <input
        ref={fileRef}
        type="file"
        accept={accept}
        onChange={handleFileSelect}
        style={{ display: 'none' }}
      />
      <button
        className="ca-btn ca-btn-ghost ca-btn-sm"
        onClick={() => { setResult(null); fileRef.current?.click(); }}
        disabled={uploading || confirming}
      >
        {uploading ? 'Reading…' : label}
      </button>

      {preview && (
        <div style={{
          marginTop: 10, padding: '12px 14px', borderRadius: 8,
          border: '1px solid var(--border)', background: 'var(--surface)',
          fontSize: 12,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                {preview.filename} &middot; {preview.rows_processed} row{preview.rows_processed !== 1 ? 's' : ''} ready
              </div>
              {preview.errors.length > 0 && (
                <div style={{ color: 'var(--accent3)', marginBottom: 6 }}>
                  <span style={{ fontWeight: 600 }}>{preview.errors.length} row{preview.errors.length !== 1 ? 's' : ''} will be skipped:</span>
                  <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                    {preview.errors.slice(0, 5).map((e, i) => (
                      <li key={i}>{e.row ? `Row ${e.row}: ` : ''}{e.message}</li>
                    ))}
                    {preview.errors.length > 5 && <li>…and {preview.errors.length - 5} more</li>}
                  </ul>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handleCancel} disabled={confirming}>
                Cancel
              </button>
              <button
                className="ca-btn ca-btn-primary ca-btn-sm"
                onClick={handleConfirm}
                disabled={confirming || preview.rows_processed === 0}
              >
                {confirming ? 'Importing…' : `Import ${preview.rows_processed} row${preview.rows_processed !== 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {result && !preview && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--accent)' }}>
          {result.rows_processed} rows imported from {result.filename}
        </div>
      )}
      {error && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--accent2)' }}>
          {error}
        </div>
      )}
    </div>
  );
}
