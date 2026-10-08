import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { useAuth } from '../../AuthContext';
import { useConfirm } from '../../components/ConfirmDialog';
import { useToast } from '../../components/Toast';
import {
  DetailHeader, Tabs, TabPanel, useTabParam, RegionChips, SupplyBadge, EmptyState, ErrorState,
  LoadingPage, useApi, normalizeRegion, fmt, lineFlags, productHref, lineHref, productsHref, linesHref,
  UNPUBLISHED_LINE,
} from '../../components/intel';
import MarketTab from './product/MarketTab';
import ProductIntelTab from './product/ProductIntelTab';
import '../../styles/intel-product.css';

/* Intelligence › Products › :pid — the product page shell.
 *
 * One request for the header and tab 2 (GET /api/intel/products/{pid}); tab 1
 * fetches its own market payload for the viewing region. URL state:
 *   ?tab=market|intel   ?region=<drop code>   ?variant=<recipe variant>  */

const TABS = [
  { id: 'market', label: 'Market & Costs' },
  { id: 'intel', label: 'Product Intelligence' },
];
const TAB_IDS = TABS.map((t) => t.id);

/* A region code from the URL, matched against the product's own drop codes
 * (so ?region=gl, GL and GLOBAL all land on "GL"). */
function matchRegion(regions, raw) {
  if (!raw) return null;
  const want = normalizeRegion(raw);
  return regions.find((r) => normalizeRegion(r) === want) || null;
}

function HeaderMeta({ product, regionCount }) {
  const p = product;
  const absorbed = p.absorbed_into_card;
  // A group card usually carries its member's own name, so name it by code.
  const absorbedLabel = absorbed && (absorbed.is_group ? `group ${absorbed.code}` : (absorbed.name || absorbed.code));
  const memberCount = (p.group_members || []).length;
  return (
    <>
      <span className="ix-chip" title="Product ID"><span className="ixd-pid">{p.pid}</span></span>
      {p.form && <span className="ix-chip"><strong>Form</strong> {p.form}</span>}
      {p.cas && <span className="ix-chip"><strong>CAS</strong> {p.cas}</span>}
      {regionCount > 0 && (
        <span className="ix-chip">{fmt.plural(regionCount, 'region')}</span>
      )}
      {p.is_group && (
        <>
          <span className="ix-badge lg st-neutral">Group</span>
          {memberCount > 0 && (
            <span className="ix-chip">represents {fmt.plural(memberCount, 'product')}</span>
          )}
        </>
      )}
      {absorbed && (
        absorbed.exists !== false && absorbed.listed !== false ? (
          <Link className="ix-chip ixd-chip-link" to={productHref(absorbed.code)}
            title={`In the catalogue this product is represented by ${absorbed.code}${absorbed.name ? ` (${absorbed.name})` : ''}`}>
            shown under <strong>{absorbedLabel}</strong>
          </Link>
        ) : (
          <span className="ix-chip">shown under <strong>{absorbedLabel}</strong></span>
        )
      )}
      {lineFlags(p.line?.flags).map((f) => (
        <span key={f.code} className="ix-badge lg plain st-warn">{f.label}</span>
      ))}
    </>
  );
}

/* The supply status in the header: the badge, then its one-line meaning.
 * Never a number. Absorbed, pointer and duplicate cards have no status. */
function StatusLine({ product }) {
  const st = product.status;
  if (!st) return null;
  return (
    <div className="ixd-status">
      <SupplyBadge status={st} size="lg" title={st.label} />
      {st.description && <span className="ixd-status-text">{st.description}</span>}
    </div>
  );
}

/* A pointer or duplicate PID answers with the card it stands for; the page
 * replaced its URL and says so in one line. */
const REDIRECT_TEXT = {
  pointer: 'points to this product',
  duplicate: 'is a duplicate of this product',
};
function RedirectNote({ from }) {
  if (!from?.pid) return null;
  return (
    <div className="ix-callout info ixd-redirect" role="note">
      <span className="ixd-pid">{from.pid}</span> {REDIRECT_TEXT[from.kind] || 'now opens this product'}, so it opens here.
    </div>
  );
}

/* Add to portfolio (docs/api/intel_catalogue.md §5.2, `add_to_portfolio`).
 *
 * If the active team already has a product linked to the recipe that prices
 * this card, the action becomes "In your portfolio" and opens it. Otherwise it
 * asks, creates a team product linked to that platform template
 * (POST /api/products) and opens the Cost Model Builder on it, which loads the
 * recipe; the template gives the product its family and product line. The
 * shared axios client sends every 403 to /dashboard, so these
 * requests accept 403 as an answer and the button is disabled up front for a
 * role that cannot add products. The role check mirrors the server's fallback
 * rule (owner and admin may edit, member may view); a custom team role is not
 * visible to the browser, so a 403 from the POST is still handled in place. */
const QUIET_403 = { validateStatus: (s) => (s >= 200 && s < 300) || s === 403 };

function usePortfolioMatch(product, activeTeamId) {
  const a = product.add_to_portfolio;
  const [state, setState] = useState({ status: 'idle', product: null, costModelId: null });
  useEffect(() => {
    if (!a || !activeTeamId) { setState({ status: 'idle', product: null, costModelId: null }); return undefined; }
    let alive = true;
    setState({ status: 'loading', product: null, costModelId: null });
    const templates = new Set([a.template_id, product.template_id].filter(Boolean));
    (async () => {
      try {
        const res = await api.get('/api/products', { params: { team_id: activeTeamId }, ...QUIET_403 });
        if (!alive) return;
        if (res.status === 403) { setState({ status: 'forbidden', product: null, costModelId: null }); return; }
        const match = (res.data || []).find((p) => templates.has(p.formula_template_id));
        if (!match) { setState({ status: 'absent', product: null, costModelId: null }); return; }
        const cms = await api.get('/api/cost-models', { params: { team_id: activeTeamId }, ...QUIET_403 });
        if (!alive) return;
        const cm = cms.status === 403 ? null : (cms.data || []).find((c) => c.product_id === match.id);
        setState({ status: 'present', product: match, costModelId: cm?.id || null });
      } catch {
        if (alive) setState({ status: 'error', product: null, costModelId: null });
      }
    })();
    return () => { alive = false; };
  }, [a, product.template_id, activeTeamId]);
  return state;
}

function DisabledAction({ label, tip }) {
  return (
    <span className="ixd-add-wrap" title={tip} tabIndex={0} aria-label={`${label}: ${tip}`}>
      <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" disabled aria-hidden>{label}</button>
    </span>
  );
}

function AddToPortfolio({ product }) {
  const { user, teams, activeTeamId } = useAuth();
  const confirm = useConfirm();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const a = product.add_to_portfolio;
  const team = teams.find((t) => t.id === activeTeamId) || null;
  const match = usePortfolioMatch(product, activeTeamId);
  const name = product.name || product.pid;

  if (!a) {
    return <DisabledAction label="Add to portfolio" tip="This product has no cost recipe yet, so it cannot be added to a portfolio." />;
  }
  if (!team) {
    return <DisabledAction label="Add to portfolio" tip="Select a team first: products are added to the active team's portfolio." />;
  }
  if (match.status === 'present') {
    const p = match.product;
    const to = match.costModelId ? `/portfolio/${match.costModelId}` : a.route;
    const tip = match.costModelId
      ? `${p.name} is in ${team.name}'s portfolio. Open it in Portfolio.`
      : `${p.name} is in ${team.name}'s portfolio without a cost model yet. Open the Cost Model Builder to complete it.`;
    return (
      <Link className="ca-btn ca-btn-ghost ca-btn-sm ixd-in-portfolio" to={to}
        state={match.costModelId ? undefined : { productId: p.id }} title={tip}>
        <span className="ixd-in-portfolio-tick" aria-hidden>✓</span> In your portfolio
      </Link>
    );
  }
  const canAdd = !!user && (user.is_super_admin || team.role === 'owner' || team.role === 'admin');
  if (!canAdd || match.status === 'forbidden') {
    return (
      <DisabledAction label="Add to portfolio"
        tip={`Your role in ${team.name} cannot add products. Ask a team owner or admin to add ${name}.`} />
    );
  }
  if (match.status === 'loading') {
    return <DisabledAction label="Add to portfolio" tip={`Checking ${team.name}'s portfolio…`} />;
  }

  const onAdd = async () => {
    const regionName = (product.region_info || []).find((r) => r.app_region === a.default_region)?.name
      || a.default_region;
    const regionNote = a.default_region && a.default_region !== 'Europe'
      ? ` The builder opens on Europe; this product is priced in ${regionName} by default, so pick that region there.`
      : '';
    const ok = await confirm({
      title: `Add ${name} to ${team.name}'s portfolio?`,
      message: `This creates the product in ${team.name}, linked to the ${product.pid} cost recipe, `
        + `and opens the Cost Model Builder to finish the cost model.${regionNote}`,
      confirmLabel: 'Add to portfolio',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await api.post('/api/products', {
        name,
        unit: a.unit || 't',
        formula_template_id: a.template_id,
      }, { params: { team_id: activeTeamId }, ...QUIET_403 });
      if (res.status === 403) {
        addToast(`Your role in ${team.name} cannot add products.`, 'error');
        return;
      }
      navigate(a.route, { state: { productId: res.data.id } });
    } catch (err) {
      addToast(`Could not add ${name}: ${formatApiError(err)}`, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={onAdd} disabled={busy}
      title={`Add ${name} to ${team.name}'s portfolio and open the Cost Model Builder`}>
      {busy ? 'Adding…' : 'Add to portfolio'}
    </button>
  );
}

export default function ProductDetailPage() {
  const { pid } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useTabParam(TAB_IDS);
  const { data: product, error, status, loading, reload } = useApi(
    pid ? `/api/intel/products/${encodeURIComponent(pid)}` : null,
  );

  /* A pointer or duplicate PID is answered with its target: replace the URL
   * with the target's (keeping tab and region), and carry the note across. */
  useEffect(() => {
    const from = product?.redirected_from;
    if (!from || !product.pid || product.pid === pid) return;
    navigate(`${productHref(product.pid)}${location.search}`, { replace: true, state: { redirectedFrom: from } });
  }, [product, pid, navigate, location.search]);
  const redirectedFrom = product?.redirected_from || location.state?.redirectedFrom || null;

  const regions = useMemo(() => product?.regions || [], [product]);
  const region = matchRegion(regions, params.get('region'))
    || (product?.default_region && matchRegion(regions, product.default_region))
    || regions[0]
    || null;
  const regionVariants = useMemo(() => {
    const info = (product?.region_info || []).find((r) => r.code === region);
    return info?.variants || [];
  }, [product, region]);
  const rawVariant = params.get('variant');
  const variant = rawVariant && regionVariants.includes(rawVariant) ? rawVariant : null;

  const setRegion = useCallback((code) => {
    const next = matchRegion(regions, code);
    if (!next) return;
    setParams((prev) => {
      const q = new URLSearchParams(prev);
      q.set('region', next);
      q.delete('variant');
      return q;
    }, { replace: true });
  }, [regions, setParams]);

  const setVariant = useCallback((v) => {
    setParams((prev) => {
      const q = new URLSearchParams(prev);
      if (v) q.set('variant', v); else q.delete('variant');
      return q;
    }, { replace: true });
  }, [setParams]);

  if (loading && !product) {
    return <div className="ix-detail-body"><LoadingPage panels={3} /></div>;
  }
  if (status === 404) {
    return (
      <div className="ix-page ix-narrow">
        <EmptyState
          title="Product not found"
          body={<>There is no product with the code <strong className="ixd-pid">{pid}</strong> in the Intelligence catalogue.</>}
          action={<Link className="ca-btn ca-btn-ghost ca-btn-sm" to="/intelligence/products">Back to products</Link>}
        />
      </div>
    );
  }
  if (error || !product) {
    return (
      <div className="ix-page ix-narrow">
        <ErrorState error={error} onRetry={reload} title="Could not load this product" />
      </div>
    );
  }

  const title = product.name || product.full_name || product.pid;
  const subtitle = product.full_name && product.full_name !== title ? product.full_name : null;
  /* Family › Sub-family › Product line › (the product is the title). Each
   * step is null-safe: an unnamed sub-family is skipped, a missing line reads
   * "Product line not yet published" and links nowhere. */
  const fam = product.family;
  const sub = product.subfamily;
  const crumbs = [
    { label: 'Products', to: '/intelligence/products' },
    fam?.name && { label: fam.name, to: productsHref({ family_id: fam.id }) },
    sub?.name && { label: sub.name, to: linesHref({ familyId: fam?.id, subfamilyId: sub.id }) },
    product.line
      ? { label: product.line.name, to: lineHref(product.line.id) }
      : { label: product.line_label || UNPUBLISHED_LINE },
  ];
  const showRegionBar = tab === 'market' && regions.length > 0;

  return (
    <div className="ixd-page ca-fade-in">
      <DetailHeader
        crumbs={crumbs}
        title={title}
        subtitle={subtitle}
        meta={<HeaderMeta product={product} regionCount={regions.length} />}
        actions={<AddToPortfolio product={product} />}
        tabs={(
          <div className="ixd-tabbar">
            <Tabs tabs={TABS} value={tab} onChange={setTab} idBase="product" ariaLabel="Product sections" />
            {showRegionBar && (
              <div className="ixd-regionbar">
                <RegionChips
                  label="Viewing region"
                  regions={regions}
                  active={region}
                  onSelect={setRegion}
                  ariaLabel="Viewing region"
                />
              </div>
            )}
          </div>
        )}
      >
        <StatusLine product={product} />
        <RedirectNote from={redirectedFrom} />
      </DetailHeader>
      <div className="ix-detail-body">
        <TabPanel id="market" active={tab} idBase="product">
          <MarketTab
            key={product.pid}
            pid={product.pid}
            product={product}
            region={region}
            variant={variant}
            onVariantChange={setVariant}
            onShowIntel={() => setTab('intel')}
          />
        </TabPanel>
        <TabPanel id="intel" active={tab} idBase="product">
          <ProductIntelTab pid={product.pid} product={product} />
        </TabPanel>
      </div>
    </div>
  );
}
