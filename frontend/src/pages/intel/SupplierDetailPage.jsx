import { useCallback, useEffect } from 'react';
import { useLocation, useParams, useSearchParams } from 'react-router-dom';
import {
  DetailHeader, Tabs, TabPanel, useTabParam, RegionChips, EmptyState, ErrorState, LoadingPage,
  useApi, fmt,
} from '../../components/intel';
import SupplierOverviewTab, { Profile, UnlistedCodes, DossierRoles } from './suppliers/SupplierOverviewTab';
import SupplierProductsTab from './suppliers/SupplierProductsTab';
import { SUPPLIERS_URL, PRODUCT_COUNT_HINT, useFamilyColors } from './suppliers/supplierUtils';
import '../../styles/intel-suppliers.css';

const TAB_IDS = ['overview', 'products'];
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/* Intelligence › Suppliers › one producer: what it makes, where, with what
 * evidence, and which other makers are counted on the same products.
 * `?family_id=` narrows the Products tab. */
export default function SupplierDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useTabParam(TAB_IDS);
  const familyColor = useFamilyColors();

  // A competitor link opens another supplier on the same route: start it at the top.
  useEffect(() => { window.scrollTo({ top: 0 }); }, [id]);

  const validId = UUID_RE.test(id || '');
  const { data: s, error, status, loading, reload } = useApi(validId ? `${SUPPLIERS_URL}/${id}` : null);

  const family = params.get('family_id') || '';
  const setFamily = useCallback((f) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (f) next.set('family_id', f); else next.delete('family_id');
      return next;
    }, { replace: true, state: location.state });
  }, [setParams, location.state]);
  const openFamily = useCallback((f) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('tab', 'products');
      if (f) next.set('family_id', f); else next.delete('family_id');
      return next;
    }, { replace: true, state: location.state });
    window.scrollTo({ top: 0 });
  }, [setParams, location.state]);

  // Reached from the directory or another supplier page, "Back" is history
  // (the list keeps its filters); opened directly, it goes to the directory.
  const backTo = location.state?.fromList ? undefined : '/intelligence/suppliers';
  const crumbs = [{ label: 'Suppliers', to: '/intelligence/suppliers' }];

  if (!validId || status === 404) {
    return (
      <>
        <DetailHeader backTo={backTo} crumbs={crumbs} title="Supplier not found" />
        <div className="ix-detail-body">
          <EmptyState title="Supplier not found"
            body="The link may be out of date. Search the directory for the supplier by name." />
        </div>
      </>
    );
  }

  if (error && !s) {
    return (
      <>
        <DetailHeader backTo={backTo} crumbs={crumbs} title="Supplier" />
        <div className="ix-detail-body"><ErrorState error={error} onRetry={reload} /></div>
      </>
    );
  }

  if (loading || !s) {
    return <div className="ix-detail-body"><LoadingPage panels={3} /></div>;
  }

  const bucket = !!s.is_bucket;
  // Not a counted maker on any product: its directory numbers are all zero,
  // so the page is the products it is named on, like a generic name's (A-09).
  const outside = !bucket && s.in_directory === false;
  const plain = bucket || outside;
  // The tab counts what the header counts: the counted products (A-10).
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'products', label: 'Products', count: s.product_count },
  ];

  const meta = (
    <div className="ixs-dh-meta">
      {bucket && <span className="ix-badge plain st-warn">Generic supplier name</span>}
      {!bucket && s.in_directory !== false && (
        <span className="ix-chip" title={PRODUCT_COUNT_HINT}>
          <strong>{fmt.plural(s.product_count, 'product')}</strong>&nbsp;tracked here
        </span>
      )}
      {outside && (
        <span className="ix-badge plain st-neutral" title="Not a counted maker on any product in the grid">
          Not in the directory
        </span>
      )}
      {!plain && <span className="ix-chip">{fmt.plural(s.family_count, 'family', 'families')}</span>}
      {!plain && <span className="ix-chip">{fmt.plural(s.line_count, 'product line')}</span>}
      {s.integrated_count > 0 && <span className="ix-badge plain st-good">{s.integrated_count} integrated</span>}
      {(s.regions || []).length > 0 && <RegionChips regions={s.regions} size="sm" />}
    </div>
  );

  return (
    <>
      <DetailHeader
        backTo={backTo}
        crumbs={crumbs}
        title={s.name}
        subtitle={s.hq ? `HQ ${s.hq}` : undefined}
        meta={meta}
        tabs={plain ? undefined : <Tabs tabs={tabs} value={tab} onChange={setTab} idBase="supplier" ariaLabel="Supplier sections" />}
      />
      <div className="ix-detail-body ca-fade-in">
        {plain ? (
          <div className="ix-stack">
            {bucket ? (
              <div className="ix-callout warn">
                <div className="ix-callout-label">Not a company</div>
                The source names this supplier only as a group of producers. It is not counted or ranked in the
                directory and has no competitor list. The products it is named on are below.
              </div>
            ) : (
              <div className="ix-callout info" role="note">
                <div className="ix-callout-label">Not in the directory</div>
                {(s.products || []).length > 0
                  ? 'This producer is named on the products below, but not as a counted maker on any of them, so the directory does not list or rank it and it has no competitor list.'
                  : 'This producer is not named as a maker on any product in the grid, so the directory does not list or rank it.'}
              </div>
            )}
            {(s.products || []).length > 0 && (
              <SupplierProductsTab supplier={s} family={family} onFamilyChange={setFamily} />
            )}
            <div className="ix-split">
              <Profile supplier={s} compact />
              {outside && <DossierRoles roles={s.dossier_roles} />}
              <UnlistedCodes codes={s.unlisted_codes} />
            </div>
          </div>
        ) : (
          <>
            <TabPanel id="overview" active={tab} idBase="supplier">
              <SupplierOverviewTab supplier={s} familyColor={familyColor} onFamilyClick={openFamily} />
            </TabPanel>
            <TabPanel id="products" active={tab} idBase="supplier">
              <SupplierProductsTab supplier={s} family={family} onFamilyChange={setFamily} />
            </TabPanel>
          </>
        )}
      </div>
    </>
  );
}
