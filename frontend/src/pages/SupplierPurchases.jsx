import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { qLabel, QUARTER_OPTS } from '../utils/quarters';
import QuarterPriceList from '../components/QuarterPriceList';

export default function SupplierPurchases() {
  const { supplierId } = useParams();
  const navigate = useNavigate();
  const [supplierName, setSupplierName] = useState('');
  const [models, setModels] = useState(null); // per-model purchase data
  const [loadError, setLoadError] = useState(null);
  const [expandedQuarter, setExpandedQuarter] = useState(null);
  const [addingQuarter, setAddingQuarter] = useState(false);
  const [newQuarterVal, setNewQuarterVal] = useState(
    `${new Date().getFullYear()}-${Math.ceil((new Date().getMonth() + 1) / 3)}`
  );

  const fetchData = () => {
    setLoadError(null);
    api.get(`/api/suppliers/${supplierId}/purchase-history`)
      .then(res => {
        setSupplierName(res.data.supplier_name);
        setModels(res.data.models);
      })
      // A bad or unknown id used to leave the page on "Loading..." for ever.
      .catch(err => setLoadError([404, 422].includes(err?.response?.status)
        ? 'This supplier was not found. It may have been deleted, or the link is wrong.'
        : `Couldn't load this supplier's purchases: ${formatApiError(err)}`));
  };

  useEffect(fetchData, [supplierId]);

  // Pivot from per-model data to per-quarter data
  const getQuarters = () => {
    if (!models) return [];
    const quarterMap = {};

    for (const modelData of models) {
      for (const row of modelData.rows) {
        const qKey = `${row.year}-${row.quarter}`;
        if (!quarterMap[qKey]) {
          quarterMap[qKey] = { year: row.year, quarter: row.quarter, products: {} };
        }
        quarterMap[qKey].products[modelData.cost_model_id] = {
          cost_model_id: modelData.cost_model_id,
          product_name: modelData.product_name,
          product_reference: modelData.product_reference,
          region: modelData.region,
          currency: modelData.currency,
          price: row.price,
          volume: row.volume,
        };
      }
    }

    // All models — used to fill gaps (show every product in every quarter)
    const allModels = models.map(m => ({
      cost_model_id: m.cost_model_id,
      product_name: m.product_name,
      product_reference: m.product_reference,
      region: m.region,
      currency: m.currency,
    }));

    return Object.values(quarterMap)
      .sort((a, b) => b.year - a.year || b.quarter - a.quarter)
      .map(q => ({
        year: q.year,
        quarter: q.quarter,
        label: qLabel(q.year, q.quarter),
        products: allModels.map(m =>
          q.products[m.cost_model_id] || { ...m, price: null, volume: null }
        ),
      }));
  };

  const handleAddQuarter = () => {
    const [y, q] = newQuarterVal.split('-').map(Number);
    const qKey = `${y}-${q}`;

    // Seed empty rows for all models so the quarter appears
    setModels(prev => {
      if (!prev || prev.length === 0) return prev;
      return prev.map(modelData => {
        const hasQuarter = modelData.rows.some(r => r.year === y && r.quarter === q);
        if (hasQuarter) return modelData;
        return { ...modelData, rows: [...modelData.rows, { year: y, quarter: q, price: null, volume: null }] };
      });
    });

    setExpandedQuarter(qKey);
    setAddingQuarter(false);
  };

  if (loadError && !models) return (
    <div className="ca-page ca-fade-in">
      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate('/suppliers')}>← Suppliers</button>
      <div className="ca-card" style={{ color: 'var(--accent2)', marginTop: 12 }}>{loadError}</div>
    </div>
  );

  const quarters = getQuarters();

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div>
          <div className="ca-h1">Purchase History</div>
          <p className="ca-subtitle" style={{ margin: 0 }}>{supplierName || 'Loading...'}</p>
        </div>
        <button className="ca-btn ca-btn-ghost" onClick={() => navigate('/suppliers')}>
          Back to Suppliers
        </button>
      </div>

      <div className="ca-card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <div className="ca-card-title" style={{ margin: 0, flex: 1 }}>Quarters</div>
          {addingQuarter ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <select className="ca-select" style={{ fontSize: 12, padding: '4px 6px' }}
                value={newQuarterVal}
                onChange={e => setNewQuarterVal(e.target.value)}>
                {QUARTER_OPTS.map(o => (
                  <option key={o.label} value={`${o.year}-${o.quarter}`}>{o.label}</option>
                ))}
              </select>
              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleAddQuarter}>Add</button>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setAddingQuarter(false)}>Cancel</button>
            </div>
          ) : (
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setAddingQuarter(true)}>
              + Add Quarter
            </button>
          )}
        </div>

        {!models && (
          <div style={{ padding: 20, color: 'var(--muted)' }}>Loading...</div>
        )}

        {models && quarters.length === 0 && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-secondary)' }}>
            No purchase data yet. Add a quarter to get started.
          </div>
        )}

        {quarters.length > 0 && (
          <table className="ca-table">
            <thead>
              <tr>
                <th style={{ width: 30 }}></th>
                <th>Quarter</th>
                <th className="center">Products with data</th>
                <th className="center">Total Products</th>
              </tr>
            </thead>
            <tbody>
              {quarters.map(q => {
                const qKey = `${q.year}-${q.quarter}`;
                const isQExpanded = expandedQuarter === qKey;
                const filledCount = q.products.filter(p => p.price != null || p.volume != null).length;
                return (
                  <React.Fragment key={qKey}>
                    <tr style={{ cursor: 'pointer' }}
                      onClick={() => setExpandedQuarter(isQExpanded ? null : qKey)}>
                      <td style={{ fontSize: 11, color: 'var(--muted)', textAlign: 'center' }}>
                        {isQExpanded ? '\u25BC' : '\u25B6'}
                      </td>
                      <td style={{ fontWeight: 600, fontSize: 14 }}>{q.label}</td>
                      <td className="center">
                        <span style={{
                          padding: '1px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                          background: filledCount === q.products.length ? 'var(--success-bg)' : 'var(--warn-bg)',
                          color: filledCount === q.products.length ? 'var(--accent)' : 'var(--accent2)',
                        }}>
                          {filledCount}
                        </span>
                      </td>
                      <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>
                        {q.products.length}
                      </td>
                    </tr>
                    {isQExpanded && (
                      <tr>
                        <td colSpan={4} style={{ padding: '0 8px 8px 30px', borderLeft: '3px solid var(--accent)' }}>
                          <QuarterPriceList
                            year={q.year}
                            quarter={q.quarter}
                            products={q.products}
                            onDataChanged={fetchData}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
