import { Fragment, useMemo } from 'react';
import { StatusBadge, fmt } from '../../../components/intel';
import LineProducts from './LineProducts';
import { STATUSES, STATUS_DEF, mergeLines } from './status';

/* The expanded body of one category: what the catalogue holds (member lines
 * → products, then shared objects → lines → products), and what is still to
 * build. Category notes are internal and not served. */
function CategoryBody({ category: c, sharedByCode }) {
  const lines = useMemo(() => mergeLines(c.members), [c.members]);
  const shared = c.shared || [];
  const build = c.build || [];
  const held = lines.length > 0 || shared.length > 0;

  return (
    <div className="ixc-cat-body">
      {!held && build.length > 0 && (
        <div className="ixc-nothing-held">Nothing in the catalogue serves this category in the right grade yet.</div>
      )}
      <div className={`ixc-cat-grid${held && build.length ? '' : ' one'}`}>
        {held && (
          <div className="ixc-held">
            <div className="ix-section-label">
              Held in the catalogue
              <span className="ixc-label-meta">
                {fmt.plural(c.line_count, 'product line')} · {fmt.plural(c.product_count, 'product')}
              </span>
            </div>
            {lines.map((line, i) => <LineProducts key={line.line_id ?? `u-${line.family}-${i}`} line={line} />)}
            {shared.map((s) => {
              const meta = sharedByCode.get(s.code);
              const narrowed = Array.isArray(s.narrowed_to) ? new Set(s.narrowed_to) : null;
              const sLines = mergeLines(s.lines).map((l) => (narrowed
                ? { ...l, products: l.products.filter((p) => narrowed.has(p.pid)) }
                : l));
              return (
                <div key={s.code} className="ixc-shared">
                  <div className="ixc-shared-head">
                    <span className="ix-badge plain st-info" title="A shared object: a set of product lines several industries reference instead of redrawing">
                      Shared object
                    </span>
                    <span className="ixc-shared-name">{s.name}</span>
                    <span className="ixc-shared-meta">
                      {s.code}
                      {meta?.owner && <> · owned by {meta.owner}</>}
                      {narrowed && <> · this industry buys {fmt.plural(narrowed.size, 'of its products', 'of its products')}</>}
                    </span>
                  </div>
                  {sLines.length > 0
                    ? sLines.map((line, i) => <LineProducts key={line.line_id ?? `u-${line.family}-${i}`} line={line} />)
                    : <div className="ixc-line-empty">No product line loaded for this object.</div>}
                </div>
              );
            })}
          </div>
        )}
        {build.length > 0 && (
          <div className="ixc-build">
            <div className="ix-section-label">
              To build
              <span className="ixc-label-meta">not in the catalogue yet</span>
            </div>
            <ul className="ixc-build-list">
              {build.map((b) => <li key={b}>{b}</li>)}
            </ul>
          </div>
        )}
        {!held && !build.length && (
          <div className="ixc-line-empty">Not in the source data.</div>
        )}
      </div>
    </div>
  );
}

/* Categories of one industry as a table with expandable rows.
 * `open` is a Set of category codes; `onToggle(code)` flips one. */
export default function CategoryTable({ categories, open, onToggle, sharedObjects = [] }) {
  const sharedByCode = useMemo(() => new Map(sharedObjects.map((s) => [s.code, s])), [sharedObjects]);

  return (
    <div className="ix-table-wrap">
      <table className="ix-table ixc-cat-table">
        <thead>
          <tr>
            <th className="ixc-col-chev" aria-label="Expand" />
            <th className="ixc-col-code">Code</th>
            <th>Category</th>
            <th className="ixc-col-fn">Function</th>
            <th className="ixc-col-status">Status</th>
            <th className="num">Lines</th>
            <th className="num">Products</th>
          </tr>
        </thead>
        <tbody>
          {categories.map((c) => {
            const isOpen = open.has(c.code);
            const bodyId = `ixc-cat-${c.code}`;
            return (
              <Fragment key={c.code}>
                <tr
                  id={`cat-${c.code}`}
                  className={`is-clickable ixc-cat-row${isOpen ? ' is-open' : ''} s-${c.status}`}
                  onClick={() => onToggle(c.code)}
                >
                  <td className="ixc-col-chev">
                    <button
                      type="button"
                      className="ixc-chev-btn"
                      aria-expanded={isOpen}
                      aria-controls={bodyId}
                      aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${c.code} ${c.name}`}
                      onClick={(e) => { e.stopPropagation(); onToggle(c.code); }}
                    >
                      <span className="ix-chev" aria-hidden>▾</span>
                    </button>
                  </td>
                  <td className="ixc-col-code"><span className="ixc-code">{c.code}</span></td>
                  <td>
                    <div className="ix-cell-main">{c.name}</div>
                    {c.alias && <div className="ix-cell-sub">{c.alias}</div>}
                  </td>
                  <td className="ixc-col-fn">{c.fn || <span className="ix-muted">—</span>}</td>
                  <td className="ixc-col-status">
                    <StatusBadge status={c.status} title={STATUSES.includes(c.status) ? STATUS_DEF[c.status] : undefined} />
                  </td>
                  <td className="num">{c.line_count ? fmt.num(c.line_count) : <span className="ix-muted">0</span>}</td>
                  <td className="num">
                    {c.product_count ? <strong>{fmt.num(c.product_count)}</strong> : <span className="ix-muted">0</span>}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="ixc-cat-detail">
                    <td colSpan={7} id={bodyId}>
                      <CategoryBody category={c} sharedByCode={sharedByCode} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
