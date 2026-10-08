import { Fragment } from 'react';

/* Page title + muted subtitle with counts, actions on the right.
 *
 *   <PageHeader title="Product Intelligence"
 *     meta={['483 formulas', '24 families', 'base 100 = Jan 2023']}
 *     actions={<button className="ix-pill">Trending up</button>} />
 *
 * `meta` items are joined with a middle dot; `subtitle` (a node) goes first. */
export default function PageHeader({ title, subtitle, meta = [], eyebrow, actions, children }) {
  const items = (meta || []).filter((m) => m !== null && m !== undefined && m !== false && m !== '');
  return (
    <div className="ix-page-header">
      <div style={{ minWidth: 0 }}>
        {eyebrow && <div className="ix-page-eyebrow">{eyebrow}</div>}
        <h1 className="ix-page-title">{title}</h1>
        {(subtitle || items.length > 0) && (
          <div className="ix-page-sub">
            {subtitle}
            {items.map((m, i) => (
              <Fragment key={i}>
                {(i > 0 || subtitle) && <span className="ix-dot-sep" aria-hidden>·</span>}
                {m}
              </Fragment>
            ))}
          </div>
        )}
        {children}
      </div>
      {actions && <div className="ix-page-actions">{actions}</div>}
    </div>
  );
}
