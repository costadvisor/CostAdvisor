import { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import api from '../api';
import TeamSelector from './TeamSelector';
import ThemeSelector from './ThemeSelector';
import Logo from './Logo';

// Mirrors the host-aware branching landing/index.html's own script uses for
// API_URL/APP_URL — the logo now points *out* to the marketing site (the app
// itself has a real Dashboard tab for internal navigation instead).
const LANDING_URL = window.location.hostname.includes('dev.')
  ? 'https://dev.costadvisor.org'
  : (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? 'http://localhost:3333'
    : 'https://costadvisor.org';

// A path is "inside" a destination when it is the destination or one of its
// children — `/portfolio/12` is Portfolio, `/portfolio-x` is not.
const isUnder = (pathname, path) => pathname === path || pathname.startsWith(`${path}/`);

// Pointer leaving a hover-opened dropdown closes it after this grace period,
// so crossing the gap between trigger and menu does not flicker it shut.
const HOVER_CLOSE_MS = 160;

export default function Navbar() {
  const location = useLocation();
  const { user, logout, pendingInviteCount, activeTeamId } = useAuth();
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const menuRef = useRef(null);
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);
  // An avatar that fails to load (blocked, offline) falls back to the initial
  // letter instead of a broken-image icon; keyed by URL so a new one retries.
  const [failedAvatar, setFailedAvatar] = useState(null);

  // There is no effective-permissions read in this app; the convention is a
  // per-feature probe, and this is the one for contracts.
  const [canSeeContracts, setCanSeeContracts] = useState(false);
  useEffect(() => {
    if (!activeTeamId) { setCanSeeContracts(false); return; }
    let cancelled = false;
    api.get('/api/contracts/can-access', { params: { team_id: activeTeamId } })
      .then(({ data }) => { if (!cancelled) setCanSeeContracts(!!data.can_view); })
      .catch(() => { if (!cancelled) setCanSeeContracts(false); });
    return () => { cancelled = true; };
  }, [activeTeamId]);

  // Support staff is a **platform** role, not a team one, so this probe has no
  // team dependency and must not be re-run on a team switch. Same per-feature
  // probe convention as canSeeContracts above; /auth/me carries no platform-role
  // list to check client-side, which is why the endpoint exists.
  const [isSupportStaff, setIsSupportStaff] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.get('/api/support/is-staff')
      .then(({ data }) => { if (!cancelled) setIsSupportStaff(!!data.is_staff); })
      .catch(() => { if (!cancelled) setIsSupportStaff(false); });
    return () => { cancelled = true; };
  }, []);

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

  /* ── Primary nav: the mockups' dropdown groups (DEMO_BUILD_SPEC §7) ──
   * Intelligence ▾ (the platform reference: catalogue, lines, the demand axis,
   * producers, index library) · Portfolio ▾ (what the team buys) · Strategy ·
   * Negotiation ▾ (monitor → forecast → negotiate, plus the formula library)
   * — with Dashboard, Team and Admin top-level as before. Every destination
   * the old flat tabs reached is still one click into a group. `match` widens
   * a group's active highlight to routes that have no menu entry of their own
   * (combo pages under /intelligence, cost-model pages under Portfolio). */
  const nav = [
    { kind: 'link', path: '/dashboard', label: 'Dashboard' },
    {
      kind: 'group', id: 'intelligence', label: 'Intelligence', match: ['/intelligence'],
      items: [
        { path: '/intelligence/products', label: 'Products' },
        { path: '/intelligence/lines', label: 'Product lines' },
        { path: '/intelligence/categories', label: 'Categories' },
        { path: '/intelligence/suppliers', label: 'Suppliers' },
        { path: '/index-library', label: 'Indexes' },
      ],
    },
    {
      kind: 'group', id: 'portfolio', label: 'Portfolio', match: ['/cost-models'],
      items: [
        { path: '/portfolio', label: 'Portfolio' },
        { path: '/products', label: 'Products' },
        { path: '/suppliers', label: 'Suppliers' },
      ],
    },
    { kind: 'link', path: '/strategy', label: 'Strategy' },
    {
      kind: 'group', id: 'negotiation', label: 'Negotiation',
      items: [
        { path: '/monitor', label: 'Monitor' },
        { path: '/forecast', label: 'Forecast' },
        { path: '/negotiate', label: 'Negotiate' },
        { path: '/formulas', label: 'Formulas' },
      ],
    },
    { kind: 'link', path: '/team', label: 'Team', badge: pendingInviteCount || 0 },
    ...(user?.is_super_admin ? [{ kind: 'link', path: '/admin', label: 'Admin' }] : []),
  ];

  const groupActive = (g) =>
    g.items.some((i) => isUnder(location.pathname, i.path))
    || (g.match || []).some((p) => isUnder(location.pathname, p));

  /* Dropdown state: one group open at a time. `openedBy` distinguishes a
   * hover-open (closes when the pointer leaves) from a click/keyboard open
   * (stays until outside click, Escape, Tab-out or navigation). */
  const [openGroup, setOpenGroup] = useState(null);
  const openedBy = useRef(null);
  const closeTimer = useRef(null);
  const groupRefs = useRef({});
  const triggerRefs = useRef({});
  const groupItemRefs = useRef({});
  const topRefs = useRef([]);
  const [focusReq, setFocusReq] = useState(null);

  const openGroupAs = useCallback((id, how) => {
    clearTimeout(closeTimer.current);
    openedBy.current = how;
    setOpenGroup(id);
    setOpen(false);
  }, []);
  const closeGroup = useCallback((restoreFocusTo = null) => {
    clearTimeout(closeTimer.current);
    setOpenGroup(null);
    openedBy.current = null;
    if (restoreFocusTo) triggerRefs.current[restoreFocusTo]?.focus();
  }, []);

  // Navigating anywhere closes the dropdown.
  useEffect(() => { closeGroup(); }, [location.pathname, closeGroup]);

  useEffect(() => {
    if (!openGroup) return undefined;
    const onDocDown = (e) => {
      const el = groupRefs.current[openGroup];
      if (el && !el.contains(e.target)) closeGroup();
    };
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      const el = groupRefs.current[openGroup];
      closeGroup(el && el.contains(document.activeElement) ? openGroup : null);
    };
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [openGroup, closeGroup]);

  useEffect(() => () => clearTimeout(closeTimer.current), []);

  // Keyboard-opened menus move focus to the requested item once rendered.
  useEffect(() => {
    if (!focusReq || openGroup !== focusReq.id) return undefined;
    const raf = requestAnimationFrame(() => {
      const els = (groupItemRefs.current[focusReq.id] || []).filter(Boolean);
      const i = focusReq.index < 0 ? els.length - 1 : Math.min(focusReq.index, els.length - 1);
      els[i]?.focus();
      setFocusReq(null);
    });
    return () => cancelAnimationFrame(raf);
  }, [focusReq, openGroup]);

  const focusTop = (i) => {
    const els = topRefs.current.filter(Boolean);
    if (!els.length) return;
    els[(i + els.length) % els.length]?.focus();
  };
  const topIndexOf = (el) => topRefs.current.filter(Boolean).indexOf(el);

  // Enter/Space arrive here as a click with detail 0; those open the menu with
  // focus on its first item, as the menu-button pattern expects.
  const onTriggerClick = (e, id) => {
    const viaKeyboard = e.detail === 0;
    if (openGroup === id && openedBy.current === 'hover' && !viaKeyboard) { openedBy.current = 'click'; return; }
    if (openGroup === id) { closeGroup(); return; }
    openGroupAs(id, viaKeyboard ? 'key' : 'click');
    if (viaKeyboard) setFocusReq({ id, index: 0 });
  };

  const onTriggerKeyDown = (e, id) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        openGroupAs(id, 'key');
        setFocusReq({ id, index: 0 });
        break;
      case 'ArrowUp':
        e.preventDefault();
        openGroupAs(id, 'key');
        setFocusReq({ id, index: -1 });
        break;
      case 'ArrowRight':
      case 'ArrowLeft':
        e.preventDefault();
        closeGroup();
        focusTop(topIndexOf(e.currentTarget) + (e.key === 'ArrowRight' ? 1 : -1));
        break;
      default: break;
    }
  };

  const onTopLinkKeyDown = (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      focusTop(topIndexOf(e.currentTarget) + (e.key === 'ArrowRight' ? 1 : -1));
    }
  };

  const onGroupMenuKeyDown = (e, id) => {
    const els = (groupItemRefs.current[id] || []).filter(Boolean);
    const cur = els.indexOf(document.activeElement);
    const go = (i) => els[(i + els.length) % els.length]?.focus();
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); go(cur + 1); break;
      case 'ArrowUp': e.preventDefault(); go(cur - 1); break;
      case 'Home': e.preventDefault(); go(0); break;
      case 'End': e.preventDefault(); go(els.length - 1); break;
      case 'Tab': closeGroup(); break;
      case 'ArrowRight':
      case 'ArrowLeft': {
        e.preventDefault();
        const trig = triggerRefs.current[id];
        closeGroup();
        focusTop(topIndexOf(trig) + (e.key === 'ArrowRight' ? 1 : -1));
        break;
      }
      default: break;
    }
  };

  // Old flat-nav pages with no slot in the primary nav. This is not a
  // leftovers list — /alerts and /quotes have no other inbound link anywhere
  // in the app, so for those this menu is the sole entry point. Products,
  // Suppliers and Formulas now also sit in the Portfolio/Negotiation groups;
  // they stay here so nothing a user already knows how to reach moves away.
  // The Wave 3 combo grid (/intelligence/combos) is off the menu; its route
  // stays, and the combo pages are reached from Portfolio.
  const goToLinks = [
    { path: '/products', label: 'Products' },
    { path: '/suppliers', label: 'Suppliers' },
    { path: '/formulas', label: 'Formulas' },
    { path: '/alerts', label: 'Alerts' },
    { path: '/quotes', label: 'Quotes' },
    { path: '/price-lists', label: 'Price lists' },
    { path: '/ai-cost-modeler', label: 'AI cost modeler' },
    // Contracts is conditional, not just conditionally useful: contract prices
    // and notice dates sit behind their own `contracts.*` permission category,
    // separate from costing, and a role without it must not even see the entry.
    // That separation is the reason the category exists.
    ...(canSeeContracts ? [{ path: '/contracts', label: 'Contracts' }] : []),
    { path: '/curation', label: 'Curation' },
    { path: '/dimensions', label: 'Dimensions' },
    // Reading findings is open to any authenticated user; only the Run
    // button inside is super-admin, matching the API's own split.
    { path: '/validation', label: 'Data quality' },
    { path: '/index-sourcing', label: 'Index sourcing' },
    { path: '/scenarios', label: 'Scenarios' },
    { path: '/support', label: 'Support' },
    // The staff side. A super admin already reaches it via Admin → Support;
    // this is the only door for somebody holding just the Support Agent
    // platform role, who until now had full API access and no way in.
    ...(isSupportStaff ? [{ path: '/support-console', label: 'Support console' }] : []),
    // Everything that was mocked here is built; the page now lists only the
    // blockers no coding session closes.
    { path: '/preview', label: 'What’s left' },
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

  // Refs are written only by ref callbacks, never reset during render: a
  // render React throws away (a same-state bail-out) would otherwise leave the
  // arrays empty with no commit to refill them.
  let topSlot = 0;

  return (
    <nav className="ca-nav" aria-label="Main">
      <div
        className="ca-logo"
        onClick={() => { window.location.href = LANDING_URL; }}
        title="Visit the CostAdvisor website"
        style={{ display: 'flex', alignItems: 'center', gap: 6 }}
      >
        <Logo size={34} style={{ borderRadius: 9, boxShadow: '0 3px 10px rgba(15,34,40,.18)' }} />
        Cost<span>Advisor</span>
      </div>
      {nav.map((entry) => {
        const slot = topSlot++;
        if (entry.kind === 'link') {
          const active = isUnder(location.pathname, entry.path);
          return (
            <Link
              key={entry.path}
              to={entry.path}
              ref={(el) => { topRefs.current[slot] = el; }}
              className={`ca-tab ${active ? 'active' : ''}`}
              aria-current={active ? 'page' : undefined}
              onKeyDown={onTopLinkKeyDown}
            >
              {entry.label}
              {entry.badge > 0 && (
                <span
                  aria-label={`${entry.badge} pending`}
                  style={{
                    position: 'absolute', top: 2, right: -6,
                    background: 'var(--accent2)', color: '#fff',
                    borderRadius: 999, fontSize: 9, fontWeight: 700,
                    minWidth: 16, height: 16, display: 'inline-flex',
                    alignItems: 'center', justifyContent: 'center',
                    padding: '0 4px', lineHeight: 1,
                  }}
                >
                  {entry.badge > 9 ? '9+' : entry.badge}
                </span>
              )}
            </Link>
          );
        }
        const g = entry;
        const isOpen = openGroup === g.id;
        const active = groupActive(g);
        const menuId = `ix-nav-menu-${g.id}`;
        const trigId = `ix-nav-trigger-${g.id}`;
        if (!groupItemRefs.current[g.id]) groupItemRefs.current[g.id] = [];
        return (
          <div
            key={g.id}
            className="ix-nav-group"
            ref={(el) => { groupRefs.current[g.id] = el; }}
            onMouseEnter={() => {
              clearTimeout(closeTimer.current);
              if (openGroup !== g.id || !openedBy.current) openGroupAs(g.id, 'hover');
            }}
            onMouseLeave={() => {
              if (openedBy.current !== 'hover') return;
              clearTimeout(closeTimer.current);
              closeTimer.current = setTimeout(() => {
                if (openedBy.current === 'hover') closeGroup();
              }, HOVER_CLOSE_MS);
            }}
          >
            <button
              type="button"
              id={trigId}
              ref={(el) => { triggerRefs.current[g.id] = el; topRefs.current[slot] = el; }}
              className={`ca-tab ix-nav-trigger ${active ? 'active' : ''}`}
              aria-haspopup="menu"
              aria-expanded={isOpen}
              aria-controls={isOpen ? menuId : undefined}
              onClick={(e) => onTriggerClick(e, g.id)}
              onKeyDown={(e) => onTriggerKeyDown(e, g.id)}
            >
              {g.label}
              <span className="ix-nav-caret" aria-hidden>▾</span>
            </button>
            {isOpen && (
              <div
                id={menuId}
                className="ix-nav-menu"
                role="menu"
                aria-labelledby={trigId}
                onKeyDown={(e) => onGroupMenuKeyDown(e, g.id)}
              >
                {g.items.map((item, i) => {
                  const current = isUnder(location.pathname, item.path);
                  return (
                    <Link
                      key={item.path}
                      to={item.path}
                      role="menuitem"
                      tabIndex={-1}
                      ref={(el) => { groupItemRefs.current[g.id][i] = el; }}
                      className="ix-nav-item"
                      aria-current={current ? 'page' : undefined}
                      onClick={() => closeGroup()}
                    >
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      {/* `.ix-nav-right` (intel.css) compacts this cluster and the tab padding
          at 1400px and below, so the nav fits a 1280px viewport without a
          horizontal scroll: the user name hides, the team pill caps its width. */}
      <div className="ix-nav-right">
        <TeamSelector />
        <ThemeSelector />
        {/* The company used to be a pill here, up to 160px wide. `.ca-nav` has no
            wrap or overflow handling, and a super-admin with 8 tabs + the team
            selector was already close to overflowing at 1280px — so it moved into
            the menu's identity block, where it reads better anyway. */}
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            ref={triggerRef}
            type="button"
            onClick={() => { setOpen(o => !o); closeGroup(); }}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-controls="ca-account-menu"
            aria-label={`Account menu${user?.display_name ? ` for ${user.display_name}` : ''}`}
            title={user?.display_name || user?.email}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              background: 'transparent', border: '1px solid transparent',
              borderRadius: 999, padding: '4px 10px 4px 4px', cursor: 'pointer',
              color: 'var(--text-secondary)',
              borderColor: open ? 'var(--border)' : 'transparent',
            }}
          >
            {user?.avatar_url && failedAvatar !== user.avatar_url ? (
              <img
                src={user.avatar_url}
                alt=""
                referrerPolicy="no-referrer"
                onError={() => setFailedAvatar(user.avatar_url)}
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
            <span className="ix-nav-user-name">{user?.display_name}</span>
            <span aria-hidden style={{ fontSize: 9, opacity: 0.6 }}>▾</span>
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
