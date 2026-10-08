import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Panel, KraljicBadge, EmptyState, ErrorState, LoadingPanel, useApi, fmt,
} from '../../../components/intel';
import { KraljicMatrix } from '../../../components/charts';
import AnalysisDemand from '../AnalysisDemand';
import '../../../styles/report.css';

/* Strategic Analysis: the playbook's delivered market report, cut into the
 * mockup's panels. Report HTML (sanitised server-side) renders inside
 * `.report-wrap`; the caveat banner and every piece of app UI stay outside.
 * Data: GET /api/strategy/categories/{slug}/analysis?team_id=. */

const PANELS = {
  overview: { title: 'Overview & market sizing' },
  how: { title: 'How it works', collapsed: true },
  applications: { title: 'Applications' },
  tech: { title: 'Technology / product choices' },
  process: { title: 'Process to price — cost drivers' },
  supply: { title: 'Supply landscape' },
  outlook: { title: 'Outlook' },
  pestel: { title: 'PESTEL' },
  porter: { title: 'Porter’s five forces' },
  market_drivers: { title: 'Market drivers to watch' },
  kraljic: { title: 'Kraljic positioning' },
};
const DEFAULT_ORDER = ['overview', 'how', 'applications', 'tech', 'process', 'supply', 'outlook',
  'pestel', 'porter', 'market_drivers', 'kraljic'];

const anchorId = (key) => `st-analysis-${key}`;

/* "1. Overview" → "Report §1"; "PESTEL — the forces on a market" →
   "The forces on a market". */
function captionOf(heading) {
  if (!heading) return null;
  const parts = String(heading).split(/\s+[—–]\s+/);
  if (parts.length > 1) {
    const rest = parts.slice(1).join(' — ');
    return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  const m = String(heading).match(/^(\d+)\./);
  return m ? `Report §${m[1]}` : null;
}

/* The `.src` attribution blocks of a panel's html, as html strings. */
function srcBlocks(html) {
  if (!html || typeof DOMParser === 'undefined') return [];
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
  return Array.from(doc.querySelectorAll('.src')).map((el) => el.innerHTML);
}

/* Report html in `.report-wrap`. A very long section (the supply landscape
 * runs to several screens) opens clamped, with a toggle for the rest. */
const CLAMP_OVER = 1500;
const CLAMP_TO = 1050;

function ReportHtml({ html }) {
  const ref = useRef(null);
  const [tall, setTall] = useState(false);
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => {
    if (ref.current) setTall(ref.current.scrollHeight > CLAMP_OVER);
  }, [html]);
  const clamped = tall && !open;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next) ref.current?.closest('.st-anchor')?.scrollIntoView({ block: 'start' });
  };
  return (
    <>
      <div className={`st-clamp${clamped ? ' is-clamped' : ''}`} style={clamped ? { maxHeight: CLAMP_TO } : undefined}>
        {/* eslint-disable-next-line react/no-danger */}
        <div ref={ref} className="report-wrap st-report" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
      {tall && (
        <div className="st-clamp-foot">
          <button type="button" className="ix-link ix-small" onClick={toggle} aria-expanded={open}>
            {open ? 'Show less' : 'Show the full section'}
          </button>
        </div>
      )}
    </>
  );
}

function SourceNote({ blocks }) {
  if (!blocks?.length) return null;
  return (
    <div className="st-src">
      {blocks.map((b, i) => (
        // eslint-disable-next-line react/no-danger
        <p key={i} dangerouslySetInnerHTML={{ __html: b }} />
      ))}
    </div>
  );
}

function PestelCards({ cards }) {
  return (
    <div className="st-pestel">
      {cards.map((c) => (
        <div key={c.factor} className="st-card">
          <h3 className="st-card-title">
            <span className="st-card-letter" aria-hidden>{String(c.factor || '?').charAt(0)}</span>
            {c.factor}
          </h3>
          {c.text && <p className="st-card-text">{c.text}</p>}
          {c.implication && <p className="st-card-impl"><strong>For the buyer:</strong> {c.implication}</p>}
        </div>
      ))}
    </div>
  );
}

/* Low 1 · Low–Medium 1.5 · Medium 2 · Medium–High 2.5 · High 3 (of 3). */
function levelScore(text) {
  const t = String(text || '').toLowerCase();
  const has = (w) => t.includes(w);
  if (has('low') && (has('medium') || has('moderate'))) return 1.5;
  if ((has('medium') || has('moderate')) && has('high')) return 2.5;
  if (has('high')) return 3;
  if (has('medium') || has('moderate')) return 2;
  if (has('low')) return 1;
  return null;
}

function LevelMeter({ text }) {
  const s = levelScore(text);
  return (
    <span className="st-level">
      {text || '—'}
      {s != null && (
        <span className="st-meter" aria-hidden>
          {[1, 2, 3].map((i) => (
            <span key={i} className={s >= i ? 'on' : s >= i - 0.5 ? 'half' : ''} />
          ))}
        </span>
      )}
    </span>
  );
}

function PorterTable({ rows }) {
  return (
    <div className="ix-table-wrap">
      <table className="ix-table st-porter">
        <thead>
          <tr>
            <th scope="col">Force</th>
            <th scope="col">Assessment</th>
            <th scope="col">What it means for the buyer</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.force || r.question}>
              <td>
                <div className="ix-cell-main">{r.force || r.question}</div>
                {r.force && r.question && <div className="ix-cell-sub">{r.question}</div>}
              </td>
              <td><LevelMeter text={r.assessment} /></td>
              <td className="st-impl">{r.implication || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DriverCards({ cards }) {
  return (
    <div className="st-drivers">
      {cards.map((c) => (
        <div key={c.title} className="st-card">
          <h3 className="st-card-title">
            {c.arrow && <span className="st-driver-arrow" aria-hidden>{c.arrow}</span>}
            {c.title}
          </h3>
          {c.text && <p className="st-card-text">{c.text}</p>}
        </div>
      ))}
    </div>
  );
}

function KraljicBody({ panel, kraljic }) {
  const k = kraljic || panel || null;
  const narrative = panel?.narrative || [];
  const lead = panel?.lead ? String(panel.lead).trim() : '';
  return (
    <div className="st-kraljic">
      <div className="st-kraljic-chart">
        <KraljicMatrix kraljic={k} size={240} />
        <KraljicBadge kraljic={k} size="lg" />
      </div>
      <div className="st-kraljic-text">
        {narrative.length > 0 ? narrative.map((p, i) => {
          if (i === 0 && lead && p.startsWith(lead)) {
            return <p key={i}><strong>{lead}</strong>{p.slice(lead.length)}</p>;
          }
          return <p key={i}>{p}</p>;
        }) : panel?.note ? <p>{panel.note}</p> : (
          <p className="ix-muted">No positioning narrative in the source data.</p>
        )}
      </div>
    </div>
  );
}

function AnalysisPanel({ name, panel, kraljic }) {
  const meta = PANELS[name] || { title: panel.heading || name };
  const caption = captionOf(panel.heading);
  let body;
  let flush = false;
  let footer = null;

  if (name === 'pestel' && panel.cards?.length) {
    body = <PestelCards cards={panel.cards} />;
  } else if (name === 'porter' && panel.rows?.length) {
    body = (
      <>
        <PorterTable rows={panel.rows} />
        {panel.implication && <div className="ix-callout" style={{ margin: '12px 16px' }}><strong>Adding it up:</strong> {panel.implication}</div>}
      </>
    );
    flush = true;
  } else if (name === 'market_drivers' && panel.cards?.length) {
    body = <DriverCards cards={panel.cards} />;
  } else if (name === 'kraljic') {
    body = <KraljicBody panel={panel} kraljic={kraljic} />;
    const blocks = srcBlocks(panel.html);
    if (blocks.length) footer = <SourceNote blocks={blocks} />;
  } else {
    body = panel.html ? <ReportHtml html={panel.html} /> : <EmptyState variant="bare" title="Not in the source data" />;
    flush = true;
  }

  return (
    <div id={anchorId(name)} className="st-anchor">
      <Panel title={meta.title} caption={caption} flush={flush} className="st-sheet"
        collapsible={!!meta.collapsed} defaultOpen={!meta.collapsed}>
        {body}
        {footer}
      </Panel>
    </div>
  );
}

function Caveat({ caveat, reportName, asOf }) {
  if (!caveat) return null;
  const lines = (caveat.lines || []).filter((l) => l.visible !== false);
  return (
    <div className="ix-callout warn st-caveat" role="note">
      <div className="ix-callout-label">
        Market report · {reportName || caveat.old_line_name} · written {fmt.month(caveat.as_of || asOf)}
      </div>
      <p>{caveat.text}</p>
      {lines.length > 0 && (
        <div className="st-caveat-lines">
          <span className="ix-muted ix-small">Current product lines:</span>
          {lines.map((l) => (
            <Link key={l.line_key} className="ix-tag plain" to={`/intelligence/lines/${encodeURIComponent(l.line_key)}`}>
              {l.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Contents({ order }) {
  if (order.length < 3) return null;
  return (
    <Panel title="In this analysis">
      <nav className="st-toc" aria-label="Analysis contents">
        {order.map((k) => (
          <a key={k} href={`#${anchorId(k)}`}
            onClick={(e) => {
              const el = document.getElementById(anchorId(k));
              if (!el) return;
              e.preventDefault();
              el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}>
            {PANELS[k]?.title || k}
          </a>
        ))}
      </nav>
    </Panel>
  );
}

/* The rail sticks under the header. When it is taller than the space left it
 * scrolls on its own, and a fade with a "More below" button marks the cut. */
function StickyRail({ children }) {
  const scrollRef = useRef(null);
  const innerRef = useRef(null);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const check = () => setMore(el.scrollHeight - el.scrollTop - el.clientHeight > 4);
    check();
    el.addEventListener('scroll', check, { passive: true });
    window.addEventListener('resize', check);
    let ro = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(check);
      ro.observe(el);
      if (innerRef.current) ro.observe(innerRef.current);
    }
    return () => {
      el.removeEventListener('scroll', check);
      window.removeEventListener('resize', check);
      ro?.disconnect();
    };
  }, []);
  const scrollOn = () => {
    const el = scrollRef.current;
    if (el) el.scrollBy({ top: Math.round(el.clientHeight * 0.7), behavior: 'smooth' });
  };
  return (
    <aside className={`ix-rail is-sticky st-rail${more ? ' has-more' : ''}`} aria-label="Context">
      <div ref={scrollRef} className="st-rail-scroll">
        <div ref={innerRef} className="st-rail-inner">{children}</div>
      </div>
      {more && (
        <button type="button" className="st-rail-more" onClick={scrollOn} tabIndex={-1} aria-hidden>
          More below <span aria-hidden>↓</span>
        </button>
      )}
    </aside>
  );
}

export default function AnalysisTab({ slug, teamId, category }) {
  const { data, error, loading, reload } = useApi(
    slug && teamId ? `/api/strategy/categories/${encodeURIComponent(slug)}/analysis` : null,
    { team_id: teamId },
  );

  const order = useMemo(() => {
    if (!data?.panels) return [];
    const listed = (data.panel_order && data.panel_order.length ? data.panel_order : DEFAULT_ORDER);
    return listed.filter((k) => data.panels[k]);
  }, [data]);

  if (loading && !data) {
    return (
      <div className="ix-two-col">
        <div className="ix-col"><LoadingPanel lines={6} /><LoadingPanel lines={4} /></div>
        <div className="ix-rail"><LoadingPanel lines={5} /></div>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  const kraljic = data.kraljic || category?.kraljic || null;
  const rail = (
    <StickyRail>
      {data.available && <Contents order={order} />}
      <AnalysisDemand key={teamId} category={category} teamId={teamId} />
    </StickyRail>
  );

  if (!data.available || !order.length) {
    return (
      <div className="ix-two-col">
        <div className="ix-col">
          <EmptyState
            title="No market report for this category yet"
            body={`${data.name} has a playbook but no delivered market report, so there is no market analysis to show. The playbook's objectives and opportunities are under Opportunities.`}
          />
          {kraljic && (
            <Panel title="Kraljic positioning" caption="From the playbook" className="st-sheet">
              <KraljicBody panel={null} kraljic={kraljic} />
            </Panel>
          )}
        </div>
        {rail}
      </div>
    );
  }

  return (
    <div className="st-analysis">
      <Caveat caveat={data.caveat} reportName={data.report_name} asOf={data.as_of} />
      <div className="ix-two-col">
        <div className="ix-col">
          {order.map((k) => (
            <AnalysisPanel key={k} name={k} panel={data.panels[k]} kraljic={kraljic} />
          ))}
        </div>
        {rail}
      </div>
    </div>
  );
}
