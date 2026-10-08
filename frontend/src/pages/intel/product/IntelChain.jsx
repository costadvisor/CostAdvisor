import { Fragment } from 'react';
import { Panel, fmt } from '../../../components/intel';

/* "Synthesis & feedstock chain" + "Indicative cost formula" — one panel, two
 * sections, as in the mockup. Feedstocks → reaction → product, then the
 * default region's cost lines (margin excluded) → weighted sum → should-cost. */

function Node({ label, sub, tone = 'feed', title }) {
  return (
    <div className={`ixi-node ${tone}`} title={title}>
      <div className="ixi-node-name">{label}</div>
      {sub && <div className="ixi-node-sub">{sub}</div>}
    </div>
  );
}

function Plus() {
  return <div className="ixi-plus" aria-hidden>+</div>;
}

function Arrow({ label }) {
  return (
    <div className="ixi-arrow">
      <div className="ixi-arrow-glyph" aria-hidden>→</div>
      {label && <div className="ixi-arrow-label">{label}</div>}
    </div>
  );
}

function SynthesisSection({ product }) {
  const s = product.synthesis;
  const feedstocks = (s?.feedstocks || []).filter(Boolean);
  const productName = product.name || product.full_name || product.pid;

  if (!feedstocks.length && !s?.note) {
    return <p className="ixi-empty-line">No synthesis route in the source data.</p>;
  }

  let narrative = null;
  if (s?.source === 'synthesis_route' && (s.note || s.reaction)) {
    narrative = s.note
      ? `${s.note}${s.reaction ? ` (${s.reaction.replace(/\.$/, '')}.)` : ''}`
      : `${s.reaction}.`;
  }

  return (
    <>
      {feedstocks.length > 0 && (
        <div className="ixi-chain" role="group" aria-label="Feedstocks to product">
          {feedstocks.map((f, i) => (
            <Fragment key={`${f}-${i}`}>
              {i > 0 && <Plus />}
              <Node label={f} sub="feedstock" tone="feed" />
            </Fragment>
          ))}
          <Arrow label={s?.reaction} />
          <Node label={productName} sub="product" tone="product" />
        </div>
      )}
      {narrative ? (
        <p className="ixi-synth-text">{narrative}</p>
      ) : (
        <p className="ixi-chain-note">
          No synthesis route in the source data. The feedstocks shown are the cost formula&rsquo;s material
          lines; the weighted breakdown is below.
        </p>
      )}
    </>
  );
}

function CostFormulaSection({ product }) {
  const cf = product.cost_formula;
  const lines = (cf?.lines || []).filter((l) => l && l.cost_category !== 'margin');

  if (!cf || !lines.length) {
    return (
      <p className="ixi-empty-line">
        {product.has_formula === false
          ? 'No cost formula for this product yet.'
          : 'Not in the source data.'}
      </p>
    );
  }

  const unindexed = lines.filter((l) => !l.indexed).length;
  const margin = cf.margin_pct;

  return (
    <>
      <div className="ixi-chain" role="group" aria-label={`Cost lines, ${cf.region} recipe`}>
        {lines.map((l, i) => (
          <Fragment key={`${l.label}-${i}`}>
            {i > 0 && <Plus />}
            <Node
              label={l.label}
              sub={`${fmt.share(l.weight_pct)} weight${l.cost_category ? ` · ${l.cost_category}` : ''}`}
              tone={l.indexed ? 'indexed' : 'unindexed'}
              title={l.indexed
                ? `Indexed${l.series_key ? `: ${l.series_key}` : ''}`
                : 'No public index for this line'}
            />
          </Fragment>
        ))}
        <Arrow label="weighted sum" />
        <Node
          label={cf.form || product.form || 'Product'}
          sub={`${cf.region}${cf.variant ? ` · ${cf.variant}` : ''} should-cost`}
          tone="product"
        />
      </div>
      <div className="ixi-chain-note">
        Cost composition for {cf.region}, from the formula&rsquo;s weighted cost lines. This is a cost breakdown,
        not a verified synthesis pathway.
        {Number(margin) > 0 && <> The supplier margin ({fmt.share(margin)}) sits inside the 100 and is not drawn.</>}
        <span className="ixi-legend">
          <span className="ixi-legend-item"><span className="ixi-swatch indexed" aria-hidden />indexed line</span>
          {unindexed > 0 && (
            <span className="ixi-legend-item"><span className="ixi-swatch unindexed" aria-hidden />no public index</span>
          )}
        </span>
      </div>
    </>
  );
}

export default function IntelChain({ product }) {
  return (
    <Panel title="Synthesis & feedstock chain" className="ixi-chain-panel" bodyClassName="ixi-chain-body">
      <SynthesisSection product={product} />
      <div className="ixi-subhead">Indicative cost formula</div>
      <CostFormulaSection product={product} />
    </Panel>
  );
}
