import { useEffect, useMemo, useState } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';

// Index refresh status (Jira 209 S5).
//
// One row per index: how it gets new values (its route), what its last
// refresh did, and whether its data is late for its kind of series. The
// alerts at the top are the ones the daily check reports to the worker log
// and Sentry — there is no email for now, so this page is where staff see
// them. Super admin only: refresh errors can carry source URLs.

const FLAG_STYLE = {
  failing: { background: 'var(--accent2-dim)', color: 'var(--accent2)' },
  late: { background: 'var(--accent3-dim)', color: 'var(--accent3)' },
  no_route: { background: 'var(--neutral-bg)', color: 'var(--muted)' },
  never_attempted: { background: 'var(--neutral-bg)', color: 'var(--muted)' },
};

const FLAG_LABEL = {
  failing: 'failing', late: 'late', no_route: 'no update method', never_attempted: 'never run',
};

function Stat({ value, label, color, title }) {
  return (
    <div className="ca-metric" style={{ flex: '1 1 140px' }} title={title}>
      <div className="ca-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ca-metric-lbl">{label}</div>
    </div>
  );
}

function when(ts) {
  return ts ? new Date(ts).toLocaleString() : '—';
}

export default function RefreshStatus() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [search, setSearch] = useState('');
  const [only, setOnly] = useState('problems'); // problems | all | failing | late | no_route | never_attempted

  useEffect(() => {
    if (!user?.is_super_admin) return;
    api.get('/api/refresh/overview')
      .then(({ data: d }) => setData(d))
      .catch(e => setErr(formatApiError(e) || 'Could not load refresh status.'));
  }, [user]);

  const rows = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.indexes.filter(r => {
      if (only === 'problems' && !r.flags.some(f => f === 'failing' || f === 'late')) return false;
      if (!['problems', 'all'].includes(only) && !r.flags.includes(only)) return false;
      if (!q) return true;
      return [r.name, r.route, r.source].some(v => (v || '').toLowerCase().includes(q));
    });
  }, [data, search, only]);

  if (!user?.is_super_admin) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Access Denied</div>
        <p className="ca-subtitle">You need super admin privileges to view this page.</p>
      </div>
    );
  }
  if (err) {
    return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div></div>;
  }
  if (!data) {
    return (
      <div className="ca-page ca-fade-in">
        <h1 className="ca-h1">Refresh status</h1>
        {[0, 1, 2, 3, 4].map(i => <div key={i} className="ca-skeleton" style={{ height: 34, marginBottom: 8 }} />)}
      </div>
    );
  }

  const c = data.counts;

  return (
    <div className="ca-page ca-fade-in">
      <h1 className="ca-h1">Refresh status</h1>
      <p className="ca-subtitle" style={{ marginBottom: 0 }}>
        How each index gets new values, what its last refresh did, and whether its data is late.
        {' '}Last daily check: {data.last_check ? `${when(data.last_check.at)} (${data.last_check.summary})` : 'not run yet'}.
      </p>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '28px 0 16px' }}>
        <Stat value={c.indexes} label="Indexes" />
        <Stat value={c.failing} label="Failing" color={c.failing ? 'var(--accent2)' : undefined}
              title="A scheduled refresh failed twice or more in a row." />
        <Stat value={c.late} label="Late" color={c.late ? 'var(--accent3)' : undefined}
              title="Newest actual value is older than its frequency allows." />
        <Stat value={c.no_route} label="No update method"
              title="Nothing will ever refresh these indexes." />
        <Stat value={c.never_attempted} label="Scrapers never run" />
      </div>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div className="ca-card-title" style={{ marginBottom: 8 }}>
          {data.alerts.length ? `${data.alerts.length} alert${data.alerts.length === 1 ? '' : 's'}` : 'No alerts'}
        </div>
        {data.alerts.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Every scheduled refresh is working and no data is late.</div>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {data.alerts.map((a, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                <span className="ca-badge" style={{ ...(FLAG_STYLE[a.kind] || FLAG_STYLE.late), marginRight: 6 }}>
                  {a.kind === 'content_drop_late' ? 'content drop' : a.kind}
                </span>
                {a.message}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 240px' }}>
            <label className="ca-label" htmlFor="rs-search">Search</label>
            <input id="rs-search" className="ca-input" value={search} placeholder="index, route or source"
                   onChange={e => setSearch(e.target.value)} style={{ width: '100%' }} />
          </div>
          {[
            ['problems', 'Failing or late'],
            ['all', 'All'],
            ['failing', 'Failing'],
            ['late', 'Late'],
            ['no_route', 'No update method'],
            ['never_attempted', 'Never run'],
          ].map(([k, label]) => (
            <button key={k} className={`ca-btn ca-btn-sm ${only === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                    aria-pressed={only === k} onClick={() => setOnly(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="ca-card">
        <div className="ca-card-title" style={{ marginBottom: 12 }}>
          {rows.length} of {data.indexes.length} indexes
        </div>
        {rows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '28px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Nothing matches</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>Widen the search or pick another filter.</div>
          </div>
        ) : (
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">Index refresh routes, last refresh and freshness.</caption>
              <thead>
                <tr>
                  <th scope="col">Index</th>
                  <th scope="col">Update method</th>
                  <th scope="col">Newest data</th>
                  <th scope="col">Late after</th>
                  <th scope="col">Last success</th>
                  <th scope="col">Last error</th>
                  <th scope="col">Flags</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.commodity_id}>
                    <td>{r.name}</td>
                    <td title={r.detail}>
                      {r.route.replace('_', ' ')}
                      {r.source && <span style={{ color: 'var(--muted)' }}> · {r.source}</span>}
                    </td>
                    <td title={r.reason}>
                      {r.last_actual || '—'}
                      {r.months_old != null && <span style={{ color: 'var(--muted)' }}> ({r.months_old} mo)</span>}
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {r.stale_after_months} mo · {r.stale_rule}
                    </td>
                    <td style={{ fontSize: 11 }}>{when(r.last_success_at)}</td>
                    <td style={{ fontSize: 11, color: r.last_error ? 'var(--accent2)' : 'var(--muted)', maxWidth: 320 }}
                        title={r.last_error || ''}>
                      {r.last_error
                        ? `${r.consecutive_failures}× · ${r.last_error.split('\n')[0].slice(0, 120)}`
                        : '—'}
                    </td>
                    <td>
                      <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                        {r.flags.map(f => (
                          <span key={f} className="ca-badge" style={FLAG_STYLE[f]}>{FLAG_LABEL[f] || f}</span>
                        ))}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
