import { Link } from 'react-router-dom';
import { fmt, SupplyBadge, UNPUBLISHED_LINE } from '../../../components/intel';
import { lineHref, productHref } from './status';

/* One product line inside a category, with the products this industry buys
 * from it. `line` is a merged member row (see mergeLines) or a shared object's
 * line: { line_id, line_key, line_name, family, is_whole_line, products }.
 * A line that is not published has no id and reads "Product line not yet
 * published"; its products are still listed. */
export default function LineProducts({ line }) {
  const products = line.products || [];
  return (
    <div className="ixc-line">
      <div className="ixc-line-head">
        <div className="ixc-line-title">
          {line.line_id != null ? (
            <Link className="ixc-line-name" to={lineHref(line.line_id)}>{line.line_name}</Link>
          ) : (
            <span className="ixc-line-name is-plain">{line.line_name || UNPUBLISHED_LINE}</span>
          )}
          {line.family && <span className="ixc-line-family">{line.family}</span>}
          {line.is_whole_line && (
            <span className="ix-tag plain" title="Every product on this line is bought for this category">Whole line</span>
          )}
        </div>
        <span className="ixc-line-count">{fmt.plural(products.length, 'product')}</span>
      </div>
      {products.length > 0 ? (
        <div className="ixc-prods">
          {products.map((p) => (
            <Link key={p.pid} to={productHref(p.pid)} className="ixc-prod" title={`${p.name} (${p.pid})`}>
              <span className="ixc-prod-name">{p.name || p.pid}</span>
              <span className="ixc-prod-code">{p.pid}</span>
              {p.is_group && <span className="ix-badge" title="A group card that represents several records">Group</span>}
              <SupplyBadge status={p.status} />
            </Link>
          ))}
        </div>
      ) : (
        <div className="ixc-line-empty">No product on this line yet.</div>
      )}
    </div>
  );
}
