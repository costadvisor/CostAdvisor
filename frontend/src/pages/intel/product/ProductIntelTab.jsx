import { useState } from 'react';
import { Panel, EmptyState } from '../../../components/intel';
import IntelIdentity from './IntelIdentity';
import IntelChain from './IntelChain';
import IntelSuppliers from './IntelSuppliers';
import IntelWhereBought from './IntelWhereBought';
import { themeColor, withBold } from './IntelUtil';
import '../../../styles/intel-product-intel.css';

/* Product page, tab 2 — "Product Intelligence".
 *
 * Everything comes from the product payload the page already fetched
 * (GET /api/intel/products/{pid}); this tab makes no request of its own.
 * Layout follows the mockup's tab 2: full-width panels and two-up rows. Panels
 * whose content is optional in the source (drivers, current events,
 * substitution) are hidden when empty; the core ones say plainly that the
 * source has nothing. There are no supply/demand bars: the splits were
 * invented, not sourced (21 Sep ruling). */

function Functionalities({ product }) {
  const items = product.functionalities || [];
  const fns = product.functions || [];
  return (
    <Panel title="Functionalities" caption="What this chemical does in formulation">
      {fns.length > 0 && (
        <div className="ixi-fn-pills" aria-label="Functions in the category tree">
          {fns.map((f) => <span key={f} className="ixi-fn-pill" title="Function in the ratified category tree">{f}</span>)}
        </div>
      )}
      {items.length === 0 ? (
        <p className="ixi-empty-line">Not in the source data.</p>
      ) : (
        <ul className="ixi-func-list">
          {items.map((f, i) => (
            <li key={`${f.name}-${i}`} className="ixi-func-item">
              <span className="ixi-func-dot" style={{ background: themeColor(f.color) }} aria-hidden />
              <div>
                <div className="ixi-func-name">{f.name}</div>
                {f.desc && <div className="ixi-func-desc">{f.desc}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/* Authored specification notes can run to a dozen long paragraphs; the first
 * two show, the rest open on request. */
const SPEC_SHOWN = 2;

function SpecNotes({ notes }) {
  const [open, setOpen] = useState(false);
  if (!notes.length) return null;
  const shown = open ? notes : notes.slice(0, SPEC_SHOWN);
  const hidden = notes.length - SPEC_SHOWN;
  return (
    <>
      {shown.map((s, j) => (
        <div key={j} className="ixi-app-spec"><span aria-hidden>⚠ </span>{s}</div>
      ))}
      {hidden > 0 && (
        <button type="button" className="ix-link ixi-app-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Show fewer specification notes' : `Show ${hidden} more specification ${hidden === 1 ? 'note' : 'notes'}`}
        </button>
      )}
    </>
  );
}

function Applications({ product }) {
  const rows = product.applications || [];
  return (
    <Panel title="Applications" caption="End uses by industry">
      {rows.length === 0 ? (
        <p className="ixi-empty-line">Not in the source data.</p>
      ) : (
        <div className="ixi-apps">
          {rows.map((a, i) => (
            <div key={`${a.industry}-${i}`} className="ixi-app-group">
              <div className="ixi-app-industry">{a.industry}</div>
              <div className="ixi-app-items">
                {(a.items || []).map((it, j) => <span key={j} className="ix-tag ixi-app-tag">{it}</span>)}
              </div>
              <SpecNotes notes={(a.spec || []).filter(Boolean)} />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* Icon + title + body cards (macro drivers, substitution). `ico` is authored. */
function NoteItem({ ico, title, badge, body }) {
  return (
    <li className="ixi-item">
      {ico && <span className="ixi-item-ico" aria-hidden>{ico}</span>}
      <div className="ixi-item-main">
        <div className="ixi-item-title">{title}{badge}</div>
        {body && <div className="ixi-item-body">{body}</div>}
      </div>
    </li>
  );
}

function MacroDrivers({ product }) {
  const rows = product.macro_drivers || [];
  if (!rows.length) return null;
  return (
    <Panel
      title="Market drivers & future trends"
      caption="Structural, multi-year demand direction, separate from the 3M / 24M cost dynamics on Market & Costs"
    >
      <ul className="ixi-items">
        {rows.map((d, i) => <NoteItem key={`${d.title}-${i}`} ico={d.ico} title={d.title} body={d.body} />)}
      </ul>
    </Panel>
  );
}

const LONG_TEXT = 700;

function CurrentEvents({ product }) {
  const text = product.current_events;
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const long = text.length > LONG_TEXT;
  return (
    <Panel title="Current events" caption="Recent market events recorded in the source data">
      <div className={`ixi-events${long && !open ? ' is-clamped' : ''}`}>
        <p>{withBold(text)}</p>
      </div>
      {long && (
        <button type="button" className="ix-link ixi-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : 'Show all'}
        </button>
      )}
    </Panel>
  );
}

const FLAG_TONE = { warn: 'warn', ok: 'ok', info: 'info' };

function Compliance({ product }) {
  const rows = product.compliance || [];
  return (
    <Panel title="Compliance & quality flags" caption="Procurement-critical parameters">
      {rows.length === 0 ? (
        <p className="ixi-empty-line">No compliance flags in the source data.</p>
      ) : (
        <ul className="ixi-comp-list">
          {rows.map((c, i) => {
            if (c.bare || !c.flag) {
              return (
                <li key={i} className="ixi-comp-row is-bare">
                  <div className="ixi-comp-name">{c.name}</div>
                  {c.desc && <div className="ixi-comp-desc">{c.desc}</div>}
                </li>
              );
            }
            return (
              <li key={i} className="ixi-comp-row">
                <span className={`ixi-flag ${FLAG_TONE[c.type] || 'info'}`}>{c.flag}</span>
                <div>
                  {c.name && <div className="ixi-comp-name">{c.name}</div>}
                  {c.desc && <div className="ixi-comp-desc">{c.desc}</div>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function IndexSources({ product }) {
  const rows = product.index_sources || [];
  if (!rows.length) {
    if (!product.cost_formula) return null;
    return (
      <Panel title="Index sources">
        <p className="ixi-empty-line">Not in the source data.</p>
      </Panel>
    );
  }
  const region = product.index_sources_region || product.cost_formula?.region;
  return (
    <Panel
      title="Index sources"
      caption={`Public feeds used in the ${region ? `${region} ` : ''}should-cost formula`}
      flush
    >
      <div className="ix-table-wrap">
        <table className="ix-table ixi-src-table">
          <thead>
            <tr>
              <th scope="col">Cost line</th>
              <th scope="col">Tag → series</th>
              <th scope="col">Agency</th>
              <th scope="col">Frequency</th>
              <th scope="col">Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => {
              const isMargin = s.cost_category === 'margin';
              return (
                <tr key={`${s.line}-${i}`}>
                  <td>
                    <div className="ix-cell-main">{s.line}</div>
                    {s.cost_category && <div className="ix-cell-sub">{s.cost_category}</div>}
                  </td>
                  {s.indexed ? (
                    <>
                      <td>
                        <div className="ixi-src-map">
                          {s.tag && <span className="ixi-mono ixi-src-tag">{s.tag}</span>}
                          {s.tag && s.series_key && <span className="ixi-muted" aria-hidden>→</span>}
                          {s.series_key && <span className="ixi-mono ixi-src-key">{s.series_key}</span>}
                        </div>
                        {s.series_name && <div className="ix-cell-sub">{s.series_name}</div>}
                      </td>
                      <td>{s.agency || <span className="ixi-muted">—</span>}</td>
                      <td className="ixi-nowrap">{s.freq || <span className="ixi-muted">—</span>}</td>
                      <td className="ixi-src-note">{s.proxy || <span className="ixi-muted">—</span>}</td>
                    </>
                  ) : (
                    <>
                      <td colSpan={3} className="ixi-muted">
                        {isMargin ? 'Margin: no index, held flat inside the 100' : 'No public index for this line'}
                      </td>
                      <td className="ixi-src-note"><span className="ixi-muted">—</span></td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const RISK_TONE = { high: 'st-bad', 'medium-high': 'st-warn', medium: 'st-warn', low: 'st-good' };

function Substitution({ product }) {
  const rows = product.substitution || [];
  if (!rows.length) return null;
  return (
    <Panel
      title="Alternatives & substitution risk"
      caption="Could a buyer credibly switch chemistry, not just supplier?"
    >
      <ul className="ixi-items">
        {rows.map((s, i) => {
          const tone = RISK_TONE[String(s.risk || '').toLowerCase()] || 'st-neutral';
          const badge = s.risk
            ? <span className={`ix-badge plain ${tone} ixi-risk`}>{s.risk} risk</span>
            : null;
          return <NoteItem key={`${s.title}-${i}`} ico={s.ico} title={s.title} badge={badge} body={s.body} />;
        })}
      </ul>
    </Panel>
  );
}

export default function ProductIntelTab({ pid, product }) {
  if (!product) {
    return <EmptyState variant="compact" title="Not in the source data" body={pid ? `No product intelligence for ${pid}.` : undefined} />;
  }
  return (
    <div className="ixi-root ix-stack" key={product.pid}>
      <IntelIdentity product={product} />
      <div className="ix-split ixi-split">
        <Functionalities product={product} />
        <Applications product={product} />
      </div>
      <IntelWhereBought product={product} />
      <MacroDrivers product={product} />
      <CurrentEvents product={product} />
      <IntelChain product={product} />
      <IntelSuppliers product={product} />
      <Compliance product={product} />
      <IndexSources product={product} />
      <Substitution product={product} />
    </div>
  );
}
