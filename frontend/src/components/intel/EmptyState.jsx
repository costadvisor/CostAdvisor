/* First-class empty state. Say plainly what is missing and, where there is
 * one, the way forward.
 *
 *   <EmptyState title="No market report for this line"
 *     body="The August 2026 drop covers 171 lines; this one is not among them." />
 *   <EmptyState variant="compact" title="Not in the source data" />
 *
 * variant: 'default' (dashed box) · 'compact' (inside a panel) · 'bare' (no box). */
export default function EmptyState({ title = 'Nothing here yet', body, icon, action, variant = 'default', children }) {
  const cls = variant === 'compact' ? ' compact' : variant === 'bare' ? ' bare' : '';
  return (
    <div className={`ix-empty${cls}`} role="status">
      {icon && <div className="ix-empty-icon" aria-hidden>{icon}</div>}
      <div className="ix-empty-title">{title}</div>
      {(body || children) && <div className="ix-empty-body">{body}{children}</div>}
      {action && <div className="ix-empty-action">{action}</div>}
    </div>
  );
}

/* An error in the same shape, for a failed request. */
export function ErrorState({ error, onRetry, title = 'Could not load this' }) {
  return (
    <EmptyState
      variant="compact"
      title={title}
      body={<span className="ix-error">{String(error || 'Unknown error')}</span>}
      action={onRetry && <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onRetry}>Retry</button>}
    />
  );
}
