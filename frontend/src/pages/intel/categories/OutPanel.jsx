import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { Panel } from '../../../components/intel';
import { outCode, productHref, industryHref } from './status';

const TONE = {
  OUT_WRONG_BUYER: 'st-neutral',
  OUT_WRONG_GRADE: 'st-warn',
  OUT_UPSTREAM: 'st-neutral',
  OUT_BUYER_OUTPUT: 'st-neutral',
  OUT_SCOPE: 'st-neutral',
  PENDING_EVIDENCE: 'st-info',
  PENDING_BOUNDARY: 'st-info',
  BUILD: 'st-warn',
  REHOME: 'st-info',
};

/* Products the industry was offered and deliberately does not place, each
 * with its reason and the industries that buy it instead. Collapsed by
 * default: it is evidence behind the tree, not the tree. The authored "why"
 * is internal and not served. */

function ProductCell({ o }) {
  const p = o.product;
  const name = p?.name || o.pid;
  let target = null;
  if (p?.redirect_to) target = p.redirect_to;
  else if (p?.listed) target = p.pid;
  return (
    <>
      {target
        ? <Link className="ix-cell-main ixc-out-link" to={productHref(target)}>{name}</Link>
        : <div className="ix-cell-main">{name}</div>}
      <div className="ix-cell-sub">
        {o.pid}
        {p?.redirect_to && <> · opens {p.redirect_to}</>}
        {!p && <> · not a loaded record</>}
      </div>
    </>
  );
}

function ToIndustries({ list = [] }) {
  if (!list.length) return <span className="ix-muted">—</span>;
  return list.map((t, i) => (
    <Fragment key={`${t.name}-${i}`}>
      {i > 0 && <span className="ix-muted">, </span>}
      {t.slug ? <Link className="ix-link" to={industryHref(t.slug)}>{t.name}</Link> : <span>{t.name}</span>}
    </Fragment>
  ));
}

export default function OutPanel({ rows = [] }) {
  if (!rows.length) return null;
  return (
    <Panel
      id="not-bought-here"
      className="ixc-out-panel"
      title={`Products not bought here (${rows.length})`}
      caption="considered and left out, with the reason"
      collapsible
      defaultOpen={false}
      flush
    >
      <div className="ix-table-wrap">
        <table className="ix-table ixc-out-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>Reason</th>
              <th>Bought instead by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o, i) => {
              const code = o.reason?.code;
              const label = o.reason?.label || outCode(code).label;
              const def = outCode(code).def;
              return (
                <tr key={`${o.pid}-${code}-${i}`}>
                  <td className="ixc-out-product"><ProductCell o={o} /></td>
                  <td>
                    <span className={`ix-badge plain ${TONE[code] || 'st-neutral'}`} title={def || label}>
                      {label}
                    </span>
                  </td>
                  <td className="ixc-out-to"><ToIndustries list={o.to_industries} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
