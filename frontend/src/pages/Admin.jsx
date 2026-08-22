import { useState, useEffect, useRef, Fragment, useMemo } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useConfirm, useAlert } from '../components/ConfirmDialog';
import Tooltip from '../components/Tooltip';
import { invalidateRegions } from '../components/RegionSelect';

export default function Admin() {
  const { user, refreshUser } = useAuth();
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [users, setUsers] = useState([]);
  const [teams, setTeams] = useState([]);
  const [plans, setPlans] = useState([]);
  const [platformRoles, setPlatformRoles] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [accessRequests, setAccessRequests] = useState([]);
  const [demoRequests, setDemoRequests] = useState([]);
  const [regions, setRegions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('users');
  const [impersonating, setImpersonating] = useState(false);
  const [search, setSearch] = useState('');
  const [showDeleted, setShowDeleted] = useState(false);
  const [auditEventType, setAuditEventType] = useState('');
  const [selectedUser, setSelectedUser] = useState(null);
  const [targetTeamId, setTargetTeamId] = useState(null);
  const [roleFilter, setRoleFilter] = useState('all');

  const fetchData = () => {
    setLoading(true);
    Promise.all([
      api.get('/api/admin/users', { params: { include_deleted: showDeleted } }),
      api.get('/api/admin/teams'),
      api.get('/api/admin/access-requests'),
      api.get('/api/settings/plans'),
      api.get('/api/settings/roles'),
    ])
      .then(([uRes, tRes, rRes, pRes, rolesRes]) => {
        setUsers(uRes.data);
        setTeams(tRes.data);
        setAccessRequests(rRes.data);
        setPlans(pRes.data);
        setPlatformRoles(rolesRes.data);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  const fetchAuditLogs = () => {
    setLoading(true);
    api.get('/api/admin/audit-logs', {
      params: { limit: 200, event_type: auditEventType || undefined },
    })
      .then(r => setAuditLogs(r.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  const fetchRequests = () => {
    setLoading(true);
    Promise.all([
      api.get('/api/admin/access-requests'),
      api.get('/api/admin/demo-requests'),
    ])
      .then(([aRes, dRes]) => { setAccessRequests(aRes.data); setDemoRequests(dRes.data); })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  const fetchRegions = () => {
    setLoading(true);
    api.get('/api/regions')
      .then(r => { setRegions(r.data); invalidateRegions(); })  // refresh the shared picker cache too
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (tab === 'audit') fetchAuditLogs();
    else if (tab === 'requests') fetchRequests();
    else if (tab === 'regions') fetchRegions();
    else if (tab !== 'settings') fetchData();
  }, [tab, showDeleted]);

  useEffect(() => {
    setImpersonating(document.cookie.split(';').some(c => c.trim().startsWith('ca_impersonating=')));
  }, []);

  const setPlatformRole = async (userId, roleName, grant) => {
    if (roleName === 'SuperAdmin') {
      const targetUser = users.find(u => u.id === userId);
      const action = grant ? 'Grant SuperAdmin' : 'Revoke SuperAdmin';
      const ok = await confirm({
        title: `${action} — ${targetUser?.display_name || targetUser?.email}?`,
        message: grant
          ? 'This user will gain full super-admin access to all platform data.'
          : 'This user will lose super-admin privileges immediately.',
        confirmLabel: action,
        danger: !grant,
      });
      if (!ok) return;
      await api.put(`/api/admin/users/${userId}`, { is_super_admin: grant });
      fetchData();
    }
  };

  const deleteUser = async (u) => {
    const ok = await confirm({
      title: `Delete ${u.email}?`,
      message: 'They will be soft-deleted and can be restored.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    await api.delete(`/api/admin/users/${u.id}`);
    fetchData();
  };

  const restoreUser = async (u) => {
    await api.post(`/api/admin/users/${u.id}/restore`);
    fetchData();
  };

  const impersonate = async (userId) => {
    const targetUser = users.find(u => u.id === userId);
    const ok = await confirm({
      title: `Impersonate ${targetUser?.display_name || targetUser?.email}?`,
      message: 'You will be logged in as this user. All actions will be performed on their behalf until you stop impersonation.',
      confirmLabel: 'Impersonate',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.post(`/api/admin/impersonate/${userId}`);
      setImpersonating(true);
      await refreshUser();
      window.location.href = '/';
    } catch (err) {
      showAlert({ title: 'Impersonation failed', message: formatApiError(err) });
    }
  };

  const filtered = users.filter(u => {
    if (roleFilter === 'super_admin' && !u.is_super_admin) return false;
    if (roleFilter === 'regular' && u.is_super_admin) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      u.email?.toLowerCase().includes(q) ||
      u.display_name?.toLowerCase().includes(q) ||
      u.teams?.some(t => t.team_name?.toLowerCase().includes(q))
    );
  });

  const handleNavigateToTeam = (teamId) => {
    setSelectedUser(null);
    setTab('teams');
    setTargetTeamId(teamId);
  };

  if (!user?.is_super_admin && !impersonating) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Access Denied</div>
        <p className="ca-subtitle">You need super admin privileges to view this page.</p>
      </div>
    );
  }

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Admin Console</div>
      </div>
      <p className="ca-subtitle">Manage users, teams, and platform activity.</p>

      <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { key: 'users', label: `Users (${users.length})` },
          { key: 'teams', label: `Teams (${teams.length})` },
          { key: 'audit', label: 'Audit Log' },
          {
            key: 'requests',
            label: (() => {
              const pending = accessRequests.filter(r => r.status === 'pending').length
                + demoRequests.filter(r => r.status === 'pending').length;
              return pending > 0 ? `Requests (${pending})` : 'Requests';
            })(),
          },
          { key: 'regions', label: regions.length ? `Regions (${regions.length})` : 'Regions' },
          { key: 'settings', label: 'Settings' },
        ].map(t => (
          <button
            key={t.key}
            className={`ca-btn ${tab === t.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
      ) : tab === 'users' ? (
        <UsersTab
          users={filtered}
          allTeams={teams}
          search={search}
          setSearch={setSearch}
          showDeleted={showDeleted}
          setShowDeleted={setShowDeleted}
          currentUser={user}
          platformRoles={platformRoles}
          onSetPlatformRole={setPlatformRole}
          onImpersonate={impersonate}
          onDelete={deleteUser}
          onRestore={restoreUser}
          onChanged={fetchData}
          isImpersonating={impersonating}
          roleFilter={roleFilter}
          setRoleFilter={setRoleFilter}
          onSelectUser={setSelectedUser}
        />
      ) : tab === 'teams' ? (
        <TeamsTab
          teams={teams}
          allUsers={users}
          plans={plans}
          onRefresh={fetchData}
          targetTeamId={targetTeamId}
          onTeamExpanded={() => setTargetTeamId(null)}
        />
      ) : tab === 'requests' ? (
        <RequestsTabWrapper
          accessRequests={accessRequests}
          demoRequests={demoRequests}
          currentUser={user}
          onRefresh={fetchRequests}
        />
      ) : tab === 'regions' ? (
        <RegionsTab regions={regions} onRefresh={fetchRegions} />
      ) : tab === 'settings' ? (
        <AdminSettingsTab users={users} />
      ) : (
        <AuditTab
          logs={auditLogs}
          eventType={auditEventType}
          setEventType={setAuditEventType}
          onRefresh={fetchAuditLogs}
          users={users}
        />
      )}

      {selectedUser && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 140 }}
            onClick={() => setSelectedUser(null)}
          />
          <UserDetailPanel
            user={selectedUser}
            onClose={() => setSelectedUser(null)}
            onNavigateToTeam={handleNavigateToTeam}
            platformRoles={platformRoles}
            onSetPlatformRole={(id, name, grant) => { setPlatformRole(id, name, grant); setSelectedUser(null); }}
            onImpersonate={(id) => { impersonate(id); setSelectedUser(null); }}
            onDelete={(u) => { deleteUser(u); setSelectedUser(null); }}
            onRestore={(u) => { restoreUser(u); setSelectedUser(null); }}
            currentUser={user}
            isImpersonating={impersonating}
            onRefresh={fetchData}
          />
        </>
      )}
    </div>
  );
}


function PlatformRoleChips({ user }) {
  const chips = [{ name: 'User', color: 'var(--muted)', bg: 'var(--neutral-bg)' }];
  if (user.is_super_admin) chips.push({ name: 'SuperAdmin', color: 'var(--accent)', bg: 'var(--success-bg)' });
  (user.platform_role_names || []).forEach(name => {
    if (name !== 'SuperAdmin') chips.push({ name, color: 'var(--accent3)', bg: 'var(--surface2)' });
  });
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {chips.map(c => (
        <span key={c.name} style={{
          display: 'inline-block', padding: '2px 8px', borderRadius: 4,
          fontSize: 10, fontWeight: 600, background: c.bg, color: c.color,
        }}>{c.name}</span>
      ))}
    </div>
  );
}

function EditRoleModal({ user, platformRoles, onSetRole, onClose, onRefresh }) {
  const [assignedRoleIds, setAssignedRoleIds] = useState(new Set());
  const [loadingRoles, setLoadingRoles] = useState(true);

  useEffect(() => {
    api.get(`/api/admin/users/${user.id}/platform-roles`)
      .then(r => setAssignedRoleIds(new Set(r.data.map(x => x.id))))
      .catch(() => {})
      .finally(() => setLoadingRoles(false));
  }, [user.id]);

  const handleToggle = async (role, grant) => {
    if (role.name === 'SuperAdmin') {
      await onSetRole(user.id, 'SuperAdmin', grant);
      onRefresh?.();
      return;
    }
    try {
      if (grant) {
        await api.post(`/api/admin/users/${user.id}/platform-roles`, { role_id: role.id });
      } else {
        await api.delete(`/api/admin/users/${user.id}/platform-roles/${role.id}`);
      }
      setAssignedRoleIds(prev => {
        const next = new Set(prev);
        grant ? next.add(role.id) : next.delete(role.id);
        return next;
      });
      onRefresh?.();
    } catch (e) {
      console.error('Role toggle failed', e);
    }
  };

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 400 }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">Edit Platform Roles</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>User</div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{user.display_name || user.email}</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>{user.email}</div>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 10 }}>Platform Roles</div>
            {loadingRoles ? (
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, cursor: 'not-allowed', opacity: 0.6 }}>
                  <input type="checkbox" checked disabled />
                  <span>User</span>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>(default — all users)</span>
                </label>
                {(platformRoles || []).filter(r => r.name !== 'User').map(r => (
                  <label key={r.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 13, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      style={{ marginTop: 2 }}
                      checked={r.name === 'SuperAdmin' ? !!user.is_super_admin : assignedRoleIds.has(r.id)}
                      onChange={e => handleToggle(r, e.target.checked)}
                    />
                    <span>
                      {r.name}
                      {r.description && (
                        <span style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{r.description}</span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="ca-modal-footer">
          <button className="ca-btn ca-btn-primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}

function UsersTab({ users, allTeams, search, setSearch, showDeleted, setShowDeleted,
  currentUser, platformRoles, onSetPlatformRole, onImpersonate, onDelete, onRestore, onChanged,
  isImpersonating, roleFilter, setRoleFilter, onSelectUser }) {
  const [editRoleUserId, setEditRoleUserId] = useState(null);

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="ca-input"
          placeholder="Search by name, email or team…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <select
          className="ca-input"
          value={roleFilter}
          onChange={e => setRoleFilter(e.target.value)}
          style={{ width: 'auto' }}
        >
          <option value="all">All users</option>
          <option value="super_admin">Super Admin</option>
          <option value="regular">Regular User</option>
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showDeleted}
            onChange={e => setShowDeleted(e.target.checked)}
          />
          Show deleted
        </label>
      </div>

      {editRoleUserId && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 15 }} onClick={() => setEditRoleUserId(null)} />
      )}

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 300 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Email</th>
                <th>Roles</th>
                <th className="center">Last Login</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 && (
                <tr><td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No users found.</td></tr>
              )}
              {users.map(u => (
                <tr
                  key={u.id}
                  style={{ opacity: u.deleted_at ? 0.5 : 1, cursor: 'pointer' }}
                  onClick={() => onSelectUser(u)}
                >
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {u.avatar_url && <img src={u.avatar_url} alt="" style={{ width: 24, height: 24, borderRadius: '50%' }} />}
                      <span style={{ fontWeight: 600 }}>{u.display_name || 'No name'}</span>
                      {u.deleted_at && <span style={{ fontSize: 9, color: 'var(--accent2)', fontWeight: 700 }}>DELETED</span>}
                    </div>
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{u.email}</td>
                  <td><PlatformRoleChips user={u} /></td>
                  <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="center">
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'center', alignItems: 'center' }}>
                      {!u.deleted_at && u.id !== currentUser?.id && (
                        <>
                          <Tooltip text="Edit platform roles">
                            <button
                              className="ca-btn ca-btn-ghost ca-btn-sm"
                              style={{ padding: '4px 8px', fontSize: 11 }}
                              onClick={e => { e.stopPropagation(); setEditRoleUserId(u.id); }}
                            >Edit Role</button>
                          </Tooltip>
                          {editRoleUserId === u.id && (
                            <EditRoleModal
                              user={u}
                              platformRoles={platformRoles}
                              onSetRole={onSetPlatformRole}
                              onClose={() => setEditRoleUserId(null)}
                              onRefresh={onChanged}
                            />
                          )}
                          <Tooltip text={u.is_super_admin ? 'Cannot impersonate a super admin' : isImpersonating ? 'Stop current impersonation first' : 'Impersonate'}>
                            <button
                              className="ca-btn ca-btn-ghost ca-btn-sm"
                              style={{ padding: '4px 8px', fontSize: 13, lineHeight: 1, color: 'var(--accent3)', borderColor: 'var(--accent3)', opacity: (isImpersonating || u.is_super_admin) ? 0.4 : 1 }}
                              onClick={e => { e.stopPropagation(); onImpersonate(u.id); }}
                              disabled={isImpersonating || u.is_super_admin}
                            >⇒</button>
                          </Tooltip>
                          <Tooltip text="Delete">
                            <button
                              className="ca-btn ca-btn-ghost ca-btn-sm"
                              style={{ padding: '4px 8px', fontSize: 13, lineHeight: 1, color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                              onClick={e => { e.stopPropagation(); onDelete(u); }}
                            >✕</button>
                          </Tooltip>
                        </>
                      )}
                      {u.deleted_at && (
                        <Tooltip text="Restore">
                          <button
                            className="ca-btn ca-btn-ghost ca-btn-sm"
                            style={{ padding: '4px 8px', fontSize: 13, lineHeight: 1, color: 'var(--accent)', borderColor: 'var(--accent)' }}
                            onClick={e => { e.stopPropagation(); onRestore(u); }}
                          >↺</button>
                        </Tooltip>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}


function TeamsTab({ teams, allUsers, plans, onRefresh, targetTeamId, onTeamExpanded }) {
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState(null);
  const [teamSearch, setTeamSearch] = useState('');
  // Available roles per expanded team: { [teamId]: [{id, name}] }
  const [teamRoles, setTeamRoles] = useState({});

  useEffect(() => {
    if (targetTeamId) {
      setExpanded(targetTeamId);
      setError(null);
      onTeamExpanded?.();
    }
  }, [targetTeamId]);

  const [pendingRemovals, setPendingRemovals] = useState(new Set());
  const [pendingAdditions, setPendingAdditions] = useState([]);

  const hasPending = pendingRemovals.size > 0 || pendingAdditions.length > 0;

  const clearPending = () => {
    setPendingRemovals(new Set());
    setPendingAdditions([]);
  };

  const handleExpand = (teamId) => {
    if (expanded !== teamId) {
      clearPending();
      // Fetch available roles for this team
      api.get(`/api/teams/${teamId}/roles`).then(r => {
        setTeamRoles(prev => ({ ...prev, [teamId]: r.data }));
      }).catch(() => {});
    }
    setExpanded(prev => prev === teamId ? null : teamId);
    setError(null);
  };

  const stageRemove = (userId) => setPendingRemovals(p => new Set([...p, userId]));
  const unstageRemove = (userId) => setPendingRemovals(p => { const n = new Set(p); n.delete(userId); return n; });

  const stageAdd = (user) => {
    if (!pendingAdditions.find(a => a.userId === user.id)) {
      setPendingAdditions(p => [...p, { userId: user.id, email: user.email, displayName: user.display_name }]);
    }
  };

  const memberLabel = (userId) => {
    const u = allUsers.find(u => u.id === userId);
    if (!u) return userId?.slice(0, 12) + '…';
    return u.display_name ? `${u.display_name} (${u.email})` : u.email;
  };

  const addMemberRole = async (teamId, userId, roleId) => {
    try {
      await api.post(`/api/teams/${teamId}/member-roles`, { user_id: userId, role_id: roleId });
      onRefresh();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to add role');
    }
  };

  const removeMemberRole = async (teamId, userId, roleId) => {
    try {
      await api.delete(`/api/teams/${teamId}/member-roles`, { data: { user_id: userId, role_id: roleId } });
      onRefresh();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to remove role');
    }
  };

  const handleSave = async (team) => {
    const lines = [];
    for (const uid of pendingRemovals) lines.push(`· Remove ${memberLabel(uid)}`);
    for (const { userId, email, displayName } of pendingAdditions) {
      lines.push(`· Add ${displayName ? `${displayName} (${email})` : email}`);
    }

    const ok = await confirm({
      title: `Save changes to "${team.name}"?`,
      message: lines.join('\n'),
      confirmLabel: 'Save changes',
    });
    if (!ok) return;

    const errors = [];
    for (const uid of pendingRemovals) {
      try { await api.delete(`/api/admin/teams/${team.id}/members/${uid}`); }
      catch (e) { errors.push(e.response?.data?.detail || 'Remove failed'); }
    }
    for (const { userId } of pendingAdditions) {
      try { await api.post(`/api/admin/users/${userId}/add-team`, { team_id: team.id, role: 'member' }); }
      catch (e) { errors.push(e.response?.data?.detail || 'Add failed'); }
    }

    clearPending();
    if (errors.length) setError(errors.join('; '));
    onRefresh();
  };

  const deleteTeam = async (t) => {
    const ok = await confirm({
      title: `Delete team "${t.name}"?`,
      message: 'All team data will be permanently lost. This cannot be undone.',
      confirmLabel: 'Delete team',
      danger: true,
    });
    if (!ok) return;
    try { await api.delete(`/api/admin/teams/${t.id}`); onRefresh(); }
    catch (e) { setError(e.response?.data?.detail || e.message || 'Error'); }
  };

  const handlePlanChange = async (teamId, planId) => {
    try {
      await api.put(`/api/admin/teams/${teamId}/plan`, { plan_id: planId || null });
      onRefresh();
    } catch (e) {
      showAlert({ title: 'Error', message: e.response?.data?.detail || e.message || 'Plan update failed' });
    }
  };

  const filteredTeams = teamSearch
    ? teams.filter(t => {
        const q = teamSearch.toLowerCase();
        return t.name.toLowerCase().includes(q) ||
               t.members.some(m => m.email?.toLowerCase().includes(q));
      })
    : teams;

  return (
    <>
      {error && (
        <div style={{
          padding: '10px 16px', marginBottom: 12, borderRadius: 8, fontSize: 12,
          background: 'var(--accent2-dim)', color: 'var(--accent2)',
          border: '1px solid var(--danger-bg-strong)', display: 'flex', justifyContent: 'space-between',
        }}>
          {error}
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent2)', fontWeight: 700 }}>×</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input
          className="ca-input"
          placeholder="Search by team name or member email…"
          value={teamSearch}
          onChange={e => setTeamSearch(e.target.value)}
          style={{ maxWidth: 340 }}
        />
      </div>

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 300 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>Team</th>
                <th className="center">Members</th>
                <th className="center">Plan</th>
                <th className="center">Created</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredTeams.length === 0 && (
                <tr><td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No teams found.</td></tr>
              )}
              {filteredTeams.map(t => {
                const isOpen = expanded === t.id;
                const memberIds = new Set(t.members.map(m => m.user_id));
                const addable = allUsers.filter(u => !memberIds.has(u.id) && !u.deleted_at && !pendingAdditions.find(a => a.userId === u.id));

                return (
                  <Fragment key={t.id}>
                    <tr>
                      <td style={{ fontWeight: 600, fontSize: 13 }}>{t.name}</td>
                      <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>
                        {t.member_count} member{t.member_count !== 1 ? 's' : ''}
                      </td>
                      <td className="center">
                        <select
                          className="ca-input"
                          style={{ fontSize: 11, padding: '3px 6px', width: 'auto' }}
                          value={t.plan_id || ''}
                          onChange={e => handlePlanChange(t.id, e.target.value)}
                        >
                          <option value="">No plan</option>
                          {plans.map(p => (
                            <option key={p.id} value={p.id}>{p.name}{p.is_default ? ' (default)' : ''}</option>
                          ))}
                        </select>
                      </td>
                      <td className="center" style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {t.created_at ? new Date(t.created_at).toLocaleDateString() : '—'}
                      </td>
                      <td className="center">
                        <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => handleExpand(t.id)}>
                            {isOpen ? 'Close' : 'Manage'}
                          </button>
                          <button
                            className="ca-btn ca-btn-ghost ca-btn-sm"
                            style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                            onClick={() => deleteTeam(t)}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>

                    {isOpen && (
                      <tr>
                        <td colSpan={5} style={{ padding: '16px 20px', background: 'var(--surface2)', borderLeft: '3px solid var(--accent)' }}>
                          <table className="ca-table" style={{ marginBottom: 14 }}>
                            <thead>
                              <tr>
                                <th>Member</th>
                                <th>Roles</th>
                                <th className="center">Action</th>
                              </tr>
                            </thead>
                            <tbody>
                              {t.members.map(m => {
                                const pendingRemoval = pendingRemovals.has(m.user_id);
                                const availRoles = (teamRoles[t.id] || []).filter(
                                  r => !(m.custom_roles || []).some(cr => cr.id === r.id)
                                );
                                return (
                                  <tr key={m.user_id} style={{
                                    opacity: pendingRemoval ? 0.4 : 1,
                                    borderLeft: pendingRemoval ? '2px solid var(--accent2)' : undefined,
                                  }}>
                                    <td style={{ fontSize: 12 }}>{memberLabel(m.user_id)}</td>
                                    <td>
                                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
                                        {m.role === 'owner' && (
                                          <span style={{ display: 'inline-block', padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 700, background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent)' }}>
                                            OWNER
                                          </span>
                                        )}
                                        {(m.custom_roles || []).map(r => (
                                          <span key={r.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 600, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}>
                                            {r.name}
                                            {!pendingRemoval && (
                                              <button
                                                onClick={() => removeMemberRole(t.id, m.user_id, r.id)}
                                                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', fontSize: 11, lineHeight: 1, padding: 0 }}
                                              >×</button>
                                            )}
                                          </span>
                                        ))}
                                        {(m.platform_role_names || []).map(name => (
                                          <span key={name} style={{ display: 'inline-block', padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 600, background: 'var(--surface2)', border: '1px solid var(--accent3)', color: 'var(--accent3)' }}>
                                            {name}
                                          </span>
                                        ))}
                                        {!pendingRemoval && availRoles.length > 0 && (
                                          <select
                                            className="ca-input"
                                            style={{ fontSize: 10, padding: '1px 4px', width: 'auto', minWidth: 70 }}
                                            value=""
                                            onChange={e => { if (e.target.value) addMemberRole(t.id, m.user_id, e.target.value); }}
                                          >
                                            <option value="">+ Role</option>
                                            {availRoles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
                                          </select>
                                        )}
                                        {(m.custom_roles || []).length === 0 && (m.platform_role_names || []).length === 0 && m.role !== 'owner' && (
                                          <span style={{ fontSize: 10, color: 'var(--muted)' }}>No roles</span>
                                        )}
                                      </div>
                                    </td>
                                    <td className="center">
                                      {m.role !== 'owner' && !pendingRemoval && (
                                        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }} onClick={() => stageRemove(m.user_id)}>
                                          Remove
                                        </button>
                                      )}
                                      {pendingRemoval && (
                                        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => unstageRemove(m.user_id)}>Undo</button>
                                      )}
                                      {m.role === 'owner' && !pendingRemoval && (
                                        <span style={{ fontSize: 10, color: 'var(--muted)' }}>transfer to remove</span>
                                      )}
                                    </td>
                                  </tr>
                                );
                              })}

                              {pendingAdditions.map(({ userId, email, displayName }) => (
                                <tr key={userId} style={{ borderLeft: '2px solid var(--accent)', opacity: 0.75 }}>
                                  <td style={{ fontSize: 12 }}>{displayName ? `${displayName} (${email})` : email}</td>
                                  <td><span style={{ fontSize: 10, color: 'var(--accent)' }}>pending</span></td>
                                  <td className="center">
                                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setPendingAdditions(p => p.filter(a => a.userId !== userId))}>
                                      Undo
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>

                          {addable.length > 0 && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                              <span style={{ fontSize: 11, color: 'var(--muted)' }}>Add:</span>
                              <select
                                className="ca-input"
                                style={{ fontSize: 11, padding: '4px 8px' }}
                                value=""
                                onChange={e => {
                                  const u = allUsers.find(u => u.id === e.target.value);
                                  if (u) stageAdd(u);
                                }}
                              >
                                <option value="" disabled>Select user…</option>
                                {addable.map(u => (
                                  <option key={u.id} value={u.id}>{u.display_name ? `${u.display_name} (${u.email})` : u.email}</option>
                                ))}
                              </select>
                            </div>
                          )}

                          {hasPending && (
                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={clearPending}>Discard</button>
                              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => handleSave(t)}>
                                Save {Object.keys(pendingRoles).length + pendingRemovals.size + pendingAdditions.length} change{Object.keys(pendingRoles).length + pendingRemovals.size + pendingAdditions.length !== 1 ? 's' : ''}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}


const AUDIT_EVENT_TYPES = [
  '', 'admin_impersonate_start', 'admin_impersonate_stop',
  'admin_update_user', 'admin_delete_user', 'admin_restore_user',
  'admin_set_team', 'admin_add_team', 'admin_remove_team',
  'admin_update_role', 'admin_remove_member',
  'create', 'update', 'delete',
];

function resolveUserName(entityId, usersMap) {
  const u = usersMap[entityId];
  return u ? (u.display_name || u.email) : (entityId?.slice(0, 12) + '…');
}

function formatAuditDetail(log, usersMap) {
  const nv = log.new_value || {};
  const pv = log.previous_value || {};
  const target = resolveUserName(log.entity_id, usersMap);
  switch (log.event_type) {
    case 'admin_add_team':
    case 'admin_set_team':
      return `${target} · role: ${nv.role} · team: ${nv.team}`;
    case 'admin_update_user': {
      const parts = Object.entries(nv.changes || {}).map(([k, v]) => `${k}: ${v.from}→${v.to}`);
      return `${target} · ${parts.join(', ')}`;
    }
    case 'admin_delete_user':  return `deleted: ${nv.email}`;
    case 'admin_restore_user': return `restored: ${nv.email}`;
    case 'admin_impersonate_start': return `target: ${nv.target_email}`;
    case 'admin_impersonate_stop':  return nv.target_email ? `stopped — was on ${nv.target_email}` : '(impersonation ended)';
    case 'admin_update_role':  return `${target} · role: ${pv.role}→${nv.role}`;
    case 'admin_remove_team':
    case 'admin_remove_member': return `${target} · removed`;
    default: {
      const parts = Object.entries(nv)
        .filter(([k]) => k !== 'by')
        .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
      return parts.join(' · ') || '—';
    }
  }
}

function AuditTab({ logs, eventType, setEventType, onRefresh, users }) {
  const [actorSearch, setActorSearch] = useState('');

  const usersMap = useMemo(
    () => Object.fromEntries((users || []).map(u => [u.id, u])),
    [users],
  );

  const filtered = actorSearch
    ? logs.filter(l => {
        const q = actorSearch.toLowerCase();
        const actor = usersMap[l.user_id];
        return l.user_email?.toLowerCase().includes(q) ||
               actor?.display_name?.toLowerCase().includes(q);
      })
    : logs;

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          className="ca-input"
          value={eventType}
          onChange={e => setEventType(e.target.value)}
          style={{ maxWidth: 240 }}
        >
          {AUDIT_EVENT_TYPES.map(t => (
            <option key={t} value={t}>{t || 'All event types'}</option>
          ))}
        </select>
        <input
          className="ca-input"
          placeholder="Search actor…"
          value={actorSearch}
          onChange={e => setActorSearch(e.target.value)}
          style={{ maxWidth: 200 }}
        />
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onRefresh}>Refresh</button>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{filtered.length} entries</span>
      </div>

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 300 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Event</th>
                <th>Entity</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No log entries.</td></tr>
              )}
              {filtered.map(log => {
                const actor = usersMap[log.user_id];
                const actorLabel = actor?.display_name
                  ? `${actor.display_name} (${log.user_email})`
                  : (log.user_email || log.user_id?.slice(0, 8));
                return (
                  <tr key={log.id}>
                    <td style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td style={{ fontSize: 11 }}>{actorLabel}</td>
                    <td>
                      <span style={{
                        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
                        fontSize: 10, fontWeight: 600,
                        background: log.event_type.startsWith('admin') ? 'var(--neutral-bg)' : 'var(--surface2)',
                        color: log.event_type.includes('delete') ? 'var(--accent2)'
                          : log.event_type.includes('impersonate') ? 'var(--accent3)'
                            : 'var(--muted)',
                      }}>
                        {log.event_type}
                      </span>
                    </td>
                    <td style={{ fontSize: 11 }}>
                      <span style={{ color: 'var(--muted)' }}>{log.entity_type}/</span>{log.entity_id?.slice(0, 12)}…
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--muted)', maxWidth: 300 }}>
                      {formatAuditDetail(log, usersMap)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}


// Flatten regions into a parent-first, depth-tagged list (tree via indentation).
function orderRegionTree(regions) {
  const byParent = new Map();
  for (const r of regions) {
    const key = r.parent_id ?? 'root';
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(r);
  }
  for (const list of byParent.values()) list.sort((a, b) => a.name.localeCompare(b.name));
  const out = [];
  const walk = (key, depth) => {
    for (const r of byParent.get(key) || []) {
      out.push({ ...r, depth });
      walk(r.id, depth + 1);
    }
  };
  walk('root', 0);
  // Append any orphan (parent not present) so nothing is hidden.
  const seen = new Set(out.map(r => r.id));
  for (const r of regions) if (!seen.has(r.id)) out.push({ ...r, depth: 0 });
  return out;
}

// All descendant ids of a region — excluded from its parent picker to prevent cycles.
function descendantIds(regions, rootId) {
  const kids = new Map();
  for (const r of regions) {
    if (r.parent_id == null) continue;
    if (!kids.has(r.parent_id)) kids.set(r.parent_id, []);
    kids.get(r.parent_id).push(r.id);
  }
  const out = new Set();
  const stack = [rootId];
  while (stack.length) {
    const id = stack.pop();
    for (const c of kids.get(id) || []) if (!out.has(c)) { out.add(c); stack.push(c); }
  }
  return out;
}

function RegionsTab({ regions, onRefresh }) {
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [formRegion, setFormRegion] = useState(undefined); // undefined = closed, null = create, obj = edit

  const ordered = orderRegionTree(regions);
  const nameOf = (id) => regions.find(r => r.id === id)?.name || '—';

  const handleDelete = async (r) => {
    const ok = await confirm({
      title: `Delete region "${r.name}"?`,
      message: 'Regions still used by a cost model, index value, or freight lane cannot be deleted. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(`/api/regions/${r.id}`);
      onRefresh();
    } catch (e) {
      showAlert({ title: 'Cannot delete region', message: formatApiError(e) });
    }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setFormRegion(null)}>+ Add region</button>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{regions.length} region{regions.length !== 1 ? 's' : ''}</span>
        <span style={{ fontSize: 11, color: 'var(--muted)', marginLeft: 'auto' }}>
          Global reference data — shared across all teams. Subregions nest under a parent.
        </span>
      </div>

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 300 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>Region</th>
                <th>Code</th>
                <th>Parent</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ordered.length === 0 && (
                <tr><td colSpan={4} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No regions yet.</td></tr>
              )}
              {ordered.map(r => (
                <tr key={r.id}>
                  <td style={{ fontWeight: r.depth ? 400 : 600 }}>
                    <span style={{ paddingLeft: r.depth * 20, color: r.depth ? 'var(--text-secondary)' : 'var(--text)' }}>
                      {r.depth ? '↳ ' : ''}{r.name}
                    </span>
                  </td>
                  <td style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: 'var(--muted)' }}>{r.code}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{r.parent_id ? nameOf(r.parent_id) : '—'}</td>
                  <td className="center">
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setFormRegion(r)}>Edit</button>
                      <button
                        className="ca-btn ca-btn-ghost ca-btn-sm"
                        style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                        onClick={() => handleDelete(r)}
                      >Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {formRegion !== undefined && (
        <RegionFormModal
          region={formRegion}
          allRegions={regions}
          onClose={() => setFormRegion(undefined)}
          onSaved={() => { setFormRegion(undefined); onRefresh(); }}
        />
      )}
    </>
  );
}

function RegionFormModal({ region, allRegions, onClose, onSaved }) {
  const isEdit = !!region;
  const [code, setCode] = useState(region?.code || '');
  const [name, setName] = useState(region?.name || '');
  const [parentId, setParentId] = useState(region?.parent_id ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // A region can't be parented to itself or one of its descendants.
  const excluded = isEdit ? new Set([region.id, ...descendantIds(allRegions, region.id)]) : new Set();
  const parentOptions = allRegions
    .filter(r => !excluded.has(r.id))
    .sort((a, b) => a.name.localeCompare(b.name));

  const save = async () => {
    if (!isEdit && !code.trim()) { setError('Code is required.'); return; }
    if (!name.trim()) { setError('Name is required.'); return; }
    setSaving(true);
    setError(null);
    try {
      const parent_id = parentId === '' ? null : Number(parentId);
      if (isEdit) {
        await api.put(`/api/regions/${region.id}`, { name: name.trim(), parent_id });
      } else {
        await api.post('/api/regions', { code: code.trim(), name: name.trim(), parent_id });
      }
      onSaved();
    } catch (e) {
      setError(formatApiError(e));
      setSaving(false);
    }
  };

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div className="ca-modal" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
        <div className="ca-modal-header">
          <div className="ca-modal-title">{isEdit ? 'Edit region' : 'Add region'}</div>
          <button className="ca-modal-close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">
          <div style={{ marginBottom: 14 }}>
            <label className="ca-label">Code</label>
            <input
              className="ca-input"
              value={code}
              onChange={e => setCode(e.target.value)}
              placeholder="e.g. NWE"
              maxLength={20}
              disabled={isEdit}
            />
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              {isEdit
                ? 'Code is the stable key data is stored under and cannot be changed.'
                : 'Stable key stored on records (e.g. cost models, index values).'}
            </div>
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="ca-label">Name</label>
            <input className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Northwest Europe" />
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="ca-label">
              Parent region <span style={{ fontWeight: 400, color: 'var(--muted)' }}>(optional — makes this a subregion)</span>
            </label>
            <select className="ca-select" value={parentId} onChange={e => setParentId(e.target.value)}>
              <option value="">— top-level —</option>
              {parentOptions.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </div>
          {error && (
            <div style={{ padding: '8px 12px', borderRadius: 6, fontSize: 11, background: 'var(--accent2-dim)', color: 'var(--accent2)' }}>
              {error}
            </div>
          )}
        </div>
        <div className="ca-modal-footer">
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose}>Cancel</button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save' : 'Add region'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Proxy/composite ("derived") index management moved to the Index Library
// (components/DerivedIndexesModal.jsx). Left here intentionally blank.


function UserDetailPanel({ user, onClose, onNavigateToTeam, platformRoles,
  onSetPlatformRole, onImpersonate, onDelete, onRestore, currentUser, isImpersonating, onRefresh }) {
  const [showEditRole, setShowEditRole] = useState(false);
  if (!user) return null;
  return (
    <div style={{
      position: 'fixed', top: 0, right: 0,
      width: 360, height: '100vh',
      background: 'var(--surface)',
      borderLeft: '1px solid var(--border)',
      zIndex: 150, overflowY: 'auto',
      padding: 24,
      boxShadow: '-4px 0 24px rgba(0,0,0,0.15)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>User Details</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 20, lineHeight: 1 }}>×</button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        {user.avatar_url
          ? <img src={user.avatar_url} alt="" style={{ width: 48, height: 48, borderRadius: '50%' }} />
          : <div style={{
              width: 48, height: 48, borderRadius: '50%',
              background: 'var(--surface2)', display: 'flex',
              alignItems: 'center', justifyContent: 'center',
              fontSize: 20, color: 'var(--muted)', fontWeight: 700,
            }}>
              {(user.display_name || user.email)?.[0]?.toUpperCase()}
            </div>
        }
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{user.display_name || 'No name'}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{user.email}</div>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.8px', marginBottom: 6 }}>Platform Roles</div>
        <PlatformRoleChips user={user} />
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.8px', marginBottom: 6 }}>Last Login</div>
        <div style={{ fontSize: 12 }}>
          {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : '—'}
        </div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.8px', marginBottom: 8 }}>Teams</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {(!user.teams || user.teams.length === 0) && (
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>No teams</span>
          )}
          {user.teams?.map((t, i) => (
            <button
              key={i}
              onClick={() => onNavigateToTeam(t.team_id)}
              style={{
                background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '7px 12px', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                color: 'var(--text)', fontSize: 12, textAlign: 'left',
                transition: 'border-color .15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              <span>{t.team_name}</span>
              <span style={{ fontSize: 9, color: 'var(--accent3)', fontWeight: 600 }}>{t.role}</span>
            </button>
          ))}
        </div>
      </div>

      {!user.deleted_at && user.id !== currentUser?.id && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
          <button
            className="ca-btn ca-btn-ghost ca-btn-sm"
            style={{ width: '100%' }}
            onClick={() => setShowEditRole(true)}
          >
            ⚙ Edit Role
          </button>
          {showEditRole && (
            <EditRoleModal
              user={user}
              platformRoles={platformRoles}
              onSetRole={onSetPlatformRole}
              onClose={() => setShowEditRole(false)}
              onRefresh={onRefresh}
            />
          )}
          <button
            className="ca-btn ca-btn-ghost ca-btn-sm"
            style={{ color: 'var(--accent3)', borderColor: 'var(--accent3)', opacity: (isImpersonating || user.is_super_admin) ? 0.4 : 1 }}
            onClick={() => onImpersonate(user.id)}
            disabled={isImpersonating || user.is_super_admin}
            title={user.is_super_admin ? 'Cannot impersonate a super admin' : undefined}
          >
            ⇒ {user.is_super_admin ? 'Cannot impersonate (super admin)' : 'Impersonate'}
          </button>
          <button
            className="ca-btn ca-btn-ghost ca-btn-sm"
            style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
            onClick={() => onDelete(user)}
          >
            ✕ Delete
          </button>
        </div>
      )}
      {user.deleted_at && user.id !== currentUser?.id && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
          <button
            className="ca-btn ca-btn-ghost ca-btn-sm"
            style={{ color: 'var(--accent)', borderColor: 'var(--accent)', width: '100%' }}
            onClick={() => onRestore(user)}
          >
            ↺ Restore
          </button>
        </div>
      )}
    </div>
  );
}


function RequestsTab({ requests, onRefresh }) {
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');

  const filtered = requests.filter(r => {
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return r.email.toLowerCase().includes(q) ||
           r.name?.toLowerCase().includes(q) ||
           r.company?.toLowerCase().includes(q);
  });

  const handleAccept = async (req) => {
    const ok = await confirm({
      title: `Grant access to ${req.email}?`,
      message: "They'll receive an email confirming access and a welcome email.",
      confirmLabel: 'Grant Access',
    });
    if (!ok) return;
    try {
      await api.post(`/api/admin/access-requests/${req.id}/accept`);
      onRefresh();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    }
  };

  const handleReject = async (req) => {
    const ok = await confirm({
      title: `Reject request from ${req.email}?`,
      message: 'No email will be sent. They can request access again later.',
      confirmLabel: 'Reject',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.post(`/api/admin/access-requests/${req.id}/reject`);
      onRefresh();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    }
  };

  const statusBadge = (status) => {
    const styles = {
      pending:  { background: 'var(--accent3-dim, #fef3c7)', color: 'var(--accent3, #f59e0b)' },
      accepted: { background: 'var(--success-bg)',           color: 'var(--accent)'             },
      rejected: { background: 'var(--accent2-dim)',          color: 'var(--accent2)'            },
    };
    const s = styles[status] || {};
    return (
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
        fontSize: 10, fontWeight: 600, ...s,
      }}>
        {status.toUpperCase()}
      </span>
    );
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="ca-input"
          placeholder="Search by email, name or company…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <select
          className="ca-input"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{ width: 'auto' }}
        >
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="rejected">Rejected</option>
        </select>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onRefresh}>Refresh</button>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{filtered.length} request{filtered.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 300 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Company</th>
                <th className="center">Requested</th>
                <th className="center">Status</th>
                <th>Reviewed by</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={7} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No requests found.</td></tr>
              )}
              {filtered.map(r => (
                <tr key={r.id}>
                  <td style={{ fontSize: 12, fontWeight: 600 }}>{r.email}</td>
                  <td style={{ fontSize: 12, color: 'var(--muted)' }}>{r.name || '—'}</td>
                  <td style={{ fontSize: 12, color: 'var(--muted)' }}>{r.company || '—'}</td>
                  <td className="center" style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                    {new Date(r.created_at).toLocaleDateString()}
                  </td>
                  <td className="center">{statusBadge(r.status)}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>
                    {r.reviewed_by_email
                      ? <>{r.reviewed_by_email}<br /><span style={{ fontSize: 10 }}>{new Date(r.reviewed_at).toLocaleDateString()}</span></>
                      : '—'}
                  </td>
                  <td className="center">
                    {r.status === 'pending' && (
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                        <button
                          className="ca-btn ca-btn-primary ca-btn-sm"
                          style={{ fontSize: 11 }}
                          onClick={() => handleAccept(r)}
                        >
                          Accept
                        </button>
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          style={{ fontSize: 11, color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                          onClick={() => handleReject(r)}
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}


function RequestsTabWrapper({ accessRequests, demoRequests, currentUser, onRefresh }) {
  const [subTab, setSubTab] = useState('access');
  const pendingAccess = accessRequests.filter(r => r.status === 'pending').length;
  const pendingDemo   = demoRequests.filter(r => r.status === 'pending').length;

  return (
    <>
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
        <button
          className={`ca-btn ca-btn-sm ${subTab === 'access' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setSubTab('access')}
        >
          Access{pendingAccess > 0 ? ` (${pendingAccess})` : ''}
        </button>
        <button
          className={`ca-btn ca-btn-sm ${subTab === 'demo' ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
          onClick={() => setSubTab('demo')}
        >
          Demo{pendingDemo > 0 ? ` (${pendingDemo})` : ''}
        </button>
      </div>
      {subTab === 'access'
        ? <RequestsTab requests={accessRequests} onRefresh={onRefresh} />
        : <DemoRequestsTab requests={demoRequests} currentUser={currentUser} onRefresh={onRefresh} />
      }
    </>
  );
}


function DemoRequestsTab({ requests, currentUser, onRefresh }) {
  const showAlert = useAlert();
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [actionDialog, setActionDialog] = useState(null); // { type: 'accept'|'reject', req }
  const [actionRemarks, setActionRemarks] = useState('');
  const [editingRemarks, setEditingRemarks] = useState(null); // req.id
  const [editingRemarksText, setEditingRemarksText] = useState('');

  const filtered = requests.filter(r => {
    if (statusFilter !== 'all' && r.status !== statusFilter) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return r.email.toLowerCase().includes(q) ||
           r.name?.toLowerCase().includes(q) ||
           r.company?.toLowerCase().includes(q);
  });

  const handleAction = async () => {
    const req = actionDialog?.req;
    const type = actionDialog?.type;
    if (!req) return;
    try {
      await api.post(`/api/admin/demo-requests/${req.id}/${type}`, { remarks: actionRemarks || null });
      setActionDialog(null);
      setActionRemarks('');
      onRefresh();
    } catch (err) {
      showAlert({ title: `${type === 'accept' ? 'Accept' : 'Reject'} failed`, message: formatApiError(err) });
    }
  };

  const saveRemarks = async (req) => {
    try {
      await api.patch(`/api/admin/demo-requests/${req.id}/remarks`, { remarks: editingRemarksText });
      setEditingRemarks(null);
      onRefresh();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    }
  };

  const statusBadge = (status) => {
    const styles = {
      pending:  { background: 'var(--accent3-dim, #fef3c7)', color: 'var(--accent3, #f59e0b)' },
      accepted: { background: 'var(--success-bg)',           color: 'var(--accent)'             },
      rejected: { background: 'var(--accent2-dim)',          color: 'var(--accent2)'            },
    };
    const s = styles[status] || {};
    return (
      <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600, ...s }}>
        {status.toUpperCase()}
      </span>
    );
  };

  return (
    <>
      {actionDialog && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)' }}
            onClick={() => setActionDialog(null)}
          />
          <div style={{
            position: 'fixed', zIndex: 201, top: '50%', left: '50%',
            transform: 'translate(-50%,-50%)', background: 'var(--surface)',
            border: '1px solid var(--border)', borderRadius: 10, padding: 24, width: 420,
            boxShadow: 'var(--shadow-popover)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>
              {actionDialog.type === 'accept' ? 'Accept Demo Request' : 'Reject Demo Request'}
            </div>
            <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 16 }}>
              {actionDialog.type === 'accept' ? (
                <>Accept demo for <strong>{actionDialog.req.name}</strong> ({actionDialog.req.email})?<br />
                A Google Calendar event and Meet link will be created on your calendar.</>
              ) : (
                <>Reject demo request from <strong>{actionDialog.req.name}</strong>?</>
              )}
            </div>
            <div style={{ marginBottom: 16 }}>
              <label className="ca-label">Remarks (optional)</label>
              <textarea
                className="ca-input"
                rows={3}
                value={actionRemarks}
                onChange={e => setActionRemarks(e.target.value)}
                placeholder={actionDialog.type === 'accept' ? 'e.g. Looking forward to chatting!' : 'e.g. No availability this week.'}
                style={{ resize: 'vertical', fontFamily: 'inherit' }}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="ca-btn ca-btn-ghost ca-btn-sm"
                onClick={() => { setActionDialog(null); setActionRemarks(''); }}>Cancel</button>
              {actionDialog.type === 'accept' ? (
                <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleAction}>
                  Accept &amp; Create Meet
                </button>
              ) : (
                <button
                  className="ca-btn ca-btn-ghost ca-btn-sm"
                  style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                  onClick={handleAction}
                >Reject</button>
              )}
            </div>
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="ca-input"
          placeholder="Search by email, name or company…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <select className="ca-input" value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ width: 'auto' }}>
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="rejected">Rejected</option>
        </select>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onRefresh}>Refresh</button>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{filtered.length} request{filtered.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="ca-card">
        <div className="ca-scroll-x" style={{ minHeight: 200 }}>
          <table className="ca-table">
            <thead>
              <tr>
                <th>Name / Company</th>
                <th>Contact</th>
                <th>Requested</th>
                <th className="center">Status</th>
                <th>Remarks</th>
                <th>Meet</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={7} style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>No demo requests yet.</td></tr>
              )}
              {filtered.map(r => (
                <tr key={r.id}>
                  <td>
                    <div style={{ fontWeight: 600, fontSize: 12 }}>{r.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.company}</div>
                  </td>
                  <td>
                    <div style={{ fontSize: 12 }}>{r.email}</div>
                    {r.phone && <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.phone}</div>}
                  </td>
                  <td style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                    <div>{r.requested_date}</div>
                    <div style={{ color: 'var(--muted)' }}>{r.requested_start}–{r.requested_end}</div>
                    {r.visitor_timezone && r.visitor_timezone !== 'UTC' &&
                      <div style={{ fontSize: 10, color: 'var(--muted)' }}>{r.visitor_timezone}</div>}
                  </td>
                  <td className="center">
                    <div>{statusBadge(r.status)}</div>
                    {(r.assigned_host_name || r.reviewed_by_name) && (
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3, whiteSpace: 'nowrap' }}>
                        by {r.assigned_host_name || r.reviewed_by_name}
                        {r.reviewed_at && <><br />{new Date(r.reviewed_at).toLocaleDateString()}</>}
                      </div>
                    )}
                  </td>
                  <td style={{ maxWidth: 160 }}>
                    {editingRemarks === r.id ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <textarea
                          className="ca-input"
                          rows={2}
                          value={editingRemarksText}
                          onChange={e => setEditingRemarksText(e.target.value)}
                          style={{ fontSize: 11, resize: 'vertical' }}
                        />
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button className="ca-btn ca-btn-primary ca-btn-sm" style={{ fontSize: 10 }}
                            onClick={() => saveRemarks(r)}>Save</button>
                          <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
                            onClick={() => setEditingRemarks(null)}>Cancel</button>
                        </div>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4 }}>
                        <span style={{ fontSize: 11, color: r.remarks ? 'var(--text)' : 'var(--muted)' }}>
                          {r.remarks || '—'}
                        </span>
                        <button
                          title="Edit remarks"
                          onClick={() => { setEditingRemarks(r.id); setEditingRemarksText(r.remarks || ''); }}
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', fontSize: 12, padding: '0 2px', flexShrink: 0 }}
                        >✎</button>
                      </div>
                    )}
                  </td>
                  <td>
                    {r.meet_link
                      ? <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
                          onClick={() => navigator.clipboard.writeText(r.meet_link)}>Copy</button>
                      : <span style={{ fontSize: 11, color: 'var(--muted)' }}>—</span>
                    }
                  </td>
                  <td className="center">
                    {r.status === 'pending' && (
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                        <button
                          className="ca-btn ca-btn-primary ca-btn-sm"
                          style={{ fontSize: 11 }}
                          onClick={() => { setActionDialog({ type: 'accept', req: r }); setActionRemarks(''); }}
                        >Accept</button>
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          style={{ fontSize: 11, color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                          onClick={() => { setActionDialog({ type: 'reject', req: r }); setActionRemarks(''); }}
                        >Reject</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}


function AdminSettingsTab({ users = [] }) {
  const showAlert = useAlert();
  const confirm = useConfirm();
  const [section, setSection] = useState('permissions');
  const [permissions, setPermissions] = useState([]);
  const [plans, setPlans] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loadingPerms, setLoadingPerms] = useState(true);
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [loadingRoles, setLoadingRoles] = useState(false);
  const [editingPlan, setEditingPlan] = useState(null);
  const [newPlan, setNewPlan] = useState(null);
  const [editingRole, setEditingRole] = useState(null);
  const [newRole, setNewRole] = useState(null);

  // Demo hosts
  const [demoHosts, setDemoHosts] = useState([]);
  const [loadingDemoHosts, setLoadingDemoHosts] = useState(false);
  const [addingHost, setAddingHost] = useState(false);
  const [newHostForm, setNewHostForm] = useState({ user_id: '', timezone: 'UTC', slot_duration_minutes: 30, working_days: [0,1,2,3,4], working_start: '09:00', working_end: '18:00' });
  const [editingHost, setEditingHost] = useState(null);
  const [editHostForm, setEditHostForm] = useState({});
  const [expandedSlots, setExpandedSlots] = useState(null);
  const [blockedSlots, setBlockedSlots] = useState({});
  const [newBlock, setNewBlock] = useState({ blocked_date: '', start_time: '09:00', end_time: '10:00' });

  useEffect(() => {
    api.get('/api/settings/permissions')
      .then(r => setPermissions(r.data))
      .catch(console.error)
      .finally(() => setLoadingPerms(false));
  }, []);

  useEffect(() => {
    if (section === 'plans') fetchPlans();
    if (section === 'roles') fetchRoles();
    if (section === 'demo_hosts') fetchDemoHosts();
  }, [section]);

  const fetchPlans = () => {
    setLoadingPlans(true);
    Promise.all([api.get('/api/settings/plans'), api.get('/api/settings/permissions')])
      .then(([pRes, permRes]) => { setPlans(pRes.data); setPermissions(permRes.data); })
      .catch(console.error)
      .finally(() => setLoadingPlans(false));
  };

  const fetchRoles = () => {
    setLoadingRoles(true);
    Promise.all([api.get('/api/settings/roles'), api.get('/api/settings/permissions')])
      .then(([rRes, permRes]) => { setRoles(rRes.data); setPermissions(permRes.data); })
      .catch(console.error)
      .finally(() => setLoadingRoles(false));
  };

  const permsByCategory = permissions.reduce((acc, p) => {
    if (!acc[p.category]) acc[p.category] = [];
    acc[p.category].push(p);
    return acc;
  }, {});

  const savePlan = async (planData, planId) => {
    try {
      planId ? await api.put(`/api/settings/plans/${planId}`, planData)
             : await api.post('/api/settings/plans', planData);
      setEditingPlan(null); setNewPlan(null); fetchPlans();
    } catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const deletePlan = async (plan) => {
    if (plan.is_default) { showAlert({ title: 'Cannot delete', message: 'The default plan cannot be deleted.' }); return; }
    const ok = await confirm({ title: `Delete plan "${plan.name}"?`, message: 'Teams on this plan will lose their assignment.', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    try { await api.delete(`/api/settings/plans/${plan.id}`); fetchPlans(); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const saveRole = async (roleData, roleId) => {
    try {
      roleId ? await api.put(`/api/settings/roles/${roleId}`, roleData)
             : await api.post('/api/settings/roles', roleData);
      setEditingRole(null); setNewRole(null); fetchRoles();
    } catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const deleteRole = async (role) => {
    const ok = await confirm({ title: `Delete role "${role.name}"?`, message: 'This cannot be undone.', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    try { await api.delete(`/api/settings/roles/${role.id}`); fetchRoles(); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const fetchDemoHosts = () => {
    setLoadingDemoHosts(true);
    api.get('/api/admin/demo-hosts')
      .then(r => setDemoHosts(r.data))
      .catch(console.error)
      .finally(() => setLoadingDemoHosts(false));
  };

  const addDemoHost = async () => {
    if (!newHostForm.user_id) return;
    try {
      await api.post('/api/admin/demo-hosts', newHostForm);
      setAddingHost(false);
      setNewHostForm({ user_id: '', timezone: 'UTC', slot_duration_minutes: 30, working_days: [0,1,2,3,4], working_start: '09:00', working_end: '18:00' });
      fetchDemoHosts();
    } catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const removeDemoHost = async (host) => {
    const ok = await confirm({ title: `Remove ${host.user_name || host.user_email} as demo host?`, message: 'Host configuration will be deleted.', confirmLabel: 'Remove', danger: true });
    if (!ok) return;
    try { await api.delete(`/api/admin/demo-hosts/${host.id}`); fetchDemoHosts(); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const disconnectCalendar = async (host) => {
    const ok = await confirm({ title: 'Disconnect Google Calendar?', message: `Remove calendar access for ${host.user_name || host.user_email}.`, confirmLabel: 'Disconnect', danger: true });
    if (!ok) return;
    try { await api.delete(`/api/admin/demo-hosts/${host.id}/calendar`); fetchDemoHosts(); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const saveHostEdit = async (hostId) => {
    try { await api.put(`/api/admin/demo-hosts/${hostId}`, editHostForm); setEditingHost(null); fetchDemoHosts(); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const loadBlockedSlots = async (hostId) => {
    try {
      const r = await api.get(`/api/admin/demo-hosts/${hostId}/blocked-slots`);
      setBlockedSlots(prev => ({ ...prev, [hostId]: r.data }));
    } catch (e) { console.error(e); }
  };

  const toggleExpandedSlots = (hostId) => {
    if (expandedSlots === hostId) { setExpandedSlots(null); return; }
    setExpandedSlots(hostId);
    loadBlockedSlots(hostId);
  };

  const addBlockedSlot = async (hostId) => {
    if (!newBlock.blocked_date) return;
    try {
      await api.post(`/api/admin/demo-hosts/${hostId}/blocked-slots`, newBlock);
      setNewBlock({ blocked_date: '', start_time: '09:00', end_time: '10:00' });
      loadBlockedSlots(hostId);
    } catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const deleteBlockedSlot = async (hostId, slotId) => {
    try { await api.delete(`/api/admin/demo-hosts/${hostId}/blocked-slots/${slotId}`); loadBlockedSlots(hostId); }
    catch (e) { showAlert({ title: 'Error', message: e.response?.data?.detail || e.message }); }
  };

  const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { key: 'permissions', label: `Permissions (${permissions.length})` },
          { key: 'plans', label: `Plans (${plans.length})` },
          { key: 'roles', label: `Roles (${roles.length})` },
          { key: 'demo_hosts', label: `Demo Hosts (${demoHosts.length})` },
        ].map(s => (
          <button key={s.key} className={`ca-btn ca-btn-sm ${section === s.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
            onClick={() => setSection(s.key)}>{s.label}</button>
        ))}
      </div>

      {section === 'permissions' && (
        <div className="ca-card">
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12 }}>
            Permissions are managed by developers when new features are added. They cannot be edited here.
          </div>
          <div style={{ maxHeight: 520, overflowY: 'auto', paddingRight: 4 }}>
            {loadingPerms ? (
              <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
            ) : (
              Object.entries(permsByCategory).map(([cat, perms]) => (
                <div key={cat} style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.8px', color: 'var(--muted)', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>{cat}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {perms.map(p => (
                      <div key={p.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', fontSize: 11 }}>
                        <span style={{ fontFamily: 'monospace', color: 'var(--accent)', fontSize: 10 }}>{p.key}</span>
                        <span style={{ color: 'var(--muted)' }}>·</span>
                        <span>{p.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {section === 'plans' && (
        <>
          <div className="ca-card" style={{ marginBottom: 16 }}>
            {loadingPlans ? (
              <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
            ) : (
              <table className="ca-table">
                <thead><tr><th>Name</th><th>Description</th><th className="center">Default</th><th className="center">Permissions</th><th className="center">Actions</th></tr></thead>
                <tbody>
                  {plans.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No plans yet.</td></tr>}
                  {plans.map(p => (
                    <Fragment key={p.id}>
                      <tr>
                        <td style={{ fontWeight: 600, fontSize: 13 }}>
                          {p.name}
                          {p.is_default && <span style={{ marginLeft: 6, fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: 'var(--accent-dim)', color: 'var(--accent)' }}>DEFAULT</span>}
                        </td>
                        <td style={{ fontSize: 11, color: 'var(--muted)' }}>{p.description || '—'}</td>
                        <td className="center" style={{ fontSize: 11, color: p.is_default ? 'var(--accent)' : 'var(--muted)' }}>{p.is_default ? 'Yes' : 'No'}</td>
                        <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>{p.permission_count}</td>
                        <td className="center">
                          <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditingPlan(editingPlan?.id === p.id ? null : p)}>
                              {editingPlan?.id === p.id ? 'Close' : 'Edit'}
                            </button>
                            {!p.is_default && (
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }} onClick={() => deletePlan(p)}>Delete</button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {editingPlan?.id === p.id && (
                        <tr>
                          <td colSpan={5} style={{ padding: '16px 20px', background: 'var(--surface2)', borderLeft: '3px solid var(--accent3)' }}>
                            <PlanForm initial={editingPlan} permissions={permissions} onSave={data => savePlan(data, p.id)} onCancel={() => setEditingPlan(null)}
                              fetchInitialDetail={() => api.get(`/api/settings/plans/${p.id}`).then(r => r.data)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {newPlan ? (
            <div className="ca-card" style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>New Plan</div>
              <PlanForm initial={{ name: '', description: '', is_default: false, permission_ids: [] }} permissions={permissions} onSave={data => savePlan(data, null)} onCancel={() => setNewPlan(null)} />
            </div>
          ) : (
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setNewPlan({})}>+ New Plan</button>
          )}
        </>
      )}

      {section === 'roles' && (
        <>
          <div className="ca-card" style={{ marginBottom: 16 }}>
            {loadingRoles ? (
              <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
            ) : (
              <table className="ca-table">
                <thead><tr><th>Role</th><th>Description</th><th className="center">Permissions</th><th className="center">Actions</th></tr></thead>
                <tbody>
                  {roles.length === 0 && <tr><td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>No platform roles yet.</td></tr>}
                  {roles.map(r => (
                    <Fragment key={r.id}>
                      <tr>
                        <td style={{ fontWeight: 600, fontSize: 13 }}>
                          {r.name}
                          {['User', 'SuperAdmin', 'Chemist', 'FX Manager'].includes(r.name) && (
                            <span style={{ marginLeft: 6, fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: 'var(--accent-dim)', color: 'var(--accent)' }}>default</span>
                          )}
                        </td>
                        <td style={{ fontSize: 11, color: 'var(--muted)' }}>{r.description || '—'}</td>
                        <td className="center" style={{ fontSize: 11, color: 'var(--muted)' }}>{r.permission_count}</td>
                        <td className="center">
                          <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditingRole(editingRole?.id === r.id ? null : r)}>
                              {editingRole?.id === r.id ? 'Close' : 'Edit'}
                            </button>
                            {!['User', 'SuperAdmin', 'Chemist', 'FX Manager'].includes(r.name) && (
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }} onClick={() => deleteRole(r)}>Delete</button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {editingRole?.id === r.id && (
                        <tr>
                          <td colSpan={4} style={{ padding: '16px 20px', background: 'var(--surface2)', borderLeft: '3px solid var(--accent3)' }}>
                            <PlanForm
                              initial={editingRole}
                              permissions={permissions}
                              onSave={data => saveRole({ name: r.name, description: data.description, permission_ids: data.permission_ids }, r.id)}
                              onCancel={() => setEditingRole(null)}
                              fetchInitialDetail={() => api.get(`/api/settings/roles/${r.id}`).then(res => res.data)}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {newRole ? (
            <div className="ca-card" style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>New Platform Role</div>
              <PlanForm
                initial={{ name: '', description: '', is_default: false, permission_ids: [] }}
                permissions={permissions}
                showNameField
                onSave={data => saveRole(data, null)}
                onCancel={() => setNewRole(null)}
              />
            </div>
          ) : (
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setNewRole({})}>+ New Role</button>
          )}
        </>
      )}

      {section === 'demo_hosts' && (
        <>
          <div className="ca-card" style={{ marginBottom: 16 }}>
            {loadingDemoHosts ? (
              <div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div>
            ) : demoHosts.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
                No demo hosts configured yet. Add one below.
              </div>
            ) : (
              <table className="ca-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Schedule</th>
                    <th>Calendar</th>
                    <th className="center">Active</th>
                    <th className="center">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {demoHosts.map(h => (
                    <Fragment key={h.id}>
                      <tr>
                        <td>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>{h.user_name || h.user_email}</div>
                          {h.user_name && <div style={{ fontSize: 11, color: 'var(--muted)' }}>{h.user_email}</div>}
                        </td>
                        <td style={{ fontSize: 11 }}>
                          <div>{h.working_start}–{h.working_end} · {h.slot_duration_minutes}min slots</div>
                          <div style={{ color: 'var(--muted)' }}>{(h.working_days || []).map(d => DAY_NAMES[d]).join(', ')}</div>
                          <div style={{ color: 'var(--muted)' }}>{h.timezone}</div>
                        </td>
                        <td style={{ fontSize: 11 }}>
                          {h.calendar_connected ? (
                            <div>
                              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>Connected</span>
                              {h.google_email && <div style={{ color: 'var(--muted)' }}>{h.google_email}</div>}
                              <button
                                className="ca-btn ca-btn-ghost ca-btn-sm"
                                style={{ marginTop: 4, fontSize: 10, color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                                onClick={() => disconnectCalendar(h)}
                              >Disconnect</button>
                            </div>
                          ) : (
                            <div>
                              <span style={{ color: 'var(--muted)' }}>Not connected</span>
                              <div>
                                <button
                                  className="ca-btn ca-btn-ghost ca-btn-sm"
                                  style={{ marginTop: 4, fontSize: 10 }}
                                  onClick={() => { window.location.href = '/auth/google-calendar/start'; }}
                                >Connect Google Calendar</button>
                              </div>
                            </div>
                          )}
                        </td>
                        <td className="center">
                          <label style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={h.is_active}
                              onChange={async e => {
                                await api.put(`/api/admin/demo-hosts/${h.id}`, { is_active: e.target.checked });
                                fetchDemoHosts();
                              }}
                            />
                          </label>
                        </td>
                        <td className="center">
                          <div style={{ display: 'flex', gap: 4, justifyContent: 'center', flexWrap: 'wrap' }}>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => {
                              setEditingHost(editingHost === h.id ? null : h.id);
                              setEditHostForm({ timezone: h.timezone, slot_duration_minutes: h.slot_duration_minutes, working_days: h.working_days, working_start: h.working_start, working_end: h.working_end });
                            }}>
                              {editingHost === h.id ? 'Close' : 'Edit'}
                            </button>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => toggleExpandedSlots(h.id)}>
                              Blocked Slots
                            </button>
                            <button
                              className="ca-btn ca-btn-ghost ca-btn-sm"
                              style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                              onClick={() => removeDemoHost(h)}
                            >Remove</button>
                          </div>
                        </td>
                      </tr>

                      {editingHost === h.id && (
                        <tr>
                          <td colSpan={5} style={{ padding: '14px 20px', background: 'var(--surface2)', borderLeft: '3px solid var(--accent3)' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 8, marginBottom: 10 }}>
                              <div>
                                <label className="ca-label">Timezone</label>
                                <input className="ca-input" value={editHostForm.timezone || ''} onChange={e => setEditHostForm(f => ({ ...f, timezone: e.target.value }))} placeholder="e.g. Europe/London" />
                              </div>
                              <div>
                                <label className="ca-label">Slot (min)</label>
                                <select className="ca-input" value={editHostForm.slot_duration_minutes || 30} onChange={e => setEditHostForm(f => ({ ...f, slot_duration_minutes: +e.target.value }))}>
                                  <option value={15}>15</option>
                                  <option value={30}>30</option>
                                  <option value={60}>60</option>
                                </select>
                              </div>
                              <div>
                                <label className="ca-label">Start</label>
                                <input className="ca-input" type="time" value={editHostForm.working_start || ''} onChange={e => setEditHostForm(f => ({ ...f, working_start: e.target.value }))} />
                              </div>
                              <div>
                                <label className="ca-label">End</label>
                                <input className="ca-input" type="time" value={editHostForm.working_end || ''} onChange={e => setEditHostForm(f => ({ ...f, working_end: e.target.value }))} />
                              </div>
                            </div>
                            <div style={{ marginBottom: 10 }}>
                              <label className="ca-label">Working Days</label>
                              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                {DAY_NAMES.map((d, i) => (
                                  <label key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer' }}>
                                    <input
                                      type="checkbox"
                                      checked={(editHostForm.working_days || []).includes(i)}
                                      onChange={e => {
                                        const days = editHostForm.working_days || [];
                                        setEditHostForm(f => ({ ...f, working_days: e.target.checked ? [...days, i].sort() : days.filter(x => x !== i) }));
                                      }}
                                    />
                                    {d}
                                  </label>
                                ))}
                              </div>
                            </div>
                            <div style={{ display: 'flex', gap: 8 }}>
                              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => saveHostEdit(h.id)}>Save</button>
                              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEditingHost(null)}>Cancel</button>
                            </div>
                          </td>
                        </tr>
                      )}

                      {expandedSlots === h.id && (
                        <tr>
                          <td colSpan={5} style={{ padding: '14px 20px', background: 'var(--surface2)', borderLeft: '3px solid var(--border)' }}>
                            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8 }}>Blocked Slots</div>
                            {(blockedSlots[h.id] || []).length === 0 ? (
                              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 10 }}>No blocked slots.</div>
                            ) : (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                                {(blockedSlots[h.id] || []).map(s => (
                                  <span key={s.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 5, padding: '3px 8px', fontSize: 11 }}>
                                    {s.blocked_date} {s.start_time}–{s.end_time}
                                    <button
                                      onClick={() => deleteBlockedSlot(h.id, s.id)}
                                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent2)', fontSize: 12, padding: '0 2px', lineHeight: 1 }}
                                    >×</button>
                                  </span>
                                ))}
                              </div>
                            )}
                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                              <div>
                                <label className="ca-label">Date</label>
                                <input className="ca-input" type="date" value={newBlock.blocked_date} onChange={e => setNewBlock(b => ({ ...b, blocked_date: e.target.value }))} />
                              </div>
                              <div>
                                <label className="ca-label">From</label>
                                <input className="ca-input" type="time" value={newBlock.start_time} onChange={e => setNewBlock(b => ({ ...b, start_time: e.target.value }))} />
                              </div>
                              <div>
                                <label className="ca-label">To</label>
                                <input className="ca-input" type="time" value={newBlock.end_time} onChange={e => setNewBlock(b => ({ ...b, end_time: e.target.value }))} />
                              </div>
                              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => addBlockedSlot(h.id)}>Save</button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {addingHost ? (
            <div className="ca-card" style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>Add Demo Host</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 8, marginBottom: 10 }}>
                <div>
                  <label className="ca-label">User</label>
                  <select className="ca-input" value={newHostForm.user_id} onChange={e => setNewHostForm(f => ({ ...f, user_id: e.target.value }))}>
                    <option value="">Select user…</option>
                    {users.filter(u => !u.deleted_at && !demoHosts.some(h => h.user_id === u.id)).map(u => (
                      <option key={u.id} value={u.id}>{u.display_name || u.email}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="ca-label">Timezone</label>
                  <input className="ca-input" value={newHostForm.timezone} onChange={e => setNewHostForm(f => ({ ...f, timezone: e.target.value }))} placeholder="UTC" />
                </div>
                <div>
                  <label className="ca-label">Slot (min)</label>
                  <select className="ca-input" value={newHostForm.slot_duration_minutes} onChange={e => setNewHostForm(f => ({ ...f, slot_duration_minutes: +e.target.value }))}>
                    <option value={15}>15</option>
                    <option value={30}>30</option>
                    <option value={60}>60</option>
                  </select>
                </div>
                <div>
                  <label className="ca-label">Start</label>
                  <input className="ca-input" type="time" value={newHostForm.working_start} onChange={e => setNewHostForm(f => ({ ...f, working_start: e.target.value }))} />
                </div>
                <div>
                  <label className="ca-label">End</label>
                  <input className="ca-input" type="time" value={newHostForm.working_end} onChange={e => setNewHostForm(f => ({ ...f, working_end: e.target.value }))} />
                </div>
              </div>
              <div style={{ marginBottom: 10 }}>
                <label className="ca-label">Working Days</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {DAY_NAMES.map((d, i) => (
                    <label key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={(newHostForm.working_days || []).includes(i)}
                        onChange={e => {
                          const days = newHostForm.working_days || [];
                          setNewHostForm(f => ({ ...f, working_days: e.target.checked ? [...days, i].sort() : days.filter(x => x !== i) }));
                        }}
                      />
                      {d}
                    </label>
                  ))}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={addDemoHost}>Add Host</button>
                <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setAddingHost(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setAddingHost(true)}>+ Add Demo Host</button>
          )}
        </>
      )}
    </>
  );
}

// dummy placeholder (old PermissionForm removed — permissions are dev-managed)
function _unused_PermissionForm({ onSave, onCancel }) {
  const [form, setForm] = useState({ key: '', label: '', category: '', action: '' });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = () => {
    if (!form.key || !form.label || !form.category || !form.action) return;
    onSave(form);
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8, alignItems: 'end' }}>
      <div>
        <label className="ca-label">Key</label>
        <input className="ca-input" value={form.key} onChange={e => set('key', e.target.value)} placeholder="e.g. products.view" />
      </div>
      <div>
        <label className="ca-label">Label</label>
        <input className="ca-input" value={form.label} onChange={e => set('label', e.target.value)} placeholder="e.g. View Products" />
      </div>
      <div>
        <label className="ca-label">Category</label>
        <input className="ca-input" value={form.category} onChange={e => set('category', e.target.value)} placeholder="e.g. products" />
      </div>
      <div>
        <label className="ca-label">Action</label>
        <input className="ca-input" value={form.action} onChange={e => set('action', e.target.value)} placeholder="e.g. view" />
      </div>
      <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onCancel}>Cancel</button>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleSubmit}>Create</button>
      </div>
    </div>
  );
}

function PlanForm({ initial, permissions, onSave, onCancel, fetchInitialDetail, showNameField = true }) {
  const [name, setName] = useState(initial?.name || '');
  const [description, setDescription] = useState(initial?.description || '');
  const [isDefault, setIsDefault] = useState(initial?.is_default || false);
  const [selectedIds, setSelectedIds] = useState(new Set(initial?.permission_ids || []));
  const [loadingDetail, setLoadingDetail] = useState(!!fetchInitialDetail);

  useEffect(() => {
    if (!fetchInitialDetail) return;
    fetchInitialDetail().then(detail => {
      setName(detail.name);
      setDescription(detail.description || '');
      setIsDefault(detail.is_default);
      setSelectedIds(new Set(detail.permissions.map(p => p.id)));
    }).finally(() => setLoadingDetail(false));
  }, []);

  const permsByCategory = permissions.reduce((acc, p) => {
    if (!acc[p.category]) acc[p.category] = [];
    acc[p.category].push(p);
    return acc;
  }, {});

  const togglePerm = (id) => {
    setSelectedIds(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const toggleCategory = (perms) => {
    const allSelected = perms.every(p => selectedIds.has(p.id));
    setSelectedIds(prev => {
      const n = new Set(prev);
      perms.forEach(p => allSelected ? n.delete(p.id) : n.add(p.id));
      return n;
    });
  };

  if (loadingDetail) return <div style={{ padding: 12, color: 'var(--muted)' }}>Loading…</div>;

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: showNameField ? '1fr 2fr auto' : '2fr auto', gap: 8, marginBottom: 16, alignItems: 'end' }}>
        {showNameField && (
          <div>
            <label className="ca-label">Name</label>
            <input className="ca-input" value={name} onChange={e => setName(e.target.value)} />
          </div>
        )}
        <div>
          <label className="ca-label">Description</label>
          <input className="ca-input" value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        {showNameField === true && (
          <div style={{ paddingBottom: 2 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={isDefault} onChange={e => setIsDefault(e.target.checked)} />
              Default plan
            </label>
          </div>
        )}
      </div>

      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.6px' }}>Permissions ({selectedIds.size}/{permissions.length})</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginBottom: 16 }}>
        {Object.entries(permsByCategory).map(([cat, perms]) => {
          const allSel = perms.every(p => selectedIds.has(p.id));
          const someSel = perms.some(p => selectedIds.has(p.id));
          return (
            <div key={cat} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.6px', color: allSel ? 'var(--accent)' : someSel ? 'var(--accent3)' : 'var(--muted)' }}>{cat}</span>
                <button
                  style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 10, color: 'var(--muted)', padding: 0 }}
                  onClick={() => toggleCategory(perms)}
                >{allSel ? 'Deselect all' : 'Select all'}</button>
              </div>
              {perms.map(p => (
                <label key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, marginBottom: 4 }}>
                  <input type="checkbox" checked={selectedIds.has(p.id)} onChange={() => togglePerm(p.id)} />
                  {p.label}
                </label>
              ))}
            </div>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', borderTop: '1px solid var(--border)', paddingTop: 12 }}>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onCancel}>Cancel</button>
        <button
          className="ca-btn ca-btn-primary ca-btn-sm"
          onClick={() => onSave({ name, description, is_default: isDefault, permission_ids: [...selectedIds] })}
        >Save</button>
      </div>
    </div>
  );
}


function TeamCell({ userId, userTeams, allTeams, onChanged }) {
  const [picking, setPicking] = useState(false);
  const [search, setSearch] = useState('');
  const inputRef = useRef(null);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (picking && inputRef.current) inputRef.current.focus();
  }, [picking]);

  useEffect(() => {
    if (!picking) return;
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setPicking(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [picking]);

  const filtered = allTeams.filter(t =>
    t.name.toLowerCase().includes(search.toLowerCase())
  );

  const addToTeam = async (teamId) => {
    await api.post(`/api/admin/users/${userId}/add-team`, { team_id: teamId, role: 'member' });
    setPicking(false);
    setSearch('');
    onChanged();
  };

  const removeFromTeam = async (teamId) => {
    await api.delete(`/api/admin/users/${userId}/teams/${teamId}`);
    onChanged();
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', position: 'relative' }} ref={wrapRef}>
      {userTeams.map((t, i) => (
        <span key={i} style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          fontSize: 10, background: 'var(--surface2)', border: '1px solid var(--border)',
          borderRadius: 4, padding: '2px 6px 2px 8px', color: 'var(--text)',
        }}>
          {t.team_name}
          <span style={{ color: 'var(--accent3)', fontSize: 9 }}>{t.role}</span>
          <button
            onClick={() => removeFromTeam(t.team_id)}
            style={{
              background: 'none', border: 'none', color: 'var(--accent2)', cursor: 'pointer',
              fontSize: 11, padding: '0 2px', lineHeight: 1, opacity: 0.6,
            }}
            title="Remove from team"
          >×</button>
        </span>
      ))}
      {!picking ? (
        <button
          onClick={() => setPicking(true)}
          style={{
            background: 'none', border: '1px dashed var(--border)', borderRadius: 4,
            color: 'var(--muted)', cursor: 'pointer', fontSize: 10, padding: '2px 8px',
            transition: 'border-color .15s',
          }}
          onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent)'}
          onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
        >+ team</button>
      ) : (
        <div style={{
          position: 'absolute', top: '100%', left: 0, zIndex: 50, marginTop: 4,
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 8, padding: 6, minWidth: 220, boxShadow: 'var(--shadow-popover)',
        }}>
          <input
            ref={inputRef}
            className="ca-input"
            placeholder="Search teams…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ fontSize: 11, padding: '6px 8px', marginBottom: 4 }}
          />
          <div style={{ maxHeight: 150, overflowY: 'auto' }}>
            {filtered.length === 0 ? (
              <div style={{ padding: '6px 8px', fontSize: 11, color: 'var(--muted)' }}>No teams found</div>
            ) : (
              filtered.map(t => {
                const alreadyIn = userTeams.some(ut => ut.team_id === t.id);
                return (
                  <div
                    key={t.id}
                    onClick={() => !alreadyIn && addToTeam(t.id)}
                    style={{
                      padding: '5px 8px', fontSize: 11, borderRadius: 4,
                      cursor: alreadyIn ? 'default' : 'pointer',
                      color: alreadyIn ? 'var(--muted)' : 'var(--text)',
                    }}
                    onMouseEnter={e => { if (!alreadyIn) e.currentTarget.style.background = 'var(--surface2)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                  >
                    {t.name}
                    {alreadyIn && <span style={{ marginLeft: 6, fontSize: 9, color: 'var(--accent)' }}>joined</span>}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
