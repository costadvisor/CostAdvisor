import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { Sparkline } from './wsCharts';

/* ──────────────────────────────────────────────────────────────────────
 * Intelligence library, at formula x region **combo** grain (Wave 3, SCRUM-75).
 *
 * The grain change is the point. This page renders the *platform catalogue* with
 * region as a selector, and there is no CostModel behind those tiles at all — so
 * the old per-cost-model shape could not serve it. A team's own products still
 * reach the same numbers through the cost-model route, and the payload's
 * `resolved_via` says which way it came.
 *
 * And the grid is now **one request**. It used to fire a POST per visible tile
 * behind an IntersectionObserver, which is tolerable against a team's handful of
 * cost models and does not scale to a catalogue of hundreds.
 *
 * Two things this must not smooth over:
 *   - `evaluable: false` is the majority state today, because most combos have
 *     no base-price anchor. It gets a real design with the reason named and a
 *     route to fixing it, not a blank tile.
 *   - the caveat comes from `trust.caveat`, shipped with the grade that produced
 *     it. An unconditional "not reviewed by an expert" caveats combos nobody
 *     questioned; an absent one vouches for numbers nobody looked at.
 * ──────────────────────────────────────────────────────────────────── */

const BATCH = 50;

const GRADE = {
  high: { label: 'HIGH', color: 'var(--accent)', bg: 'var(--success-bg)' },
  medium: { label: 'MED', color: 'var(--accent4)', bg: 'var(--info-bg)' },
  low: { label: 'LOW', color: 'var(--accent3)', bg: 'var(--warn-bg)' },
  blocked: { label: 'BLOCKED', color: 'var(--accent2)', bg: 'var(--danger-bg)' },
  unrated: { label: 'UNRATED', color: 'var(--muted)', bg: 'var(--neutral-bg)' },
};

const CYCLE = {
  near_the_top: { color: 'var(--accent2)', label: 'near the top' },
  mid_range: { color: 'var(--text-secondary)', label: 'mid-range' },
  near_the_bottom: { color: 'var(--accent)', label: 'near the bottom' },
  // A percentile cannot express "has not moved", so it is its own state.
  flat: { color: 'var(--muted)', label: 'flat' },
};

function GradeBadge({ trust }) {
  const g = GRADE[trust?.grade || 'unrated'];
  return (
    <span className="ca-badge" title={trust?.caveat || 'No caveat — this combo is graded clean.'}
      style={{ background: g.bg, color: g.color, fontWeight: 600, fontSize: 9 }}>
      {g.label}
    </span>
  );
}

function ComboTile({ result, name, family, onOpen }) {
  const r = result;
  const levels = (r?.series || []).map(p => p.level);
  const change = r?.change?.short_pct;
  const cycle = r?.cycle;
  const cy = CYCLE[cycle?.verdict] || null;

  return (
    <div className="ca-card" style={{ cursor: 'pointer', transition: 'border-color .15s' }}
      onClick={onOpen}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 14 }}>{name}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            {family}
            {r?.coverage_region && r.coverage_region !== r.region_requested && (
              <span title={`No combo priced for ${r.region_requested}; falling back to ${r.coverage_region}`}
                style={{ color: 'var(--accent3)' }}> · via {r.coverage_region}</span>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
          {r?.trust && <GradeBadge trust={r.trust} />}
          {change !== null && change !== undefined && (
            <span className="ca-badge" style={{
              background: change > 0 ? 'var(--danger-bg)' : 'var(--success-bg)',
              color: change > 0 ? 'var(--accent2)' : 'var(--accent)', fontSize: 9,
            }}>
              {change > 0 ? '↑ +' : '↓ '}{change.toFixed(1)}%
            </span>
          )}
        </div>
      </div>

      {!r ? (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12 }}>Loading…</div>
      ) : !r.evaluable ? (
        /* The majority state. Naming the reason and where to fix it beats a
           blank tile that reads like a loading failure. */
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--muted)', lineHeight: 1.55 }}>
          <span style={{ color: 'var(--accent3)' }}>Not evaluable</span> — {r.reason}
        </div>
      ) : levels.length < 2 ? (
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--muted)' }}>
          Only one period of history — nothing to chart yet.
        </div>
      ) : (
        <>
          <div style={{ marginTop: 12 }}>
            <Sparkline data={levels} width={248} height={40}
              label={`Index level history, ${levels.length} quarters`} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 10 }}>
            <span style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
              {levels[levels.length - 1].toFixed(1)} <span style={{ fontFamily: 'inherit' }}>index</span>
            </span>
            {cy && (
              <span style={{ color: cy.color }} title={cycle.sentence}>
                {cy.label}
                {cycle.percentile !== null && cycle.percentile !== undefined
                  && ` (${Math.round(cycle.percentile)}%)`}
              </span>
            )}
          </div>
        </>
      )}

      {r?.data_gaps?.length > 0 && (
        <div style={{ fontSize: 10, color: 'var(--accent3)', marginTop: 8 }}
          title={r.data_gaps.map(g => `${g.line}: ${g.reason}`).join('\n')}>
          {r.data_gaps.length} cost line{r.data_gaps.length === 1 ? '' : 's'} rode flat
        </div>
      )}
    </div>
  );
}

export default function IntelligenceArea() {
  const navigate = useNavigate();
  const { activeTeamId } = useAuth();

  const [templates, setTemplates] = useState([]);
  const [combos, setCombos] = useState([]);       // [{template_id, region, ...}]
  const [results, setResults] = useState({});     // "tid:region" -> payload
  const [loading, setLoading] = useState(true);
  const [deriving, setDeriving] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [regionFilter, setRegionFilter] = useState('all');
  const [page, setPage] = useState(0);

  // The catalogue index: every (template, region) pair. This is the cross-library
  // coverage listing unit 11 added — a catalogue needs all of them, so it asks
  // without the review filter.
  useEffect(() => {
    if (!activeTeamId) return;
    setLoading(true); setError(null);
    Promise.all([
      api.get('/api/formulas/', { params: { team_id: activeTeamId } }),
      api.get('/api/formulas/review-queue', {
        params: { team_id: activeTeamId, order_by: 'code', limit: 500 },
      }),
    ])
      .then(([t, q]) => { setTemplates(t.data); setCombos(q.data.rows || []); })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setLoading(false));
  }, [activeTeamId]);

  const byId = useMemo(
    () => Object.fromEntries(templates.map(t => [t.id, t])), [templates]);

  const regions = useMemo(
    () => [...new Set(combos.map(c => c.region))].sort(), [combos]);

  const q = search.trim().toLowerCase();
  const filtered = useMemo(() => combos.filter(c => {
    if (regionFilter !== 'all' && c.region !== regionFilter) return false;
    if (!q) return true;
    const t = byId[c.template_id];
    return `${c.template_code || ''} ${c.template_name || ''} ${t?.family_name || ''}`
      .toLowerCase().includes(q);
  }), [combos, regionFilter, q, byId]);

  const pageCombos = filtered.slice(page * BATCH, page * BATCH + BATCH);
  const pageKey = pageCombos.map(c => `${c.template_id}:${c.region}`).join('|');

  // One request per page of tiles, not one per tile.
  const derive = useCallback(() => {
    if (!activeTeamId || pageCombos.length === 0) return;
    const missing = pageCombos.filter(c => !results[`${c.template_id}:${c.region}`]);
    if (missing.length === 0) return;
    setDeriving(true);
    api.post('/api/intelligence/combos',
      { combos: missing.map(c => ({ template_id: c.template_id, region: c.region })) },
      { params: { team_id: activeTeamId } })
      .then(({ data }) => {
        setResults(prev => {
          const next = { ...prev };
          for (const r of data.results) next[`${r.template_id}:${r.region_requested}`] = r;
          return next;
        });
      })
      .catch(err => setError(formatApiError(err)))
      .finally(() => setDeriving(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId, pageKey]);

  useEffect(derive, [derive]);
  useEffect(() => { setPage(0); }, [regionFilter, q]);

  // Grouped by family for the same reason the old page was: a catalogue this
  // size is unreadable as a flat grid.
  const groups = useMemo(() => {
    const map = new Map();
    for (const c of pageCombos) {
      const label = byId[c.template_id]?.family_name || 'Uncategorised';
      if (!map.has(label)) map.set(label, []);
      map.get(label).push(c);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [pageCombos, byId]);

  const evaluableCount = pageCombos.filter(
    c => results[`${c.template_id}:${c.region}`]?.evaluable).length;
  const loadedCount = pageCombos.filter(
    c => results[`${c.template_id}:${c.region}`]).length;

  const filterBtn = (active) => (active ? 'ca-btn ca-btn-primary ca-btn-sm' : 'ca-btn ca-btn-ghost ca-btn-sm');

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Intelligence</div>
      <p className="ca-subtitle">
        The catalogue at formula × region grain — index history, drivers, cycle position,
        seasonality and volatility for each priced combo.
      </p>

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : error ? (
        <div className="ca-card" style={{ color: 'var(--accent2)' }}>Error: {error}</div>
      ) : combos.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
            No priced combos yet — a formula needs at least one region with pricing.
          </div>
          <button className="ca-btn ca-btn-primary" onClick={() => navigate('/formulas')}>
            Open the formula catalogue
          </button>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 0', flexWrap: 'wrap' }}>
            <input className="ca-input" style={{ width: 220 }}
              placeholder="Search formula, code or family…"
              value={search} onChange={e => setSearch(e.target.value)} />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              <button className={filterBtn(regionFilter === 'all')}
                onClick={() => setRegionFilter('all')}>All regions</button>
              {regions.map(r => (
                <button key={r} className={filterBtn(regionFilter === r)}
                  onClick={() => setRegionFilter(r)}>{r}</button>
              ))}
            </div>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
              onClick={() => navigate('/portfolio')}
              title="Your own products reach the same numbers through their cost model">
              My products →
            </button>
          </div>

          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>
            {filtered.length} combo{filtered.length === 1 ? '' : 's'}
            {filtered.length > BATCH && ` · showing ${page * BATCH + 1}–${Math.min((page + 1) * BATCH, filtered.length)}`}
            {loadedCount > 0 && ` · ${evaluableCount} of ${loadedCount} on this page carry enough data to evaluate`}
            {deriving && ' · deriving…'}
          </div>

          {/* Stated once, at the top, rather than repeated on every tile: most
              of the catalogue has recipes and index history but no base-price
              anchor, which is a data-acquisition gap, not a failure here. */}
          {loadedCount > 0 && evaluableCount < loadedCount && (
            <div style={{
              fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6,
              padding: '8px 12px', background: 'var(--surface2)', borderRadius: 6, marginBottom: 12,
            }}>
              Combos without a base-price anchor can show an index level but not a
              should-cost. Anchors are set on{' '}
              <button onClick={() => navigate('/formulas')}
                style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer',
                         color: 'var(--accent4)', textDecoration: 'underline', font: 'inherit' }}>
                Formulas → Import Prices</button>.
            </div>
          )}

          {groups.map(([label, items]) => (
            <div key={label} style={{ marginBottom: 22 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 13, color: 'var(--text-secondary)' }}>
                  {label}
                </div>
                <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{items.length}</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
                {items.map(c => (
                  <ComboTile
                    key={`${c.template_id}:${c.region}`}
                    result={results[`${c.template_id}:${c.region}`]}
                    name={`${c.template_name || c.template_code} · ${c.region}`}
                    family={byId[c.template_id]?.family_name || 'Uncategorised'}
                    onOpen={() => navigate(
                      `/intelligence/combo/${c.template_id}/${encodeURIComponent(c.region)}`)}
                  />
                ))}
              </div>
            </div>
          ))}

          {filtered.length > BATCH && (
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 16 }}>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" disabled={page === 0}
                onClick={() => setPage(p => p - 1)}>← Previous</button>
              <button className="ca-btn ca-btn-ghost ca-btn-sm"
                disabled={(page + 1) * BATCH >= filtered.length}
                onClick={() => setPage(p => p + 1)}>Next →</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
