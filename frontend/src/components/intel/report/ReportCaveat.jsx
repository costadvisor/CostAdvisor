import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { asOfLabel, lineHref } from './reportUtil';

/* The August-2026 banner that goes above a delivered report — app UI, so it
 * sits OUTSIDE .report-wrap and uses the theme's tokens.
 *
 *   <ReportCaveat caveat={report.caveat} currentLineId={line.id} />
 *
 * `caveat` is the `/api/intel/reports/{slug}` object:
 *   { as_of, text, old_line_name, split_into_n_lines, lines:[{id, line_key, name, visible}] }
 * The sentence is rebuilt from the structured fields so each current line can
 * be a link, by id (hidden lines stay plain text). When the fields are
 * missing, or a join points at a line that is not published (id null, name
 * "Product line not yet published"), the server's ready-made `text` is shown
 * instead: it already words the unpublished lines. */
export default function ReportCaveat({ caveat, currentLineId, linkTo = lineHref, className = '' }) {
  if (!caveat) return null;
  const lines = caveat.lines || [];
  const n = caveat.split_into_n_lines ?? lines.length;
  const when = asOfLabel(caveat.as_of) || 'August 2026';
  const anyUnpublished = lines.some((l) => l.id == null);
  const structured = caveat.old_line_name && Array.isArray(caveat.lines) && !(anyUnpublished && caveat.text);

  const lineItem = (l) => {
    const isCurrent = currentLineId != null && l.id != null && String(l.id) === String(currentLineId);
    if (isCurrent) {
      return <span className="ixr-caveat-current">{l.name} <span className="ixr-caveat-this">(this line)</span></span>;
    }
    if (l.visible === false || l.id == null) {
      return <span title="Not published in the catalogue">{l.name}</span>;
    }
    return <Link to={linkTo(l.id)}>{l.name}</Link>;
  };

  let body;
  if (!structured) {
    body = <p>{caveat.text}</p>;
  } else {
    let now;
    if (n >= 2) {
      now = (
        <>
          That line is now split into {n} product lines:{' '}
          {lines.map((l, i) => (
            <Fragment key={l.id ?? `unpublished-${i}`}>
              {i > 0 && (i === lines.length - 1 ? ' and ' : ', ')}
              {lineItem(l)}
            </Fragment>
          ))}.
        </>
      );
    } else if (n === 1 && lines[0]) {
      now = <>That line is now called {lineItem(lines[0])}.</>;
    } else {
      now = <>No current product line maps to it.</>;
    }
    body = (
      <p>
        <strong>Written in {when} for the product line then called {caveat.old_line_name}.</strong>{' '}
        {now}{' '}
        The report has not been regenerated since, and the industry names in it are the {when} vocabulary.
      </p>
    );
  }

  return (
    <div className={`ixr-caveat ${className}`} role="note" aria-label="About this report">
      <span className="ixr-caveat-icon" aria-hidden>i</span>
      <div className="ixr-caveat-body">{body}</div>
    </div>
  );
}
