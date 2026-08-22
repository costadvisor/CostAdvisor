import { useNavigate } from 'react-router-dom';

export default function NotFound() {
  const navigate = useNavigate();

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '60vh',
      gap: 16,
      textAlign: 'center',
      padding: '0 24px',
    }}>
      <span style={{ fontSize: 56, lineHeight: 1, color: 'var(--muted)' }}>404</span>
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Page not found</h1>
      <p style={{ color: 'var(--text-secondary)', margin: 0, maxWidth: 340 }}>
        The page you're looking for doesn't exist or has been moved.
      </p>
      <button
        className="ca-btn ca-btn-primary"
        onClick={() => navigate('/dashboard')}
        style={{ marginTop: 8 }}
      >
        Go to Dashboard
      </button>
    </div>
  );
}
