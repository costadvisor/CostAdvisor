import { STATUSES, STATUS_DEF } from './status';

/* A category-status split: a thin three-segment bar plus its counts.
 *
 *   <StatusMix counts={{ servable: 2, partial: 11, build: 4 }} highlight="servable" />
 *
 * Segment widths are proportional to the counts; a zero count draws no segment
 * but keeps its number in the legend (a zero is information here). */
export default function StatusMix({ counts = {}, highlight, size }) {
  const total = STATUSES.reduce((s, k) => s + (Number(counts[k]) || 0), 0);
  const label = STATUSES.map((k) => `${Number(counts[k]) || 0} ${k}`).join(', ');
  return (
    <div className={`ixc-mix${size === 'lg' ? ' lg' : ''}${highlight ? ' has-highlight' : ''}`}>
      <div className="ixc-mix-bar" role="img" aria-label={`Categories: ${label}`}>
        {total > 0 && STATUSES.map((k) => {
          const n = Number(counts[k]) || 0;
          if (!n) return null;
          return (
            <span key={k} className={`ixc-seg s-${k}${highlight === k ? ' is-on' : ''}`}
              style={{ flexGrow: n }} />
          );
        })}
      </div>
      <div className="ixc-mix-legend" aria-hidden>
        {STATUSES.map((k) => {
          const n = Number(counts[k]) || 0;
          return (
            <span key={k} className={`ixc-mix-item s-${k}${n ? '' : ' is-zero'}${highlight === k ? ' is-on' : ''}`}
              title={STATUS_DEF[k]}>
              <i className="ixc-dot" />
              <b>{n}</b> {k}
            </span>
          );
        })}
      </div>
    </div>
  );
}
