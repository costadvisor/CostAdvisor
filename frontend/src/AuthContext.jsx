import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api from './api';
import { applyTheme, cacheTheme } from './utils/theme';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [teams, setTeams] = useState([]);
  const [activeTeamId, setActiveTeamId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pendingInviteCount, setPendingInviteCount] = useState(0);

  // Read ?login_error from URL once on mount and clear it immediately so it
  // doesn't persist across navigations or reloads.
  const [loginError, setLoginError] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get('login_error') || null;
    if (err) {
      params.delete('login_error');
      const newSearch = params.toString();
      window.history.replaceState(null, '', newSearch ? `?${newSearch}` : window.location.pathname);
    }
    return err;
  });

  const fetchUser = useCallback(async () => {
    try {
      const { data } = await api.get('/auth/me');
      setUser(data);

      if (data.theme) {
        applyTheme(data.theme);
        cacheTheme(data.theme);
      }

      // Extract teams from memberships
      const userTeams = data.memberships?.map(m => ({
        id: m.team_id,
        name: m.team?.name || 'Team',
        role: m.role,
        created_at: m.team?.created_at || null,
      })) || [];
      setTeams(userTeams);

      // Set active team (first one, or from localStorage)
      const savedTeam = localStorage.getItem('ca_active_team');
      if (savedTeam && userTeams.find(t => t.id === savedTeam)) {
        setActiveTeamId(savedTeam);
      } else if (userTeams.length > 0) {
        setActiveTeamId(userTeams[0].id);
      }

      // Fetch pending invites for badge count
      try {
        const { data: invites } = await api.get('/api/invites/pending');
        setPendingInviteCount(invites.length);
      } catch {
        setPendingInviteCount(0);
      }
    } catch {
      setUser(null);
      setTeams([]);
      setPendingInviteCount(0);
    } finally {
      setLoading(false);
    }
  }, []);

  const setTheme = useCallback(async (theme) => {
    applyTheme(theme);
    cacheTheme(theme);
    setUser(prev => (prev ? { ...prev, theme } : prev));
    try {
      await api.put('/auth/me/theme', { theme });
    } catch (err) {
      console.error('Failed to persist theme', err);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const switchTeam = (teamId) => {
    setActiveTeamId(teamId);
    localStorage.setItem('ca_active_team', teamId);
  };

  const logout = async () => {
    await api.post('/auth/logout');
    setUser(null);
    setTeams([]);
    setActiveTeamId(null);
    // Landing page URL — set VITE_LANDING_URL per environment (e.g. localhost:3333 locally);
    // defaults to the public landing site on deploy.
    window.location.href = import.meta.env.VITE_LANDING_URL || 'https://www.costadvisor.org';
  };

  return (
    <AuthContext.Provider value={{
      user,
      teams,
      activeTeamId,
      switchTeam,
      loading,
      logout,
      refreshUser: fetchUser,
      setTheme,
      pendingInviteCount,
      loginError,
      clearLoginError: () => setLoginError(null),
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}