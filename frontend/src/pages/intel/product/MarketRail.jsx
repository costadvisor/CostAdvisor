import { Link } from 'react-router-dom';
import { Panel, KpiTile, StatusBadge, REGION_LABELS, normalizeRegion, fmt } from '../../../components/intel';
import { Gauge, MonthBars } from '../../../components/charts';

/* Tab 1 right rail: market snapshot, cycle position, seasonality and
 * volatility, formula confidence, variants. The snapshot is the latest month
 * of the source data (`index_latest`), not "today". */

const regionName = (code) => REGION_LABELS[normalizeRegion(code)] || code;

function Snapshot({ data }) {
  const s = data.snapshot;
  if (!s) return null;
  const signedOff = s.review_status === 'signed_off';
  return (
    <Panel title="Market snapshot" caption={regionName(s.region || data.region)}>
      <div className="ix-kpis two">
        <KpiTile label="Should-cost index, latest month" value={fmt.index(s.index_latest)}
          sub={`${s.region || data.region} · ${fmt.month(s.index_latest_period)} · base 100 = Jan 2023`} />
        <KpiTile label="Cost lines" value={fmt.num(s.cost_lines)} sub="in this recipe, margin included" />
        <KpiTile label="Regions" value={fmt.num(s.regions)} sub="priced for this product" />
        <KpiTile label="Review status" value={signedOff ? 'Signed off' : 'Pending'}
          tone={signedOff ? 'good' : 'warn'}
          sub={signedOff ? 'Recipe signed off by a reviewer' : (s.review_status_label || 'Pending review')} />
      </div>
    </Panel>
  );
}

const VERDICT_TONE = { high: 'danger', mid: 'warn', low: '', flat: 'info' };

function Cycle({ data }) {
  const c = data.cycle;
  if (!c) return null;
  const win = c.window_months ? `${c.window_months}M` : '';
  return (
    <Panel title="Cycle position" caption={`${data.region} · ${c.window_label || `${c.window_months}-month`} range`}>
      <Gauge
        value={c.percentile}
        valueLabel={c.percentile == null ? 'n/a' : `${fmt.ordinal(c.percentile)} percentile`}
        labels={{ left: `${win} low`, right: `${win} high` }}
        ends={{ left: fmt.index(c.low), right: fmt.index(c.high) }}
        ariaLabel={`Cycle position: ${fmt.ordinal(c.percentile)} percentile of the ${c.window_label || ''} range`}
      />
      {c.verdict && <div className={`ix-callout ixd-verdict ${VERDICT_TONE[c.position] ?? ''}`}>{c.verdict}</div>}
    </Panel>
  );
}

function SeasonVol({ data }) {
  const s = data.seasonality;
  const v = data.volatility;
  if (!s && !v) return null;
  const hasFactors = s && Array.isArray(s.factors) && s.factors.some((x) => x != null);
  const MONTHS = fmt.MONTHS;
  return (
    <Panel title="Seasonality & volatility" caption={data.region}>
      {hasFactors && (
        <div className="ixd-rail-section">
          <div className="ix-section-label">Seasonality — blended monthly factor</div>
          <MonthBars values={s.factors} baseline={100} height={70}
            ariaLabel="Blended seasonal factor by month, 100 = no seasonal effect" />
          <div className="ixd-rail-meta">
            {s.peak_month && <>Peak {MONTHS[s.peak_month - 1]}</>}
            {s.trough_month && <> · trough {MONTHS[s.trough_month - 1]}</>}
            {s.spread != null && <> · spread {fmt.index(s.spread)} pts</>}
            {s.seasonal_weight_pct != null && <> · {fmt.share(s.seasonal_weight_pct)} of the weight has seasonal factors</>}
          </div>
          {s.note && <div className="ixd-rail-note">{s.note}</div>}
        </div>
      )}
      {v && (
        <div className="ixd-rail-section">
          <div className="ix-section-label">Price volatility{v.months ? ` — ${v.months}-month history` : ''}</div>
          {v.percentile != null ? (
            <Gauge
              value={v.percentile}
              valueLabel={`${fmt.ordinal(v.percentile)} percentile`}
              labels={{ left: 'Low', right: 'High' }}
              ariaLabel={`Volatility: ${fmt.ordinal(v.percentile)} percentile of the index library`}
            />
          ) : (
            <div className="ixd-rail-note">{v.reason || 'Not in the source data'}</div>
          )}
          {v.note && <div className="ixd-rail-note">{v.note}</div>}
        </div>
      )}
    </Panel>
  );
}

/* An imported recipe whose series resolve grades "high" until an expert
 * reviews it (a pricing gap caps it at medium), so the grade says where it
 * comes from. */
const IMPORTED_HIGH_TIP = 'The source data carries no proxy classification; every imported recipe '
  + 'whose series resolve is graded high until an expert reviews it.';

function Confidence({ data }) {
  const t = data.trust;
  if (!t) return null;
  const nonMargin = (data.components || []).filter((c) => !c.is_margin);
  const indexedCount = nonMargin.filter((c) => c.indexed).length;
  return (
    <Panel title="Formula confidence" caption={data.region}>
      <dl className="ixd-dl">
        {nonMargin.length > 0 && (
          <>
            <dt>Indexed lines</dt>
            <dd>{indexedCount} of {nonMargin.length} cost lines follow a public index</dd>
          </>
        )}
        {t?.grade && (
          <>
            <dt>Trust grade</dt>
            <dd>
              {t.grade === 'high' && t.provenance === 'imported' ? (
                <span className="ixd-grade-tip" title={IMPORTED_HIGH_TIP} tabIndex={0}>
                  High (as imported)
                </span>
              ) : (
                t.grade.charAt(0).toUpperCase() + t.grade.slice(1)
              )}
              {t.caveat && <span className="ix-muted"> · {t.caveat}</span>}
            </dd>
          </>
        )}
        {t && (
          <>
            <dt>Review</dt>
            <dd>
              <StatusBadge status={t.review_status === 'signed_off' ? 'Reviewed' : 'Pending'}
                title={t.review_status_label} />
              {t.reviewed_at && <span className="ix-muted"> {fmt.date(t.reviewed_at)}</span>}
            </dd>
          </>
        )}
        {t?.provenance && (
          <>
            <dt>Provenance</dt>
            <dd>{t.provenance === 'imported' ? 'Imported from the source data' : t.provenance}</dd>
          </>
        )}
      </dl>
    </Panel>
  );
}

function deltaTone(delta) {
  const d = String(delta || '');
  if (/^base$/i.test(d)) return 'info';
  if (d.startsWith('+')) return 'warn';
  if (d.startsWith('-') || d.startsWith('−')) return 'good';
  return 'neutral';
}

function Variants({ data, product, onVariantChange }) {
  const rows = product?.variants?.rows || [];
  const recipe = data.variants || [];
  if (rows.length < 2 && recipe.length < 2) return null;
  return (
    <Panel title="Variants" caption={rows.length > 1 ? 'Cost delta vs base form' : `Recipe variants · ${data.region}`} flush>
      {rows.length > 1 && (
        <div className="ixd-vars">
          {rows.map((r) => {
            const body = (
              <>
                <div className="ixd-var-main">
                  <div className="ixd-var-name">{r.name}</div>
                  {r.spec && <div className="ixd-var-spec">{r.spec}</div>}
                </div>
                {r.active && <span className="ixd-var-active" title="Active content">{r.active}</span>}
                {r.delta && <StatusBadge status={r.delta} tone={deltaTone(r.delta)} />}
              </>
            );
            if (!r.is_this && r.exists) {
              return (
                <Link key={r.pid} className="ixd-var" to={`/intelligence/products/${encodeURIComponent(r.pid)}`}
                  title={`Open ${r.pid}`}>
                  {body}
                </Link>
              );
            }
            return <div key={r.pid} className={`ixd-var${r.is_this ? ' is-this' : ''}`}>{body}</div>;
          })}
        </div>
      )}
      {rows.length < 2 && recipe.length > 1 && (
        <div className="ixd-vars">
          {recipe.map((v) => {
            const on = v === data.variant;
            return (
              <button key={v} type="button" className={`ixd-var${on ? ' is-this' : ''}`} aria-pressed={on}
                onClick={() => !on && onVariantChange?.(v)}>
                <div className="ixd-var-main">
                  <div className="ixd-var-name">{v.charAt(0).toUpperCase() + v.slice(1)}</div>
                  <div className="ixd-var-spec">Separate recipe in {regionName(data.region)}</div>
                </div>
                {on && <StatusBadge status="Shown" tone="info" />}
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

export default function MarketRail({ data, product, onVariantChange }) {
  return (
    <div className="ix-rail">
      <Snapshot data={data} />
      <Cycle data={data} />
      <Variants data={data} product={product} onVariantChange={onVariantChange} />
      <SeasonVol data={data} />
      <Confidence data={data} />
    </div>
  );
}
