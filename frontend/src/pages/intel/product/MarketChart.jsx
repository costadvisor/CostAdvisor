import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Panel, RegionChips, normalizeRegion, fmt } from '../../../components/intel';
import { IndexLineChart } from '../../../components/charts';

/* Should-cost index for every priced region (tab 1, first panel).
 *
 * No forecast cone: the demo's cone was a heuristic, not a fitted interval,
 * and the API sends `band: null`. The forecast is a dashed line only. The
 * chart is labelled with the data vintage (`data_as_of`, the last actual
 * month) and the forecast vintage, never as "today". */
function monthsBetween(from, to) {
  const a = String(from || '').match(/^(\d{4})-(\d{2})/);
  const b = String(to || '').match(/^(\d{4})-(\d{2})/);
  if (!a || !b) return null;
  return (Number(b[1]) - Number(a[1])) * 12 + (Number(b[2]) - Number(a[2])) + 1;
}

function VariantBar({ product, data, onVariantChange }) {
  const navigate = useNavigate();
  const rows = product?.variants?.rows || [];
  const recipeVariants = data.variants || [];
  const hasProductVariants = rows.length > 1;
  const hasRecipeVariants = recipeVariants.length > 1;
  if (!hasProductVariants && !hasRecipeVariants) return null;
  const thisRow = rows.find((r) => r.is_this) || rows[0];
  const note = hasProductVariants ? product.variants.note : null;
  return (
    <>
      <div className="ixd-variantbar">
        {hasProductVariants && (
          <>
            <label htmlFor="ixd-variant-product">Variant</label>
            <select id="ixd-variant-product" className="ix-select" value={thisRow?.pid || ''}
              onChange={(e) => {
                const row = rows.find((r) => r.pid === e.target.value);
                if (row && !row.is_this && row.exists) navigate(`/intelligence/products/${encodeURIComponent(row.pid)}`);
              }}>
              {rows.map((r) => (
                <option key={r.pid} value={r.pid} disabled={!r.exists && !r.is_this}>
                  {r.name}{r.exists || r.is_this ? '' : ' (not in the catalogue)'}
                </option>
              ))}
            </select>
          </>
        )}
        {hasRecipeVariants && (
          <>
            <label htmlFor="ixd-variant-recipe">Recipe variant · {data.region}</label>
            <select id="ixd-variant-recipe" className="ix-select" value={data.variant || recipeVariants[0]}
              onChange={(e) => onVariantChange?.(e.target.value)}>
              {recipeVariants.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </>
        )}
      </div>
      {note && <div className="ixd-variant-note">{note}</div>}
    </>
  );
}

export default function MarketChart({ data, product, onVariantChange }) {
  const regions = useMemo(() => data.regions || [], [data.regions]);
  const [hidden, setHidden] = useState(() => new Set());
  const shown = regions.filter((r) => !hidden.has(r));

  const toggle = (code) => {
    const r = regions.find((x) => normalizeRegion(x) === normalizeRegion(code));
    if (!r) return;
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(r)) next.delete(r);
      else if (regions.length - next.size > 1) next.add(r); // keep at least one line
      return next;
    });
  };

  const series = useMemo(() => regions
    .filter((code) => !hidden.has(code))
    .map((code) => {
      const points = data.series_by_region?.[code] || [];
      return { key: code, label: code, points };
    })
    .filter((s) => s.points.length > 0),
  [regions, hidden, data.series_by_region]);

  // The shared chart pads its domain evenly; add headroom on top so the
  // last-actual / forecast labels never sit on a line that peaks at the edge.
  const yDomain = useMemo(() => {
    const vals = [100];
    series.forEach((s) => {
      s.points.forEach((p) => { if (Number.isFinite(Number(p.level)) && p.level != null) vals.push(Number(p.level)); });
    });
    if (vals.length < 3) return undefined;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const pad = Math.max(1.5, (max - min) * 0.06);
    return [min - pad, max + pad + (max - min + 2 * pad) * 0.1];
  }, [series]);

  const tl = data.timeline || {};
  const histMonths = monthsBetween(tl.actual_from, tl.actual_to);
  const foreMonths = monthsBetween(tl.forecast_from, tl.forecast_to);
  const span = [histMonths && `${histMonths}M history`, foreMonths && `${foreMonths}M forecast`]
    .filter(Boolean).join(' + ');
  const title = `Should-cost index — all regions${span ? ` · ${span}` : ''}`;

  const fc = data.forecast || null;
  const fcKind = fc?.kind || (foreMonths ? 'model' : 'none');
  const asOf = data.data_as_of || tl.actual_to;
  const asOfText = fmt.month(asOf);
  const flatShown = shown.filter((r) => data.flat_forecast_by_region?.[r]);
  const selFlat = !!data.flat_forecast || fcKind === 'flat_carry_forward';
  const flatWeight = fc?.flat_weight_pct;
  const partialFlat = !selFlat && flatWeight != null && flatWeight > 0 && flatWeight < 100;

  /* "Source data to Jun 2026; forecast points from the September 2026 vintage". */
  let vintage = `Source data to ${asOfText}`;
  if (fcKind === 'none' || !foreMonths) vintage += '; no forecast';
  else if (fc?.vintage_label) vintage += `; forecast points from the ${fc.vintage_label} vintage`;
  if (fcKind === 'flat_carry_forward') vintage += ', held flat at the last actual level';

  const footer = (
    <>
      Each region is priced with its own recipe and local index feeds; the bold line is the viewing
      region. Click a region above to show or hide it. The dashed line is the forecast; no range is drawn.
      {flatShown.length > 0 && (
        <> Flat carry-forward ({flatShown.join(', ')}): the {asOfText} level held for the forecast months.</>
      )}
      {partialFlat && (
        <> In {data.region}, {fmt.share(100 - flatWeight)} of the indexed weight has a series forecast; the rest is
          held at its {asOfText} level.</>
      )}
    </>
  );

  return (
    <Panel
      title={title}
      flush
      actions={(
        <>
          {selFlat && <span className="ixd-flat-tag" title="Every forecast month equals the last actual">flat carry-forward</span>}
          {regions.length > 1 && (
            <RegionChips regions={regions} active={shown} onSelect={toggle} ariaLabel="Regions shown on the chart" />
          )}
        </>
      )}
      footer={footer}
    >
      <div className="ixd-chart-head">
        <div className="ixd-sub">
          {data.name}
          <span className="ix-dot-sep" aria-hidden>·</span>formula-weighted
          <span className="ix-dot-sep" aria-hidden>·</span>base 100 = {fmt.month(tl.actual_from || data.base_period)}
          <span className="ix-dot-sep" aria-hidden>·</span>
          <span className="ixd-vintage">{vintage}</span>
        </div>
      </div>
      <VariantBar product={product} data={data} onVariantChange={onVariantChange} />
      <div className="ixd-chart-body">
        <IndexLineChart
          series={series}
          highlight={data.region}
          height={280}
          yDomain={yDomain}
          asOfLabel={`Data to ${fmt.monthShort(asOf)}`}
          forecastLabel={selFlat ? 'Flat carry-forward →' : `${foreMonths || 6}-month forecast →`}
          ariaLabel={`Should-cost index by region for ${data.name}, base 100 = January 2023`}
          emptyText="No index series for this product."
        />
      </div>
    </Panel>
  );
}
