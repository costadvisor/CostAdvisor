import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useConfirm } from '../components/ConfirmDialog';
import { DriftBar } from './workspace/wsCharts';
import exportCsv from '../utils/exportCsv';

export default function Suppliers() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const [suppliers, setSuppliers] = useState([]);
  const [portfolio, setPortfolio] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [country, setCountry] = useState('');
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [view, setView] = useState('directory');   // 'directory' | 'benchmark'
  const [benchmark, setBenchmark] = useState(null);
  const [benchLoading, setBenchLoading] = useState(false);
  const [benchErr, setBenchErr] = useState(null);
  // Scrum 32 — trust grading, keyed by supplier_id for the benchmark table's grade column.
  const [trustScores, setTrustScores] = useState(null);
  const [trustResolution, setTrustResolution] = useState(null);
  const [computingTrust, setComputingTrust] = useState(false);

  const fetchData = () => {
    if (!activeTeamId) return;
    setLoading(true);
    Promise.all([
      api.get('/api/suppliers', { params: { team_id: activeTeamId } }),
      api.get('/api/portfolio/summary', { params: { team_id: activeTeamId } }),
    ])
      .then(([sRes, pRes]) => {
        setSuppliers(sRes.data);
        setPortfolio(pRes.data.models || []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(fetchData, [activeTeamId]);

  // Benchmarking is owner/admin-only (server-gated); fetch lazily on first open.
  const fetchBenchmark = () => {
    if (!activeTeamId) return;
    setBenchLoading(true);
    setBenchErr(null);
    api.get('/api/suppliers/benchmark', { params: { team_id: activeTeamId } })
      .then(res => setBenchmark(res.data.suppliers || []))
      .catch(err => setBenchErr(formatApiError(err)))
      .finally(() => setBenchLoading(false));
  };

  useEffect(() => {
    if (view === 'benchmark' && benchmark === null && !benchLoading) fetchBenchmark();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeTeamId]);

  // Trust grades — read-only fetch of whatever's already persisted; a
  // supplier with nothing computed yet just shows no grade until the
  // "Compute trust scores" button runs (owner/admin only, server-gated).
  const fetchTrustScores = () => {
    if (!activeTeamId) return;
    api.get('/api/suppliers/trust-scores', { params: { team_id: activeTeamId } })
      .then(res => {
        setTrustResolution(res.data.resolution);
        setTrustScores(Object.fromEntries((res.data.suppliers || []).map(s => [s.supplier_id, s])));
      })
      .catch(() => {});
  };

  useEffect(() => {
    if (view === 'benchmark' && trustScores === null) fetchTrustScores();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeTeamId]);

  const handleComputeTrustScores = () => {
    setComputingTrust(true);
    api.post('/api/suppliers/trust-scores/compute-all', null, { params: { team_id: activeTeamId } })
      .then(() => fetchTrustScores())
      .catch(console.error)
      .finally(() => setComputingTrust(false));
  };

  const handleCreate = () => {
    if (!name.trim()) return;
    setSaving(true);
    api.post(`/api/suppliers?team_id=${activeTeamId}`, { name: name.trim(), country: country.trim() || null })
      .then(() => { setName(''); setCountry(''); setShowForm(false); fetchData(); })
      .catch(console.error)
      .finally(() => setSaving(false));
  };

  const handleDelete = async (id) => {
    const ok = await confirm({ title: 'Delete this supplier?', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    api.delete(`/api/suppliers/${id}`).then(fetchData).catch(console.error);
  };

  const handleExportExcel = (id, name) => {
    api.get(`/api/suppliers/${id}/export-excel`, { responseType: 'blob' })
      .then(res => {
        const url = URL.createObjectURL(res.data);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${name.replace(/ /g, '_')}_Cost_Models.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch(console.error);
  };

  const getSupplierModels = (supplierId) =>
    portfolio.filter(m => m.supplier_name === suppliers.find(s => s.id === supplierId)?.name);

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Suppliers</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={`ca-btn ${view === 'directory' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('directory')}>Directory</button>
          <button className={`ca-btn ${view === 'benchmark' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('benchmark')}>Benchmarking</button>
          {view === 'directory' && (
            <button className="ca-btn ca-btn-primary" onClick={() => setShowForm(!showForm)}>
              {showForm ? 'Cancel' : '+ Add Supplier'}
            </button>
          )}
        </div>
      </div>
      <p className="ca-subtitle">
        {view === 'directory'
          ? 'Manage suppliers for your team.'
          : 'How closely each supplier tracks your should-cost — who prices near it, who pads margin. Ranked by average gap, biggest opportunity first.'}
      </p>

      {view === 'benchmark' ? (
        <BenchmarkView
          data={benchmark} loading={benchLoading} error={benchErr}
          trustScores={trustScores} trustResolution={trustResolution}
          computingTrust={computingTrust} onComputeTrustScores={handleComputeTrustScores}
        />
      ) : (
      <>
      {showForm && (
        <div className="ca-card" style={{ marginBottom: 16 }}>
          <div className="ca-card-title">New Supplier</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'flex-end' }}>
            <div>
              <label className="ca-label">Name *</label>
              <input className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="Supplier name" />
            </div>
            <div>
              <label className="ca-label">Country</label>
              <input className="ca-input" value={country} onChange={e => setCountry(e.target.value)} placeholder="e.g. Germany" />
            </div>
            <button className="ca-btn ca-btn-primary" onClick={handleCreate} disabled={saving || !name.trim()}>
              {saving ? 'Saving...' : 'Create'}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading...</div>
      ) : suppliers.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 14 }}>
            No suppliers yet — add the companies you buy from to track their price against your should-cost.
          </div>
          <button className="ca-btn ca-btn-primary" onClick={() => setShowForm(true)}>+ Add your first supplier</button>
        </div>
      ) : (
        <div className="ca-card">
          <table className="ca-table">
            <thead>
              <tr>
                <th style={{ width: 30 }}></th>
                <th>Name</th>
                <th>Country</th>
                <th className="center">Products</th>
                <th className="center">Created</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {suppliers.map(s => {
                const models = getSupplierModels(s.id);
                const isExpanded = expandedId === s.id;
                return (
                  <>
                    <tr key={s.id} style={{ cursor: models.length > 0 ? 'pointer' : 'default' }}
                      onClick={() => models.length > 0 && setExpandedId(isExpanded ? null : s.id)}>
                      <td style={{ fontSize: 11, color: 'var(--muted)', textAlign: 'center' }}>
                        {models.length > 0 ? (isExpanded ? '\u25BC' : '\u25B6') : ''}
                      </td>
                      <td style={{ fontWeight: 600 }}>{s.name}</td>
                      <td style={{ color: 'var(--muted)' }}>{s.country || '\u2014'}</td>
                      <td className="center" style={{ fontSize: 11 }}>
                        <span style={{
                          padding: '1px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                          background: models.length > 0 ? 'var(--success-bg)' : 'transparent',
                          color: models.length > 0 ? 'var(--accent)' : 'var(--muted)',
                        }}>
                          {models.length}
                        </span>
                      </td>
                      <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>
                        {s.created_at ? new Date(s.created_at).toLocaleDateString() : '\u2014'}
                      </td>
                      <td className="center">
                        <button className="ca-btn ca-btn-ghost ca-btn-sm"
                          onClick={e => { e.stopPropagation(); navigate(`/suppliers/${s.id}/purchases`); }}>
                          Purchases
                        </button>
                        {models.length > 0 && (
                          <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 4 }}
                            onClick={e => { e.stopPropagation(); handleExportExcel(s.id, s.name); }}>
                            Export
                          </button>
                        )}
                        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 4, color: 'var(--accent2)' }}
                          onClick={e => { e.stopPropagation(); handleDelete(s.id); }}>Delete</button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${s.id}-products`}>
                        <td colSpan={6} style={{ padding: '0 0 0 40px', background: 'var(--surface2)' }}>
                          <table className="ca-table" style={{ margin: '8px 0' }}>
                            <thead>
                              <tr>
                                <th>Family</th>
                                <th>Reference</th>
                                <th>Producing Region</th>
                                <th className="center">Gap %</th>
                                <th className="center">Actions</th>
                              </tr>
                            </thead>
                            <tbody>
                              {models.map(m => (
                                <tr key={m.cost_model_id}>
                                  <td style={{ fontWeight: 500 }}>{m.product_name}</td>
                                  <td style={{ color: 'var(--text-secondary)' }}>{m.product_reference || '\u2014'}</td>
                                  <td style={{ color: 'var(--muted)' }}>{m.region}</td>
                                  <td className="center">
                                    {m.gap_pct !== null ? (
                                      <span style={{
                                        padding: '1px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                                        color: m.gap_pct > 0 ? 'var(--accent2)' : m.gap_pct < 0 ? 'var(--accent)' : 'var(--muted)',
                                      }}>
                                        {m.gap_pct > 0 ? '+' : ''}{m.gap_pct.toFixed(1)}%
                                      </span>
                                    ) : (
                                      <span style={{ color: 'var(--muted)', fontSize: 11 }}>{'\u2014'}</span>
                                    )}
                                  </td>
                                  <td className="center">
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm"
                                      onClick={e => { e.stopPropagation(); navigate(`/cost-models/${m.cost_model_id}`); }}>
                                      Edit
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </>
      )}
    </div>
  );
}

/* Benchmarking ranking table — how closely each supplier tracks should-cost.
 * Data from GET /api/suppliers/benchmark (owner/admin only). A positive avg gap%
 * means the supplier prices above should-cost (pads margin); negative means below. */
const TRUST_GRADE_COLOR = {
  A: 'var(--accent)', B: 'var(--accent)', C: 'var(--accent3)', D: 'var(--accent2)', F: 'var(--accent2)',
};

function TrustGradeBadge({ summary }) {
  if (!summary) {
    return <span style={{ fontSize: 11, color: 'var(--muted)' }}>Not computed</span>;
  }
  if (summary.insufficient_data || summary.overall_grade == null) {
    return <span style={{ fontSize: 11, color: 'var(--muted)', fontStyle: 'italic' }}>Insufficient data</span>;
  }
  const breakdown = summary.scores
    .filter(s => !s.insufficient_data)
    .map(s => `${s.grain}${s.product_id ? '' : ` #${s.subfamily_id}`}: ${s.grade} (${s.score}) — avg gap ${s.inputs.avg_gap_pct}%, drift ${s.inputs.slope_pct_per_quarter}pt/qtr`)
    .join('\n');
  return (
    <span
      title={breakdown || 'No sufficient-data grain yet'}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 26, height: 22, borderRadius: 5, cursor: 'help',
        fontWeight: 700, fontSize: 12,
        color: '#fff', background: TRUST_GRADE_COLOR[summary.overall_grade] || 'var(--muted)',
      }}
    >
      {summary.overall_grade}
    </span>
  );
}

function BenchmarkView({ data, loading, error, trustScores, trustResolution, computingTrust, onComputeTrustScores }) {
  if (loading) return <div style={{ padding: 20, color: 'var(--muted)' }}>Loading benchmarking…</div>;
  if (error) return (
    <div className="ca-card" style={{ textAlign: 'center', padding: 40 }}>
      <div style={{ color: 'var(--accent2)', marginBottom: 6 }}>{error}</div>
      <div style={{ color: 'var(--muted)', fontSize: 12 }}>Benchmarking is available to team owners and admins.</div>
    </div>
  );
  if (!data) return null;

  const priced = data.filter(s => s.avg_gap_pct !== null);
  if (priced.length === 0) return (
    <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
      <div style={{ color: 'var(--text-secondary)' }}>
        No benchmarking data yet — add actual prices to your suppliers' cost models to compare them against should-cost.
      </div>
    </div>
  );

  const maxAbs = Math.max(5, ...priced.map(s => Math.abs(s.avg_gap_pct)));
  const gapColor = (g) => (g > 1 ? 'var(--accent2)' : g < -1 ? 'var(--accent)' : 'var(--muted)');
  const trendArrow = (t) => {
    if (!t || t.length < 2) return null;
    const d = t[t.length - 1].avg_gap_pct - t[0].avg_gap_pct;
    if (Math.abs(d) < 0.5) return <span title="Stable" style={{ color: 'var(--muted)' }}>→</span>;
    return d > 0
      ? <span title={`Widening +${d.toFixed(1)} pts`} style={{ color: 'var(--accent2)' }}>↑</span>
      : <span title={`Narrowing ${d.toFixed(1)} pts`} style={{ color: 'var(--accent)' }}>↓</span>;
  };

  const handleExport = () => exportCsv(
    'supplier-benchmark.csv',
    ['Rank', 'Supplier', 'Country', 'Cost Models', 'Priced Quarters', 'Avg Gap %', 'Latest Gap %', 'Exposure', 'Trend (first→last pts)'],
    priced.map((s, i) => [
      i + 1, s.supplier_name, s.country || '', s.n_models, s.n_quarters_priced,
      s.avg_gap_pct, s.latest_gap_pct, s.exposure,
      s.trend.length >= 2 ? (s.trend[s.trend.length - 1].avg_gap_pct - s.trend[0].avg_gap_pct).toFixed(1) : '',
    ]),
  );

  return (
    <div className="ca-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          {trustResolution === 'raw_supplier_name' && (
            <span title="No producer/alias canonicalisation entity exists yet — a supplier appearing under two spellings is scored as two separate suppliers.">
              Trust grades resolve by raw supplier name, not a canonical producer ⓘ
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onComputeTrustScores} disabled={computingTrust}>
            {computingTrust ? 'Computing…' : 'Compute trust scores'}
          </button>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={handleExport}>Export CSV</button>
        </div>
      </div>
      <div className="ca-scroll-x">
        <table className="ca-table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th className="center" style={{ width: 40 }}>#</th>
              <th>Supplier</th>
              <th>Country</th>
              <th className="center">Models</th>
              <th className="center">Priced Qtrs</th>
              <th>Avg Gap %</th>
              <th className="center">Latest</th>
              <th className="center">Trend</th>
              <th className="center">Exposure</th>
              <th className="center">Trust Grade</th>
            </tr>
          </thead>
          <tbody>
            {priced.map((s, i) => (
              <tr key={s.supplier_id}>
                <td className="center" style={{ color: 'var(--muted)', fontWeight: 600 }}>{i + 1}</td>
                <td style={{ fontWeight: 600 }}>{s.supplier_name}</td>
                <td style={{ color: 'var(--muted)' }}>{s.country || '—'}</td>
                <td className="center">{s.n_models}</td>
                <td className="center" style={{ color: 'var(--muted)' }}>{s.n_quarters_priced}</td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, minWidth: 56, color: gapColor(s.avg_gap_pct) }}>
                      {s.avg_gap_pct > 0 ? '+' : ''}{s.avg_gap_pct.toFixed(1)}%
                    </span>
                    <DriftBar value={Math.abs(s.avg_gap_pct)} max={maxAbs} color={gapColor(s.avg_gap_pct)} />
                  </div>
                </td>
                <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: gapColor(s.latest_gap_pct) }}>
                  {s.latest_gap_pct > 0 ? '+' : ''}{s.latest_gap_pct.toFixed(1)}%
                </td>
                <td className="center" style={{ fontSize: 16 }}>{trendArrow(s.trend)}</td>
                <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: s.exposure > 0 ? 'var(--accent2)' : 'var(--muted)' }}>
                  {s.exposure ? `$${Math.round(s.exposure).toLocaleString()}` : '—'}
                </td>
                <td className="center">
                  <TrustGradeBadge summary={trustScores ? trustScores[s.supplier_id] : null} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
