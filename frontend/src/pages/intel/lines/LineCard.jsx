import { Link } from 'react-router-dom';
import { ReportBadge, PendingBadge } from './LineBadges';
import { lineHref, count, listedOf } from './lineUtil';

/* One product line in the Product lines grid: name, products tracked,
 * industries, top maker names (never a count of makers), report badge and
 * the "Supplier validation pending" flag. */

const MAX_FN = 2;
const MAX_IND = 2;
const MAX_SUP = 3;

export function MoreChip({ items = [], shown }) {
  const extra = items.length - shown;
  if (extra <= 0) return null;
  return (
    <span className="ix-tag plain ixl-more" title={items.slice(shown).join(', ')}>+{extra}</span>
  );
}

export default function LineCard({ line, fromSearch }) {
  const fns = line.functions || [];
  const inds = line.industries || [];
  const sups = (line.top_suppliers || []).slice(0, MAX_SUP);
  const generic = line.generic_suppliers || [];
  const listed = listedOf(line);

  return (
    <Link to={lineHref(line.id)} state={{ fromSearch }} className={`ix-card ixl-card${listed ? '' : ' is-empty'}`}>
      <div className="ixl-card-top">
        <div className="ixl-card-eyebrow">
          {listed ? `${count(listed, 'product')} tracked` : 'No tracked product yet'}
        </div>
        <div className="ixl-card-badges">
          <ReportBadge reports={line.reports} />
        </div>
      </div>
      <div className="ixl-card-name">{line.name}</div>
      <PendingBadge show={line.pending} flags={line.flags} />
      {(fns.length > 0 || inds.length > 0) && (
        <div className="ixl-chips">
          {fns.slice(0, MAX_FN).map((f) => <span key={`f-${f}`} className="ix-tag fn" title={f}>{f}</span>)}
          {inds.slice(0, MAX_IND).map((i) => <span key={`i-${i}`} className="ix-tag ixl-ind" title={i}>{i}</span>)}
          <MoreChip items={inds} shown={MAX_IND} />
        </div>
      )}
      <div className="ixl-card-foot">
        {sups.length > 0 ? (
          <span className="ixl-card-sups" title={`Top makers: ${(line.top_suppliers || []).map((s) => s.name).join(', ')}`}>
            <span className="ixl-card-sups-names">{sups.map((s) => s.name).join(', ')}</span>
          </span>
        ) : (
          <span className="ixl-muted">
            {generic.length ? 'Unnamed producers only' : 'No counted maker yet'}
          </span>
        )}
      </div>
    </Link>
  );
}
