import { Link } from 'react-router-dom';
import { fmt } from '../../../components/intel';
import StatusMix from './StatusMix';
import { industryHref } from './status';

const SCOPE_TAG = { new: 'New', split: 'Split' };

/* One industry on the Categories landing. */
export default function IndustryCard({ industry: it, highlight }) {
  const counts = it.categories_by_status || {};
  const scopeTag = SCOPE_TAG[it.scope_status];
  return (
    <Link to={industryHref(it.slug)} className="ix-card ixc-card">
      <div className="ix-card-top">
        <div className="ix-card-eyebrow">
          {fmt.plural(it.category_count, 'category', 'categories')} · {fmt.plural(it.product_count, 'product')}
        </div>
        {scopeTag && (
          <span className="ix-badge plain st-neutral"
            title={it.scope_status === 'new'
              ? 'Added to the industry list in the September 2026 revision'
              : 'Split out of an earlier, broader industry in the September 2026 revision'}>
            {scopeTag}
          </span>
        )}
      </div>
      <div className="ix-card-name">{it.name}</div>
      <p className="ixc-buyer" title={it.buyer_one_line || undefined}>
        {it.buyer_one_line || <span className="ix-muted">No reference buyer in the source data.</span>}
      </p>
      <StatusMix counts={counts} highlight={highlight} />
      <div className="ix-card-foot">
        <span>{fmt.plural(it.line_count, 'product line')}</span>
        <span className="ix-card-foot-cta">Open →</span>
      </div>
    </Link>
  );
}
