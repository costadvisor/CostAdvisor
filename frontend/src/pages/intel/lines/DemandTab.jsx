import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { Panel, StatusBadge, EmptyState } from '../../../components/intel';
import { industryHref, productHref, count, listedOf } from './lineUtil';

/* Line detail › Demand: where the line's products are bought — the ratified
 * demand tree's industries → categories that place them. */
export default function DemandTab({ line }) {
  const demand = line.demand || {};
  const industries = demand.industries || [];

  if (!industries.length) {
    return (
      <EmptyState title="Not in the source data"
        body="No category of the demand tree places this line's products yet." />
    );
  }

  const placed = demand.placed_product_count ?? 0;
  const caption = [
    count(demand.industry_count ?? industries.length, 'industry', 'industries'),
    count(demand.category_count ?? 0, 'category', 'categories'),
    `${placed} of ${count(listedOf(line), 'product')} placed`,
  ].join(' · ');

  return (
    <Panel title="Where this line's products are bought" caption={caption} flush>
      <div className="ix-table-wrap">
        <table className="ix-table ixl-demand">
          <thead>
            <tr>
              <th style={{ width: '22%' }}>Industry</th>
              <th>Category</th>
              <th style={{ width: '17%' }}>Function</th>
              <th style={{ width: 96 }}>Status</th>
              <th style={{ width: '24%' }}>Products</th>
            </tr>
          </thead>
          <tbody>
            {industries.map((ind) => (
              <Fragment key={ind.slug || ind.industry}>
                {(ind.categories || []).map((c, i) => (
                  <tr key={`${ind.slug}-${c.code}`} className={i === 0 ? 'ixl-ind-first' : undefined}>
                    {i === 0 && (
                      <td className="ixl-ind-cell" rowSpan={ind.categories.length}>
                        {ind.slug ? <Link to={industryHref(ind.slug)}>{ind.industry}</Link> : ind.industry}
                        {ind.categories.length > 1 && (
                          <div className="ix-cell-sub">{count(ind.categories.length, 'category', 'categories')}</div>
                        )}
                      </td>
                    )}
                    <td>
                      <span className="ixl-code">{c.code}</span>{' '}
                      <span className="ix-cell-main">{c.name}</span>
                    </td>
                    <td className="ix-muted">{c.fn}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>
                      <div className="ixl-pid-chips">
                        {(c.pids || []).map((pid) => (
                          <Link key={pid} to={productHref(pid)} className="ix-tag plain">{pid}</Link>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
