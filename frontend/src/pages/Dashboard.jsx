import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import exportCsv from '../utils/exportCsv';
import { useConfirm, useAlert } from '../components/ConfirmDialog';
import { DriftBar } from './workspace/wsCharts';
import PriorityMatrix from '../components/PriorityMatrix';
import BuyWindows from '../components/BuyWindows';

// Severity colour tier mirrors the Monitor triage screen: price drift = alert,
// index moved = watch, otherwise on-track.
const severityColor = (m) =>
  m.flag_price_drift ? 'var(--accent2)' : m.flag_index_moved ? 'var(--accent3)' : 'var(--accent)';

export default function Dashboard() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const [portfolio, setPortfolio] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState('exposure');
  const [sortDir, setSortDir] = useState('desc');
  const [view, setView] = useState('table');
  const [matrix, setMatrix] = useState(null);
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixErr, setMatrixErr] = useState(null);
  const [buy, setBuy] = useState(null);
  const [buyLoading, setBuyLoading] = useState(false);
  const [buyErr, setBuyErr] = useState(null);

  const fetchPortfolio = () => {
    if (!activeTeamId) return;
    setLoading(true);
    api.get('/api/portfolio/summary', { params: { team_id: activeTeamId } })
      .then(res => { setPortfolio(res.data); })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(fetchPortfolio, [activeTeamId]);

  // Priority matrix (Scrum 20) — lazy-fetched on first open of the Matrix view.
  useEffect(() => {
    if (view !== 'matrix' || !activeTeamId || matrix !== null || matrixLoading) return;
    setMatrixLoading(true);
    setMatrixErr(null);
    api.get('/api/portfolio/priority-matrix', { params: { team_id: activeTeamId } })
      .then(res => setMatrix(res.data))
      .catch(err => setMatrixErr(formatApiError(err)))
      .finally(() => setMatrixLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeTeamId]);

  // Buy windows (Scrum 22) — lazy-fetched on first open of the Buy Windows view.
  useEffect(() => {
    if (view !== 'buy' || !activeTeamId || buy !== null || buyLoading) return;
    setBuyLoading(true);
    setBuyErr(null);
    api.get('/api/portfolio/buy-windows', { params: { team_id: activeTeamId } })
      .then(res => setBuy(res.data))
      .catch(err => setBuyErr(formatApiError(err)))
      .finally(() => setBuyLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeTeamId]);

  const confirm = useConfirm();
  const showAlert = useAlert();
  const [loadingExample, setLoadingExample] = useState(false);

  // Scrum 16 — self-serve onboarding: seed a runnable demo for a brand-new team.
  const handleLoadExampleData = async () => {
    const ok = await confirm({
      title: 'Load example data?',
      message: 'This adds 5 sample products, suppliers, cost models and 3 years of prices/volumes to your team so you can explore CostAdvisor immediately. Safe to run more than once.',
      confirmLabel: 'Load example data',
    });
    if (!ok) return;
    setLoadingExample(true);
    try {
      await api.post(`/api/teams/${activeTeamId}/load-example-data`);
      fetchPortfolio();
    } catch (err) {
      showAlert({ title: 'Could not load example data', message: formatApiError(err) });
    } finally {
      setLoadingExample(false);
    }
  };

  const handleDelete = async (id) => {
    const ok = await confirm({
      title: 'Delete this cost model?',
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(`/api/cost-models/${id}`);
      fetchPortfolio();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    }
  };

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const sortedModels = portfolio ? [...portfolio.models].sort((a, b) => {
    let va, vb;
    if (sortKey === 'exposure') {
      va = Math.abs(a.cumulative_impact || a.gap || 0);
      vb = Math.abs(b.cumulative_impact || b.gap || 0);
    } else if (sortKey === 'gap_pct') {
      va = Math.abs(a.gap_pct || 0);
      vb = Math.abs(b.gap_pct || 0);
    } else if (sortKey === 'product') {
      va = a.product_name.toLowerCase();
      vb = b.product_name.toLowerCase();
      return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    } else if (sortKey === 'should_cost') {
      va = a.current_should_cost;
      vb = b.current_should_cost;
    } else {
      va = 0; vb = 0;
    }
    return sortDir === 'asc' ? va - vb : vb - va;
  }) : [];

  // Shared scale so the severity bars are comparable across rows (min 25% so a
  // portfolio with only small gaps doesn't render every bar near-full).
  const maxAbsGap = Math.max(25, ...sortedModels.map(m => Math.abs(m.gap_pct || 0)));

  const SortHeader = ({ label, field }) => (
    <th className="center" style={{ cursor: 'pointer', userSelect: 'none' }} onClick={() => toggleSort(field)}>
      {label} {sortKey === field ? (sortDir === 'asc' ? '\u25B2' : '\u25BC') : ''}
    </th>
  );

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Portfolio Dashboard</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={`ca-btn ${view === 'table' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('table')}>Table</button>
          <button className={`ca-btn ${view === 'cards' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('cards')}>Cards</button>
          <button className={`ca-btn ${view === 'matrix' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('matrix')}>Matrix</button>
          <button className={`ca-btn ${view === 'buy' ? 'ca-btn-primary' : 'ca-btn-ghost'}`} onClick={() => setView('buy')}>Buy Windows</button>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => portfolio && exportCsv(
            'portfolio.csv',
            ['Supplier', 'Product Family', 'Product Reference', 'Region', 'Currency', 'Should-Cost', 'Actual', 'Gap %', 'Exposure', 'Index Flag', 'Drift Flag'],
            sortedModels.map(m => [m.supplier_name, m.product_name, m.product_reference, m.region, m.currency, m.current_should_cost, m.latest_actual_price, m.gap_pct, Math.abs(m.cumulative_impact || m.gap || 0), m.flag_index_moved, m.flag_price_drift])
          )}>Export CSV</button>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/cost-models/new')}>+ New Model</button>
        </div>
      </div>
      <p className="ca-subtitle">Q{Math.ceil((new Date().getMonth() + 1) / 3)} {new Date().getFullYear()} &mdash; All cost models for your team, ranked by exposure.</p>

      {view === 'matrix' ? (
        <PriorityMatrix data={matrix} loading={matrixLoading} error={matrixErr} />
      ) : view === 'buy' ? (
        <BuyWindows data={buy} loading={buyLoading} error={buyErr} />
      ) : loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading...</div>
      ) : !portfolio || portfolio.models.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
            No cost models yet. Create your first one.
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
            <button className="ca-btn ca-btn-primary" onClick={() => navigate('/cost-models/new')}>
              New Cost Model
            </button>
            <button className="ca-btn ca-btn-ghost" onClick={handleLoadExampleData} disabled={loadingExample}>
              {loadingExample ? 'Loading…' : 'Load example data'}
            </button>
          </div>
        </div>
      ) : (
        <>
          {view === 'table' ? (
            <div className="ca-card">
              <div className="ca-scroll-x">
                <table className="ca-table">
                  <thead>
                    <tr>
                      <th>Supplier</th>
                      <SortHeader label="Product Family" field="product" />
                      <th>Product Reference</th>
                      <th className="center">Producing Region</th>
                      <SortHeader label="Should-Cost" field="should_cost" />
                      <th className="center">Actual</th>
                      <SortHeader label="Gap %" field="gap_pct" />
                      <th>Severity</th>
                      <SortHeader label="Exposure" field="exposure" />
                      <th className="center">Flags</th>
                      <th className="center">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedModels.map(m => {
                      const exposure = Math.abs(m.cumulative_impact || m.gap || 0);
                      return (
                        <tr key={m.cost_model_id}>
                          <td style={{ color: 'var(--muted)' }}>{m.supplier_name || '\u2014'}</td>
                          <td style={{ fontWeight: 600 }}>{m.product_name}</td>
                          <td style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{m.product_reference || '\u2014'}</td>
                          <td className="center">{m.region}</td>
                          <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)' }}>
                            ${m.current_should_cost.toFixed(3)}
                          </td>
                          <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent4)' }}>
                            {m.latest_actual_price !== null ? `$${m.latest_actual_price.toFixed(3)}` : '\u2014'}
                          </td>
                          <td className="center" style={{ color: m.gap_pct > 0 ? 'var(--accent2)' : m.gap_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                            {m.gap_pct !== null ? `${m.gap_pct > 0 ? '+' : ''}${m.gap_pct.toFixed(1)}%` : '\u2014'}
                          </td>
                          <td>
                            {m.gap_pct !== null
                              ? <DriftBar value={Math.abs(m.gap_pct)} max={maxAbsGap} color={severityColor(m)} />
                              : <span style={{ color: 'var(--muted)' }}>{'\u2014'}</span>}
                          </td>
                          <td className="center" style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>
                            ${exposure.toLocaleString()}
                          </td>
                          <td className="center">
                            {m.flag_index_moved && <span title="Index moved >5%" style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 4, fontSize: 9, background: 'var(--info-bg)', color: 'var(--accent3)', marginRight: 4 }}>IDX</span>}
                            {m.flag_price_drift && <span title="Price drift >10%" style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 4, fontSize: 9, background: 'var(--danger-bg)', color: 'var(--accent2)' }}>DRIFT</span>}
                          </td>
                          <td className="center">
                            <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${m.cost_model_id}`)}>View</button>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${m.cost_model_id}/evolution`)}>Evo</button>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => navigate(`/cost-models/${m.cost_model_id}/brief`)}>Brief</button>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)' }} onClick={() => handleDelete(m.cost_model_id)}>Del</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
              {sortedModels.map(m => {
                const exposure = Math.abs(m.cumulative_impact || m.gap || 0);
                return (
                  <div key={m.cost_model_id} className="ca-card" style={{ cursor: 'pointer', transition: 'border-color .2s' }}
                    onClick={() => navigate(`/cost-models/${m.cost_model_id}/evolution`)}
                    onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent)'}
                    onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                      <div>
                        <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 15 }}>{m.product_name}</div>
                        <div style={{ fontSize: 11, color: 'var(--muted)' }}>{[m.supplier_name || 'No supplier', m.region].filter(Boolean).join(' \u00B7 ')}</div>
                      </div>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {m.flag_index_moved && <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 9, background: 'var(--info-bg)', color: 'var(--accent3)' }}>IDX</span>}
                        {m.flag_price_drift && <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 9, background: 'var(--danger-bg)', color: 'var(--accent2)' }}>DRIFT</span>}
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
                      <div>
                        <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Should-Cost</div>
                        <div style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent)', fontWeight: 600 }}>${m.current_should_cost.toFixed(3)}</div>
                      </div>
                      <div>
                        <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Actual</div>
                        <div style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--accent4)', fontWeight: 600 }}>
                          {m.latest_actual_price !== null ? `$${m.latest_actual_price.toFixed(3)}` : '\u2014'}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Gap</div>
                        <div style={{ fontWeight: 600, color: m.gap_pct > 0 ? 'var(--accent2)' : m.gap_pct < 0 ? 'var(--accent)' : 'var(--muted)' }}>
                          {m.gap_pct !== null ? `${m.gap_pct > 0 ? '+' : ''}${m.gap_pct.toFixed(1)}%` : '\u2014'}
                        </div>
                      </div>
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>
                      Exposure: <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>${exposure.toLocaleString()}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={e => { e.stopPropagation(); navigate(`/cost-models/${m.cost_model_id}`); }}>View</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={e => { e.stopPropagation(); navigate(`/cost-models/${m.cost_model_id}/brief`); }}>Brief</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)' }} onClick={e => { e.stopPropagation(); handleDelete(m.cost_model_id); }}>Delete</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
