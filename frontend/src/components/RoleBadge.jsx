// Extracted from Team.jsx (was defined locally there) so TeamSelector.jsx can
// reuse the same owner/admin/member colour treatment instead of duplicating it.
export const ROLE_COLORS = {
  owner:  { bg: 'var(--accent-dim)',  color: 'var(--accent)' },
  admin:  { bg: 'var(--info-bg)',     color: 'var(--accent3)' },
  member: { bg: 'var(--neutral-bg)',  color: 'var(--muted)' },
};

export default function RoleBadge({ role }) {
  const s = ROLE_COLORS[role] || ROLE_COLORS.member;
  return (
    <span style={{
      display: 'inline-block', padding: '1px 8px', borderRadius: 4,
      fontSize: 10, fontWeight: 600, background: s.bg, color: s.color,
    }}>{role}</span>
  );
}
