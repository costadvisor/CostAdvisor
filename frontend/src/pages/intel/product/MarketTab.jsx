import { Link } from 'react-router-dom';
import { EmptyState, ErrorState, LoadingPanel, useApi } from '../../../components/intel';
import MarketChart from './MarketChart';
import { ComponentsTable, CostBuildUp } from './MarketComponents';
import { MarketDynamics, MarketOutlook } from './MarketDynamics';
import MarketRail from './MarketRail';

/* Tab 1 "Market & Costs": GET /api/intel/products/{pid}/market?region=&variant=
 * Main column: index chart (all regions), components, build-up, dynamics,
 * outlook. Right rail: snapshot, cycle, variants, seasonality/volatility,
 * formula confidence. Levels are base 100 = Jan 2023; nothing is money. */

function Banner() {
  return (
    <div className="ix-note ixd-banner">
      <span className="ixd-banner-ico" aria-hidden>i</span>
      <span>
        Every figure here is a <strong>should-cost index (base 100 = Jan 2023)</strong> built from public commodity
        indexes. No absolute prices are used. To track against your contracted price, add the product to
        your <Link to="/portfolio">Portfolio</Link>.
      </span>
    </div>
  );
}

/* Pricing gap / margin status: fixed buyer-facing text from the API, shown
 * as it comes. Never built from other fields. */
function Warnings({ warnings }) {
  if (!warnings?.length) return null;
  return (
    <div className="ix-callout warn ixd-warnings" role="note" aria-label="About this formula">
      {warnings.map((w, i) => (
        <div key={`${w.kind}-${i}`} className="ixd-warning">
          <span className="ixd-warning-ico" aria-hidden>!</span>
          <span>{w.text}</span>
        </div>
      ))}
    </div>
  );
}

function SourceNote({ product, data }) {
  if (product?.is_group && data.source_pid) {
    const n = (product.group_members || []).length;
    return (
      <div className="ix-callout info ixd-banner">
        This group card represents {n > 0 ? `${n} products` : 'several products'}. Its {data.region} numbers come from
        the member product that carries the recipe,{' '}
        <Link className="ix-link" to={`/intelligence/products/${encodeURIComponent(data.source_pid)}`}>{data.source_pid}</Link>.
      </div>
    );
  }
  if (data.source_pid && data.source_pid !== product?.pid) {
    return (
      <div className="ix-callout info ixd-banner">
        The {data.region} numbers come from the recipe of{' '}
        <Link className="ix-link" to={`/intelligence/products/${encodeURIComponent(data.source_pid)}`}>{data.source_pid}</Link>.
      </div>
    );
  }
  return null;
}

function Skeleton() {
  return (
    <div className="ix-two-col" role="status" aria-label="Loading market data">
      <div className="ix-col">
        <LoadingPanel chart height={280} />
        <LoadingPanel lines={6} />
      </div>
      <div className="ix-rail">
        <LoadingPanel lines={4} />
        <LoadingPanel lines={3} />
      </div>
    </div>
  );
}

export default function MarketTab({ pid, product, region, variant, onVariantChange, onShowIntel }) {
  const { data, error, loading, reload } = useApi(
    pid ? `/api/intel/products/${encodeURIComponent(pid)}/market` : null,
    { region, variant },
    { keepPrevious: true },
  );

  if (!data && loading) return <><Banner /><Skeleton /></>;
  if (!data && error) return <ErrorState error={error} onRetry={reload} title="Could not load market data" />;
  if (!data) return null;

  if (data.evaluable === false) {
    return (
      <>
        <Warnings warnings={data.warnings?.length ? data.warnings : product?.warnings} />
        <EmptyState
          title="No cost formula for this product yet"
          body={<>There is no should-cost index to show{data.reason ? ` (${data.reason})` : ''}.</>}
          action={onShowIntel && (
            <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onShowIntel}>
              See product intelligence
            </button>
          )}
        />
      </>
    );
  }

  const refreshing = loading || (region && data.region && data.region !== region);

  return (
    <div className={`ixd-body${refreshing ? ' is-refreshing' : ''}`} aria-busy={refreshing || undefined}>
      <Banner />
      <Warnings warnings={data.warnings} />
      <SourceNote product={product} data={data} />
      {error && <ErrorState error={error} onRetry={reload} title="Could not refresh market data" />}
      <div className="ix-two-col">
        <div className="ix-col">
          <MarketChart data={data} product={product} onVariantChange={onVariantChange} />
          <ComponentsTable data={data} />
          <CostBuildUp data={data} />
          <MarketDynamics data={data} />
          <MarketOutlook data={data} />
        </div>
        <MarketRail data={data} product={product} onVariantChange={onVariantChange} />
      </div>
    </div>
  );
}
