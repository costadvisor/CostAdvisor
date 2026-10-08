import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Panel, StatusBadge, fmt } from '../../../components/intel';
import { industryHref, productHref } from './IntelUtil';

/* "Where this product is bought": the ratified category tree's placements,
 * grouped by industry (the API already orders them by industry, then
 * category). Each industry links to its Categories page. */

const STATUS_ORDER = ['servable', 'partial', 'build'];

function groupByIndustry(placements) {
  const groups = [];
  const index = new Map();
  placements.forEach((pl) => {
    const key = pl.industry_slug || pl.industry || '—';
    if (!index.has(key)) {
      const g = { key, slug: pl.industry_slug, name: pl.industry || pl.industry_slug || '—', rows: [] };
      index.set(key, g);
      groups.push(g);
    }
    index.get(key).rows.push(pl);
  });
  return groups;
}

function IndustryGroup({ group, pid }) {
  return (
    <section className="ixi-where-group" aria-label={group.name}>
      <h3 className="ixi-where-industry">
        {group.slug ? <Link to={industryHref(group.slug)}>{group.name}</Link> : group.name}
      </h3>
      <ul className="ixi-where-list">
        {group.rows.map((pl, i) => (
          <li key={`${pl.category_code}-${i}`} className="ixi-where-row">
            <span className="ixi-where-code">{pl.category_code}</span>
            <span className="ixi-where-name">
              <span className="ixi-where-cat">{pl.category_name || pl.placement_name}</span>
              <span className="ixi-where-fn">
                {pl.fn}
                {pl.via_shared && (
                  <span className="ixi-where-shared" title={`Placed through the shared object ${pl.via_shared}`}>
                    {' · shared'}
                  </span>
                )}
                {pl.pid && pl.pid !== pid && (
                  <>
                    {' · via '}
                    <Link to={productHref(pl.pid)}>{pl.pid}</Link>
                  </>
                )}
              </span>
            </span>
            <StatusBadge status={pl.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/* A product placed in 40+ industries would make this the longest panel on the
 * page; past COLLAPSE_AT industries the list opens on request. */
const COLLAPSE_AT = 30;
const SHOWN_COLLAPSED = 24;

export default function IntelWhereBought({ product }) {
  const [showAll, setShowAll] = useState(false);
  const placements = product.placements || [];
  const groups = groupByIndustry(placements);
  const collapsible = groups.length > COLLAPSE_AT;
  const visible = collapsible && !showAll ? groups.slice(0, SHOWN_COLLAPSED) : groups;

  const counts = {};
  placements.forEach((pl) => { counts[pl.status] = (counts[pl.status] || 0) + 1; });
  const statusText = STATUS_ORDER.filter((s) => counts[s]).map((s) => `${counts[s]} ${s}`).join(' · ');
  const caption = placements.length
    ? `${fmt.plural(placements.length, 'category', 'categories')} in ${fmt.plural(groups.length, 'industry', 'industries')}${statusText ? ` · ${statusText}` : ''}`
    : 'Ratified category tree';

  return (
    <Panel title="Where this product is bought" caption={caption}>
      {placements.length === 0 ? (
        <p className="ixi-empty-line">This product has no placement in the ratified category tree.</p>
      ) : (
        <>
          <div className="ixi-where">
            {visible.map((g) => <IndustryGroup key={g.key} group={g} pid={product.pid} />)}
          </div>
          {collapsible && (
            <button type="button" className="ix-link ixi-where-more" aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show fewer industries' : `Show all ${groups.length} industries`}
            </button>
          )}
        </>
      )}
    </Panel>
  );
}
