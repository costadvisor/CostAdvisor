/* A small stat tile (market snapshot, spend totals). Lay several out in
 * <div className="ix-kpis"> (auto-fit) or <div className="ix-kpis two">.
 *
 *   <KpiTile label="Should-cost index, latest month" value="101.5" sub="EU · base 100 Jan 2023" />
 *   <KpiTile label="Actions overdue" value={1} tone="bad" />
 *
 * tone: good | bad | warn | info colours the value; size="lg" for a hero number. */
export default function KpiTile({ label, value, sub, tone, size, title, children }) {
  const cls = `ix-kpi${tone ? ` tone-${tone}` : ''}${size === 'lg' ? ' is-large' : ''}`;
  return (
    <div className={cls} title={title}>
      <div className="ix-kpi-label">{label}</div>
      <div className="ix-kpi-value">{value ?? '—'}</div>
      {sub && <div className="ix-kpi-sub">{sub}</div>}
      {children}
    </div>
  );
}
