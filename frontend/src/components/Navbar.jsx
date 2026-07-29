import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import TeamSelector from './TeamSelector';


export default function Navbar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout, pendingInviteCount } = useAuth();
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // The 8-tab journey shell (Scrum 61/UI-1): raw index feeds → portfolio →
  // monitor → forecast → negotiate, plus the two cross-cutting/back-office
  // tabs (Intelligence, Team) and Admin (super-admin only). This is the
  // primary nav now — Dashboard/Formulas/Products/Suppliers no longer get a
  // persistent tab (Monitor/Portfolio are their new-IA homes; the other two
  // stay reachable, not gone, via the account menu below).
  const tabs = [
    { path: '/index-library', label: 'Indexes' },
    { path: '/portfolio', label: 'Portfolio' },
    { path: '/monitor', label: 'Monitor' },
    { path: '/forecast', label: 'Forecast' },
    { path: '/negotiate', label: 'Negotiate' },
    { path: '/intelligence', label: 'Intelligence' },
    { path: '/team', label: 'Team', badge: pendingInviteCount || 0 },
    ...(user?.is_super_admin ? [{ path: '/admin', label: 'Admin' }] : []),
  ];

  // Old flat-nav pages that don't have a slot in the 8-tab journey shell but
  // are still functional and still linked-to from within it (e.g. Portfolio's
  // "+ Add product" goes to /products) — kept one click away in the account
  // menu instead of dropped outright, so nothing becomes a dead end.
  const moreLinks = [
    { path: '/dashboard', label: 'Dashboard' },
    { path: '/products', label: 'Products' },
    { path: '/suppliers', label: 'Suppliers' },
    { path: '/formulas', label: 'Formulas' },
    { path: '/alerts', label: 'Alerts' },
  ];

  const goProfile = () => { setOpen(false); navigate('/profile'); };
  const handleLogout = async () => { setOpen(false); await logout(); };

  return (
    <nav className="ca-nav">
      <div className="ca-logo" onClick={() => navigate('/dashboard')}>
        Cost<span>Advisor</span>
      </div>
      {tabs.map(t => (
        <div
          key={t.path}
          className={`ca-tab ${location.pathname.startsWith(t.path) ? 'active' : ''}`}
          onClick={() => navigate(t.path)}
          style={{ position: 'relative' }}
        >
          {t.label}
          {t.badge > 0 && (
            <span style={{
              position: 'absolute', top: 2, right: -6,
              background: 'var(--accent2)', color: '#fff',
              borderRadius: 999, fontSize: 9, fontWeight: 700,
              minWidth: 16, height: 16, display: 'inline-flex',
              alignItems: 'center', justifyContent: 'center',
              padding: '0 4px', lineHeight: 1,
            }}>
              {t.badge > 9 ? '9+' : t.badge}
            </span>
          )}
        </div>
      ))}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <TeamSelector />
        {user?.company && (
          <span style={{
            fontSize: 10, fontWeight: 600, letterSpacing: '0.4px',
            padding: '3px 10px', borderRadius: 999,
            background: 'var(--surface2)', border: '1px solid var(--border)',
            color: 'var(--muted)', whiteSpace: 'nowrap', maxWidth: 160,
            overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {user.company}
          </span>
        )}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            aria-haspopup="menu"
            aria-expanded={open}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              background: 'transparent', border: '1px solid transparent',
              borderRadius: 999, padding: '4px 10px 4px 4px', cursor: 'pointer',
              color: 'var(--text-secondary)',
              borderColor: open ? 'var(--border)' : 'transparent',
            }}
          >
            {user?.avatar_url ? (
              <img
                src={user.avatar_url}
                alt=""
                style={{ width: 28, height: 28, borderRadius: '50%' }}
              />
            ) : (
              <span style={{
                width: 28, height: 28, borderRadius: '50%',
                background: 'var(--surface2)', display: 'inline-flex',
                alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 600, color: 'var(--text)',
              }}>
                {(user?.display_name || user?.email || '?').slice(0, 1).toUpperCase()}
              </span>
            )}
            <span style={{ fontSize: 11 }}>{user?.display_name}</span>
            <span style={{ fontSize: 9, opacity: 0.6 }}>▾</span>
          </button>
          {open && (
            <div
              role="menu"
              style={{
                position: 'absolute', top: 'calc(100% + 6px)', right: 0,
                minWidth: 200, background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                boxShadow: '0 8px 24px rgba(0,0,0,0.18)', padding: 6, zIndex: 50,
              }}
            >
              <div style={{
                padding: '8px 10px', borderBottom: '1px solid var(--border)',
                marginBottom: 4,
              }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)' }}>
                  {user?.display_name}
                </div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
                  {user?.email}
                </div>
              </div>
              <div style={{ padding: '4px 10px 2px', fontSize: 9, fontWeight: 700, letterSpacing: '0.5px', textTransform: 'uppercase', color: 'var(--muted)' }}>More</div>
              {moreLinks.map(l => (
                <MenuItem key={l.path} onClick={() => { setOpen(false); navigate(l.path); }} label={l.label} />
              ))}
              <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
              <MenuItem onClick={goProfile} label="Profile" />
              <MenuItem onClick={handleLogout} label="Logout" />
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}

function MenuItem({ onClick, label }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      style={{
        display: 'block', width: '100%', textAlign: 'left',
        background: 'transparent', border: 'none', cursor: 'pointer',
        padding: '8px 10px', borderRadius: 6, fontSize: 11,
        color: 'var(--text)',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface2)'; }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
    >
      {label}
    </button>
  );
}
