import { Fragment, useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Panel, RegionChips, StatusBadge } from '../../../components/intel';
import { supplierHref, cap } from './IntelUtil';

/* Makers panel: one row per authored supplier name, from the maker evidence
 * (`producer_formulas`), never from a supplier block.
 *
 * House rules this panel keeps (design §4.1):
 * - no share column and no share value;
 * - no count of makers anywhere: not in the title, the caption or a tooltip.
 *   `counts` is used for the order only;
 * - regions are where it is made; "region not stated" when the source is unsure;
 * - tags and the supplier note are shown as authored.
 *
 * Order: the API keeps the authored order; the screen sorts by `display_rank`
 * (verified and counted, then not yet audited, then the rest, distributors
 * last), keeping the authored order within a rank. */

const EVIDENCE_TONE = {
  verified: 'good',
  not_audited: 'neutral',
  weak: 'warn',
  not_counted: 'neutral',
  unverified: 'neutral',
  family_only: 'neutral',
  distributor: 'info',
};

const INTEGRATION_LABEL = {
  INTEGRATED: 'Integrated',
  FULLY_INTEGRATED: 'Integrated',
  PARTIALLY_INTEGRATED: 'Partly integrated',
  NOT_INTEGRATED: 'Not integrated',
  NOT_EVIDENCED: 'Not evidenced',
};

const SCOPE_LABEL = { product: 'makes this product', range: 'makes the product range', company: 'company site' };

function SupplierName({ s }) {
  const linkable = (s.producers || []).filter((p) => p.producer_id && !p.is_bucket);
  const generic = s.is_bucket || (s.producers || []).some((p) => p.is_bucket);

  let name;
  if (linkable.length === 1 && (s.producers || []).length === 1) {
    name = <Link className="ixi-sup-link" to={supplierHref(linkable[0].producer_id)}>{s.name}</Link>;
  } else {
    name = <span className="ixi-sup-name">{s.name}</span>;
  }

  const showProfiles = linkable.length > 1 || (linkable.length === 1 && (s.producers || []).length > 1);

  return (
    <>
      <div className="ixi-sup-main">{name}</div>
      {showProfiles && (
        <div className="ixi-sup-profiles">
          {linkable.map((p, i) => (
            <Fragment key={p.producer_id}>
              {i > 0 && <span aria-hidden> · </span>}
              <Link className="ixi-sup-link sm" to={supplierHref(p.producer_id)}>{p.name}</Link>
            </Fragment>
          ))}
        </div>
      )}
      {generic && (
        <div className="ixi-sup-generic" title="A placeholder for unnamed producers, not a single company">
          {s.is_bucket ? 'Unnamed producers' : 'Includes unnamed producers'}
        </div>
      )}
      {(s.tags || []).length > 0 && (
        <div className="ixi-sup-tags">
          {s.tags.map((t, j) => <span key={j} className="ix-tag plain">{t}</span>)}
        </div>
      )}
    </>
  );
}

/* Shared with the supplier page's Products tab (same row fields). */
export function Evidence({ s }) {
  const ev = s.evidence;
  return (
    <div className="ixi-sup-evidence">
      {ev?.label
        ? <StatusBadge status={ev.label} tone={EVIDENCE_TONE[ev.code] || 'neutral'} />
        : <span className="ixi-muted">—</span>}
      {s.origin_restriction?.label && (
        <span className="ixi-sup-origin" title={s.origin_restriction.label}>
          <span aria-hidden>⚑ </span>{s.origin_restriction.label}
        </span>
      )}
    </div>
  );
}

export function Integration({ s }) {
  const it = s.integration || {};
  const label = INTEGRATION_LABEL[it.status] || (it.integrated ? 'Integrated' : null);
  const basis = it.basis || null;
  if (!label) return <span className="ixi-muted">—</span>;
  if (it.integrated) {
    return (
      <span className={`ix-badge plain st-good${basis ? ' ixi-has-tip' : ''}`} title={basis || undefined}
        tabIndex={basis ? 0 : undefined}>
        {label}
      </span>
    );
  }
  return (
    <span className={`ixi-muted${basis ? ' ixi-has-tip' : ''}`} title={basis || undefined} tabIndex={basis ? 0 : undefined}>
      {label}
    </span>
  );
}

export function Regions({ s }) {
  if (s.region_uncertain) return <span className="ixi-muted">Region not stated</span>;
  if (!(s.regions || []).length) return <span className="ixi-muted">—</span>;
  return <RegionChips regions={s.regions} size="sm" />;
}

function siteText(site) {
  const where = [site.plant, site.town, site.area, site.country].filter(Boolean).join(', ');
  return where || 'Location not stated';
}

export function Sites({ sites }) {
  return (
    <ul className="ixi-sites">
      {sites.map((site, i) => (
        <li key={i} className="ixi-site">
          <span className="ixi-site-where">{siteText(site)}</span>
          {site.region && <span className="ixi-site-meta">{site.region}</span>}
          {site.scope && <span className="ixi-site-meta">{SCOPE_LABEL[site.scope] || site.scope}</span>}
          {site.status && <span className="ixi-site-meta">{cap(site.status)}</span>}
        </li>
      ))}
    </ul>
  );
}

function MakerRow({ s }) {
  const [open, setOpen] = useState(false);
  const sitesId = useId();
  const sites = s.sites || [];
  return (
    <>
      <tr className={s.evidence?.code === 'distributor' ? 'ixi-sup-dist' : undefined}>
        <td className="ixi-sup-cell"><SupplierName s={s} /></td>
        <td className="ixi-nowrap">{s.hq || <span className="ixi-muted">—</span>}</td>
        <td className="ixi-nowrap">{s.role ? cap(s.role) : <span className="ixi-muted">—</span>}</td>
        <td><Evidence s={s} /></td>
        <td><Integration s={s} /></td>
        <td className="ixi-sup-regions"><Regions s={s} /></td>
        <td className="ixi-nowrap">
          {sites.length > 0 ? (
            <button type="button" className="ix-link ixi-sites-btn" aria-expanded={open} aria-controls={sitesId}
              onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide sites' : 'Where it is made'}
            </button>
          ) : <span className="ixi-muted">Not stated</span>}
        </td>
      </tr>
      {open && sites.length > 0 && (
        <tr className="ixi-sites-row" id={sitesId}>
          <td colSpan={7}><Sites sites={sites} /></td>
        </tr>
      )}
    </>
  );
}

export default function IntelSuppliers({ product }) {
  // A stable sort: rank first, the authored order inside a rank.
  const rows = useMemo(() => (product.suppliers || [])
    .map((s, i) => ({ s, i }))
    .sort((a, b) => ((a.s.display_rank ?? 9) - (b.s.display_rank ?? 9)) || (a.i - b.i))
    .map(({ s }) => s), [product.suppliers]);

  return (
    <Panel
      title="Makers"
      caption={rows.length ? 'Verified makers first, distributors last · where each one makes it' : null}
      flush={rows.length > 0}
      footer={product.supplier_note ? <span className="ixi-note-text">Note: {product.supplier_note}</span> : null}
    >
      {rows.length === 0 ? (
        <p className="ixi-empty-line">No maker named in the source data yet.</p>
      ) : (
        <div className="ix-table-wrap">
          <table className="ix-table ixi-sup-table">
            <thead>
              <tr>
                <th scope="col">Company</th>
                <th scope="col">HQ</th>
                <th scope="col">Role</th>
                <th scope="col">Evidence</th>
                <th scope="col">Integration</th>
                <th scope="col">Makes it in</th>
                <th scope="col">Sites</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s, i) => <MakerRow key={`${s.name}-${s.row_order ?? i}`} s={s} />)}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
