import { MONTHS } from '../../../components/intel/fmt';
import { isOverdue } from './OppShared';

/* Gantt view of a list of actions: one bar per action from start to due date,
 * filled to its % complete, coloured by status, with a "today" line. The time
 * window is shared by every block on the tab (`window` from actionWindow), so
 * bars are comparable across opportunities. */

const DAY = 86400000;
const toTime = (iso) => (iso ? Date.parse(`${iso}T00:00:00Z`) : null);

export function actionWindow(actions, today) {
  const times = [];
  actions.forEach((a) => {
    if (a.start_date) times.push(toTime(a.start_date));
    if (a.due_date) times.push(toTime(a.due_date));
  });
  if (today) times.push(toTime(today));
  if (!times.length) return null;
  const lo = new Date(Math.min(...times));
  const hi = new Date(Math.max(...times));
  const start = Date.UTC(lo.getUTCFullYear(), lo.getUTCMonth(), 1);
  const end = Date.UTC(hi.getUTCFullYear(), hi.getUTCMonth() + 1, 1);
  const months = [];
  for (let y = lo.getUTCFullYear(), m = lo.getUTCMonth(); Date.UTC(y, m, 1) < end; m += 1) {
    if (m > 11) { m = 0; y += 1; }
    months.push({ t: Date.UTC(y, m, 1), label: MONTHS[m], year: y, m });
  }
  return { start, end, span: end - start, months, today: toTime(today) };
}

const pos = (w, t) => `${(100 * (t - w.start)) / w.span}%`;

export function TimelineAxis({ win }) {
  if (!win) return null;
  return (
    <div className="so-tl-row so-tl-axisrow" aria-hidden>
      <div className="so-tl-label" />
      <div className="so-tl-axis">
        {win.months.map((mo, i) => (
          <span key={mo.t} className="so-tl-tick" style={{ left: pos(win, mo.t) }}>
            {mo.label}{(i === 0 || mo.m === 0) ? ` ${mo.year}` : ''}
          </span>
        ))}
      </div>
      <div className="so-tl-pct" />
      <div className="so-tl-ops" />
    </div>
  );
}

export default function ActionTimeline({ actions, win, today, onEdit, onDelete, readOnly }) {
  return (
    <div className="so-tl" role="list">
      <TimelineAxis win={win} />
      {actions.map((a) => {
        const s = toTime(a.start_date);
        const d = toTime(a.due_date);
        const overdue = isOverdue(a, today);
        let bar = null;
        if (win && s != null && d != null) {
          const width = Math.max(((d - s + DAY) / win.span) * 100, 0.8);
          bar = (
            <span className={`so-tl-bar st-${a.status.replace(/\s/g, '').toLowerCase()}${overdue ? ' is-overdue' : ''}`}
              style={{ left: pos(win, s), width: `${width}%` }}
              title={`${a.start_date} → ${a.due_date} · ${a.status} · ${a.pct_complete}%`}>
              <span className="so-tl-fill" style={{ width: `${a.pct_complete || 0}%` }} />
            </span>
          );
        } else if (win && (d != null || s != null)) {
          const t = d ?? s;
          bar = (
            <span className={`so-tl-mark${overdue ? ' is-overdue' : ''}`} style={{ left: pos(win, t) }}
              title={d != null ? `Due ${a.due_date} (no start date)` : `Starts ${a.start_date} (no due date)`} />
          );
        }
        return (
          <div key={a.id} className={`so-tl-row${overdue ? ' is-overdue' : ''}`} role="listitem">
            <div className="so-tl-label">
              <span className="so-tl-title" title={a.title}>{a.title}</span>
              <span className="so-tl-sub">
                {a.assignee?.name || 'Unassigned'}
                {overdue && <span className="so-overdue-tag">Overdue</span>}
              </span>
            </div>
            <div className="so-tl-track">
              {win?.today != null && <span className="so-tl-today" style={{ left: pos(win, win.today) }} />}
              {bar || <span className="so-tl-nodates">No dates set</span>}
            </div>
            <div className="so-tl-pct">{a.pct_complete ?? 0}%</div>
            <div className="so-tl-ops">
              {!readOnly && (
                <>
                  <button type="button" className="ca-btn-link so-small-link" onClick={() => onEdit(a)}>Edit</button>
                  <button type="button" className="ca-btn-link so-small-link so-danger-link" onClick={() => onDelete(a)}>Delete</button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
