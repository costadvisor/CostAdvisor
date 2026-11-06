import { Link, Navigate, Outlet } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { SUPPORT_EMAIL } from './components/Footer';

export default function ProtectedRoute() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        minHeight: '100vh', color: 'var(--muted)', fontSize: 14,
      }}>
        Loading...
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

// What a signed-in user with no membership sees instead of a team-scoped page.
// Those pages wait for a team before they fetch, so without this they sit on
// "Loading…" for ever (fresh access-request approvals and staff sign-ins start
// with no team).
export function NoTeamNotice() {
  const { pendingInviteCount } = useAuth();
  return (
    <div className="ca-card" style={{ maxWidth: 560, padding: 28 }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>You are not in a team yet</div>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: '0 0 16px' }}>
        {pendingInviteCount > 0
          ? 'You have a pending invite. Accept it on the Team page to join that team.'
          : 'Create a team on the Team page, or ask your admin to invite you to theirs.'}
      </p>
      <Link
        to={pendingInviteCount > 0 ? '/team?tab=requests' : '/team'}
        className="ca-btn ca-btn-primary"
        style={{ textDecoration: 'none' }}
      >
        Go to Team
      </Link>
      <p style={{ fontSize: 12, color: 'var(--muted)', margin: '16px 0 0' }}>
        Need help? Email <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: 'var(--accent)' }}>{SUPPORT_EMAIL}</a>.
      </p>
    </div>
  );
}

// Layout route for every page whose data belongs to a team.
export function RequireTeam() {
  const { activeTeamId } = useAuth();
  if (activeTeamId) return <Outlet />;
  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Welcome to CostAdvisor</div>
      <p className="ca-subtitle">Your products, cost models and alerts live inside a team.</p>
      <NoTeamNotice />
    </div>
  );
}