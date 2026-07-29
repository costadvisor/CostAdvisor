import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';
import { qLabel, QUARTER_OPTS } from '../utils/quarters';

export default function Pricing() {
  const { costModelId } = useParams();
  const navigate = useNavigate();

  // Pricing history
  const [prices, setPrices] = useState([]);
  const [volumes, setVolumes] = useState([]);
  const [loadingPrices, setLoadingPrices] = useState(true);

  // Inline add form
  const [addYear, setAddYear] = useState(2025);
  const [addQuarter, setAddQuarter] = useState(1);
  const [addPrice, setAddPrice] = useState('');
  const [addVolume, setAddVolume] = useState('');
  const [saving, setSaving] = useState(false);

  // Editing state
  const [editKey, setEditKey] = useState(null); // "year-quarter"
  const [editPrice, setEditPrice] = useState('');
  const [editVolume, setEditVolume] = useState('');

  // Upload feedback
  const [uploadError, setUploadError] = useState(null);
  const [uploadErrors, setUploadErrors] = useState([]); // per-row errors
  const [uploadPreview, setUploadPreview] = useState(null); // { filename, rows_processed, errors }
  const [pendingUploadFile, setPendingUploadFile] = useState(null);

  // Price change analyzer
  const [fromYear, setFromYear] = useState(2024);
  const [fromQuarter, setFromQuarter] = useState(1);
  const [toYear, setToYear] = useState(2025);
  const [toQuarter, setToQuarter] = useState(1);
  const [analysis, setAnalysis] = useState(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);


  // Model metadata
  const [model, setModel] = useState(null);

  useEffect(() => {
    if (!costModelId) return;
    api.get(`/api/cost-models/${costModelId}`).then(({ data }) => setModel(data));
    fetchData();
  }, [costModelId]);

  const fetchData = () => {
    setLoadingPrices(true);
    Promise.all([
      api.get(`/api/prices/${costModelId}`),
      api.get(`/api/volumes/${costModelId}`),
    ])
      .then(([pRes, vRes]) => { setPrices(pRes.data); setVolumes(vRes.data); })
      .finally(() => setLoadingPrices(false));
  };

  const addPriceRow = () => {
    if (!addPrice && !addVolume) return;
    setSaving(true);
    const promises = [];
    if (addPrice) {
      const body = { year: addYear, quarter: addQuarter, price: parseFloat(addPrice) };
      promises.push(api.put(`/api/prices/${costModelId}/${addYear}/${addQuarter}`, body));
    }
    if (addVolume) {
      promises.push(api.put(`/api/volumes/${costModelId}/${addYear}/${addQuarter}`, {
        year: addYear, quarter: addQuarter, volume: parseFloat(addVolume),
      }));
    }
    Promise.all(promises)
      .then(() => { setAddPrice(''); setAddVolume(''); fetchData(); })
      .finally(() => setSaving(false));
  };

  const startEdit = (row) => {
    setEditKey(`${row.year}-${row.quarter}`);
    setEditPrice(row.price != null ? String(row.price) : '');
    setEditVolume(row.volume != null ? String(row.volume) : '');
  };

  const saveEdit = (row) => {
    const promises = [];
    if (editPrice !== '') {
      const body = { year: row.year, quarter: row.quarter, price: parseFloat(editPrice) };
      promises.push(api.put(`/api/prices/${costModelId}/${row.year}/${row.quarter}`, body));
    }
    if (editVolume !== '') {
      promises.push(api.put(`/api/volumes/${costModelId}/${row.year}/${row.quarter}`, {
        year: row.year, quarter: row.quarter, volume: parseFloat(editVolume),
      }));
    }
    Promise.all(promises).then(() => { setEditKey(null); fetchData(); });
  };

  const deleteRow = (row) => {
    const promises = [];
    if (row.price != null) {
      promises.push(api.delete(`/api/prices/${costModelId}/${row.year}/${row.quarter}`).catch(() => {}));
    }
    if (row.volume != null) {
      promises.push(api.delete(`/api/volumes/${costModelId}/${row.year}/${row.quarter}`).catch(() => {}));
    }
    Promise.all(promises).then(() => fetchData());
  };

  const runAnalysis = () => {
    setLoadingAnalysis(true);
    api.post('/api/costing/price-change', {
      cost_model_id: costModelId,
      from_year: fromYear,
      from_quarter: fromQuarter,
      to_year: toYear,
      to_quarter: toQuarter,
    })
      .then(({ data }) => setAnalysis(data))
      .finally(() => setLoadingAnalysis(false));
  };

  // Upload handler — dry_run preview first
  const handleUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadError(null);
    setUploadErrors([]);
    setUploadPreview(null);
    const formData = new FormData();
    formData.append('file', file);
    api.post(`/api/prices/${costModelId}/upload?dry_run=true`, formData)
      .then(({ data }) => {
        setPendingUploadFile(file);
        setUploadPreview({ filename: data.filename || file.name, rows_processed: data.rows_processed, errors: data.errors || [] });
      })
      .catch(err => {
        const detail = err.response?.data?.detail;
        setUploadError(typeof detail === 'string' ? detail : 'Upload failed. Check the file format and try again.');
      });
    e.target.value = '';
  };

  const handleConfirmUpload = () => {
    if (!pendingUploadFile) return;
    const formData = new FormData();
    formData.append('file', pendingUploadFile);
    api.post(`/api/prices/${costModelId}/upload`, formData)
      .then(({ data }) => {
        fetchData();
        if (data.errors?.length) setUploadErrors(data.errors);
        setUploadPreview(null);
        setPendingUploadFile(null);
      })
      .catch(err => {
        const detail = err.response?.data?.detail;
        setUploadError(typeof detail === 'string' ? detail : 'Import failed.');
      });
  };

  const handleCancelUpload = () => {
    setUploadPreview(null);
    setPendingUploadFile(null);
  };

  const sym = model?.currency === 'EUR' ? '\u20AC' : '$';
  const unitLabel = model?.product_unit || '';  // product unit for price/quantity labels (no hardcoded 'kg')

  // Merge prices and volumes into unified rows keyed by year-quarter
  const mergedRows = (() => {
    const map = {};
    prices.forEach(p => {
      const key = `${p.year}-${p.quarter}`;
      map[key] = { year: p.year, quarter: p.quarter, price: p.price };
    });
    volumes.forEach(v => {
      const key = `${v.year}-${v.quarter}`;
      if (map[key]) {
        map[key].volume = v.volume;
        map[key].unit = v.unit;
      } else {
        map[key] = { year: v.year, quarter: v.quarter, volume: v.volume, unit: v.unit };
      }
    });
    return Object.values(map).sort((a, b) => a.year - b.year || a.quarter - b.quarter);
  })();

  return (
    <div className="ca-page ca-fade-in">
      <nav style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
        <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => navigate('/dashboard')}>Dashboard</button>
        <span>›</span>
        <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => navigate(`/cost-models/${costModelId}`)}>{model?.product_name ?? '…'}</button>
        <span>›</span>
        <span>Pricing</span>
      </nav>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Pricing</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}`)}>View Model</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>Evolution</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/brief`)}>Brief</button>
        </div>
      </div>
      {model && (
        <p className="ca-subtitle">
          {model.product_name}{model.supplier_name ? ` \u00B7 ${model.supplier_name}` : ''} \u00B7 {model.region} \u00B7 {model.currency}{(() => { const fv = model.formula_versions?.[0]; const ic = fv?.incoterm || model.incoterm; const np = fv?.named_place; return ic ? ` \u00B7 ${ic}${np ? ' ' + np : ''}` : ''; })()}
        </p>
      )}

      {/* Two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, alignItems: 'start' }}>
        {/* LEFT: Pricing History */}
        <div>
          <div className="ca-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div className="ca-card-title" style={{ margin: 0 }}>Pricing & Volume History</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <a
                  href={`/api/volumes/${costModelId}/template`}
                  download="volumes_template.csv"
                  className="ca-btn ca-btn-ghost ca-btn-sm"
                  style={{ fontSize: 11 }}
                >
                  Volume template
                </a>
                <a
                  href="data:text/csv;charset=utf-8,period%2Cprice%2Cincoterm%0AQ1-2023%2C1050%2CCIF"
                  download="prices_template.csv"
                  className="ca-btn ca-btn-ghost ca-btn-sm"
                  style={{ fontSize: 11 }}
                >
                  Price template
                </a>
                <label className="ca-btn ca-btn-ghost ca-btn-sm" style={{ cursor: 'pointer' }}>
                  Upload Prices
                  <input type="file" accept=".csv,.xlsx" onChange={handleUpload} style={{ display: 'none' }} />
                </label>
              </div>
            </div>
            {uploadPreview && (
              <div style={{ marginBottom: 10, padding: '12px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', fontSize: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>
                      {uploadPreview.filename} &middot; {uploadPreview.rows_processed} row{uploadPreview.rows_processed !== 1 ? 's' : ''} ready
                    </div>
                    {uploadPreview.errors.length > 0 && (
                      <div style={{ color: 'var(--accent3)' }}>
                        <span style={{ fontWeight: 600 }}>{uploadPreview.errors.length} row{uploadPreview.errors.length !== 1 ? 's' : ''} will be skipped:</span>
                        <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                          {uploadPreview.errors.slice(0, 5).map((e, i) => <li key={i}>Row {e.row}: {e.message}</li>)}
                          {uploadPreview.errors.length > 5 && <li>…and {uploadPreview.errors.length - 5} more</li>}
                        </ul>
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handleCancelUpload}>Cancel</button>
                    <button
                      className="ca-btn ca-btn-primary ca-btn-sm"
                      onClick={handleConfirmUpload}
                      disabled={uploadPreview.rows_processed === 0}
                    >
                      Import {uploadPreview.rows_processed} row{uploadPreview.rows_processed !== 1 ? 's' : ''}
                    </button>
                  </div>
                </div>
              </div>
            )}
            {uploadError && (
              <div style={{ fontSize: 12, color: 'var(--accent2)', background: 'var(--danger-bg, #fff1f0)', border: '1px solid var(--accent2)', borderRadius: 6, padding: '8px 12px', marginBottom: 10 }}>
                {uploadError}
              </div>
            )}
            {uploadErrors.length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--accent3)', background: 'var(--warn-bg)', border: '1px solid var(--accent3)', borderRadius: 6, padding: '8px 12px', marginBottom: 10 }}>
                <strong>{uploadErrors.length} row{uploadErrors.length > 1 ? 's' : ''} skipped:</strong>
                <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                  {uploadErrors.map((e, i) => <li key={i}>Row {e.row}: {e.message}</li>)}
                </ul>
              </div>
            )}

            {loadingPrices ? (
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading...</div>
            ) : mergedRows.length === 0 ? (
              <div style={{ color: 'var(--muted)', fontSize: 12, padding: '16px 0' }}>
                No data yet. Add prices and quantities below or upload a file.
              </div>
            ) : (
              <div className="ca-scroll-x">
                <table className="ca-table">
                  <thead>
                    <tr>
                      <th>Period</th>
                      <th className="center">Price{unitLabel ? ` (${model.currency}/${unitLabel})` : ''}</th>
                      <th className="center">Quantity{unitLabel ? ` (${unitLabel})` : ''}</th>
                      <th className="center" style={{ width: 100 }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mergedRows.map(row => {
                      const key = `${row.year}-${row.quarter}`;
                      const isEditing = editKey === key;
                      return (
                        <tr key={key}>
                          <td>{qLabel(row.year, row.quarter)}</td>
                          <td className="center">
                            {isEditing ? (
                              <input
                                type="number"
                                step="0.01"
                                className="ca-input"
                                style={{ width: 100, textAlign: 'center', fontSize: 12, padding: '3px 6px' }}
                                value={editPrice}
                                onChange={e => setEditPrice(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && saveEdit(row)}
                                autoFocus
                              />
                            ) : (
                              <span style={{ fontWeight: 500 }}>
                                {row.price != null ? `${sym}${row.price.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 })}` : '\u2014'}
                              </span>
                            )}
                          </td>
                          <td className="center">
                            {isEditing ? (
                              <input
                                type="number"
                                step="1"
                                className="ca-input"
                                style={{ width: 100, textAlign: 'center', fontSize: 12, padding: '3px 6px' }}
                                value={editVolume}
                                onChange={e => setEditVolume(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && saveEdit(row)}
                              />
                            ) : (
                              <span style={{ fontWeight: 500 }}>
                                {row.volume != null ? `${Number(row.volume).toLocaleString()}${(row.unit || unitLabel) ? ` ${row.unit || unitLabel}` : ''}` : '\u2014'}
                              </span>
                            )}
                          </td>
                          <td className="center">
                            {isEditing ? (
                              <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                                <button className="ca-btn ca-btn-sm" onClick={() => saveEdit(row)}>Save</button>
                                <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditKey(null)}>Cancel</button>
                              </div>
                            ) : (
                              <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                                <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => startEdit(row)}>Edit</button>
                                <button
                                  className="ca-btn ca-btn-ghost ca-btn-sm"
                                  style={{ color: 'var(--accent2)' }}
                                  onClick={() => deleteRow(row)}
                                >Del</button>
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Inline add row */}
            <div style={{ marginTop: 12, padding: '12px 0', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                className="ca-select"
                style={{ width: 90, fontSize: 11, padding: '4px 8px' }}
                value={`${addYear}-${addQuarter}`}
                onChange={e => {
                  const [y, q] = e.target.value.split('-').map(Number);
                  setAddYear(y); setAddQuarter(q);
                }}
              >
                {QUARTER_OPTS.map(o => (
                  <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>
                ))}
              </select>
              <input
                type="number"
                step="0.01"
                className="ca-input"
                style={{ width: 100, fontSize: 12, padding: '4px 8px' }}
                placeholder="Price"
                value={addPrice}
                onChange={e => setAddPrice(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPriceRow()}
              />
              <input
                type="number"
                step="1"
                className="ca-input"
                style={{ width: 100, fontSize: 12, padding: '4px 8px' }}
                placeholder="Quantity"
                value={addVolume}
                onChange={e => setAddVolume(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPriceRow()}
              />
              <button className="ca-btn ca-btn-sm" onClick={addPriceRow} disabled={saving || (!addPrice && !addVolume)}>
                Add
              </button>
            </div>
          </div>
        </div>

        {/* RIGHT: Price Change Analyzer */}
        <div>
          <div className="ca-card">
            <div className="ca-card-title" style={{ marginBottom: 12 }}>Price Change Analyzer</div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 }}>
              <select
                className="ca-select"
                style={{ width: 90, fontSize: 11, padding: '4px 8px' }}
                value={`${fromYear}-${fromQuarter}`}
                onChange={e => {
                  const [y, q] = e.target.value.split('-').map(Number);
                  setFromYear(y); setFromQuarter(q);
                }}
              >
                {QUARTER_OPTS.map(o => (
                  <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>
                ))}
              </select>
              <span style={{ fontSize: 10, color: 'var(--muted)' }}>to</span>
              <select
                className="ca-select"
                style={{ width: 90, fontSize: 11, padding: '4px 8px' }}
                value={`${toYear}-${toQuarter}`}
                onChange={e => {
                  const [y, q] = e.target.value.split('-').map(Number);
                  setToYear(y); setToQuarter(q);
                }}
              >
                {QUARTER_OPTS.map(o => (
                  <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>
                ))}
              </select>
              <button className="ca-btn ca-btn-sm" onClick={runAnalysis} disabled={loadingAnalysis}>
                {loadingAnalysis ? 'Analyzing...' : 'Analyze'}
              </button>
            </div>

            {analysis && (
              <>
                {/* Summary metric */}
                <div className="ca-metric" style={{ marginBottom: 16 }}>
                  <div className="ca-metric-lbl">Should-Cost Change</div>
                  <div className="ca-metric-val" style={{ color: analysis.fair_change_pct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
                    {analysis.fair_change_pct > 0 ? '+' : ''}{analysis.fair_change_pct.toFixed(2)}%
                  </div>
                </div>

                {/* Component breakdown table */}
                <div className="ca-scroll-x">
                  <table className="ca-table">
                    <thead>
                      <tr>
                        <th>Component</th>
                        <th className="center">Weight</th>
                        <th className="center">Index ({analysis.from_label})</th>
                        <th className="center">Index ({analysis.to_label})</th>
                        <th className="center">Index Change</th>
                        <th className="center">Contribution</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analysis.components.map(c => (
                        <tr key={c.label}>
                          <td>{c.label}</td>
                          <td className="center">{c.weight.toFixed(1)}%</td>
                          <td className="center" style={{ fontSize: 11 }}>{c.index_start?.toFixed(2) ?? '\u2014'}</td>
                          <td className="center" style={{ fontSize: 11 }}>{c.index_end?.toFixed(2) ?? '\u2014'}</td>
                          <td className="center" style={{ color: c.index_change_pct > 0 ? 'var(--accent2)' : c.index_change_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                            {c.index_change_pct > 0 ? '+' : ''}{c.index_change_pct.toFixed(1)}%
                          </td>
                          <td className="center" style={{ fontWeight: 500, color: c.contribution_pct > 0 ? 'var(--accent2)' : c.contribution_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                            {c.contribution_pct > 0 ? '+' : ''}{c.contribution_pct.toFixed(2)}%
                          </td>
                        </tr>
                      ))}
                      {/* Margin row — always shown, zeros if no margin */}
                      <tr style={{ opacity: 0.6 }}>
                        <td>Margin</td>
                        <td className="center">{(analysis.margin_weight || 0).toFixed(1)}%</td>
                        <td className="center">0.00</td>
                        <td className="center">0.00</td>
                        <td className="center" style={{ color: 'var(--muted)' }}>0.0%</td>
                        <td className="center" style={{ color: 'var(--muted)' }}>0.00%</td>
                      </tr>
                      {/* Total row */}
                      <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 600 }}>
                        <td>Total</td>
                        <td className="center">100%</td>
                        <td></td>
                        <td></td>
                        <td></td>
                        <td className="center" style={{ color: analysis.fair_change_pct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
                          {analysis.fair_change_pct > 0 ? '+' : ''}{analysis.fair_change_pct.toFixed(2)}%
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {!analysis && !loadingAnalysis && (
              <div style={{ color: 'var(--muted)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
                Select a period range and click Analyze to see what a fair price change should be based on index movements.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
