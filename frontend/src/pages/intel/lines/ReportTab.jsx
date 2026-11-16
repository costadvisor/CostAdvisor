import {
  useCallback, useEffect, useLayoutEffect, useRef,
} from 'react';
import {
  Link, useNavigationType, useSearchParams,
} from 'react-router-dom';
import {
  EmptyState, ErrorState, LoadingPanel, useApi, useTabParam,
} from '../../../components/intel';
import ReportView from '../../../components/intel/report/ReportView';
import ReportCaveat from '../../../components/intel/report/ReportCaveat';
import { playbookHref } from './lineUtil';

/* Coming back to a line's report (Back, a reload) lands where the reader
 * was. Kept per line and report for the session; opening the line afresh
 * starts at the top. Saved from the last scroll event, and only after a real
 * one, so StrictMode's rehearsal unmount saves nothing. The line page keeps
 * this tab mounted behind the others and restores a tab switch itself
 * (B-04), so both halves here wait for `active`: a scroll on Products is not
 * the report's place, and a Back that lands on Products must not scroll it
 * to the report's. */
function useReadingPlace(storeKey, ready, active) {
  const navType = useNavigationType();
  const lastY = useRef(0);
  const restored = useRef(false);
  const shown = useRef(active);

  useLayoutEffect(() => { shown.current = active; }, [active]);

  useEffect(() => {
    if (navType !== 'PUSH') return;
    try { sessionStorage.removeItem(storeKey); } catch { /* storage off */ }
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useLayoutEffect(() => {
    let scrolled = false;
    const onScroll = () => {
      if (!shown.current) return;
      scrolled = true;
      lastY.current = window.scrollY;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      if (!scrolled) return;
      try { sessionStorage.setItem(storeKey, String(Math.round(lastY.current))); } catch { /* storage off */ }
    };
  }, [storeKey]);

  useEffect(() => {
    if (!ready || !active || restored.current) return;
    restored.current = true;
    if (navType === 'PUSH') return;
    let y = 0;
    try { y = Number(sessionStorage.getItem(storeKey)) || 0; } catch { y = 0; }
    if (y > 0) requestAnimationFrame(() => window.scrollTo(0, y));
  }, [ready, active, navType, storeKey]);
}

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
  // The line page's own tab rule (LineDetailPage): is this tab the one shown?
  const [shownTab] = useTabParam(['report', 'products', 'demand'], reports.length ? 'report' : 'products');
  useReadingPlace(`ixl-report-y:${line.id}:${current?.slug || ''}`, !!report && !loading, shownTab === 'report');

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
