import { useEffect, useLayoutEffect, useRef } from 'react';
import {
  Link, useLocation, useNavigate, useNavigationType, useParams, useSearchParams,
} from 'react-router-dom';
import {
  DetailHeader, Tabs, TabPanel, useTabParam, EmptyState, ErrorState, LoadingPage, useApi,
  parseStatusParam, statusQuery,
} from '../../components/intel';
import { ReportBadge, PendingBadge } from './lines/LineBadges';
import ReportTab from './lines/ReportTab';
import ProductsTab from './lines/ProductsTab';
import DemandTab from './lines/DemandTab';
import {
  LINES_PATH, linesHref, playbookHref, lineHref, listedOf, count,
} from './lines/lineUtil';
import '../../styles/intel-lines.css';

/* Intelligence › Product lines › one line: its market report(s), its
 * products and where those products are bought.
 *
 * The URL names the line by id. A current or former line key still opens it
 * (older links, the Strategy caveat); the page then replaces its URL with the
 * id. `?status=` filters the Products tab like the grid: absent = Verified
 * makers + Concentrated supply. */

const TAB_IDS = ['report', 'products', 'demand'];

/* The Report tab stays mounted (B-04: no second ~100 KB fetch on return),
 * so ReportTab's own place-keeping, which runs when it mounts, misses a tab
 * switch: the switch restores each tab's window offset here. Taken from the
 * last scroll while the tab was shown; kept per line for the session, so a
 * Back that lands on another tab still finds the report's place; cleared
 * when the line is opened afresh (by id, or by key: the id REPLACE). */
function useTabPlaces(tab, storeKey) {
  const navType = useNavigationType();
  const places = useRef(null);
  if (places.current === null) {
    let saved = {};
    if (navType === 'POP') {
      try { saved = JSON.parse(sessionStorage.getItem(storeKey)) || {}; } catch { saved = {}; }
    }
    places.current = saved;
  }
  const shown = useRef(tab);

  useEffect(() => {
    const onScroll = () => { places.current[shown.current] = Math.round(window.scrollY); };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      try { sessionStorage.setItem(storeKey, JSON.stringify(places.current)); } catch { /* storage off */ }
    };
  }, [storeKey]);

  useLayoutEffect(() => {
    if (shown.current === tab) return;
    shown.current = tab;
    const y = places.current[tab];
    if (y != null) window.scrollTo(0, y);
  }, [tab]);
}

/* The page is keyed by the line it shows: moving from one line to another
 * (a caveat link, the back button) mounts a fresh page, so the previous
 * line's payload is never taken for the next one. */
export default function LineDetailPage() {
  const { lineRef = '' } = useParams();
  return <LineDetail key={lineRef} lineRef={lineRef} />;
}

function LineDetail({ lineRef }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const statuses = parseStatusParam(params.get('status'));
  const { data: line, error, status, loading, reload } = useApi(
    lineRef ? `/api/intel/lines/${encodeURIComponent(lineRef)}` : null,
    { status: statusQuery(statuses) },
    { keepPrevious: true },
  );

  /* Opened by key: show the id in the address bar from now on (only once
   * the request has answered). */
  useEffect(() => {
    if (status === 200 && line?.id != null && String(line.id) !== lineRef) {
      navigate(`${lineHref(line.id)}${location.search}`, { replace: true, state: location.state });
    }
  }, [status, line, lineRef, navigate, location.search, location.state]);

  const reports = line?.reports || [];
  const [tab, setTab] = useTabParam(TAB_IDS, line && !reports.length ? 'products' : 'report');
  useTabPlaces(tab, `ixl-tab-y:${lineRef}`);
  const backTo = `${LINES_PATH}${location.state?.fromSearch || ''}`;

  if (status === 404) {
    return (
      <div className="ix-page ixl-detail">
        <EmptyState title="This product line is not in the catalogue"
          body="This product line is not published, or the link is out of date. Find the line by name in the list."
          action={<Link className="ca-btn ca-btn-ghost ca-btn-sm" to={LINES_PATH}>All product lines</Link>} />
      </div>
    );
  }
  if (error && !line) {
    return (
      <div className="ix-page ixl-detail">
        <ErrorState error={error} onRetry={reload} title="Could not load this product line" />
      </div>
    );
  }
  if (!line) {
    return <div className="ix-page ixl-detail"><LoadingPage panels={2} /></div>;
  }

  const fam = line.family;
  const ssf = line.subfamily;
  const crumbs = [
    { label: 'Product lines', to: backTo },
    fam?.name ? { label: fam.name, to: linesHref({ familyId: fam.id }) } : null,
    ssf?.name ? { label: ssf.name, to: linesHref({ familyId: fam?.id, subfamilyId: ssf.id }) } : null,
  ];
  const listed = listedOf(line);
  const playbooks = line.playbooks || [];

  const tabs = [
    { id: 'report', label: 'Report', count: reports.length || undefined },
    { id: 'products', label: 'Products', count: listed || undefined },
    { id: 'demand', label: 'Demand', count: line.demand?.industry_count || undefined },
  ];

  return (
    <div className="ixl-detail ca-fade-in">
      <DetailHeader
        backTo={backTo}
        crumbs={crumbs}
        title={line.name}
        subtitle={line.platform}
        meta={(
          <>
            <span className="ix-chip ixl-dh-chip">{listed ? `${count(listed, 'product')} tracked` : 'No tracked product yet'}</span>
            {line.industries?.length > 0 && (
              <span className="ix-chip ixl-dh-chip" title={line.industries.join(', ')}>
                <strong>{line.industries.length}</strong> {line.industries.length === 1 ? 'industry' : 'industries'}
              </span>
            )}
            {line.functions?.length > 0 && (
              <span className="ix-chip ixl-dh-chip" title="Functions, most-reached first">
                {line.functions.slice(0, 3).join(' · ')}
                {line.functions.length > 3 ? ` +${line.functions.length - 3}` : ''}
              </span>
            )}
            <ReportBadge reports={reports} size="lg" />
            <PendingBadge show={line.pending} flags={line.flags} size="lg" />
          </>
        )}
        actions={playbooks.length === 1 ? (
          <Link className="ca-btn ca-btn-ghost ca-btn-sm" to={playbookHref(playbooks[0].slug)}
            title={`Strategy playbook: ${playbooks[0].name}`}>
            Strategy playbook →
          </Link>
        ) : null}
        tabs={<Tabs tabs={tabs} value={tab} onChange={setTab} idBase="line" ariaLabel="Product line sections" />}
      />

      <div className={`ix-detail-body${loading ? ' is-refreshing' : ''}`}>
        <TabPanel id="report" active={tab} idBase="line" keepMounted>
          <ReportTab line={line} />
        </TabPanel>
        <TabPanel id="products" active={tab} idBase="line">
          <ProductsTab line={line} statuses={statuses} loading={loading} />
        </TabPanel>
        <TabPanel id="demand" active={tab} idBase="line">
          <DemandTab line={line} />
        </TabPanel>
      </div>
    </div>
  );
}

