import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import EvoChart from '../components/EvoChart';
import FileUpload from '../components/FileUpload';
import api, { formatApiError } from '../api';
import exportCsv from '../utils/exportCsv';

export default function Squeeze() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [granularity, setGranularity] = useState('quarterly');
  const [includeMargin, setIncludeMargin] = useState(true);
  const [volumeProjection, setVolumeProjection] = useState('flat');

  const fetchSqueeze = () => {
    if (!costModelId) return;
    setLoading(true);
    api.post('/api/costing/squeeze', {
      cost_model_id: costModelId,
      granularity,
      include_margin: includeMargin,
      volume_projection: volumeProjection,
    })
      .then(({ data }) => { setData(data); setError(null); })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  };

  useEffect(fetchSqueeze, [costModelId, granularity, includeMargin, volumeProjection]);

  if (loading) return <div className="ca-page" style={{ color: 'var(--muted)' }}>Loading...</div>;
  if (error) return <div className="ca-page" style={{ color: 'var(--accent2)' }}>Error: {error}</div>;
  if (!data) return null;

  const { periods, product_name, supplier_name, reference_cost, region, currency, unit, cumulative_impact, total_volume } = data;
  const theoretical = periods.map(p => p.theoretical);
  const actual = periods.map(p => p.actual ?? 0);
  const periodLabels = periods.map(p => p.period);
  const sym = currency === 'EUR' ? '\u20AC' : '$';

  const impactBars = periods.filter(p => p.impact !== null);
  const maxImpact = Math.max(...impactBars.map(p => Math.abs(p.impact || 0)), 1);

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Squeeze / Desqueeze</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}`)}>Edit Model</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/pricing`)}>Pricing</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>Evolution</button>
          <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/brief`)}>Brief</button>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => exportCsv(
            `squeeze_${product_name}.csv`,
            ['Period', 'Theoretical', 'Actual', 'Gap', 'Gap %', 'Volume', 'Projected', 'Impact', 'Cumulative'],
            periods.map(p => [p.period, p.theoretical, p.actual, p.gap, p.gap_pct, p.volume, p.volume_projected, p.impact, p.cumulative_impact])
          )}>Export CSV</button>
        </div>
      </div>
      {/* Built from parts: a bare \u00B7 in JSX text renders literally, and an empty
          segment would otherwise leave a dangling separator. */}
      <p className="ca-subtitle">{[product_name, supplier_name, region].filter(Boolean).join(' \u00B7 ')}</p>

      {/* Controls */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title">Controls</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          <div>
            <label className="ca-label">Granularity</label>
            <select className="ca-select" value={granularity} onChange={e => setGranularity(e.target.value)}>
              <option value="quarterly">Quarterly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="ca-label">Margin</label>
            <select className="ca-select" value={includeMargin ? 'yes' : 'no'}
              onChange={e => setIncludeMargin(e.target.value === 'yes')}>
              <option value="yes">With Margin</option>
              <option value="no">Without Margin</option>
            </select>
          </div>
          <div>
            <label className="ca-label">Volume Projection</label>
            <select className="ca-select" value={volumeProjection} onChange={e => setVolumeProjection(e.target.value)}>
              <option value="flat">Flat (Last Known)</option>
              <option value="seasonal">Seasonal (YoY)</option>
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button className="ca-btn ca-btn-primary" onClick={fetchSqueeze}>Apply</button>
          </div>
        </div>
      </div>

      {/* Volume upload */}
      <div style={{ marginBottom: 16 }}>
        <FileUpload endpoint={`/api/volumes/${costModelId}/upload`} onSuccess={fetchSqueeze} />
      </div>

      {/* KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 16 }}>
        <div className="ca-metric">
          <div className="ca-metric-lbl">Cumulative Impact</div>
          <div className="ca-metric-val" style={{ color: cumulative_impact > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
            {cumulative_impact > 0 ? '+' : ''}{sym}{cumulative_impact.toFixed(0)}
          </div>
        </div>
        <div className="ca-metric">
          <div className="ca-metric-lbl">Total Volume</div>
          <div className="ca-metric-val">{total_volume.toLocaleString()} {unit}</div>
        </div>
        <div className="ca-metric">
          <div className="ca-metric-lbl">Ref Cost</div>
          <div className="ca-metric-val" style={{ color: 'var(--accent)' }}>{sym}{reference_cost?.toFixed(3)}</div>
        </div>
      </div>

      {/* Evolution chart */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title">Price Evolution</div>
        <div className="ca-scroll-x">
          <EvoChart periods={periodLabels} theoretical={theoretical} actual={actual} refCost={reference_cost} />
        </div>
      </div>

      {/* Impact bar chart */}
      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title">Financial Impact by Period</div>
        <div className="ca-scroll-x" style={{ padding: '12px 0' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 120 }}>
            {impactBars.map((p, i) => {
              const h = Math.abs(p.impact) / maxImpact * 100;
              const color = p.impact > 0 ? 'var(--accent2)' : 'var(--accent)';
              return (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, minWidth: 24 }}>
                  <div style={{ fontSize: 8, color: 'var(--muted)', marginBottom: 2 }}>
                    {sym}{Math.abs(p.impact).toFixed(0)}
                  </div>
                  <div style={{
                    width: '70%', height: `${h}%`, minHeight: 2,
                    background: color, borderRadius: '2px 2px 0 0', opacity: 0.8,
                  }} />
                  <div style={{ fontSize: 7, color: 'var(--muted)', marginTop: 2, whiteSpace: 'nowrap' }}>
                    {p.period}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
          Green = supplier undercharging (squeeze), Red = supplier overcharging (desqueeze)
        </div>
      </div>

      {/* Detail table */}
      <div className="ca-card">
        <div className="ca-card-title">Detail Table</div>
        <div className="ca-scroll-x">
          <table className="ca-table">
            <thead>
              <tr>
                <th>Metric</th>
                {periods.map(p => <th key={p.period} className="center">{p.period}</th>)}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Theoretical</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ color: 'var(--accent)' }}>{sym}{p.theoretical.toFixed(3)}</td>)}
              </tr>
              <tr style={{ background: 'var(--success-bg-soft)' }}>
                <td>Actual</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ color: 'var(--accent4)' }}>{p.actual !== null ? `${sym}${p.actual.toFixed(3)}` : '\u2014'}</td>)}
              </tr>
              <tr>
                <td>Gap ({sym})</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ color: p.gap > 0 ? 'var(--accent2)' : p.gap < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                  {p.gap !== null ? `${p.gap > 0 ? '+' : ''}${sym}${p.gap.toFixed(3)}` : '\u2014'}
                </td>)}
              </tr>
              <tr>
                <td>Volume</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ color: p.volume_projected ? 'var(--muted)' : 'var(--text)' }}>
                  {p.volume !== null ? `${p.volume.toLocaleString()}${p.volume_projected ? '*' : ''}` : '\u2014'}
                </td>)}
              </tr>
              <tr>
                <td>Impact ({sym})</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ color: p.impact > 0 ? 'var(--accent2)' : p.impact < 0 ? 'var(--accent)' : 'var(--muted)', fontWeight: 600 }}>
                  {p.impact !== null ? `${p.impact > 0 ? '+' : ''}${sym}${p.impact.toFixed(0)}` : '\u2014'}
                </td>)}
              </tr>
              <tr style={{ background: 'var(--neutral-bg-soft)' }}>
                <td>Cumulative</td>
                {periods.map((p, i) => <td key={i} className="center" style={{ fontWeight: 600 }}>
                  {sym}{p.cumulative_impact.toFixed(0)}
                </td>)}
              </tr>
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>* projected volume</div>
      </div>
    </div>
  );
}
