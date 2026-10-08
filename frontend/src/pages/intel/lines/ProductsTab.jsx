import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Panel, RegionChips, EmptyState, SupplyBadge, SUBFAMILY_LABEL, STATUS_CODES, STATUS_LABELS,
  DEFAULT_STATUSES, statusParamOf, isAllStatuses, isDefaultStatus, lineFlags, fmt,
} from '../../../components/intel';
import { MoreChip } from './LineCard';
import {
  productHref, supplierHref, count, listedOf,
} from './lineUtil';
import { productsHref, statusQuery } from '../../../components/intel';

/* Line detail › Products: one card per product on the line (groups last and
 * marked), each with its supply-status badge, under the same status filter as
 * the Products grid; the line's makers and its axis facts in the rail. */

const SUPPLIERS_SHOWN = 12;

/* Where each product is placed in the demand tree: pid → the (industry,
 * category) placements from the line's `demand` block. */
function placementsByPid(demand) {
  const out = new Map();
  (demand?.industries || []).forEach((ind) => {
    (ind.categories || []).forEach((c) => {
      (c.pids || []).forEach((pid) => {
        if (!out.has(pid)) out.set(pid, []);
        out.get(pid).push({ industry: ind.industry, fn: c.fn, code: c.code });
      });
    });
  });
  return out;
}

/* A card's industries and functions, most placements first (ties keep the
 * API's alphabetical order), so the chips show where the product is bought
 * most — Municipal Water for ferric chloride, not Agri-Processing. A group
 * card or a variant base also counts the records it stands for. */
function rankByPlacements(p, placements) {
  const seen = new Set();
  const perInd = new Map();
  const perFn = new Map();
  [p.pid, ...(p.represents || []), ...(p.group_members || []).map((m) => m.pid)].forEach((pid) => {
    (placements.get(pid) || []).forEach((pl) => {
      const key = `${pl.industry}|${pl.code}`;
      if (seen.has(key)) return;
      seen.add(key);
      perInd.set(pl.industry, (perInd.get(pl.industry) || 0) + 1);
      if (pl.fn) perFn.set(pl.fn, (perFn.get(pl.fn) || 0) + 1);
    });
  });
  const rank = (list, n) => [...list].sort((a, b) => (n.get(b) || 0) - (n.get(a) || 0));
  return {
    industries: rank(p.industries || [], perInd),
    functions: rank(p.functions || [], perFn),
    perInd,
  };
}

function chipTitle(name, n) {
  return n ? `${name}: ${count(n, 'category', 'categories')}` : name;
}

function ProductCard({ p, placements }) {
  const ranked = useMemo(() => rankByPlacements(p, placements), [p, placements]);
  const fns = ranked.functions;
  const inds = ranked.industries;
  const sups = p.top_suppliers || [];
  const members = p.group_members || [];
  const cls = `ix-card ixl-pcard${p.is_group ? ' is-group' : ''}`;

  return (
    <Link to={productHref(p.pid)} className={cls}>
      <div className="ix-card-top">
        <span className="ixl-pid">{p.pid}</span>
        <div className="ix-card-badges">
          {p.is_group && <span className="ix-badge plain st-info" title="A group card that stands for several records">Group</span>}
        </div>
      </div>
      <div className="ixl-pcard-name">{p.name}</div>
      <div className="ixl-pcard-status"><SupplyBadge status={p.status} /></div>
      {p.full_name && p.full_name !== p.name && <div className="ixl-pcard-note">{p.full_name}</div>}
      <div className="ixl-pcard-meta">
        {p.form && <span>{p.form}</span>}
        {p.regions?.length > 0
          ? <RegionChips regions={p.regions} size="sm" ariaLabel="Regions with a cost formula" />
          : <span>No cost formula yet</span>}
      </div>
      {p.is_group && members.length > 0 && (
        <div className="ixl-pcard-note">
          <strong>Group of {count(members.length, 'record')}:</strong>{' '}
          <span className="ixl-codes">{members.map((m) => m.pid).join(', ')}</span>
        </div>
      )}
      {!p.is_group && p.represents?.length > 0 && (
        <div className="ixl-pcard-note">
          <strong>Also stands for:</strong> <span className="ixl-codes">{p.represents.join(', ')}</span>
        </div>
      )}
      {(fns.length > 0 || inds.length > 0) && (
        <div className="ixl-chips">
          {fns.slice(0, 2).map((f) => <span key={`f-${f}`} className="ix-tag fn" title={f}>{f}</span>)}
          {inds.slice(0, 2).map((i) => (
            <span key={`i-${i}`} className="ix-tag ixl-ind" title={chipTitle(i, ranked.perInd.get(i))}>{i}</span>
          ))}
          <MoreChip items={inds} shown={2} />
        </div>
      )}
      <div className="ixl-card-foot">
        {sups.length > 0 ? (
          <span className="ixl-card-sups" title={`Makers: ${sups.join(', ')}`}>
            <span className="ixl-card-sups-names">{sups.join(', ')}</span>
          </span>
        ) : (
          <span className="ixl-muted">
            {p.generic_suppliers?.length ? 'Unnamed producers only' : 'No counted maker yet'}
          </span>
        )}
      </div>
    </Link>
  );
}

/* The line's makers: a per-row list is fine; its length is never a
 * headline, so the panel title and caption carry no number. */
function SuppliersPanel({ line }) {
  const [all, setAll] = useState(false);
  const list = line.suppliers || [];
  const generic = line.generic_suppliers || [];
  const shown = all ? list : list.slice(0, SUPPLIERS_SHOWN);
  const total = listedOf(line);

  return (
    <Panel title="Makers on this line" caption="counted makers, most products first"
      footer={generic.length > 0 ? (
        <span><strong>Unnamed producers:</strong> {generic.join(', ')}</span>
      ) : null}>
      {list.length === 0 ? (
        <div className="ix-muted ix-small">No counted maker on this line yet.</div>
      ) : (
        <>
          <ul className="ixl-suplist">
            {shown.map((s) => (
              <li key={s.id}>
                <span>
                  <Link to={supplierHref(s.id)}>{s.name}</Link>
                  {s.hq && <span className="ixl-sup-hq">{s.hq}</span>}
                </span>
                <span className="ixl-sup-n" title="Products on this line the maker is counted on">
                  {s.products_on_line} of {total}
                </span>
              </li>
            ))}
          </ul>
          {list.length > SUPPLIERS_SHOWN && (
            <button type="button" className="ix-link ix-small" style={{ marginTop: 8 }} onClick={() => setAll((a) => !a)}>
              {all ? 'Show fewer' : 'Show every maker'}
            </button>
          )}
        </>
      )}
    </Panel>
  );
}

function AboutPanel({ line }) {
  const ssf = line.subfamily;
  const flags = lineFlags(line.flags);
  return (
    <Panel title="About this line">
      <dl className="ixl-facts">
        <div>
          <dt>{SUBFAMILY_LABEL}</dt>
          <dd>
            {ssf?.name ? <strong>{ssf.name}</strong> : <span className="ix-muted">Not on a named {SUBFAMILY_LABEL.toLowerCase()}</span>}
          </dd>
        </div>
        {line.platform && (
          <div>
            <dt>Platform</dt>
            <dd className="ixl-mono">{line.platform}</dd>
          </div>
        )}
        {line.former_names?.length > 0 && (
          <div>
            <dt>Former names</dt>
            <dd>{line.former_names.join(' · ')}</dd>
          </div>
        )}
      </dl>
      {flags.length > 0 && (
        <div className="ix-stack" style={{ gap: 8, marginTop: 14 }}>
          {flags.map((f) => (
            <div key={f.code} className="ix-callout warn">{f.label}</div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* The status filter, as on the Products grid: four toggles (at least one
 * stays on), plus the switch between the verified view and every status. */
function StatusBar({ line, statuses, shown }) {
  const [, setParams] = useSearchParams();
  const setStatuses = (codes) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      const v = statusParamOf(codes);
      if (v) next.set('status', v); else next.delete('status');
      return next;
    }, { replace: true });
  };
  const toggle = (code) => {
    const next = statuses.includes(code) ? statuses.filter((c) => c !== code) : [...statuses, code];
    if (next.length) setStatuses(next);
  };
  const listed = line.counts?.listed ?? 0;
  const dv = line.counts?.default_view ?? 0;
  return (
    <div className="ixl-statusbar" role="group" aria-label="Supply status">
      {STATUS_CODES.map((code) => (
        <button key={code} type="button" className="ix-pill" aria-pressed={statuses.includes(code)}
          onClick={() => toggle(code)}>
          {STATUS_LABELS[code]}
        </button>
      ))}
      <span className="ixl-statusbar-meta">
        {fmt.num(shown)} shown
        {' · '}
        {isAllStatuses(statuses) ? (
          <button type="button" className="ix-link" onClick={() => setStatuses(DEFAULT_STATUSES)}>
            Verified supply only ({fmt.num(dv)})
          </button>
        ) : (
          <button type="button" className="ix-link" onClick={() => setStatuses(STATUS_CODES)}>
            Show all statuses ({fmt.num(listed)})
          </button>
        )}
        {' · '}
        <Link className="ix-link" to={productsHref({
          line_id: line.id, status: isDefaultStatus(statuses) ? null : statusQuery(statuses),
        })}>
          Open in the Products grid
        </Link>
      </span>
    </div>
  );
}

export default function ProductsTab({ line, statuses = DEFAULT_STATUSES }) {
  const products = line.products || [];
  const placements = useMemo(() => placementsByPid(line.demand), [line.demand]);
  const singles = products.filter((p) => !p.is_group);
  const groups = products.filter((p) => p.is_group);
  const listed = listedOf(line);

  let empty = null;
  if (products.length === 0) {
    empty = listed === 0
      ? <EmptyState title="No tracked product on this line yet" body="The supply axis names this line; no product in the grid sits on it yet." />
      : (
        <EmptyState
          title={isDefaultStatus(statuses) ? 'No product on this line has verified makers yet' : 'No product on this line has these statuses'}
          body="Show all statuses to see the products on this line."
        />
      );
  }

  return (
    <div className="ix-two-col">
      <div className="ix-col" style={{ gap: 0 }}>
        {listed > 0 && <StatusBar line={line} statuses={statuses} shown={products.length} />}
        {empty || (
          <>
            {singles.length > 0 && (
              <>
                <div className="ix-section-label">{count(singles.length, 'product')}</div>
                <div className="ixl-pgrid">
                  {singles.map((p) => <ProductCard key={p.pid} p={p} placements={placements} />)}
                </div>
              </>
            )}
            {groups.length > 0 && (
              <>
                <div className="ix-section-label ixl-group-label">
                  {count(groups.length, 'group')}
                </div>
                <div className="ixl-pgrid">
                  {groups.map((p) => <ProductCard key={p.pid} p={p} placements={placements} />)}
                </div>
              </>
            )}
          </>
        )}
        <div style={{ marginTop: 20 }}><AboutPanel line={line} /></div>
      </div>
      <aside className="ix-rail">
        <SuppliersPanel line={line} />
      </aside>
    </div>
  );
}
