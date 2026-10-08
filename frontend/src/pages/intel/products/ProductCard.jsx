import { memo } from 'react';
import { Link } from 'react-router-dom';
import {
  TrendBadge, SupplyBadge, fmt, regionColor, REGION_LABELS, UNPUBLISHED_LINE, productHref,
} from '../../../components/intel';
import { Sparkline } from '../../../components/charts';

/* The mockup's region-dot strip: its seven regions always shown (greyed when the
 * card is not priced there), plus GL for the few global-only recipes. */
const DOT_REGIONS = ['NA', 'LA', 'EU', 'MEA', 'IN', 'CN', 'APAC'];

function RegionDots({ regions }) {
  const on = new Set(regions || []);
  const list = on.has('GL') ? [...DOT_REGIONS, 'GL'] : DOT_REGIONS;
  const priced = list.filter((r) => on.has(r)).map((r) => REGION_LABELS[r] || (r === 'GL' ? 'Global' : r));
  return (
    <div className="ixp-rdots" role="img" aria-label={`Priced in ${priced.join(', ') || 'no region'}`}>
      {list.map((r) => (
        <span key={r} className={`ix-rchip sm ixp-rdot${on.has(r) ? '' : ' off'}`}
          style={on.has(r) ? { '--rg': regionColor(r) } : undefined} aria-hidden>
          {r}
        </span>
      ))}
    </div>
  );
}

const DIR_CLASS = { up: 'ix-up', down: 'ix-down', flat: 'ix-flat' };

/* One catalogue card. A real link: focusable, Enter opens, middle-click opens a tab.
 * `sparkWidth` is measured once for the whole grid so the sparklines do not
 * each carry a ResizeObserver. The supply-status badge says only whether the
 * supplier floor is met: it never carries a number. */
function ProductCard({ item, sparkWidth }) {
  const n = item.regions?.length || 0;
  const regionsText = `${n} region${n === 1 ? '' : 's'}`;
  const lineName = item.line?.name || UNPUBLISHED_LINE;
  const where = [item.family?.name, item.subfamily?.name, lineName].filter(Boolean).join(' › ');
  const hasSpark = Array.isArray(item.sparkline) && item.sparkline.length > 1;

  return (
    <Link className={`ix-card ixp-card${item.line ? '' : ' ixp-unplaced'}`} to={productHref(item.pid)}
      title={item.full_name ? `${item.name}\n${item.full_name}` : item.name}
      aria-label={[
        item.name,
        item.is_group ? `group of ${item.member_count}` : null,
        item.status?.label,
        where,
        item.current_index != null ? `index ${fmt.index(item.current_index)}, ${fmt.pct(item.trend_pct)} since January 2023` : null,
        regionsText,
      ].filter(Boolean).join('; ')}>
      <div className="ix-card-top">
        <div className="ix-card-eyebrow" title={where}>{lineName}</div>
        <div className="ix-card-badges">
          {item.trend_pct != null && (
            <TrendBadge pct={item.trend_pct} threshold={2} title="Change since January 2023" />
          )}
        </div>
      </div>
      <div className="ix-card-name">{item.name}</div>
      <div className="ixp-status"><SupplyBadge status={item.status} /></div>
      <div className="ix-card-meta ixp-meta">
        <span>{[item.form, regionsText].filter(Boolean).join(' · ')}</span>
        {item.is_group && (
          <span className="ixp-group" title={`Group card: ${fmt.plural(item.member_count, 'member product')}`}>
            Group · {item.member_count}
          </span>
        )}
      </div>
      <div className="ix-card-spark ixp-spark">
        {hasSpark && sparkWidth > 0 && (
          <Sparkline data={item.sparkline} width={sparkWidth} height={36} trend={item.trend_dir || 'flat'}
            label={`${item.sparkline_region || ''} index, ${fmt.month(item.sparkline_from)} to ${fmt.month(item.sparkline_to)}`} />
        )}
      </div>
      <div className="ix-card-sparkmeta">
        <span className="ix-card-base">
          {item.sparkline_region ? `${item.sparkline_region} · ` : ''}Base 100 · Jan 2023
        </span>
        <span className={`ix-card-level ${DIR_CLASS[item.trend_dir] || ''}`}>{fmt.index(item.current_index)}</span>
      </div>
      <RegionDots regions={item.regions} />
      <div className="ix-card-foot">
        <span className="ixp-anchors">{(item.top_lines || []).join(' · ')}</span>
        <span className="ix-card-foot-cta">{regionsText} →</span>
      </div>
    </Link>
  );
}

export default memo(ProductCard);
