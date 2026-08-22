import { useState, useEffect, useRef } from 'react';
import { useAuth } from '../AuthContext';
import RoleBadge from './RoleBadge';

/* Team switcher — a `.ca-menu` dropdown mirroring the account-menu button right
 * next to it in Navbar.jsx (same shared primitive, same roving-tabindex keyboard
 * model). Was previously a bare native <select> forced narrow with inline style
 * overrides on `.ca-select` (a full-width form-input class), which showed only
 * the team name — no role, no keyboard-menu semantics, and visually inconsistent
 * with the account menu one flex item away.
 *
 * `role` is already present on every team object in AuthContext (`AuthContext.jsx`
 * builds it straight from the membership list), so the badge costs nothing extra
 * to fetch. Plan tier / member count are NOT in that payload today and would need
 * a backend schema change — deliberately out of scope here. */
export default function TeamSelector() {
  const { teams, activeTeamId, switchTeam } = useAuth();
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
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
    // Escape returns focus to the trigger — same reasoning as the account menu:
    // without this the menu closes and focus is dropped on <body>.
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

  if (teams.length <= 1) return null;

  const activeTeam = teams.find(t => t.id === activeTeamId) || teams[0];

  const focusItem = (i) => {
    const next = (i + teams.length) % teams.length;
    setActiveIdx(next);
    itemRefs.current[next]?.focus();
  };

  const onMenuKeyDown = (e) => {
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); focusItem(activeIdx + 1); break;
      case 'ArrowUp': e.preventDefault(); focusItem(activeIdx - 1); break;
      case 'Home': e.preventDefault(); focusItem(0); break;
      case 'End': e.preventDefault(); focusItem(teams.length - 1); break;
      case 'Tab': closeMenu(); break;
      default: break;
    }
  };

  const selectTeam = (teamId) => {
    switchTeam(teamId);
    setOpen(false);
  };

  return (
    <div ref={menuRef} style={{ position: 'relative' }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(o => !o)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="ca-team-menu"
        title={activeTeam?.name}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          // A visible border at rest — not just on open — is what reads as
          // clickable; the account-menu trigger next to it gets away with
          // transparent-until-open because its avatar circle already looks
          // interactive, but plain text + a dot didn't carry that on its own.
          background: 'transparent',
          border: `1px solid ${open || hover ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 999, padding: '5px 10px', cursor: 'pointer',
          color: open || hover ? 'var(--accent)' : 'var(--text-secondary)',
          fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          transition: 'border-color .15s, color .15s',
        }}
      >
        <span aria-hidden style={{
          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: 'var(--accent)',
        }} />
        <span style={{
          maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {activeTeam?.name}
        </span>
        <span style={{ fontSize: 9, opacity: 0.6 }}>▾</span>
      </button>

      {open && (
        <div
          id="ca-team-menu"
          className="ca-menu"
          role="menu"
          aria-label="Switch team"
          onKeyDown={onMenuKeyDown}
          style={{ minWidth: 240 }}
        >
          <div className="ca-menu-label">Switch team</div>
          {teams.map((t, i) => {
            const isActive = t.id === activeTeamId;
            return (
              // role="menuitemradio" (not the account menu's plain "menuitem") — this
              // list represents a persistent single-choice state, not a set of
              // one-shot navigation actions, so aria-checked is the correct contract.
              <button
                key={t.id}
                ref={el => { itemRefs.current[i] = el; }}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                className="ca-menu-item"
                tabIndex={i === activeIdx ? 0 : -1}
                onFocus={() => setActiveIdx(i)}
                onClick={() => selectTeam(t.id)}
                title={t.name}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
                  <span aria-hidden style={{
                    width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                    background: isActive ? 'var(--accent)' : 'transparent',
                    border: isActive ? 'none' : '1px solid var(--border-light)',
                  }} />
                  <span style={{
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    color: isActive ? 'var(--accent)' : 'var(--text)',
                  }}>
                    {t.name}
                  </span>
                </span>
                <RoleBadge role={t.role} />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
