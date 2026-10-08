import { Link } from 'react-router-dom';
import { Panel, fmt } from '../../../components/intel';
import { industryHref } from './status';

/* An industry name as a link when it is one of the 50; otherwise plain text
 * (goes_to can be a sentence, e.g. a "held, no receiver" ruling). */
export function IndustryRef({ name, index }) {
  if (!name) return <span className="ix-muted">—</span>;
  const slug = index?.get(String(name).toLowerCase());
  return slug ? <Link className="ix-link" to={industryHref(slug)}>{name}</Link> : <span>{name}</span>;
}

/* Who the categories are written for. */
export function ReferenceBuyerPanel({ buyer }) {
  const rb = buyer || {};
  const inScope = rb.in_scope || [];
  if (!rb.buyer && !inScope.length) {
    return (
      <Panel title="Reference buyer">
        <div className="ix-muted ix-small">Not in the source data.</div>
      </Panel>
    );
  }
  return (
    <Panel title="Reference buyer" caption="who the categories are written for">
      {rb.buyer && <p className="ix-prose ixc-rb-text">{rb.buyer}</p>}
      {inScope.length > 0 && (
        <>
          <div className="ix-section-label ixc-rb-label">In scope</div>
          <ul className="ixc-list ixc-list-check">
            {inScope.map((s) => <li key={s}>{s}</li>)}
          </ul>
        </>
      )}
    </Panel>
  );
}

/* Where this industry stops: what belongs to another industry, and the rules
 * that draw each boundary. Collapsed by default: it is the fine print behind
 * the categories, and when open it doubles the rail's height next to a
 * collapsed categories table. The caption keeps the counts visible. */
export function BoundariesPanel({ buyer, index }) {
  const out = buyer?.out_of_scope || [];
  const rules = buyer?.boundaries || [];
  if (!out.length && !rules.length) return null;
  const caption = [
    out.length > 0 && `${fmt.num(out.length)} out of scope`,
    rules.length > 0 && fmt.plural(rules.length, 'rule'),
  ].filter(Boolean).join(' · ');
  return (
    <Panel title="Boundaries" caption={caption} className="ixc-fold" collapsible defaultOpen={false}>
      {out.length > 0 && (
        <>
          <div className="ix-section-label">Out of scope</div>
          <ul className="ixc-list ixc-out-list">
            {out.map((o) => (
              <li key={`${o.what}-${o.goes_to}`}>
                <div>{o.what}</div>
                {o.goes_to && (
                  <div className="ixc-goes">
                    <span aria-hidden>→</span> <IndustryRef name={o.goes_to} index={index} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {rules.length > 0 && (
        <>
          <div className="ix-section-label">Boundary rules</div>
          <ul className="ixc-list ixc-rule-list">
            {rules.map((r) => (
              <li key={`${r.with}-${r.rule}`}>
                {r.with && (
                  <div className="ixc-rule-with">
                    With <IndustryRef name={r.with} index={index} />
                  </div>
                )}
                <div>{r.rule}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

/* Shared objects this industry's categories reference (chain feedstocks,
 * effluent treatment…). Hidden when there are none; collapsed by default like
 * Boundaries (an expanded category shows the ones it uses). */
export function SharedObjectsPanel({ objects = [], index }) {
  if (!objects.length) return null;
  return (
    <Panel title="Shared objects" caption={`${objects.length} referenced here`} className="ixc-fold"
      collapsible defaultOpen={false}>
      <ul className="ixc-list ixc-shared-list">
        {objects.map((o) => (
          <li key={o.code}>
            <div className="ixc-shared-name">{o.name}</div>
            <div className="ixc-shared-meta">
              {o.code}
              {o.fn && <> · {o.fn}</>}
              {o.referenced_by > 0 && <> · {fmt.plural(o.referenced_by, 'category', 'categories')} here</>}
            </div>
            {o.owner && (
              <div className="ixc-shared-meta">Owned by <IndustryRef name={o.owner} index={index} /></div>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
