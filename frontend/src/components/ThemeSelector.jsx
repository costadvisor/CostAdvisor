import { useState } from 'react';
import { useAuth } from '../AuthContext';
import { THEMES } from '../utils/theme';

/* Theme cycler — a single icon button between TeamSelector and the account
 * menu. Each click advances to the next theme (wrapping around); the icon
 * is a 3-dot swatch scoped to the CURRENT theme via data-theme, so it always
 * renders that theme's own --accent/--accent2/--accent3 (same scoping trick
 * TeamSelector's dot and Profile's ThemePreview cards use) — so the icon
 * itself changes color combo on every click, not just the app around it.
 * Calls the existing setTheme() from AuthContext — no new API, no
 * duplicated apply/cache/persist logic. */
export default function ThemeSelector() {
  const { user, setTheme } = useAuth();
  const [hover, setHover] = useState(false);
  const activeThemeId = user?.theme || 'default';
  const activeIdx = Math.max(0, THEMES.findIndex(t => t.id === activeThemeId));
  const activeTheme = THEMES[activeIdx];
  const nextTheme = THEMES[(activeIdx + 1) % THEMES.length];

  const cycleTheme = () => setTheme(nextTheme.id);

  return (
    <button
      type="button"
      onClick={cycleTheme}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={`Theme: ${activeTheme.label} — click to switch to ${nextTheme.label}`}
      aria-label={`Current theme: ${activeTheme.label}. Click to switch to ${nextTheme.label}.`}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, flexShrink: 0,
        background: 'transparent',
        border: `1px solid ${hover ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 999, cursor: 'pointer',
        transition: 'border-color .15s',
      }}
    >
      <span data-theme={activeTheme.id} aria-hidden style={{ display: 'inline-flex', lineHeight: 0 }}>
        <svg width="18" height="18" viewBox="0 0 18 18">
          <circle cx="6.5" cy="10.5" r="5.5" fill="var(--accent)" />
          <circle cx="11.5" cy="10.5" r="5.5" fill="var(--accent2)" fillOpacity="0.82" />
          <circle cx="9" cy="5.5" r="3.4" fill="var(--accent3)" fillOpacity="0.9" />
        </svg>
      </span>
    </button>
  );
}
