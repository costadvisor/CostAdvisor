import { Fragment } from 'react';
import { Link, useNavigate } from 'react-router-dom';

/* Sticky header strip for detail pages: back + breadcrumb (family › sub-family › line),
 * title, subtitle, meta chips, actions, and the tab bar.
 *
 *   <DetailHeader
 *     backTo="/intelligence/products"
 *     crumbs={[{ label: 'Family name', to: '/intelligence/lines?family_id=…' },
 *              { label: 'Line name', to: `/intelligence/lines/${enc}` }]}
 *     title="Product name"
 *     subtitle="PID"
 *     meta={<><span className="ix-chip"><strong>CAS</strong> 0000-00-0</span><SupplyBadge status={st} /></>}
 *     actions={<button className="ca-btn ca-btn-primary ca-btn-sm">Add to portfolio</button>}
 *     tabs={<Tabs tabs={TABS} value={tab} onChange={setTab} idBase="product" />} />
 *
 * `backTo` omitted → history back. The strip sticks under the nav
 * (top: var(--nav-h)). */
export default function DetailHeader({
  backTo, backLabel = 'Back', crumbs = [], title, subtitle, meta, actions, tabs, children,
}) {
  const navigate = useNavigate();
  const onBack = () => {
    if (backTo) navigate(backTo);
    else if (window.history.length > 1) navigate(-1);
    else navigate('/dashboard');
  };
  const trail = (crumbs || []).filter(Boolean);

  return (
    <header className="ix-dh">
      <div className={`ix-dh-inner${tabs ? '' : ' no-tabs'}`}>
        <div className="ix-dh-row">
          <div className="ix-dh-lead">
            <nav className="ix-dh-crumbs" aria-label="Breadcrumb">
              <button type="button" className="ix-dh-back" onClick={onBack}>
                <span aria-hidden>←</span> {backLabel}
              </button>
              {trail.map((c, i) => (
                <Fragment key={`${c.label}-${i}`}>
                  {i > 0 && <span className="ix-dh-sep" aria-hidden>›</span>}
                  {c.to ? <Link to={c.to}>{c.label}</Link> : <span>{c.label}</span>}
                </Fragment>
              ))}
            </nav>
            <div className="ix-dh-titlerow">
              <h1 className="ix-dh-title">{title}</h1>
              {subtitle && <span className="ix-dh-sub">{subtitle}</span>}
            </div>
            {meta && <div className="ix-dh-meta">{meta}</div>}
            {children}
          </div>
          {actions && <div className="ix-dh-actions">{actions}</div>}
        </div>
        {tabs && <div className="ix-dh-tabs">{tabs}</div>}
      </div>
    </header>
  );
}
