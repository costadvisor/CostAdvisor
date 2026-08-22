import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { Sparkline } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * Intelligence — product-centric market & pricing intelligence, modelled on
 * sample_idea/intelligence_mockup.html. Landing: family-grouped product cards
 * (one per cost model) with a should-cost sparkline + trend, loaded lazily per
 * card (IntersectionObserver) from the existing /evolution output. Click a card
 * → /intelligence/:costModelId. Read-only; no new backend engine.
 * ──────────────────────────────────────────────────────────────────── */

const curSym = (c) => (c === 'EUR' ? '€' : c === 'USD' ? '$' : c === 'GBP' ? '£' : c ? `${c} ` : '');

// One card. Fetches its own should-cost history only once it scrolls into view,
// so a large portfolio doesn't fire N evolution calls up front.
function IntelCard({ cm, familyLabel, onOpen }) {
  const ref = useRef(null);
  const [spark, setSpark] = useState({ status: 'idle' });   // idle | loading | ok | err

  useEffect(() => {
    const el = ref.current;
    if (!el || spark.status !== 'idle') return;
    const io = new IntersectionObserver((entries) => {
      if (!entries[0].isIntersecting) return;
      io.disconnect();
      setSpark({ status: 'loading' });
      api.post('/api/costing/evolution', { cost_model_id: cm.id, granularity: 'quarterly' })
        .then(({ data }) => {
          const vals = (data.periods || []).map(p => p.theoretical).filter(v => v != null);
          setSpark({ status: 'ok', values: vals, currency: data.currency, unit: data.unit });
        })
        .catch(() => setSpark({ status: 'err' }));
    }, { rootMargin: '120px' });
    io.observe(el);
    return () => io.disconnect();
  }, [cm.id, spark.status]);

  const fv = cm.formula_versions?.[0];
  const anchors = (fv?.components || []).slice(0, 3).map(c => c.commodity_name || c.label).filter(Boolean);
  const vals = spark.values || [];
  const trendPct = vals.length >= 2 && vals[0] ? ((vals[vals.length - 1] - vals[0]) / vals[0]) * 100 : null;
  const latest = vals.length ? vals[vals.length - 1] : null;
  const sym = curSym(spark.currency || cm.currency);

  return (
    <div ref={ref} className="ca-card" style={{ cursor: 'pointer', transition: 'border-color .15s' }}
      onClick={() => onOpen(cm.id)}
      onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent)'}
      onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div>
          <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 15 }}>{cm.product_name}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{familyLabel}{cm.supplier_name ? ` · ${cm.supplier_name}` : ''}</div>
        </div>
        {trendPct != null && (
          <span className="ca-badge" style={{ background: trendPct > 0 ? 'var(--danger-bg)' : 'var(--success-bg)', color: trendPct > 0 ? 'var(--accent2)' : 'var(--accent)' }}>
            {trendPct > 0 ? '↑ +' : '↓ '}{trendPct.toFixed(1)}%
          </span>
        )}
      </div>

      <div style={{ margin: '10px 0 6px' }}>
        {spark.status === 'ok' && vals.length >= 2 ? (
          <Sparkline data={vals} width={240} height={40} />
        ) : (
          <div style={{ height: 40, display: 'flex', alignItems: 'center', color: 'var(--muted)', fontSize: 11 }}>
            {spark.status === 'err' ? 'No history' : spark.status === 'ok' ? 'Not enough history' : '…'}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)' }}>
        <span>{cm.region || '—'}</span>
        {latest != null && <span style={{ color: 'var(--text)', fontFamily: "'JetBrains Mono', monospace" }}>{sym}{latest >= 100 ? Math.round(latest).toLocaleString() : latest.toFixed(2)}/{spark.unit || cm.product_unit || ''}</span>}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{anchors.length ? anchors.join(' · ') : 'No linked indices'}</span>
        <span style={{ fontSize: 11, color: 'var(--accent)' }}>Open →</span>
      </div>
    </div>
  );
}

export default function IntelligenceArea() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();

  const [costModels, setCostModels] = useState([]);
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [regionFilter, setRegionFilter] = useState('all');

  useEffect(() => {
    if (!activeTeamId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      api.get('/api/cost-models', { params: { team_id: activeTeamId } }),
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/chemical-families'),
    ])
      .then(([cmRes, pRes, fRes]) => { setCostModels(cmRes.data); setProducts(pRes.data); setFamilies(fRes.data); })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [activeTeamId]);

  const familyName = (fid) => families.find(f => f.id === fid)?.name || null;
  const productById = useMemo(() => Object.fromEntries(products.map(p => [p.id, p])), [products]);

  const cards = useMemo(() => costModels.map(cm => {
    const fid = productById[cm.product_id]?.chemical_family_id ?? null;
    return { cm, familyLabel: familyName(fid) || 'No family' };
  }), [costModels, products, families, productById]);

  const regions = useMemo(() => [...new Set(costModels.map(cm => cm.region).filter(Boolean))].sort(), [costModels]);

  const q = search.trim().toLowerCase();
  const filtered = cards.filter(({ cm }) => {
    if (regionFilter !== 'all' && cm.region !== regionFilter) return false;
    if (q && !`${cm.product_name} ${cm.product_reference || ''} ${cm.supplier_name || ''}`.toLowerCase().includes(q)) return false;
    return true;
  });

  const groups = useMemo(() => {
    const map = new Map();
    filtered.forEach(c => { if (!map.has(c.familyLabel)) map.set(c.familyLabel, []); map.get(c.familyLabel).push(c); });
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([label, items]) => ({ label, items }));
  }, [filtered]);

  const filterBtn = (active) => (active ? 'ca-btn ca-btn-primary ca-btn-sm' : 'ca-btn ca-btn-ghost ca-btn-sm');

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Product Intelligence</div>
      <p className="ca-subtitle">
        {costModels.length} formula{costModels.length === 1 ? '' : 's'} · {new Set(cards.map(c => c.familyLabel)).size} families · should-cost index history, drivers, market narrative and a forward view, product by product.
      </p>

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : costModels.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>No cost models yet — build one to unlock product intelligence.</div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/cost-models/new')}>New cost model</button>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 0', flexWrap: 'wrap' }}>
            <input className="ca-input" style={{ width: 220 }} placeholder="Search product, ref or supplier…" value={search} onChange={e => setSearch(e.target.value)} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              <button className={filterBtn(regionFilter === 'all')} onClick={() => setRegionFilter('all')}>All regions</button>
              {regions.map(r => <button key={r} className={filterBtn(regionFilter === r)} onClick={() => setRegionFilter(r)}>{r}</button>)}
            </div>
          </div>

          {groups.length === 0 ? (
            <div className="ca-card" style={{ textAlign: 'center', padding: 32, color: 'var(--text-secondary)' }}>No products match these filters.</div>
          ) : groups.map(group => (
            <div key={group.label} style={{ marginBottom: 22 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 13, color: 'var(--text-secondary)' }}>{group.label}</div>
                <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{group.items.length} formula{group.items.length === 1 ? '' : 's'}</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
                {group.items.map(({ cm, familyLabel }) => (
                  <IntelCard key={cm.id} cm={cm} familyLabel={familyLabel} onOpen={(id) => navigate(`/intelligence/${id}`)} />
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
