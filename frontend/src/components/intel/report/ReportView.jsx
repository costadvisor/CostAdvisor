import { useEffect, useLayoutEffect, useMemo, useRef, useState, useCallback } from 'react';
import EmptyState from '../EmptyState';
import { asOfLabel, sectionDomId } from './reportUtil';
import '../../../styles/report.css';

/* A delivered market report, shown as the document it is.
 *
 *   <ReportView report={report} lead={<ReportCaveat caveat={report.caveat} />} />
 *
 * report: the `/api/intel/reports/{slug}` response. Its `sections[].html` are
 *         rendered exactly as stored, in order, inside `.report-wrap` (each
 *         starts with its own <h2> — no heading is added).
 * lead:   app UI placed above the document in the same column (switcher,
 *         caveat banner). It stays outside `.report-wrap`.
 * toc:    sticky table of contents on the left (default on). The entry for
 *         the section in view is highlighted and lists that section's <h3>s.
 * only:   optional list of section ids to show (e.g. ['strategic']).
 *
 * The document keeps the report template's own light palette in every app
 * theme (styles/report.css); the frame around it uses the theme tokens. */

const FOOTER_NOTE = 'This report combines publicly available market data, CostAdvisor’s proprietary cost models, '
  + 'and analytical judgment. Source attribution is provided per section. Analytical assessments '
  + '(PESTEL, Porter’s, Kraljic) should be validated against the reader’s own market experience. '
  + 'This does not constitute commercial advice.';

/* Height of the page's sticky detail header (.ix-dh), so the contents rail
   and section jumps clear it. 0 when the page has none. */
function useDetailHeaderHeight() {
  const [h, setH] = useState(0);
  useLayoutEffect(() => {
    const el = document.querySelector('.ix-dh');
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const update = () => setH(Math.round(el.getBoundingClientRect().height));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return h;
}

/* Bottom edge of whatever is pinned to the top of the viewport (the app nav,
   and the detail header strip when the page has one). */
function stickyBottom() {
  const bottoms = ['.ix-dh', '.ca-nav']
    .map((sel) => document.querySelector(sel))
    .filter(Boolean)
    .map((el) => el.getBoundingClientRect().bottom);
  return Math.max(0, ...bottoms);
}

function splitHeading(heading = '') {
  const m = String(heading).match(/^(\d+)\.\s*(.*)$/);
  return m ? { num: m[1], text: m[2] } : { num: null, text: heading };
}

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export default function ReportView({ report, lead, toc = true, only, header = true, footer = true, className = '' }) {
  const docRef = useRef(null);
  const dhH = useDetailHeaderHeight();
  const [active, setActive] = useState(null);
  const [subs, setSubs] = useState({});

  const sections = useMemo(() => {
    const list = [...(report?.sections || [])].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0));
    return only ? list.filter((s) => only.includes(s.section_id)) : list;
  }, [report, only]);
  const slug = report?.slug || 'report';

  // Index each section's <h3> blocks for the contents rail (ids are assigned
  // on the rendered DOM: the stored html carries none).
  useLayoutEffect(() => {
    const root = docRef.current;
    if (!root) return;
    const next = {};
    sections.forEach((s) => {
      const el = root.querySelector(`#${sectionDomId(slug, s.section_id)}`);
      if (!el) return;
      next[s.section_id] = Array.from(el.querySelectorAll('h3')).map((h, i) => {
        const id = `${sectionDomId(slug, s.section_id)}-h${i + 1}`;
        h.id = id;
        return { id, text: h.textContent.trim() };
      });
    });
    setSubs(next);
    setActive(sections[0]?.section_id ?? null);
  }, [sections, slug]);

  // Which section is in view: the last one whose top has passed under the
  // sticky header.
  useEffect(() => {
    if (!toc || !sections.length) return undefined;
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!docRef.current) return;
        const line = stickyBottom() + 40;
        let current = sections[0].section_id;
        for (const s of sections) {
          const el = document.getElementById(sectionDomId(slug, s.section_id));
          if (el && el.getBoundingClientRect().top <= line) current = s.section_id;
        }
        setActive(current);
      });
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, [toc, sections, slug, dhH]);

  const jump = useCallback((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
  }, []);

  if (!report) return null;
  if (!sections.length) {
    return <EmptyState title="This report has no sections" body="Not in the source data." />;
  }

  const when = asOfLabel(report.as_of);
  const style = { '--ixr-dh-h': `${dhH}px` };

  return (
    <div className={`ixr-viewer${toc ? '' : ' no-toc'} ${className}`} style={style} ref={docRef}>
      {toc && (
        <nav className="ixr-toc" aria-label="Report contents">
          <div className="ixr-toc-title">Contents</div>
          <ol className="ixr-toc-list">
            {sections.map((s) => {
              const { num, text } = splitHeading(s.heading);
              const isActive = s.section_id === active;
              const subList = subs[s.section_id] || [];
              return (
                <li key={s.section_id}>
                  <button type="button" className={`ixr-toc-link${isActive ? ' is-active' : ''}`}
                    aria-current={isActive ? 'location' : undefined}
                    onClick={() => jump(sectionDomId(slug, s.section_id))}>
                    {num && <span className="ixr-toc-num">{num}</span>}
                    <span className="ixr-toc-text">{text}</span>
                  </button>
                  {isActive && subList.length > 0 && (
                    <ol className="ixr-toc-sub">
                      {subList.map((h) => (
                        <li key={h.id}>
                          <button type="button" className="ixr-toc-sublink" onClick={() => jump(h.id)} title={h.text}>
                            {h.text}
                          </button>
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="ixr-toc-foot">
            {report.word_count ? `${Number(report.word_count).toLocaleString('en-GB')} words` : null}
            {report.word_count && when ? ' · ' : null}
            {when}
          </div>
        </nav>
      )}

      <div className="ixr-main">
        {lead && <div className="ixr-lead">{lead}</div>}
        <article className="ixr-paper" aria-label={`${report.name} market report`}>
          <div className="report-wrap">
            {header && (
              <header className="report-header">
                <div className="container">
                  <div className="badge">Product Line Intelligence Report</div>
                  {report.family && <p className="ixr-hdr-crumb">{report.family} › {report.name}</p>}
                  <h1>{report.name}</h1>
                  {when && <p className="meta">CostAdvisor · {when}</p>}
                </div>
              </header>
            )}
            <div className="container">
              {sections.map((s) => (
                <section key={s.section_id} id={sectionDomId(slug, s.section_id)}
                  dangerouslySetInnerHTML={{ __html: s.html }} />
              ))}
            </div>
            {footer && (
              <footer className="footer">
                <p>CostAdvisor · Product Line Intelligence Report · {report.name}{when ? ` · ${when}` : ''}</p>
                <p>{FOOTER_NOTE}</p>
              </footer>
            )}
          </div>
        </article>
      </div>
    </div>
  );
}
