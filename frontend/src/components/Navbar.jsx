import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import TeamSelector from './TeamSelector';


export default function Navbar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout, pendingInviteCount } = useAuth();
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const menuRef = useRef(null);
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);

  const closeMenu = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    // Escape returns focus to the trigger — without this the menu closes and
    // focus is dropped on <body>, stranding keyboard users at the top of the page.
    const onKey = (e) => { if (e.key === 'Escape') closeMenu(true); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // role="menu" promises arrow-key navigation under WAI-ARIA; move focus into
  // the menu on open so that contract is actually honoured.
  useEffect(() => {
    if (!open) return;
    setActiveIdx(0);
    const id = requestAnimationFrame(() => itemRefs.current[0]?.focus());
    return () => cancelAnimationFrame(id);
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

  // Old flat-nav pages with no slot in the 8-tab journey shell. This is not a
  // leftovers list — /formulas and /alerts have no other inbound link anywhere
  // in the app, and /suppliers' only one is a back-button from its own child,
  // so for three of these five this menu is the sole entry point.
  const goToLinks = [
    { path: '/dashboard', label: 'Dashboard' },
    { path: '/products', label: 'Products' },
    { path: '/suppliers', label: 'Suppliers' },
    { path: '/formulas', label: 'Formulas' },
    { path: '/alerts', label: 'Alerts' },
  ];

  const handleLogout = async () => { setOpen(false); await logout(); };

  // Flat list backing the roving tabindex; the two rendered groups slice it, so
  // arrow keys traverse the whole menu while the group labels stay unfocusable.
  const menuItems = [
    ...goToLinks,
    { path: '/profile', label: 'Profile' },
    { label: 'Logout', onClick: handleLogout },
  ];

  const focusItem = (i) => {
    const next = (i + menuItems.length) % menuItems.length;
    setActiveIdx(next);
    itemRefs.current[next]?.focus();
  };

  const onMenuKeyDown = (e) => {
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); focusItem(activeIdx + 1); break;
      case 'ArrowUp': e.preventDefault(); focusItem(activeIdx - 1); break;
      case 'Home': e.preventDefault(); focusItem(0); break;
      case 'End': e.preventDefault(); focusItem(menuItems.length - 1); break;
      case 'Tab': closeMenu(); break;
      default: break;
    }
  };

  const renderItem = (item, idx) => {
    const shared = {
      ref: el => { itemRefs.current[idx] = el; },
      role: 'menuitem',
      className: 'ca-menu-item',
      tabIndex: idx === activeIdx ? 0 : -1,
      onFocus: () => setActiveIdx(idx),
    };
    if (!item.path) {
      return <button key={item.label} type="button" {...shared} onClick={item.onClick}>{item.label}</button>;
    }
    return (
      <Link
        key={item.path}
        to={item.path}
        {...shared}
        aria-current={location.pathname.startsWith(item.path) ? 'page' : undefined}
        onClick={() => setOpen(false)}
      >
        {item.label}
      </Link>
    );
  };

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
        {/* The company used to be a pill here, up to 160px wide. `.ca-nav` has no
            wrap or overflow handling, and a super-admin with 8 tabs + the team
            selector was already close to overflowing at 1280px — so it moved into
            the menu's identity block, where it reads better anyway. */}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setOpen(o => !o)}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls="ca-account-menu"
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
              id="ca-account-menu"
              className="ca-menu"
              role="menu"
              aria-label="Account and navigation"
              onKeyDown={onMenuKeyDown}
            >
              <div className="ca-menu-identity">
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text)' }}>
                  {user?.display_name}
                </div>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2, wordBreak: 'break-all' }}>
                  {user?.email}
                </div>
                {user?.company && (
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                    {user.company}
                  </div>
                )}
              </div>

              {/* Two named groups rather than one heading called "More" — these are
                  destinations, and the account actions are not simply what's left
                  after the divider. role=group gives screen readers the same split. */}
              <div role="group" aria-labelledby="ca-menu-goto">
                <div className="ca-menu-label" id="ca-menu-goto">Go to</div>
                {goToLinks.map((l, i) => renderItem(l, i))}
              </div>

              <div className="ca-menu-sep" />

              <div role="group" aria-labelledby="ca-menu-account">
                <div className="ca-menu-label" id="ca-menu-account">Account</div>
                {menuItems.slice(goToLinks.length).map((item, i) => renderItem(item, goToLinks.length + i))}
              </div>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}

