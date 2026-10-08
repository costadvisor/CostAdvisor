import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Panel, StatusBadge, fmt } from '../../components/intel';

/* "Demand categories this serves": the ratified demand categories (industry ×
 * function) that this playbook's products are bought for, grouped by
 * industry, with the buyer's own industry first and the others listed
 * compactly.
 *
 * Which industry leads, in order:
 *   1. the viewer's pick in "Your industry" (remembered per team in this
 *      browser);
 *   2. the team's own industry, `team_industry` on the category response,
 *      when this playbook's products are bought there;
 *   3. otherwise the industry where the team's products reach the most
 *      categories, labelled as exactly that (never as "your industry").
 * A team industry the playbook does not reach is said so in one line. */

function groupByIndustry(rows) {
  const map = new Map();
  rows.forEach((r) => {
    const key = r.industry_slug || r.industry;
    if (!map.has(key)) map.set(key, { key, industry: r.industry, slug: r.industry_slug, rows: [], reached: 0 });
    const g = map.get(key);
    g.rows.push(r);
    if ((r.team_pids || []).length) g.reached += 1;
  });
  const groups = [...map.values()];
  groups.forEach((g) => g.rows.sort((a, b) =>
    ((b.team_pids || []).length > 0) - ((a.team_pids || []).length > 0) || String(a.code).localeCompare(String(b.code))));
  return groups.sort((a, b) => (b.reached - a.reached) || (b.rows.length - a.rows.length)
    || String(a.industry).localeCompare(String(b.industry)));
}

const industryHref = (g) => (g.slug ? `/intelligence/categories/${encodeURIComponent(g.slug)}` : null);

function Group({ g }) {
  const href = industryHref(g);
  const head = (
    <>
      <span className="st-demand-ind-name">{g.industry}</span>
      <span className="st-demand-ind-count">{fmt.plural(g.rows.length, 'category', 'categories')}</span>
    </>
  );
  return (
    <div className="st-demand-group">
      {href ? <Link className="st-demand-ind" to={href}>{head}</Link> : <div className="st-demand-ind">{head}</div>}
      {g.rows.map((r) => {
        const mine = (r.team_pids || []).length;
        return (
          <div key={r.code} className="st-demand-row">
            <span className="st-demand-code">{r.code}</span>
            <div style={{ minWidth: 0 }}>
              <div className="st-demand-name">{r.name}</div>
              <div className="st-demand-fn">
                {r.fn}
                {mine > 0
                  ? ` · ${fmt.num(mine)} of your products`
                  : ` · ${fmt.plural((r.pids || []).length, 'catalogue product')}`}
              </div>
            </div>
            <StatusBadge status={r.status} />
          </div>
        );
      })}
    </div>
  );
}

const storageKey = (teamId) => `ca_strategy_industry:${teamId}`;

function readChoice(teamId) {
  if (!teamId) return null;
  try { return window.localStorage.getItem(storageKey(teamId)); } catch { return null; }
}

function writeChoice(teamId, value) {
  if (!teamId) return;
  try {
    if (value) window.localStorage.setItem(storageKey(teamId), value);
    else window.localStorage.removeItem(storageKey(teamId));
  } catch { /* storage unavailable: the choice lasts for this view only */ }
}

const sameIndustry = (g, ti) => !!ti && (
  (ti.slug && g.slug === ti.slug) || (!!ti.name && g.industry === ti.name));

export default function AnalysisDemand({ category, teamId }) {
  const [showAll, setShowAll] = useState(false);
  const [choice, setChoice] = useState(() => readChoice(teamId));
  const rows = category?.demand_categories;
  const groups = useMemo(() => groupByIndustry(rows || []), [rows]);
  const options = useMemo(() => [...groups].sort((a, b) => String(a.industry).localeCompare(String(b.industry))), [groups]);
  if (!category) return null;

  if (!groups.length) {
    return (
      <Panel title="Demand categories served">
        <p className="st-demand-intro">Not in the source data: no ratified demand category places this category&apos;s products.</p>
      </Panel>
    );
  }

  // The viewer's pick when this playbook reaches it, else the team's own
  // industry, else the industry where the team's products reach the most
  // categories, said as such.
  const teamIndustry = category.team_industry || null;
  const teamGroup = teamIndustry ? groups.find((g) => sameIndustry(g, teamIndustry)) || null : null;
  const picked = groups.find((g) => g.key === choice) || teamGroup;
  const lead = picked || groups[0];
  const tied = !picked && lead.reached > 0
    ? groups.filter((g) => g.reached === lead.reached).length : 0;
  // Picked: the select above already says "Your industry", no eyebrow.
  let leadLabel = null;
  if (!picked) {
    if (lead.reached === 0) leadLabel = 'Most demand categories';
    else if (tied > 1) leadLabel = `Most reached by your products · tied with ${fmt.plural(tied - 1, 'other industry', 'other industries')}`;
    else leadLabel = 'Most reached by your products';
  }
  const rest = groups.filter((g) => g.key !== lead.key);
  const REST_PREVIEW = 8;
  const visibleRest = showAll ? rest : rest.slice(0, REST_PREVIEW);

  return (
    <Panel title="Demand categories served"
      caption={`${fmt.num(rows.length)} in ${fmt.plural(groups.length, 'industry', 'industries')}`}>
      <p className="st-demand-intro">
        Ratified demand categories (one function in one industry) that this category&apos;s products are bought for.
      </p>
      {groups.length > 1 && (
        <label className="st-demand-pick">
          <span className="st-ctl-label">Your industry</span>
          <select className={`ix-select${picked ? '' : ' st-demand-unset'}`} value={picked ? picked.key : ''}
            onChange={(e) => { setChoice(e.target.value || null); writeChoice(teamId, e.target.value); }}>
            {!teamGroup && <option value="">Choose your industry…</option>}
            {options.map((g) => <option key={g.key} value={g.key}>{g.industry}</option>)}
          </select>
        </label>
      )}
      {teamIndustry && !teamGroup && (
        <p className="st-demand-intro">
          Your team&apos;s industry, {teamIndustry.name || teamIndustry.slug}, has no demand category that places these products.
        </p>
      )}
      {groups.length > 1 && leadLabel && <div className="st-demand-lead-label">{leadLabel}</div>}
      <Group g={lead} />
      {rest.length > 0 && (
        <div className="st-demand-group">
          <div className="st-demand-ind">
            <span className="st-demand-ind-name">Also bought in</span>
            <span className="st-demand-ind-count">{fmt.plural(rest.length, 'industry', 'industries')}</span>
          </div>
          <div className="st-demand-others">
            {visibleRest.map((g) => {
              const href = industryHref(g);
              return (
                <div key={g.key} className="st-demand-other">
                  {href ? <Link to={href}>{g.industry}</Link> : <strong>{g.industry}</strong>}
                  {' — '}
                  {g.rows.map((r) => `${r.code} ${r.name}`).join('; ')}
                </div>
              );
            })}
          </div>
          {rest.length > REST_PREVIEW && (
            <button type="button" className="ix-link ix-small st-demand-more" onClick={() => setShowAll((v) => !v)}
              aria-expanded={showAll}>
              {showAll ? 'Show fewer' : `Show all ${fmt.num(rest.length)} industries`}
            </button>
          )}
        </div>
      )}
    </Panel>
  );
}
