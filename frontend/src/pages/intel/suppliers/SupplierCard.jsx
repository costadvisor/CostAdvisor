import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { RegionChips, fmt } from '../../../components/intel';
import { supplierPath, PRODUCT_COUNT_HINT } from './supplierUtils';

const MAX_FAMILY_TAGS = 4;
const MAX_INDUSTRIES = 2;

/* Put the items the list is filtered on first, so the card shows why it matched. */
function selectedFirst(list, names) {
  if (!names.size) return list;
  const hit = (x) => names.has(x.name.toLowerCase());
  return [...list.filter(hit), ...list.filter((x) => !hit(x))];
}
const lowerSet = (names) => new Set(names.map((n) => n.toLowerCase()));

/* One maker in the directory: name, products tracked here, family chips with
 * the coloured family strip under them (width = how the maker's products
 * split across families), top industries, HQ and its regions. */
export default function SupplierCard({ supplier: s, familyColor, familyIds = [], industries: picked = [] }) {
  const families = s.families || [];
  const pickedIndustries = lowerSet(picked);
  const industries = selectedFirst(s.industries || [], pickedIndustries);
  const pickedFamilies = lowerSet(families.filter((f) => familyIds.includes(String(f.id))).map((f) => f.name));
  const shownFamilies = selectedFirst(families, pickedFamilies).slice(0, MAX_FAMILY_TAGS);
  const moreFamilies = families.length - shownFamilies.length;
  const total = s.product_count || 0;
  const moreIndustries = Math.max(0, (s.industry_count ?? industries.length) - MAX_INDUSTRIES);

  return (
    <Link to={supplierPath(s.id)} state={{ fromList: true }} className="ix-card ixs-card">
      <div className="ix-card-top">
        <div className="ix-card-name ixs-card-name">{s.name}</div>
        {s.integrated_count > 0 && (
          <div className="ix-card-badges">
            <span className="ix-badge plain st-good"
              title={`Integrated upstream on ${fmt.plural(s.integrated_count, 'product')}`}>
              {s.integrated_count} integrated
            </span>
          </div>
        )}
      </div>

      <div className="ix-card-meta ixs-card-meta">
        <span title={PRODUCT_COUNT_HINT}>{fmt.plural(total, 'product')} tracked here</span>
        <span className="ix-dot-sep" aria-hidden> · </span>
        <span>{fmt.plural(s.family_count ?? families.length, 'family', 'families')}</span>
        <span className="ix-dot-sep" aria-hidden> · </span>
        <span>{fmt.plural(s.region_count ?? (s.regions || []).length, 'region')}</span>
      </div>

      <div className="ixs-fam-tags">
        {shownFamilies.map((f) => (
          <span key={f.name}
            className={`ix-tag plain ixs-fam-tag${pickedFamilies.has(f.name.toLowerCase()) ? ' is-selected' : ''}`}
            title={`${f.name}: ${fmt.plural(f.count, 'product')}`}>
            <span className="ixs-fam-dot" style={{ background: familyColor(f.name) }} aria-hidden />
            {f.name}
          </span>
        ))}
        {moreFamilies > 0 && <span className="ix-tag plain ixs-fam-more">+{moreFamilies}</span>}
      </div>

      {total > 0 && families.length > 0 && (
        <div className="ix-seg-strip ixs-strip" aria-hidden>
          {families.map((f) => (
            <span key={f.name} title={`${f.name}: ${f.count}`}
              style={{ flexGrow: f.count, flexBasis: 0, background: familyColor(f.name) }} />
          ))}
        </div>
      )}

      {industries.length > 0 && (
        <div className="ixs-card-ind">
          <span className="ixs-card-ind-label">{picked.length ? 'Industries' : 'Top industries'}</span>
          <span className="ixs-card-ind-names">
            {industries.slice(0, MAX_INDUSTRIES).map((i, n) => (
              <Fragment key={i.name}>
                {n > 0 && ', '}
                {pickedIndustries.has(i.name.toLowerCase())
                  ? <span className="ixs-card-ind-hit">{i.name}</span>
                  : i.name}
              </Fragment>
            ))}
            {moreIndustries > 0 && <span className="ix-muted"> +{moreIndustries}</span>}
          </span>
        </div>
      )}

      <div className="ix-card-foot ixs-card-foot">
        <span className="ixs-hq" title="Headquarters">{s.hq ? `HQ ${s.hq}` : 'HQ not in the source data'}</span>
        {(s.regions || []).length > 0 && <RegionChips regions={s.regions} size="sm" />}
      </div>
    </Link>
  );
}
