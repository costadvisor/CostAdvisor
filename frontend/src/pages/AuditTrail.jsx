import { useState, useEffect } from 'react';
import api from '../api';
import { useAuth } from '../AuthContext';

const EVENT_TYPES = [
  { value: '', label: 'All Events' },
  { value: 'create', label: 'Create' },
  { value: 'update', label: 'Update' },
  { value: 'delete', label: 'Delete' },
  { value: 'clone', label: 'Clone' },
  { value: 'invite', label: 'Invite' },
  { value: 'update_role', label: 'Role Change' },
  { value: 'remove', label: 'Remove Member' },
  { value: 'upload', label: 'Upload' },
  { value: 'override', label: 'Index Override' },
  { value: 'scrape', label: 'Scrape' },
  { value: 'invite_accepted', label: 'Invite Accepted' },
  { value: 'invite_revoked', label: 'Invite Revoked' },
];

const ENTITY_TYPES = [
  { value: '', label: 'All Entities' },
  { value: 'cost_model', label: 'Cost Model' },
  { value: 'formula_version', label: 'Formula Version' },
  { value: 'price_data', label: 'Price Data' },
  { value: 'actual_volume', label: 'Volume' },
  { value: 'supplier', label: 'Supplier' },
  { value: 'product', label: 'Product' },
  { value: 'index_override', label: 'Index Override' },
  { value: 'index_overrides', label: 'Index Override File' },
  { value: 'index_cell', label: 'Index Cell' },
  { value: 'index_bulk', label: 'Index Bulk' },
  { value: 'team_member', label: 'Team Member' },
  { value: 'team_index_source', label: 'Index Source' },
  { value: 'scenario', label: 'Scenario' },
];

function formatEventDetail(log) {
  const nv = log.new_value || {};
  const pv = log.previous_value || {};
  const { _impersonated_by, by, ...data } = nv;
  const label = (k) => k.replace(/_/g, ' ');

  switch (log.event_type) {
    case 'create':
      return nv.name ? `Created: ${nv.name}` : `Created ${log.entity_type}`;
    case 'update': {
      const parts = Object.entries(data).map(([k, v]) => {
        if (v && typeof v === 'object' && 'from' in v && 'to' in v)
          return `${label(k)}: ${v.from} → ${v.to}`;
        return `${label(k)}: ${v}`;
      });
      return parts.length ? parts.join(' · ') : 'Updated';
    }
    case 'delete':
      return pv.name ? `Deleted: ${pv.name}` : nv.name ? `Deleted: ${nv.name}` : `Deleted ${log.entity_type}`;
    case 'clone':
      return nv.cloned_from ? `Cloned from "${nv.cloned_from}"` : 'Cloned';
    case 'invite':
      return `Invited ${nv.email || '?'} as ${nv.role || 'member'}`;
    case 'invite_accepted':
      return `Accepted invite as ${nv.role || 'member'}`;
    case 'invite_revoked':
      return `Revoked invite for ${nv.email || '?'}${nv.role ? ` (${nv.role})` : ''}`;
    case 'update_role': {
      const from = pv.role || '?';
      const to = nv.role || '?';
      return `Role: ${from} → ${to}`;
    }
    case 'remove':
      return `Removed from team${pv.role ? ` (was ${pv.role})` : ''}`;
    case 'upload':
      return `Uploaded: ${nv.filename || nv.name || 'file'}`;
    case 'override':
      return nv.index || nv.commodity ? `Index override: ${nv.index || nv.commodity}` : 'Index value overridden';
    case 'scrape':
      return `Index source scraped${nv.url ? `: ${nv.url}` : ''}`;
    default: {
      const parts = Object.entries(data)
        .filter(([k]) => !k.startsWith('_'))
        .map(([k, v]) => {
          if (v && typeof v === 'object' && 'from' in v && 'to' in v)
            return `${label(k)}: ${v.from} → ${v.to}`;
          if (typeof v === 'object') return `${label(k)}: ${JSON.stringify(v)}`;
          return `${label(k)}: ${v}`;
        });
      return parts.length ? parts.join(' · ') : '—';
    }
  }
}

export default function AuditTrail() {
  const { activeTeamId } = useAuth();
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [entityType, setEntityType] = useState('');
  const [eventType, setEventType] = useState('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [membersMap, setMembersMap] = useState({});
  const PAGE_SIZE = 50;

  useEffect(() => {
    if (!activeTeamId) return;
    api.get(`/api/teams/${activeTeamId}/members`)
      .then(r => setMembersMap(Object.fromEntries(r.data.map(m => [m.user_id, m]))))
      .catch(() => {});
  }, [activeTeamId]);

  const fetchLogs = (reset = false) => {
    if (!activeTeamId) return;
    const p = reset ? 0 : page;
    if (reset) setPage(0);
    setLoading(true);
    const params = { team_id: activeTeamId, skip: p * PAGE_SIZE, limit: PAGE_SIZE };
    if (entityType) params.entity_type = entityType;
    if (eventType) params.event_type = eventType;
    if (search) params.search = search;
    api.get('/api/audit', { params })
      .then(res => {
        const rows = res.data;
        if (reset) setLogs(rows);
        else setLogs(prev => [...prev, ...rows]);
        setHasMore(rows.length === PAGE_SIZE);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchLogs(true); }, [activeTeamId, entityType, eventType, search]);

  const loadMore = () => setPage(p => p + 1);
  useEffect(() => { if (page > 0) fetchLogs(); }, [page]);

  const commitSearch = () => setSearch(searchInput.trim());
  const formatDate = (iso) => iso ? new Date(iso).toLocaleString() : '\u2014';

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1" style={{ marginBottom: 4 }}>Audit Trail</div>
      <p className="ca-subtitle">All changes made by your team.</p>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 220px' }}>
            <label className="ca-label">Search</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                className="ca-input"
                placeholder="User, event type, entity\u2026"
                value={searchInput}
                onChange={e => setSearchInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && commitSearch()}
                style={{ flex: 1 }}
              />
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={commitSearch}>Search</button>
              {search && <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => { setSearch(''); setSearchInput(''); }}>\u2715</button>}
            </div>
          </div>
          <div>
            <label className="ca-label">Event</label>
            <select className="ca-select" value={eventType} onChange={e => setEventType(e.target.value)}>
              {EVENT_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label className="ca-label">Entity</label>
            <select className="ca-select" value={entityType} onChange={e => setEntityType(e.target.value)}>
              {ENTITY_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {(search || eventType || entityType) && (
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => {
                setSearch(''); setSearchInput(''); setEventType(''); setEntityType('');
              }}>Clear All</button>
            </div>
          )}
        </div>
      </div>

      {loading && logs.length === 0 ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading...</div>
      ) : logs.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>
          No audit events found.
        </div>
      ) : (
        <div className="ca-card">
          <div className="ca-scroll-x">
            <table className="ca-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>User</th>
                  <th>Event</th>
                  <th>Entity</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {logs.map(log => {
                  const m = membersMap[log.user_id];
                  const name = m?.display_name ? `${m.display_name} (${log.user_email})` : (log.user_email || '\u2014');
                  const eventLabel = EVENT_TYPES.find(e => e.value === log.event_type)?.label || log.event_type;
                  const entityLabel = ENTITY_TYPES.find(e => e.value === log.entity_type)?.label || log.entity_type;
                  const isImpersonated = !!log.new_value?._impersonated_by;
                  return (
                    <tr key={log.id}>
                      <td style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {formatDate(log.timestamp)}
                      </td>
                      <td style={{ fontSize: 11 }}>
                        {isImpersonated && (
                          <span style={{
                            fontSize: 9, fontWeight: 700, background: 'var(--accent2-dim)', color: 'var(--accent2)',
                            borderRadius: 3, padding: '1px 4px', marginRight: 5, textTransform: 'uppercase', letterSpacing: 0.5,
                          }}>Impersonated</span>
                        )}
                        {name}
                      </td>
                      <td>
                        <span style={{
                          display: 'inline-block', padding: '1px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                          background: log.event_type === 'create' || log.event_type === 'invite' ? 'var(--success-bg)' : log.event_type === 'delete' || log.event_type === 'remove' ? 'var(--danger-bg)' : 'var(--info-bg)',
                          color: log.event_type === 'create' || log.event_type === 'invite' ? 'var(--accent)' : log.event_type === 'delete' || log.event_type === 'remove' ? 'var(--accent2)' : 'var(--accent3)',
                        }}>
                          {eventLabel}
                        </span>
                      </td>
                      <td style={{ fontSize: 11 }}>
                        <span style={{ color: 'var(--text)' }}>{entityLabel}</span>
                        <span style={{ color: 'var(--muted)', marginLeft: 6, fontSize: 9 }}>{log.entity_id?.slice(0, 8)}</span>
                      </td>
                      <td style={{ maxWidth: 300, fontSize: 11, color: 'var(--text-secondary)' }}>
                        {formatEventDetail(log)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <button className="ca-btn ca-btn-ghost" onClick={loadMore} disabled={loading}>
                {loading ? 'Loading...' : 'Load More'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
