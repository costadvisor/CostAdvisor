import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { qLabel, QUARTER_OPTS, currentQuarter, quarterStartDate, quarterKey } from '../utils/quarters';
import { useConfirm } from '../components/ConfirmDialog';
import FileUpload from '../components/FileUpload';
import PriceChart from '../components/PriceChart';

export default function Pricing() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const priceInputRef = useRef(null);

  // Pricing history
  const [prices, setPrices] = useState([]);
  const [volumes, setVolumes] = useState([]);
  const [loadingPrices, setLoadingPrices] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [rowError, setRowError] = useState(null);

  const cq = currentQuarter();

  // Inline add form
  const [addYear, setAddYear] = useState(cq.year);
  const [addQuarter, setAddQuarter] = useState(cq.quarter);
  const [addPrice, setAddPrice] = useState('');
  const [addVolume, setAddVolume] = useState('');
  const [saving, setSaving] = useState(false);

  // Editing state
  const [editKey, setEditKey] = useState(null); // "year-quarter"
  const [editPrice, setEditPrice] = useState('');
  const [editVolume, setEditVolume] = useState('');

  // Price change analyzer
  const [fromYear, setFromYear] = useState(cq.year - 1);
  const [fromQuarter, setFromQuarter] = useState(cq.quarter);
  const [toYear, setToYear] = useState(cq.year);
  const [toQuarter, setToQuarter] = useState(cq.quarter);
  const [analysis, setAnalysis] = useState(null);
  const [analysisError, setAnalysisError] = useState(null);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);

  // Model metadata
  const [model, setModel] = useState(null);
  const [modelError, setModelError] = useState(null);

  useEffect(() => {
    if (!costModelId) return;
    api.get(`/api/cost-models/${costModelId}`)
      .then(({ data }) => { setModel(data); setModelError(null); })
      .catch(err => setModelError(formatApiError(err)));
    fetchData();
  }, [costModelId]);

  const fetchData = () => {
    setLoadingPrices(true);
    Promise.all([
      api.get(`/api/prices/${costModelId}`),
      api.get(`/api/volumes/${costModelId}`),
    ])
      .then(([pRes, vRes]) => { setPrices(pRes.data); setVolumes(vRes.data); setLoadError(null); })
      // A failed load must never fall through to the "no data yet" empty state —
      // telling a buyer their price history is empty when the server merely failed
      // is the one wrong answer this page can give.
      .catch(err => setLoadError(formatApiError(err)))
      .finally(() => setLoadingPrices(false));
  };

  // Merge prices and volumes into unified rows keyed by year-quarter
  const mergedRows = useMemo(() => {
    const map = {};
    prices.forEach(p => {
      map[`${p.year}-${p.quarter}`] = { year: p.year, quarter: p.quarter, price: p.price };
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
  }, [prices, volumes]);

  // Seed the period pickers from the data itself, once. Fixed defaults went stale
  // the moment the calendar moved past them, forcing a re-pick on every visit.
  const defaultsSeeded = useRef(false);
  useEffect(() => {
    if (defaultsSeeded.current || loadingPrices || mergedRows.length === 0) return;
    defaultsSeeded.current = true;
    const first = mergedRows[0];
    const last = mergedRows[mergedRows.length - 1];
    // Add row points at the quarter after the last one on record — the next entry.
    const nextQ = last.quarter === 4 ? 1 : last.quarter + 1;
    const nextY = last.quarter === 4 ? last.year + 1 : last.year;
    setAddYear(nextY); setAddQuarter(nextQ);
    if (quarterKey(first.year, first.quarter) < quarterKey(last.year, last.quarter)) {
      setFromYear(first.year); setFromQuarter(first.quarter);
      setToYear(last.year); setToQuarter(last.quarter);
    }
  }, [mergedRows, loadingPrices]);

  const addPriceRow = () => {
    if (!addPrice && !addVolume) return;
    setSaving(true);
    setRowError(null);
    const promises = [];
    if (addPrice) {
      promises.push(api.put(`/api/prices/${costModelId}/${addYear}/${addQuarter}`, {
        year: addYear, quarter: addQuarter, price: parseFloat(addPrice),
      }));
    }
    if (addVolume) {
      promises.push(api.put(`/api/volumes/${costModelId}/${addYear}/${addQuarter}`, {
        year: addYear, quarter: addQuarter, volume: parseFloat(addVolume),
      }));
    }
    Promise.all(promises)
      .then(() => { setAddPrice(''); setAddVolume(''); fetchData(); })
      .catch(err => setRowError(formatApiError(err)))
      .finally(() => setSaving(false));
  };

  const startEdit = (row) => {
    setEditKey(`${row.year}-${row.quarter}`);
    setEditPrice(row.price != null ? String(row.price) : '');
    setEditVolume(row.volume != null ? String(row.volume) : '');
  };

  const saveEdit = (row) => {
    setRowError(null);
    const promises = [];
    if (editPrice !== '') {
      promises.push(api.put(`/api/prices/${costModelId}/${row.year}/${row.quarter}`, {
        year: row.year, quarter: row.quarter, price: parseFloat(editPrice),
      }));
    }
    if (editVolume !== '') {
      promises.push(api.put(`/api/volumes/${costModelId}/${row.year}/${row.quarter}`, {
        year: row.year, quarter: row.quarter, volume: parseFloat(editVolume),
      }));
    }
    Promise.all(promises)
      .then(() => { setEditKey(null); fetchData(); })
      .catch(err => setRowError(formatApiError(err)));
  };

  const deleteRow = async (row) => {
    const ok = await confirm({
      title: `Delete ${qLabel(row.year, row.quarter)}?`,
      message: 'Removes the recorded price and quantity for this quarter. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    setRowError(null);
    const promises = [];
    if (row.price != null) promises.push(api.delete(`/api/prices/${costModelId}/${row.year}/${row.quarter}`));
    if (row.volume != null) promises.push(api.delete(`/api/volumes/${costModelId}/${row.year}/${row.quarter}`));
    Promise.all(promises)
      .then(() => fetchData())
      .catch(err => setRowError(formatApiError(err)));
  };

  const rangeInverted = quarterKey(fromYear, fromQuarter) >= quarterKey(toYear, toQuarter);

  const runAnalysis = () => {
    if (rangeInverted) {
      setAnalysisError('The "from" period must come before the "to" period.');
      return;
    }
    setLoadingAnalysis(true);
    setAnalysisError(null);
    api.post('/api/costing/price-change', {
      cost_model_id: costModelId,
      from_year: fromYear,
      from_quarter: fromQuarter,
      to_year: toYear,
      to_quarter: toQuarter,
    })
      .then(({ data }) => setAnalysis(data))
      .catch(err => { setAnalysis(null); setAnalysisError(formatApiError(err)); })
      .finally(() => setLoadingAnalysis(false));
  };

  const sym = model?.currency === 'EUR' ? '€' : '$';
  const unitLabel = model?.product_unit || '';  // product unit for price/quantity labels (no hardcoded 'kg')
  const priceUnit = unitLabel ? `${model?.currency || ''}/${unitLabel}`.replace(/^\//, '') : model?.currency || '';

  // Decimals chosen from the column's own magnitude — a fixed 4dp rendered a
  // per-tonne price as "$1,050.0000".
  const priceDecimals = useMemo(() => {
    const vals = mergedRows.map(r => r.price).filter(v => v != null).map(Math.abs);
    if (!vals.length) return 2;
    return Math.max(...vals) < 2 ? 4 : 2;
  }, [mergedRows]);
  const fmtPrice = v => `${sym}${v.toLocaleString(undefined, { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals })}`;

  const pricedRows = mergedRows.filter(r => r.price != null);
  const chartSeries = pricedRows.map(r => ({ date: quarterStartDate(r.year, r.quarter), rate: Number(r.price) }));
  const latest = pricedRows[pricedRows.length - 1];
  const latestVol = [...mergedRows].reverse().find(r => r.volume != null);
  const bothCount = mergedRows.filter(r => r.price != null && r.volume != null).length;

  // Identity line built from parts: interpolating it left a dangling separator
  // whenever a segment was blank, and bare · escapes in JSX text render literally.
  const fv = model?.formula_versions?.[0];
  const incoterm = fv?.incoterm || model?.incoterm;
  const identity = model
    ? [
        model.product_name,
        model.supplier_name,
        model.region,
        model.currency,
        incoterm ? `${incoterm}${fv?.named_place ? ` ${fv.named_place}` : ''}` : null,
      ].filter(Boolean).join(' · ')
    : '';

  const stats = mergedRows.length ? [
    { lbl: 'Periods on record', val: mergedRows.length, sub: `${pricedRows.length} priced · ${mergedRows.length - pricedRows.length} quantity only` },
    { lbl: 'Latest price', val: latest ? fmtPrice(latest.price) : '—', sub: latest ? `${qLabel(latest.year, latest.quarter)}${priceUnit ? ` · per ${unitLabel}` : ''}` : 'no price recorded yet' },
    { lbl: 'Latest quantity', val: latestVol ? Number(latestVol.volume).toLocaleString() : '—', sub: latestVol ? `${qLabel(latestVol.year, latestVol.quarter)}${latestVol.unit || unitLabel ? ` · ${latestVol.unit || unitLabel}` : ''}` : 'needed for total impact' },
    { lbl: 'Complete periods', val: `${bothCount} / ${mergedRows.length}`, sub: bothCount === mergedRows.length ? 'price and quantity on every period' : 'both price and quantity recorded' },
  ] : [];

  return (
    <div className="ca-page ca-fade-in">
      <nav aria-label="Breadcrumb" style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
        <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => navigate('/dashboard')}>Dashboard</button>
        <span aria-hidden>›</span>
        {/* `??` let an empty product name through and rendered a zero-width crumb. */}
        <button className="ca-btn-link" style={{ fontSize: 11 }} onClick={() => navigate(`/cost-models/${costModelId}`)}>
          {model?.product_name || (model ? 'Untitled product' : '…')}
        </button>
        <span aria-hidden>›</span>
        <span aria-current="page">Pricing</span>
      </nav>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 className="ca-h1">Pricing</h1>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            {identity || (modelError ? `Couldn't load this cost model — ${modelError}` : 'Loading…')}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}`)}>View model</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/squeeze`)}>Squeeze</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/brief`)}>Brief</button>
          {/* Primary is Evolution: entering an actual price only pays off when you
              see it against the should-cost, which is the next step from here. */}
          <button className="ca-btn ca-btn-primary" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>Evolution</button>
        </div>
      </div>

      <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
        {stats.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
            {stats.map(s => (
              <div key={s.lbl} className="ca-metric">
                <div className="ca-metric-val" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{s.val}</div>
                <div className="ca-metric-lbl">{s.lbl}</div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{s.sub}</div>
              </div>
            ))}
          </div>
        )}

        {/* Pricing & volume history */}
        <div className="ca-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
            <h2 className="ca-card-title" style={{ margin: 0 }}>Pricing &amp; volume history</h2>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
              <a href={`/api/prices/${costModelId}/template`} download="prices_template.csv" className="ca-btn ca-btn-ghost ca-btn-sm">
                Price template
              </a>
              <a href={`/api/volumes/${costModelId}/template`} download="volumes_template.csv" className="ca-btn ca-btn-ghost ca-btn-sm">
                Volume template
              </a>
              <FileUpload
                endpoint={`/api/prices/${costModelId}/upload`}
                onSuccess={fetchData}
                accept=".csv,.xlsx"
                label="Upload prices"
              />
            </div>
          </div>

          {rowError && (
            <div style={{ fontSize: 12, color: 'var(--accent2)', background: 'var(--danger-bg)', border: '1px solid var(--accent2)', borderRadius: 6, padding: '8px 12px', marginBottom: 10 }}>
              {rowError}
            </div>
          )}

          {loadingPrices ? (
            <div style={{ color: 'var(--muted)', fontSize: 12, padding: '16px 0' }}>Loading…</div>
          ) : loadError ? (
            <div style={{ textAlign: 'center', padding: 32 }}>
              <div style={{ color: 'var(--accent2)', fontSize: 12, marginBottom: 14 }}>
                Couldn't load the price history — {loadError}
              </div>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={fetchData}>Try again</button>
            </div>
          ) : mergedRows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 32 }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 14 }}>
                No prices recorded yet — add what your supplier actually charged, and the gap against your should-cost appears in Evolution.
              </div>
              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => priceInputRef.current?.focus()}>
                Add the first price
              </button>
            </div>
          ) : (
            <div className="ca-grid-scroll">
              <table className="ca-table">
                <caption className="ca-sr-only">
                  Recorded supplier price and purchased quantity for each quarter.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Period</th>
                    <th scope="col" className="right">Price{priceUnit ? ` (${priceUnit})` : ''}</th>
                    <th scope="col" className="right">Quantity{unitLabel ? ` (${unitLabel})` : ''}</th>
                    <th scope="col" className="right" style={{ width: 140 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {mergedRows.map(row => {
                    const key = `${row.year}-${row.quarter}`;
                    const isEditing = editKey === key;
                    const onEditKeyDown = e => {
                      if (e.key === 'Enter') saveEdit(row);
                      if (e.key === 'Escape') setEditKey(null);
                    };
                    return (
                      <tr key={key}>
                        <td>{qLabel(row.year, row.quarter)}</td>
                        <td className="right">
                          {isEditing ? (
                            <input
                              type="number" step="0.01" className="ca-input"
                              style={{ width: 110, textAlign: 'right', fontSize: 12, padding: '3px 6px' }}
                              aria-label={`Price for ${qLabel(row.year, row.quarter)}`}
                              value={editPrice}
                              onChange={e => setEditPrice(e.target.value)}
                              onKeyDown={onEditKeyDown}
                              autoFocus
                            />
                          ) : (
                            <span style={{ fontWeight: 500 }}>{row.price != null ? fmtPrice(row.price) : '—'}</span>
                          )}
                        </td>
                        <td className="right">
                          {isEditing ? (
                            <input
                              type="number" step="1" className="ca-input"
                              style={{ width: 110, textAlign: 'right', fontSize: 12, padding: '3px 6px' }}
                              aria-label={`Quantity for ${qLabel(row.year, row.quarter)}`}
                              value={editVolume}
                              onChange={e => setEditVolume(e.target.value)}
                              onKeyDown={onEditKeyDown}
                            />
                          ) : (
                            <span style={{ fontWeight: 500 }}>
                              {row.volume != null ? `${Number(row.volume).toLocaleString()}${(row.unit || unitLabel) ? ` ${row.unit || unitLabel}` : ''}` : '—'}
                            </span>
                          )}
                        </td>
                        <td className="right">
                          {isEditing ? (
                            <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => saveEdit(row)}>Save</button>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditKey(null)}>Cancel</button>
                            </div>
                          ) : (
                            <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => startEdit(row)}>
                                Edit<span className="ca-sr-only"> {qLabel(row.year, row.quarter)}</span>
                              </button>
                              <button className="ca-btn ca-btn-danger" onClick={() => deleteRow(row)}>
                                Delete<span className="ca-sr-only"> {qLabel(row.year, row.quarter)}</span>
                              </button>
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
          <div style={{ marginTop: 12, paddingTop: 14, borderTop: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label className="ca-label" htmlFor="add-period">Period</label>
              <select
                id="add-period" className="ca-select" style={{ width: 100, fontSize: 12 }}
                value={`${addYear}-${addQuarter}`}
                onChange={e => { const [y, q] = e.target.value.split('-').map(Number); setAddYear(y); setAddQuarter(q); }}
              >
                {QUARTER_OPTS.map(o => <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="ca-label" htmlFor="add-price">Price{priceUnit ? ` (${priceUnit})` : ''}</label>
              <input
                id="add-price" ref={priceInputRef} type="number" step="0.01" className="ca-input"
                style={{ width: 140, fontSize: 12 }}
                value={addPrice}
                onChange={e => setAddPrice(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPriceRow()}
              />
            </div>
            <div>
              <label className="ca-label" htmlFor="add-qty">Quantity{unitLabel ? ` (${unitLabel})` : ''}</label>
              <input
                id="add-qty" type="number" step="1" className="ca-input"
                style={{ width: 140, fontSize: 12 }}
                value={addVolume}
                onChange={e => setAddVolume(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addPriceRow()}
              />
            </div>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={addPriceRow} disabled={saving || (!addPrice && !addVolume)}>
              {saving ? 'Adding…' : 'Add'}
            </button>
          </div>
        </div>

        {/* Recorded price trend */}
        {chartSeries.length >= 2 && (
          <div className="ca-card">
            <h2 className="ca-card-title">Recorded price trend</h2>
            <PriceChart series={chartSeries} />
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6 }}>
              What the supplier actually charged, by quarter. Red means the price rose — a rising input cost is
              the buyer-side negative. Compare it against the should-cost in Evolution.
            </div>
          </div>
        )}

        {/* Price change analyzer */}
        <div className="ca-card">
          <h2 className="ca-card-title">Price change analyzer</h2>

          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 14, flexWrap: 'wrap' }}>
            <div>
              <label className="ca-label" htmlFor="pc-from">From</label>
              <select
                id="pc-from" className="ca-select" style={{ width: 100, fontSize: 12 }}
                value={`${fromYear}-${fromQuarter}`}
                onChange={e => { const [y, q] = e.target.value.split('-').map(Number); setFromYear(y); setFromQuarter(q); setAnalysisError(null); }}
              >
                {QUARTER_OPTS.map(o => <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="ca-label" htmlFor="pc-to">To</label>
              <select
                id="pc-to" className="ca-select" style={{ width: 100, fontSize: 12 }}
                value={`${toYear}-${toQuarter}`}
                onChange={e => { const [y, q] = e.target.value.split('-').map(Number); setToYear(y); setToQuarter(q); setAnalysisError(null); }}
              >
                {QUARTER_OPTS.map(o => <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>)}
              </select>
            </div>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={runAnalysis} disabled={loadingAnalysis || rangeInverted}>
              {loadingAnalysis ? 'Analyzing…' : 'Analyze'}
            </button>
          </div>

          {(analysisError || rangeInverted) && (
            <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 12 }}>
              {analysisError || 'The "from" period must come before the "to" period.'}
            </div>
          )}

          {analysis && (
            <>
              <div className="ca-result" style={{ marginBottom: 16 }}>
                <div className="ca-result-label">Should-cost change · {analysis.from_label} → {analysis.to_label}</div>
                <div className="ca-result-big" style={{ color: analysis.fair_change_pct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
                  {analysis.fair_change_pct > 0 ? '+' : ''}{analysis.fair_change_pct.toFixed(2)}%
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6 }}>
                  What the price should have moved by, given how the linked indices moved over this span.
                </div>
              </div>

              <div className="ca-grid-scroll">
                <table className="ca-table">
                  <caption className="ca-sr-only">
                    Index movement and cost contribution per formula component between {analysis.from_label} and {analysis.to_label}.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Component</th>
                      <th scope="col" className="right">Weight</th>
                      <th scope="col" className="right">Index ({analysis.from_label})</th>
                      <th scope="col" className="right">Index ({analysis.to_label})</th>
                      <th scope="col" className="right">Index change</th>
                      <th scope="col" className="right">Contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {analysis.components.map(c => (
                      <tr key={c.label}>
                        <td>{c.label}</td>
                        <td className="right">{c.weight.toFixed(1)}%</td>
                        <td className="right" style={{ fontSize: 11 }}>{c.index_start?.toFixed(2) ?? '—'}</td>
                        <td className="right" style={{ fontSize: 11 }}>{c.index_end?.toFixed(2) ?? '—'}</td>
                        <td className="right" style={{ color: c.index_change_pct > 0 ? 'var(--accent2)' : c.index_change_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                          {c.index_change_pct > 0 ? '+' : ''}{c.index_change_pct.toFixed(1)}%
                        </td>
                        <td className="right" style={{ fontWeight: 500, color: c.contribution_pct > 0 ? 'var(--accent2)' : c.contribution_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                          {c.contribution_pct > 0 ? '+' : ''}{c.contribution_pct.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                    {/* Margin row — always shown, zeros if no margin */}
                    <tr style={{ opacity: 0.6 }}>
                      <td>Margin</td>
                      <td className="right">{(analysis.margin_weight || 0).toFixed(1)}%</td>
                      <td className="right">0.00</td>
                      <td className="right">0.00</td>
                      <td className="right" style={{ color: 'var(--muted)' }}>0.0%</td>
                      <td className="right" style={{ color: 'var(--muted)' }}>0.00%</td>
                    </tr>
                    {/* Total row */}
                    <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 600 }}>
                      <td>Total</td>
                      <td className="right">100%</td>
                      <td></td>
                      <td></td>
                      <td></td>
                      <td className="right" style={{ color: analysis.fair_change_pct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
                        {analysis.fair_change_pct > 0 ? '+' : ''}{analysis.fair_change_pct.toFixed(2)}%
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </>
          )}

          {!analysis && !loadingAnalysis && !analysisError && (
            <div style={{ color: 'var(--text-secondary)', fontSize: 12, padding: '20px 0', textAlign: 'center' }}>
              Pick a period range and run the analysis to see what a fair price change would have been, based on how the linked indices moved.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
