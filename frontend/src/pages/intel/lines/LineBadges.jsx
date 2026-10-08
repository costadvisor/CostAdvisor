import { lineFlags } from '../../../components/intel';

/* Badges a product line carries on its card and in its header. */

export function ReportBadge({ reports = [], size }) {
  const n = reports.length;
  if (!n) return null;
  const names = reports.map((r) => r.name).join(' · ');
  return (
    <span className={`ix-badge ixl-badge-report${size === 'lg' ? ' lg' : ''}`}
      title={`Market report${n > 1 ? 's' : ''}: ${names}`}>
      {n > 1 ? `${n} reports` : 'Report'}
    </span>
  );
}

/* "Supplier validation pending": the one line flag served, as a fixed label
 * (the authored flag texts are internal). */
export function PendingBadge({ show, flags = [], size }) {
  const labels = lineFlags(flags).map((f) => f.label);
  if (!labels.length && show) labels.push('Supplier validation pending');
  if (!labels.length) return null;
  return (
    <span className={`ix-badge plain st-warn${size === 'lg' ? ' lg' : ''}`} title={labels.join(' · ')}>
      {labels[0]}
    </span>
  );
}
