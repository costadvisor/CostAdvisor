import { useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { EmptyState, ErrorState, LoadingPanel, useApi } from '../../../components/intel';
import ReportView from '../../../components/intel/report/ReportView';
import ReportCaveat from '../../../components/intel/report/ReportCaveat';
import { playbookHref } from './lineUtil';

/* Line detail › Report: the delivered market report(s) serving this line.
 * Several reports can serve one line (the line ↔ report link is
 * many-to-many); `?report=<slug>` picks one, default the first
 * (`report_map` rows come first). */
export default function ReportTab({ line }) {
  const [params, setParams] = useSearchParams();
  const reports = line.reports || [];
  const requested = params.get('report');
  const current = reports.find((r) => r.slug === requested) || reports[0] || null;

  const { data: report, error, loading, reload } = useApi(
    current ? `/api/intel/reports/${encodeURIComponent(current.slug)}` : null,
  );

  const pick = useCallback((slug) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('report', slug);
      return next;
    }, { replace: true });
  }, [setParams]);

  if (!current) {
    return (
      <EmptyState title="No market report for this line yet"
        body="No market report covers this product line yet." />
    );
  }

  const bar = (reports.length > 1 || current.playbook_slug) ? (
    <div className="ixl-report-bar">
      {reports.length > 1 ? (
        <div className="ixl-switch" role="group" aria-label="Market reports for this line">
          <span className="ixl-switch-label">{reports.length} market reports cover this line:</span>
          {reports.map((r) => (
            <button key={r.slug} type="button" className="ix-pill" aria-pressed={r.slug === current.slug}
              onClick={() => pick(r.slug)} title={`Written for the line then called ${r.old_line_name || r.name}`}>
              {r.name}
            </button>
          ))}
        </div>
      ) : <span />}
      {current.playbook_slug && (
        <div className="ixl-report-links">
          <Link className="ix-link" to={playbookHref(current.playbook_slug)}>
            Strategy playbook: {current.name} →
          </Link>
        </div>
      )}
    </div>
  ) : null;

  if (error && !report) {
    return (
      <div className="ixl-report-tab ix-stack">
        {bar}
        <ErrorState error={error} onRetry={reload} title="Could not load this report" />
      </div>
    );
  }

  if (loading || !report) {
    return (
      <div className="ixl-report-tab ix-stack">
        {bar}
        <LoadingPanel lines={8} />
      </div>
    );
  }

  return (
    <div className="ixl-report-tab">
      <ReportView
        key={report.slug}
        report={report}
        lead={(
          <>
            {bar}
            <ReportCaveat caveat={report.caveat} currentLineId={line.id} />
          </>
        )}
      />
    </div>
  );
}
