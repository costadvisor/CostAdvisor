import { useState } from 'react';
import { Link } from 'react-router-dom';
import { supplierPath } from './supplierUtils';

const PREVIEW = 6;

/* The source names some suppliers only generically ("Regional Chinese
 * producers"). They are not companies, so the directory never counts, ranks
 * or filters them; this note lists them apart. */
export default function GenericSuppliersNote({ items = [] }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const list = open ? items : items.slice(0, PREVIEW);
  return (
    <section className="ixs-generic" aria-label="Generic supplier names">
      <div className="ixs-generic-head">
        <span className="ixs-generic-title">Generic supplier names</span>
        <span className="ix-muted">
          These names in the source describe a group of producers, not a company.
          They are not counted, ranked or filtered above.
        </span>
      </div>
      <div className="ixs-generic-list">
        {list.map((g) => (
          <Link key={g.id} to={supplierPath(g.id)} state={{ fromList: true }} className="ix-tag plain ixs-generic-tag">
            {g.name}
            <span className="ixs-generic-count">{g.product_count}</span>
          </Link>
        ))}
        {items.length > PREVIEW && (
          <button type="button" className="ix-link ixs-generic-more" onClick={() => setOpen((o) => !o)}
            aria-expanded={open}>
            {open ? 'Show fewer' : `Show all ${items.length}`}
          </button>
        )}
      </div>
    </section>
  );
}
