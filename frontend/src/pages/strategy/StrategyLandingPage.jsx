import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import {
  PageHeader, StatusBadge, KraljicBadge, EmptyState, ErrorState, LoadingPage, useApi, fmt,
  KRALJIC_QUADRANTS, normalizeKraljicBadge,
} from '../../components/intel';
import LandingSuggestions from './LandingSuggestions';
import '../../styles/strategy.css';

/* Strategy landing: the team's adopted strategy categories (one per playbook
 * the team started), then the playbooks its portfolio reaches but has not
 * started ("Suggested from your portfolio"). Data: GET /api/strategy/categories. */

const SORTS = [
  { id: 'overdue', label: 'Actions overdue (default)' },
  { id: 'spend', label: 'Annual spend' },
  { id: 'name', label: 'Name' },
  { id: 'updated', label: 'Last updated' },
];

function kraljicKey(row) {
  const k = row.kraljic;
  if (!k) return 'none';
  return normalizeKraljicBadge(k.badge) || 'none';
}

function sortRows(rows, sort) {
  const out = [...rows];
  const bySpend = (a, b) => (b.annual_spend || 0) - (a.annual_spend || 0);
  if (sort === 'spend') return out.sort(bySpend);
  if (sort === 'name') return out.sort((a, b) => a.name.localeCompare(b.name));
  if (sort === 'updated') return out.sort((a, b) => String(b.last_updated || '').localeCompare(String(a.last_updated || '')));
  return out.sort((a, b) => ((b.actions_overdue || 0) - (a.actions_overdue || 0)) || bySpend(a, b));
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function productsTitle(row) {
  return (row.products || []).map((p) => p.name).join(', ');
}

export default function StrategyLandingPage() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const { data, error, loading, reload } = useApi(
    activeTeamId ? '/api/strategy/categories' : null,
    { team_id: activeTeamId },
  );
  const [filters, setFilters] = useState({ family: '', status: '', kraljic: '', owner: '' });
  const [sort, setSort] = useState('overdue');

  const adopted = useMemo(() => data?.adopted || [], [data]);
  const suggested = data?.suggested || [];
  const currency = data?.currency || 'EUR';
  const illustrative = !!data?.illustrative;

  const options = useMemo(() => ({
    families: uniq(adopted.map((r) => r.family)),
    statuses: uniq(adopted.map((r) => r.status)),
    kraljic: uniq(adopted.map(kraljicKey)).filter((k) => k !== 'none'),
    owners: uniq(adopted.map((r) => r.owner?.name)),
    hasUnassigned: adopted.some((r) => !r.owner),
  }), [adopted]);

  const rows = useMemo(() => sortRows(adopted.filter((r) => {
    if (filters.family && r.family !== filters.family) return false;
    if (filters.status && r.status !== filters.status) return false;
    if (filters.kraljic && kraljicKey(r) !== filters.kraljic) return false;
    if (filters.owner === '__none' && r.owner) return false;
    if (filters.owner && filters.owner !== '__none' && r.owner?.name !== filters.owner) return false;
    return true;
  }), sort), [adopted, filters, sort]);

  const overdueTotal = adopted.reduce((s, r) => s + (r.actions_overdue || 0), 0);
  const filtered = Object.values(filters).some(Boolean);
  const setFilter = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }));
  const open = (slug) => navigate(`/strategy/${encodeURIComponent(slug)}`);
  // Adding a category = starting one of the suggested playbooks: jump there.
  const toSuggestions = () => {
    const el = document.getElementById('st-suggested-title');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.focus({ preventScroll: true });
  };

  // The counts get their own line under the description, so neither wraps
  // mid-phrase at 1280.
  const counts = data ? [
    fmt.plural(adopted.length, 'strategy category', 'strategy categories'),
    suggested.length ? `${fmt.num(suggested.length)} suggested` : null,
    overdueTotal ? <span className="st-overdue">{fmt.plural(overdueTotal, 'action overdue', 'actions overdue')}</span> : null,
  ].filter(Boolean) : [];
  const header = (
    <PageHeader
      title="Strategy"
      subtitle="Category strategies for the products in your portfolio, with their action plans."
      actions={(illustrative || suggested.length > 0) ? (
        <>
          {illustrative && <span className="st-illus" title="This team's prices and volumes are made up for the demo">Illustrative demo data</span>}
          {suggested.length > 0 && (
            <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={toSuggestions}
              title="Start a strategy from the playbooks your portfolio reaches">
              + Add category
            </button>
          )}
        </>
      ) : null}
    >
      {counts.length > 0 && (
        <div className="ix-page-sub st-counts">
          {counts.map((c, i) => (
            <span key={i} className="st-nowrap">
              {i > 0 && <span className="ix-dot-sep" aria-hidden>·</span>}
              {c}
            </span>
          ))}
        </div>
      )}
    </PageHeader>
  );

  if (!activeTeamId || (loading && !data)) {
    return <div className="ix-page ca-fade-in st-landing">{header}<LoadingPage panels={2} /></div>;
  }
  if (error) {
    return <div className="ix-page ca-fade-in st-landing">{header}<ErrorState error={error} onRetry={reload} /></div>;
  }

  if (!adopted.length && !suggested.length) {
    return (
      <div className="ix-page ca-fade-in st-landing">
        {header}
        <EmptyState
          title="No strategy categories yet"
          body="Strategy categories come from the products in your portfolio. Link a product to a catalogue product and the playbooks for its product line appear here."
          action={(
            <div className="ix-row" style={{ justifyContent: 'center' }}>
              <Link className="ca-btn ca-btn-primary ca-btn-sm" to="/intelligence/products">Browse the catalogue</Link>
              <Link className="ca-btn ca-btn-ghost ca-btn-sm" to="/products">Your products</Link>
            </div>
          )}
        />
      </div>
    );
  }

  return (
    <div className="ix-page ca-fade-in st-landing">
      {header}

      {adopted.length > 0 && (
        <div className="st-filters" role="group" aria-label="Filter strategy categories">
          <span className="ix-toolbar-label">Filter</span>
          <select className="ix-select" value={filters.family} onChange={setFilter('family')} aria-label="Family">
            <option value="">All families</option>
            {options.families.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select className="ix-select" value={filters.status} onChange={setFilter('status')} aria-label="Status">
            <option value="">All statuses</option>
            {options.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="ix-select" value={filters.kraljic} onChange={setFilter('kraljic')} aria-label="Kraljic quadrant">
            <option value="">All Kraljic quadrants</option>
            {options.kraljic.map((k) => <option key={k} value={k}>{KRALJIC_QUADRANTS[k]?.label || k}</option>)}
          </select>
          <select className="ix-select" value={filters.owner} onChange={setFilter('owner')} aria-label="Owner">
            <option value="">All owners</option>
            {options.owners.map((o) => <option key={o} value={o}>{o}</option>)}
            {options.hasUnassigned && <option value="__none">Unassigned</option>}
          </select>
          {filtered && (
            <button type="button" className="ix-link ix-small"
              onClick={() => setFilters({ family: '', status: '', kraljic: '', owner: '' })}>
              Clear
            </button>
          )}
          <div className="st-filters-sort">
            <span className="ix-toolbar-label">Sort</span>
            <select className="ix-select" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
              {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </div>
        </div>
      )}

      {adopted.length > 0 ? (
        <div className="ix-panel st-table-panel">
          <div className="ix-table-wrap">
            <table className="ix-table st-landing-table">
              <thead>
                <tr>
                  <th scope="col">Family</th>
                  <th scope="col">Category</th>
                  <th scope="col">Status</th>
                  <th scope="col">Kraljic</th>
                  <th scope="col" className="num" title="Annual spend">Spend (yr)</th>
                  <th scope="col" className="num" title="Open opportunities">Open opps</th>
                  <th scope="col" className="num" title="Actions in progress">In progress</th>
                  <th scope="col" className="num" title="Actions overdue">Overdue</th>
                  <th scope="col">Owner</th>
                  <th scope="col" title="Last updated">Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const n = (r.products || []).length;
                  return (
                    <tr key={r.playbook_slug} className="is-clickable"
                      onClick={() => open(r.playbook_slug)}>
                      <td className="st-family">{r.family || '—'}</td>
                      <td>
                        <Link className="st-catname" to={`/strategy/${encodeURIComponent(r.playbook_slug)}`}
                          onClick={(e) => e.stopPropagation()}>
                          {r.name}
                        </Link>
                        <div className="ix-cell-sub" title={productsTitle(r)}>
                          {n ? `${fmt.plural(n, 'product')}: ${productsTitle(r)}` : 'No products in your portfolio'}
                        </div>
                      </td>
                      <td><StatusBadge status={r.status} /></td>
                      <td><KraljicBadge kraljic={r.kraljic} /></td>
                      <td className="num">{fmt.money(r.annual_spend, r.currency || currency)}</td>
                      <td className="num">{fmt.num(r.open_opportunities)}</td>
                      <td className="num">{fmt.num(r.actions_in_progress)}</td>
                      <td className={`num${r.actions_overdue > 0 ? ' st-overdue' : ''}`}>{fmt.num(r.actions_overdue)}</td>
                      <td className="st-owner">{r.owner?.name || <span className="st-unassigned">Unassigned</span>}</td>
                      <td className="st-date">{fmt.date(r.last_updated)}</td>
                    </tr>
                  );
                })}
                {!rows.length && (
                  <tr className="st-empty-row"><td colSpan={10}>No categories match these filters.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="st-table-foot">
            <span>
              Spend (yr): the latest 4 quarters with an actual price, per cost model, in {currency}.
              {' '}Open opps: opportunities identified, under evaluation or approved.
            </span>
            {illustrative && <span className="st-illus">Illustrative demo data</span>}
          </div>
        </div>
      ) : (
        <EmptyState
          variant="compact"
          title="No strategy started yet"
          body="Start one from the suggestions below: each comes with the playbook's objectives, opportunities and market analysis."
        />
      )}

      <LandingSuggestions
        rows={suggested}
        teamId={activeTeamId}
        currency={currency}
        onAdopted={open}
      />
    </div>
  );
}
