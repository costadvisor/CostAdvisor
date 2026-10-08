import {
  Panel, KpiTile, EmptyState, ErrorState, LoadingPanel, useApi, fmt, seriesColor, normalizeRegion,
} from '../../../components/intel';
import { HBars } from '../../../components/charts';
import { SpendDonut, SiteColumns, QuarterLines } from '../SpendCharts';
import { quarterLabel } from '../SpendFormat';

/* Spend Analysis: the team's own money on this category's products — totals,
 * concentration, spend by site / supplier / product, and the should-cost vs
 * actual price evolution. Never platform data.
 * Data: GET /api/strategy/categories/{slug}/spend?team_id=. */

const LEVEL_TONE = {
  unconcentrated: 'good',
  'moderately concentrated': 'warn',
  'highly concentrated': 'bad',
};

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/* Sites carry the app's region name ("Europe"); show the drop code ("EU")
   the Intelligence pages use. */
function siteParts(row) {
  const region = normalizeRegion(row.region);
  if (!row.name) return { label: 'Unspecified site', sub: region };
  const [city, ...rest] = String(row.name).split(',').map((s) => s.trim());
  const country = rest.join(', ');
  return { label: city, sub: [country, region].filter(Boolean).join(' · ') || null };
}

/* The last quarter where both indexes have a value. */
function latestPair(evolution = []) {
  for (let i = evolution.length - 1; i >= 0; i -= 1) {
    const e = evolution[i];
    if (e.should_cost_index != null && e.actual_price_index != null) return e;
  }
  return null;
}

export default function SpendTab({ slug, teamId, category }) {
  const { data, error, loading, reload } = useApi(
    slug && teamId ? `/api/strategy/categories/${encodeURIComponent(slug)}/spend` : null,
    { team_id: teamId },
  );

  if (loading && !data) {
    return (
      <div className="ix-stack">
        <LoadingPanel lines={2} />
        <div className="ix-split"><LoadingPanel chart height={200} /><LoadingPanel chart height={200} /></div>
        <LoadingPanel chart height={240} />
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  const cur = data.currency || 'EUR';
  const money = (v) => fmt.money(v, cur);
  const illus = data.illustrative
    ? <span className="st-illus" title="This team's prices and volumes are made up for the demo">Illustrative demo data</span>
    : null;

  if (!data.has_spend) {
    const noProducts = category && !(category.team_products || []).length;
    return (
      <EmptyState
        title="No spend recorded for this category"
        body={noProducts
          ? 'None of your portfolio products are on this category\'s product lines, so there is no spend to show.'
          : 'Spend comes from your cost models\' actual prices and volumes. None of your products in this category has an actual price yet, so there is nothing to total.'}
      />
    );
  }

  const conc = data.concentration;
  const pair = latestPair(data.evolution);
  const gapPct = pair && pair.should_cost_index
    ? ((pair.actual_price_index / pair.should_cost_index) - 1) * 100 : null;
  const basePeriod = quarterLabel(data.evolution_base);

  const supplierItems = (data.by_supplier || []).map((s, i) => ({
    key: s.key ?? i,
    label: s.name || 'No supplier set',
    value: s.value,
    pct: s.pct,
    pctText: fmt.share(s.pct, { dp: 1 }),
    color: s.name ? seriesColor(i) : 'var(--muted)',
  }));

  const siteItems = (data.by_site || []).map((s) => ({ key: s.key, value: s.value, ...siteParts(s) }));

  const productItems = (data.by_product || []).map((p) => ({
    key: p.key,
    label: p.name,
    sub: p.pid,
    value: p.has_volume === false ? null : p.value,
    display: p.has_volume === false ? 'prices, no volumes' : `${money(p.value)} · ${fmt.share(p.pct, { dp: 1 })}`,
  }));

  return (
    <div className="ix-stack">
      <div className="st-spend-top">
        <span className="st-spend-window">
          Annual figures: {data.window || 'latest 4 quarters with an actual price, per cost model'}, in {cur}.
        </span>
        {illus}
      </div>

      <div className="ix-kpis st-kpis">
        <KpiTile label="Total annual spend" value={money(data.total)} size="lg"
          sub={`${fmt.plural((data.by_product || []).length, 'product')} · ${fmt.plural((data.by_site || []).length, 'site')}`} />
        <KpiTile label="Suppliers" value={conc ? fmt.num(conc.suppliers) : '—'} sub="with spend in this category" />
        <KpiTile label="Top supplier share" value={conc ? fmt.share(conc.top_share_pct, { dp: 1 }) : '—'}
          sub={conc?.top_supplier || null} />
        <KpiTile label="Concentration (HHI)" value={conc ? fmt.num(conc.hhi) : '—'}
          sub={conc ? cap(conc.level) : null} tone={conc ? LEVEL_TONE[conc.level] : undefined} />
        <KpiTile label="Actual vs should-cost"
          value={gapPct == null ? '—' : fmt.pct(gapPct)}
          tone={gapPct == null ? undefined : gapPct > 2 ? 'bad' : gapPct < -2 ? 'good' : undefined}
          sub={pair
            ? `${quarterLabel(pair.period)}: ${fmt.index(pair.actual_price_index)} vs ${fmt.index(pair.should_cost_index)} (indexes, ${basePeriod} = 100)`
            : 'Not enough quarters'} />
      </div>

      {data.concentration_note && (
        <div className={`ix-callout ${conc?.level === 'highly concentrated' ? 'warn' : 'info'}`}>
          <div className="ix-callout-label">Supplier concentration</div>
          {data.concentration_note}
        </div>
      )}

      <div className="ix-split">
        <Panel title="Spend by supplier" caption={`${fmt.plural(supplierItems.length, 'supplier')}`}>
          <div className="st-donut-wrap">
            <SpendDonut items={supplierItems} size={190} centerValue={supplierItems.filter((s) => s.label !== 'No supplier set').length}
              colorOf={(d) => d.color} />
            <div className="st-donut-legend" role="list" aria-label="Spend by supplier">
              {supplierItems.map((s) => (
                <div key={s.key} className="st-donut-row" role="listitem">
                  <span className="st-dot" style={{ background: s.color }} aria-hidden />
                  <span className="nm" title={s.label}>{s.label}</span>
                  <span className="pc">{s.pctText}</span>
                  <span className="mv">{money(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
        <Panel title="Spend by site" caption="Highest to lowest">
          <div className="st-cols">
            <SiteColumns items={siteItems} formatValue={money} />
          </div>
        </Panel>
      </div>

      <Panel title="Spend by product" caption="Largest first">
        <HBars items={productItems} labelWidth={250} color="var(--accent)" formatValue={money}
          ariaLabel="Spend by product" />
      </Panel>

      <Panel title="Cost evolution — should-cost vs actual price"
        caption={data.evolution_base ? `Quarterly · both indexes 100 = ${basePeriod}` : 'Quarterly'}>
        {data.evolution?.length > 1 ? (
          <>
            <div className="st-evo-legend">
              <span className="ix-legend-item"><span className="ix-swatch line" style={{ background: 'var(--accent2)' }} />Actual price index (your prices)</span>
              <span className="ix-legend-item"><span className="ix-swatch line" style={{ background: 'var(--accent4)' }} />Should-cost index (catalogue formula)</span>
              <span className="ix-legend-item"><span className="ix-swatch" style={{ background: 'var(--accent2)', opacity: 0.25 }} />Actual above should-cost</span>
            </div>
            <QuarterLines
              points={data.evolution}
              height={270}
              ariaLabel="Should-cost index and actual price index by quarter"
              series={[
                { key: 'actual_price_index', label: 'Actual price index', short: 'Actual', color: 'var(--accent2)', shadeAbove: true },
                { key: 'should_cost_index', label: 'Should-cost index', short: 'Should-cost', color: 'var(--accent4)', dash: '5 4' },
              ]}
            />
            <p className="st-evo-summary">
              Each cost model is indexed on itself, then weighted by spend. The should-cost follows each
              product&apos;s catalogue formula; the actual index follows the prices you recorded.
              {pair && gapPct != null && (
                <>
                  {' '}In <strong>{quarterLabel(pair.period)}</strong> the actual price index stands at{' '}
                  <strong>{fmt.index(pair.actual_price_index)}</strong> against a should-cost of{' '}
                  <strong>{fmt.index(pair.should_cost_index)}</strong> ({fmt.pct(gapPct)}).
                </>
              )}
            </p>
          </>
        ) : (
          <EmptyState variant="bare" title="Not enough quarters with prices to draw the evolution" />
        )}
      </Panel>

      {data.fx_gaps?.length > 0 && (
        <div className="ix-note">
          No exchange rate was found for {data.fx_gaps.map((g) => `${g.currency} (${g.period})`).join(', ')}; those values are used unconverted.
        </div>
      )}
    </div>
  );
}
