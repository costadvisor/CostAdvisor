import { Link } from 'react-router-dom';

export const SUPPORT_EMAIL = 'alexis@staminachem.com';

export default function Footer() {
  return (
    <footer
      style={{
        padding: '24px 32px',
        marginTop: 48,
        borderTop: '1px solid var(--border)',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 16,
        justifyContent: 'space-between',
        alignItems: 'center',
        fontSize: 11,
        color: 'var(--muted)',
      }}
    >
      <div>
        © {new Date().getFullYear()} CostAdvisor
      </div>
      <div style={{ display: 'flex', gap: 20 }}>
        <Link to="/privacy" style={{ color: 'var(--muted)', textDecoration: 'none' }}>Privacy</Link>
        <Link to="/terms" style={{ color: 'var(--muted)', textDecoration: 'none' }}>Terms</Link>
        <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: 'var(--muted)', textDecoration: 'none' }}>
          {SUPPORT_EMAIL}
        </a>
      </div>
    </footer>
  );
}
