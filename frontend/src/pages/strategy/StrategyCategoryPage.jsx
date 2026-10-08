import { useLayoutEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import api, { formatApiError } from '../../api';
import {
  DetailHeader, Tabs, TabPanel, useTabParam, StatusBadge, KraljicBadge, EmptyState, ErrorState,
  LoadingPage, Skeleton, useApi, fmt,
} from '../../components/intel';
import CategoryControls from './CategoryControls';
import AnalysisTab from './tabs/AnalysisTab';
import SpendTab from './tabs/SpendTab';
import OpportunitiesTab from './tabs/OpportunitiesTab';
import ActionsTab from './tabs/ActionsTab';
import '../../styles/strategy.css';

/* One strategy category (a playbook) for the active team: a short sticky
 * header (name, Kraljic position, status and owner, the four tabs), then the
 * team's products in the category as a strip that scrolls with the page.
 * Data: GET /api/strategy/categories/{slug}?team_id=. */

const TAB_IDS = ['analysis', 'spend', 'opportunities', 'actions'];

function StartButton({ slug, teamId, onAdopted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/strategy/categories/${encodeURIComponent(slug)}/adopt`, null, { params: { team_id: teamId } });
      onAdopted();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="ix-row" style={{ gap: 8 }}>
      {error && <span className="st-sugg-error" role="alert">{error}</span>}
      <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={start} disabled={busy}>
        {busy ? 'Starting…' : 'Start strategy'}
      </button>
    </span>
  );
}

/* The team's products in this category: a strip at the top of the body, so
 * the sticky header stays one row plus the tabs. */
function ProductChips({ products = [], catalogueCount }) {
  if (!products.length) {
    return (
      <div className="st-products-strip">
        <span className="ix-muted ix-small">
          None of your portfolio products are on this category&apos;s product lines
          {catalogueCount ? ` (${fmt.plural(catalogueCount, 'catalogue product')} are)` : ''}.
        </span>
      </div>
    );
  }
  return (
    <div className="st-products-strip">
      <span className="st-products-label">Your products · {products.length}</span>
      {products.map((p) => (
        <Link key={p.product_id} className="st-product-chip"
          to={p.pid ? `/intelligence/products/${encodeURIComponent(p.pid)}` : '/products'}
          title={[p.pid, p.template_name, p.line_name].filter(Boolean).join(' · ') || undefined}>
          {p.name}
        </Link>
      ))}
    </div>
  );
}

/* The sticky rail on Strategic Analysis and the panels' scroll margin sit
 * under the header, whose height depends on the title and the width: measure
 * it and expose it as --ix-dh-h on the page. */
function useHeaderHeightVar(ref, active) {
  useLayoutEffect(() => {
    const root = ref.current;
    const el = root?.querySelector('.ix-dh');
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const apply = () => root.style.setProperty('--ix-dh-h', `${Math.round(el.getBoundingClientRect().height)}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, active]);
}

export default function StrategyCategoryPage() {
  const { slug } = useParams();
  const { activeTeamId } = useAuth();
  const [tab, setTab] = useTabParam(TAB_IDS);
  const pageRef = useRef(null);
  const { data, error, status, loading, reload, setData } = useApi(
    activeTeamId && slug ? `/api/strategy/categories/${encodeURIComponent(slug)}` : null,
    { team_id: activeTeamId },
  );

  const tabs = [
    { id: 'analysis', label: 'Strategic Analysis' },
    { id: 'spend', label: 'Spend Analysis' },
    { id: 'opportunities', label: 'Opportunities', count: data?.lever_count ?? undefined },
    { id: 'actions', label: 'Actions Management' },
  ];

  const notFound = !!error && status === 404;
  useHeaderHeightVar(pageRef, !notFound);

  if (notFound) {
    return (
      <div className="ix-page ca-fade-in">
        <EmptyState
          title="Strategy category not found"
          body={`There is no playbook called "${slug}".`}
          action={<Link className="ca-btn ca-btn-ghost ca-btn-sm" to="/strategy">Back to Strategy</Link>}
        />
      </div>
    );
  }

  const onSaved = (row) => setData((d) => (d ? {
    ...d,
    record: { ...(d.record || {}), status: row.status, owner: row.owner },
  } : d));

  const adopted = !!data?.adopted;
  const header = (
    <DetailHeader
      backTo="/strategy"
      crumbs={[
        { label: 'Strategy', to: '/strategy' },
        data?.family ? { label: data.family } : null,
      ]}
      title={data ? data.name : <Skeleton width={260} height={20} />}
      subtitle={data ? (
        <span className="st-dh-sub">
          <KraljicBadge kraljic={data.kraljic} />
          {!adopted && <StatusBadge status="Not started" />}
          {data.playbook_last_updated && <span>Playbook updated {fmt.date(data.playbook_last_updated)}</span>}
        </span>
      ) : null}
      actions={data ? (adopted
        ? <CategoryControls slug={slug} teamId={activeTeamId} record={data.record} onSaved={onSaved} />
        : <StartButton slug={slug} teamId={activeTeamId} onAdopted={reload} />) : null}
      tabs={<Tabs tabs={tabs} value={tab} onChange={setTab} idBase="strategy" ariaLabel="Category sections" />}
    />
  );

  let body;
  if (!activeTeamId || (loading && !data)) {
    body = <LoadingPage panels={3} />;
  } else if (error) {
    body = <ErrorState error={error} onRetry={reload} />;
  } else {
    body = (
      <>
        <ProductChips products={data.team_products} catalogueCount={data.catalogue_products?.length} />
        {!adopted && (
          <div className="ix-callout info st-notadopted" role="note">
            <strong>Not started.</strong>{' '}
            The analysis and the playbook are here to read. Start the strategy to save objectives,
            opportunity scores and actions for {data.name}.
          </div>
        )}
        <TabPanel id="analysis" active={tab} idBase="strategy">
          <AnalysisTab slug={slug} teamId={activeTeamId} category={data} />
        </TabPanel>
        <TabPanel id="spend" active={tab} idBase="strategy">
          <SpendTab slug={slug} teamId={activeTeamId} category={data} />
        </TabPanel>
        <TabPanel id="opportunities" active={tab} idBase="strategy">
          <OpportunitiesTab slug={slug} teamId={activeTeamId} />
        </TabPanel>
        <TabPanel id="actions" active={tab} idBase="strategy">
          <ActionsTab slug={slug} teamId={activeTeamId} />
        </TabPanel>
      </>
    );
  }

  return (
    <div className="ca-fade-in st-category" ref={pageRef}>
      {header}
      <div className="ix-detail-body">{body}</div>
    </div>
  );
}
