import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Panel, KpiTile, RegionChips, EmptyState, fmt, REGION_LABELS, normalizeRegion, regionColor, sortRegions,
} from '../../../components/intel';
import { HBars } from '../../../components/charts';
import { supplierPath, PRODUCT_COUNT_HINT } from './supplierUtils';

const TOP_N = 10;
const MAX_ALIASES = 6;

/* A ranked bar list that shows the first TOP_N and can open the rest. */
function RankedBars({ items, color, labelWidth = 210, onItemClick, ariaLabel, noun }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, TOP_N);
  return (
    <div className="ixs-bars">
      <HBars
        items={shown.map((i) => ({
          key: i.name, id: i.id, label: i.name, value: i.count, color: i.color || color,
        }))}
        formatValue={(v) => fmt.num(v)}
        labelWidth={labelWidth}
        onItemClick={onItemClick}
        ariaLabel={ariaLabel}
      />
      {items.length > TOP_N && (
        <button type="button" className="ix-link ixs-panel-more" onClick={() => setAll((a) => !a)} aria-expanded={all}>
          {all ? `Show top ${TOP_N}` : `Show all ${items.length} ${noun}`}
        </button>
      )}
    </div>
  );
}

function RegionPresence({ breakdown, total }) {
  return (
    <div className="ixs-regions">
      {sortRegions(breakdown.map((r) => r.region)).map((code) => {
        const r = breakdown.find((b) => b.region === code);
        const drop = normalizeRegion(code);
        return (
          <div key={code} className="ixs-region" style={{ '--rg': regionColor(drop) }}>
            <div className="ixs-region-name">{REGION_LABELS[drop] || code}</div>
            <div className="ixs-region-val">{fmt.num(r.count)}</div>
            <div className="ixs-region-lbl">
              {fmt.plural(r.count, 'product')}
              {total ? ` · ${Math.round((r.count / total) * 100)}%` : ''}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Competitors({ supplier: s }) {
  const list = s.competitors || [];
  if (!list.length) {
    return (
      <Panel title="Competitive overlap">
        <EmptyState variant="bare" title="No other counted maker on these products" />
      </Panel>
    );
  }
  const top = Math.max(...list.map((c) => c.shared_products || 0), 1);
  return (
    <Panel
      title="Competitive overlap"
      caption="ranked by shared products"
      flush
      footer={`Other counted makers on the same products as ${s.name}. Overlap = shared products as a percentage of ${s.name}'s products tracked here.`}
    >
      <div className="ix-table-wrap">
        <table className="ix-table ixs-comp">
          <thead>
            <tr>
              <th className="ixs-rank">#</th>
              <th>Supplier</th>
              <th>Shared products</th>
              <th className="num">Shared lines</th>
              <th className="num">Overlap</th>
              <th className="num" title={PRODUCT_COUNT_HINT}>Their products tracked</th>
            </tr>
          </thead>
          <tbody>
            {list.map((c, i) => (
              <tr key={c.id}>
                <td className="ixs-rank">{i + 1}</td>
                <td><Link to={supplierPath(c.id)} state={{ fromList: true }}>{c.name}</Link></td>
                <td>
                  <span className="ixs-bar">
                    <span className="ixs-bar-track">
                      <span className="ixs-bar-fill" style={{ width: `${(c.shared_products / top) * 100}%` }} />
                    </span>
                    <span className="ixs-bar-val">{fmt.num(c.shared_products)}</span>
                  </span>
                </td>
                <td className="num">{fmt.num(c.shared_lines)}</td>
                <td className="num">{c.shared_pct == null ? '—' : `${c.shared_pct}%`}</td>
                <td className="num ix-muted">{fmt.num(c.product_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Profile({ supplier: s, compact = false }) {
  const [allAliases, setAllAliases] = useState(false);
  const aliases = s.aliases || [];
  const shownAliases = allAliases ? aliases : aliases.slice(0, MAX_ALIASES);
  return (
    <Panel title="Profile">
      <dl className="ixs-dl">
        <dt>Headquarters</dt>
        <dd>{s.hq || <span className="ix-muted">Not in the source data</span>}</dd>
        {!compact && (
          <>
            <dt>Makes in</dt>
            <dd>{(s.regions || []).length ? <RegionChips regions={s.regions} /> : <span className="ix-muted">Region not stated</span>}</dd>
            <dt>Product lines</dt>
            <dd>{fmt.num(s.line_count)}</dd>
            <dt>Integrated</dt>
            <dd>
              {s.integrated_count
                ? `Owns an upstream step on ${fmt.plural(s.integrated_count, 'product')}`
                : 'Not evidenced on any product'}
            </dd>
          </>
        )}
        {aliases.length > 0 && (
          <>
            <dt>Also named as</dt>
            <dd>
              <div className="ixs-aliases">
                {shownAliases.map((a) => <span key={a} className="ix-tag plain">{a}</span>)}
              </div>
              {aliases.length > MAX_ALIASES && (
                <button type="button" className="ix-link ixs-panel-more" onClick={() => setAllAliases((o) => !o)}
                  aria-expanded={allAliases}>
                  {allAliases ? 'Show fewer' : `Show all ${aliases.length}`}
                </button>
              )}
            </dd>
          </>
        )}
      </dl>
    </Panel>
  );
}

const DOSSIER_ROLE_LABEL = { producer: 'Producer', price_setter: 'Price setter' };

function DossierRoles({ roles }) {
  if (!roles?.length) return null;
  return (
    <Panel title="Index dossier roles" caption={fmt.plural(roles.length, 'index', 'indexes')}
      footer="Named as a producer or price setter in the dossier of a commodity index.">
      <ul className="ixs-roles">
        {roles.map((r, i) => (
          <li key={`${r.series_key}-${r.role}-${i}`} className="ixs-role">
            <div className="ixs-role-name">{r.index_name || r.series_key}</div>
            <div className="ixs-role-meta">
              <span>{DOSSIER_ROLE_LABEL[r.role] || r.role || 'Role not stated'}</span>
              {r.location && <span>{r.location}</span>}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/* PIDs this producer is linked to that are not cards in the grid (an absorbed
 * record, a pointer…). Plain text. */
export function UnlistedCodes({ codes }) {
  if (!codes?.length) return null;
  return (
    <Panel title="Codes not in the grid"
      caption={fmt.plural(codes.length, 'code')}
      footer="Named for this producer in the source, on product codes that are not product cards in the grid.">
      <div className="ixs-codes">
        {codes.map((c) => <span key={c} className="ix-tag plain ixs-code">{c}</span>)}
      </div>
    </Panel>
  );
}

/* The supplier's portfolio at a glance: counts, families, industries,
 * capabilities, regions, competitors, profile. */
export default function SupplierOverviewTab({ supplier: s, familyColor, onFamilyClick }) {
  const families = (s.families || []).map((f) => ({ ...f, color: familyColor(f.name) }));
  const industries = s.industries || [];
  const functions = s.functions || [];
  const breakdown = s.region_breakdown || [];

  return (
    <div className="ix-stack">
      <div className="ix-kpis ixs-kpis">
        <KpiTile label="Products tracked here" value={fmt.num(s.product_count)} sub={fmt.plural(s.line_count, 'product line')} />
        <KpiTile label="Families" value={fmt.num(s.family_count)} />
        <KpiTile label="Industries served" value={fmt.num(s.industry_count)} />
        <KpiTile label="Regions" value={fmt.num(s.region_count)}
          sub={sortRegions(s.regions || []).join(' · ') || undefined} />
        <KpiTile label="Integrated" value={fmt.num(s.integrated_count)}
          sub={`of ${fmt.plural(s.product_count, 'product')}`} />
      </div>

      <div className="ix-two-col">
        <div className="ix-col">
          <div className="ix-split">
            {families.length > 0 && (
              <Panel title="Portfolio by family" caption="Products per family">
                <RankedBars items={families} ariaLabel="Products by family" noun="families"
                  onItemClick={onFamilyClick ? (it) => onFamilyClick(it.id != null ? String(it.id) : '') : undefined} />
              </Panel>
            )}
            {breakdown.length > 0 && (
              <Panel title="Where it makes them" caption="Products tracked here, per manufacturing region">
                <RegionPresence breakdown={breakdown} total={s.product_count} />
              </Panel>
            )}
          </div>

          {(industries.length > 0 || functions.length > 0) && (
            <div className="ix-split">
              {industries.length > 0 && (
                <Panel title="Industries served" caption={`Products used in each · ${fmt.plural(s.industry_count ?? industries.length, 'industry', 'industries')}`}>
                  <RankedBars items={industries} color="var(--accent)" ariaLabel="Products by industry" noun="industries" />
                </Panel>
              )}
              {functions.length > 0 && (
                <Panel title="Capabilities" caption="Functions its products perform">
                  <RankedBars items={functions} color="var(--accent4)" ariaLabel="Products by function" noun="functions" />
                </Panel>
              )}
            </div>
          )}

          <Competitors supplier={s} />
        </div>

        <div className="ix-rail">
          <Profile supplier={s} />
          <DossierRoles roles={s.dossier_roles} />
          <UnlistedCodes codes={s.unlisted_codes} />
        </div>
      </div>
    </div>
  );
}

export { Profile };
