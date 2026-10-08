import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import {
  StatusBadge, fmt, lineFlags, SUBFAMILY_LABEL, UNPUBLISHED_LINE,
} from '../../../components/intel';
import {
  lineHref, reportHref, strategyHref, productHref, monthYear,
} from './IntelUtil';

/* Top card of the tab: the full name, identity pills (base, CAS, class, form),
 * the reference grade when the source has one, group members, and on the
 * right the product line with its market report(s) and playbook(s). */

function basePill(basePeriod) {
  const label = monthYear(basePeriod);
  return label ? `${label} = 100` : null;
}

function GroupMembers({ product }) {
  const members = product.members || [];
  if (!product.is_group || !members.length) return null;
  return (
    <div className="ixi-id-row">
      <span className="ixi-id-label">Products in this group</span>
      <span className="ixi-id-members">
        {members.map((m, i) => (
          <Fragment key={m.pid}>
            {i > 0 && <span className="ixi-muted" aria-hidden> · </span>}
            {m.has_formula ? (
              <Link to={productHref(m.pid)} title={m.name || m.pid}>
                {m.pid}{m.region ? <span className="ixi-muted"> ({m.region})</span> : null}
              </Link>
            ) : (
              <span className="ixi-muted" title="Named by the source; no cost formula of its own">
                {m.pid}{m.region ? ` (${m.region})` : ''}
              </span>
            )}
          </Fragment>
        ))}
      </span>
    </div>
  );
}

function LineBox({ product }) {
  const line = product.line;
  const sub = product.subfamily;
  const reports = product.reports || [];
  const playbooks = product.playbooks || [];
  const flags = lineFlags(line?.flags);

  return (
    <aside className="ixi-linebox" aria-label="Product line">
      <div className="ixi-linebox-label">Product line</div>
      {line ? (
        <>
          <div className="ixi-linebox-name">
            <Link to={lineHref(line.id)}>{line.name}<span aria-hidden> →</span></Link>
            {flags.map((f) => <StatusBadge key={f.code} status={f.label} tone="warn" />)}
          </div>
          <dl className="ixi-linebox-facts">
            {sub?.name && (
              <>
                <dt>{SUBFAMILY_LABEL}</dt>
                <dd>{sub.name}</dd>
              </>
            )}
            {line.platform && (
              <>
                <dt>Platform</dt>
                <dd className="ixi-mono">{line.platform}</dd>
              </>
            )}
          </dl>
        </>
      ) : (
        <div className="ixi-muted ixi-small">{product.line_label || UNPUBLISHED_LINE}</div>
      )}

      <div className="ixi-linebox-label">{reports.length > 1 ? 'Market reports' : 'Market report'}</div>
      {reports.length === 0 ? (
        <div className="ixi-muted ixi-small">No market report for this line yet.</div>
      ) : (
        <ul className="ixi-linebox-list">
          {reports.map((r) => {
            const when = monthYear(r.as_of);
            const bits = [
              when,
              r.old_line_name && r.old_line_name !== r.name ? `written for “${r.old_line_name}”` : null,
              r.lines_served > 1 ? `covers ${fmt.plural(r.lines_served, 'line')}` : null,
            ].filter(Boolean);
            return (
              <li key={r.slug}>
                {line
                  ? <Link to={reportHref(line.id, r.slug)}>{r.name}<span aria-hidden> →</span></Link>
                  : <span className="ixi-strong">{r.name}</span>}
                {bits.length > 0 && <span className="ixi-linebox-sub">{bits.join(' · ')}</span>}
              </li>
            );
          })}
        </ul>
      )}

      {(playbooks.length > 0 || reports.length > 0) && (
        <>
          <div className="ixi-linebox-label">{playbooks.length > 1 ? 'Strategy playbooks' : 'Strategy playbook'}</div>
          {playbooks.length === 0 ? (
            <div className="ixi-muted ixi-small">No playbook for this report.</div>
          ) : (
            <ul className="ixi-linebox-list">
              {playbooks.map((pb) => (
                <li key={pb.slug}>
                  <Link to={strategyHref(pb.slug)}>{pb.name || pb.slug}<span aria-hidden> →</span></Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}

export default function IntelIdentity({ product }) {
  const p = product;
  const base = p.has_formula === false ? null : basePill(p.base_period);
  const cls = [p.family?.name, p.line?.name || p.line_label].filter(Boolean).join(' · ');

  return (
    <section className="ix-panel ixi-identity" aria-label="Product identity">
      <div className="ixi-id-main">
        <h2 className="ixi-id-name">{p.full_name || p.name || p.pid}</h2>
        <div className="ixi-id-pills">
          {base && <span className="ix-chip"><strong>Base</strong> {base}</span>}
          {p.cas && <span className="ix-chip"><strong>CAS</strong> {p.cas}</span>}
          {cls && <span className="ix-chip ixi-chip-wrap"><strong>Class</strong> {cls}</span>}
          {p.form && <span className="ix-chip"><strong>Form</strong> {p.form}</span>}
        </div>

        {/* Most cards have no reference grade: the panel is hidden then. */}
        {p.reference_grade && (
          <div className="ixi-id-grade">
            <div className="ixi-id-label">Reference grade</div>
            <p className="ixi-id-grade-text">{p.reference_grade}</p>
          </div>
        )}

        <GroupMembers product={p} />
      </div>
      <LineBox product={p} />
    </section>
  );
}
