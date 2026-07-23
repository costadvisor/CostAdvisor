import { useAuth } from '../AuthContext';
import { Link, Navigate } from 'react-router-dom';

const LOGIN_ERROR_MESSAGES = {
  access_pending:  "Your access request is under review. We'll email you when it's approved.",
  access_rejected: 'Your access request was not approved. Contact access@costadvisor.org for help.',
  access_needed:   'CostAdvisor is invite-only. Request access at costadvisor.org.',
  signup_disabled: 'Sign-ups are currently disabled. Contact your administrator.',
};

export default function Login() {
  const { user, loading, loginError } = useAuth();

  if (loading) return null;
  if (user) return <Navigate to="/" replace />;

  const errorMsg = loginError ? LOGIN_ERROR_MESSAGES[loginError] : null;

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: 'calc(100vh - 80px)', gap: 24,
    }}>
      <div style={{ textAlign: 'center' }}>
        <div style={{
          fontFamily: "'Space Grotesk', system-ui, sans-serif", fontWeight: 700,
          fontSize: 36, letterSpacing: '-0.02em', color: 'var(--accent)', marginBottom: 8,
        }}>
          Cost<span style={{ color: 'var(--muted)' }}>Advisor</span>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
          Should-Cost Estimator for Chemical Products
        </p>
      </div>

      {errorMsg && (
        <div style={{
          background: 'var(--accent2-dim, #fef2f2)',
          border: '1px solid var(--accent2, #ef4444)',
          borderRadius: 8, padding: '12px 20px',
          fontSize: 13, color: 'var(--accent2, #ef4444)',
          maxWidth: 360, textAlign: 'center', lineHeight: 1.5,
        }}>
          {errorMsg}
          {loginError === 'access_needed' && (
            <div style={{ marginTop: 8 }}>
              <a href="https://www.costadvisor.org#cta" style={{ color: 'inherit', fontWeight: 600 }}>
                Request access →
              </a>
            </div>
          )}
        </div>
      )}

      <a
        href={`${import.meta.env.VITE_API_BASE_URL || ''}/auth/login`}
        className="ca-btn ca-btn-primary"
        style={{ textDecoration: 'none', fontSize: 13, padding: '14px 32px' }}
      >
        Sign in with Google
      </a>
      <p style={{ color: 'var(--muted)', fontSize: 11, textAlign: 'center', maxWidth: 320 }}>
        By signing in you agree to our{' '}
        <Link to="/terms" style={{ color: 'var(--muted)' }}>Terms</Link> and{' '}
        <Link to="/privacy" style={{ color: 'var(--muted)' }}>Privacy Policy</Link>.
      </p>
    </div>
  );
}