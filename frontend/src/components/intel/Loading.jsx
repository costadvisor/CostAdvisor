/* Skeletons — the shape of the content, not a spinner over it. Built on the
 * app's .ca-skeleton pulse (which already honours prefers-reduced-motion). */

export function Skeleton({ width = '100%', height = 10, radius, className = '', style }) {
  return (
    <span
      className={`ca-skeleton ix-skel ${className}`}
      style={{ width, height, borderRadius: radius, ...style }}
      aria-hidden
    />
  );
}

export function SkeletonLines({ lines = 3, widths = ['92%', '78%', '85%', '60%'] }) {
  return (
    <div aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className="ix-skel-line" width={widths[i % widths.length]} />
      ))}
    </div>
  );
}

/* A grid of card placeholders for catalogue pages. */
export function LoadingCards({ count = 8, height = 210, className = 'ix-grid' }) {
  return (
    <div className={className} role="status" aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="ix-skel-card" style={{ height }}>
          <Skeleton width="55%" height={8} />
          <Skeleton width="85%" height={16} />
          <Skeleton width="40%" height={8} />
          <Skeleton height={38} style={{ marginTop: 6 }} />
          <Skeleton width="70%" height={8} />
          <Skeleton width="90%" height={8} style={{ marginTop: 'auto' }} />
        </div>
      ))}
    </div>
  );
}

/* A panel-shaped placeholder: title bar + a few lines (or a chart block). */
export function LoadingPanel({ lines = 4, chart = false, height = 180 }) {
  return (
    <div className="ix-panel" role="status" aria-label="Loading">
      <div className="ix-panel-head"><Skeleton width={160} height={10} /></div>
      <div className="ix-panel-body">
        {chart ? <Skeleton height={height} radius={8} /> : <SkeletonLines lines={lines} />}
      </div>
    </div>
  );
}

/* Whole-page placeholder: a header block and a column of panels. */
export function LoadingPage({ panels = 3 }) {
  return (
    <div role="status" aria-label="Loading">
      <Skeleton width={280} height={24} style={{ marginBottom: 10 }} />
      <Skeleton width={420} height={10} style={{ marginBottom: 24 }} />
      <div className="ix-stack">
        {Array.from({ length: panels }, (_, i) => <LoadingPanel key={i} chart={i === 0} />)}
      </div>
    </div>
  );
}
