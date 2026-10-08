import '../../../styles/report.css';

/* One stored report fragment (a section or a panel), rendered inside
 * `.report-wrap` so the delivered report template styles it and nothing else.
 * The HTML is sanitised server-side (allowlist in
 * backend/app/services/content_drop/report_sanitize.py).
 *
 *   <ReportHtml html={panel.html} />                  // a Strategy panel
 *   <ReportHtml html={panel.html} paper />            // on a white sheet
 *
 * Panel html has no heading (it is in `panel.heading`): render the heading in
 * app UI above this. Section html starts with its own <h2>. */
export default function ReportHtml({ html, paper = false, className = '', as: Tag = 'div' }) {
  if (!html) return null;
  return (
    <div className={`report-wrap ixr-fragment${paper ? ' is-paper' : ''} ${className}`}>
      <Tag dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
