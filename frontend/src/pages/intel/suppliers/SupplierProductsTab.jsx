import { Fragment, useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Panel, StatusBadge, SupplyBadge, EmptyState, fmt, UNPUBLISHED_LINE,
} from '../../../components/intel';
import {
  Evidence, Integration, Regions, Sites,
} from '../product/IntelSuppliers';
import { productPath, linePath, ROLE_LABEL } from './supplierUtils';

/* Every product card this supplier is linked to, grouped by product line, with
 * its role, evidence label, integration, manufacturing regions and sites.
 * The API puts counting links first and distributors last; groups keep that
 * order. No share column (house rule 6). */

function ProductRow({ p }) {
  const [open, setOpen] = useState(false);
  const sitesId = useId();
  const tags = (p.tags || []).join(' · ');
  const sites = p.sites || [];
  return (
    <>
      <tr>
        <td>
          <Link className="ixs-pname" to={productPath(p.pid)}>{p.name}</Link>
          <div className="ixs-pid">
            <span>{p.pid}</span>
            {p.is_group && <span className="ix-badge plain st-info" title="A group card that stands for several records">Group</span>}
            <SupplyBadge status={p.status} />
          </div>
          {tags && <div className="ixs-ptags" title={tags}>{tags}</div>}
        </td>
        <td>
          {p.role
            ? <StatusBadge status={ROLE_LABEL[p.role] || p.role} tone={p.role === 'distributor' ? 'info' : 'neutral'} />
            : <span className="ix-muted">—</span>}
        </td>
        <td><Evidence s={p} /></td>
        <td><Integration s={p} /></td>
        <td><Regions s={p} /></td>
        <td className="ixs-nowrap">
          {sites.length > 0 ? (
            <button type="button" className="ix-link ixi-sites-btn" aria-expanded={open} aria-controls={sitesId}
              onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide sites' : 'Where it is made'}
            </button>
          ) : <span className="ix-muted">Not stated</span>}
        </td>
      </tr>
      {open && sites.length > 0 && (
        <tr className="ixi-sites-row" id={sitesId}>
          <td colSpan={6}><Sites sites={sites} /></td>
        </tr>
      )}
    </>
  );
}

export default function SupplierProductsTab({ supplier: s, family, onFamilyChange }) {
  const products = useMemo(() => s.products || [], [s.products]);
  const [q, setQ] = useState('');
  const [integratedOnly, setIntegratedOnly] = useState(false);
  const [role, setRole] = useState('');

  const familyCounts = useMemo(() => {
    const m = new Map();
    products.forEach((p) => {
      const id = String(p.family?.id ?? 'none');
      const cur = m.get(id) || { id, name: p.family?.name || 'Family not given', n: 0 };
      cur.n += 1;
      m.set(id, cur);
    });
    return [...m.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
  }, [products]);
  const roleCounts = useMemo(() => {
    const m = { producer: 0, distributor: 0 };
    products.forEach((p) => { if (p.role in m) m[p.role] += 1; });
    return m;
  }, [products]);
  const integratedCount = products.filter((p) => p.integration?.integrated).length;
  const activeFamily = familyCounts.some((f) => f.id === family) ? family : '';

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return products.filter((p) => {
      if (activeFamily && String(p.family?.id ?? 'none') !== activeFamily) return false;
      if (integratedOnly && !p.integration?.integrated) return false;
      if (role && p.role !== role) return false;
      if (needle) {
        const hay = [p.name, p.pid, p.line?.name, p.raw_name, ...(p.tags || [])]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [products, activeFamily, integratedOnly, role, q]);

  const groups = useMemo(() => {
    const out = [];
    const byKey = new Map();
    filtered.forEach((p) => {
      const key = p.line?.id != null ? `l${p.line.id}` : `none-${p.family?.id ?? ''}`;
      let g = byKey.get(key);
      if (!g) {
        g = { key, line: p.line, family: p.family?.name, items: [] };
        byKey.set(key, g);
        out.push(g);
      }
      g.items.push(p);
    });
    return out;
  }, [filtered]);

  const bothRoles = roleCounts.producer > 0 && roleCounts.distributor > 0;

  return (
    <div className="ix-stack">
      <div className="ixs-prod-toolbar" role="group" aria-label="Filter products">
        <button type="button" className="ix-pill" aria-pressed={!activeFamily} onClick={() => onFamilyChange('')}>
          All families <span className="ix-count">{products.length}</span>
        </button>
        {familyCounts.length > 1 && familyCounts.map((f) => (
          <button key={f.id} type="button" className="ix-pill" aria-pressed={activeFamily === f.id}
            onClick={() => onFamilyChange(activeFamily === f.id ? '' : f.id)}>
            {f.name} <span className="ix-count">{f.n}</span>
          </button>
        ))}
        {bothRoles && ['producer', 'distributor'].map((r) => (
          <button key={r} type="button" className="ix-pill" aria-pressed={role === r}
            onClick={() => setRole(role === r ? '' : r)}>
            {ROLE_LABEL[r]} <span className="ix-count">{roleCounts[r]}</span>
          </button>
        ))}
        {integratedCount > 0 && (
          <button type="button" className="ix-pill" aria-pressed={integratedOnly}
            onClick={() => setIntegratedOnly((v) => !v)}>
            Integrated only <span className="ix-count">{integratedCount}</span>
          </button>
        )}
        <input type="search" className="ix-input" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by name, code or line…" aria-label="Filter products by name, code or line" />
      </div>

      <Panel
        title="Products"
        caption={`${fmt.num(filtered.length)} of ${fmt.plural(products.length, 'product')} · ${fmt.plural(groups.length, 'product line')}`}
        flush
        footer="Counted maker links first, distributors last. Regions are where the product is made."
      >
        {filtered.length === 0 ? (
          <EmptyState variant="bare" title="No products match these filters" />
        ) : (
          <div className="ix-table-wrap">
            <table className="ix-table ixs-ptable">
              <colgroup>
                <col className="c-prod" />
                <col className="c-role" />
                <col className="c-ev" />
                <col className="c-int" />
                <col className="c-reg" />
                <col className="c-sites" />
              </colgroup>
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Role</th>
                  <th>Evidence</th>
                  <th>Integration</th>
                  <th>Makes it in</th>
                  <th>Sites</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <Fragment key={g.key}>
                    <tr className="ixs-group">
                      <td colSpan={6}>
                        <div className="ixs-group-cell">
                          {g.family && <span className="ixs-group-fam">{g.family}</span>}
                          {g.family && <span className="ixs-group-sep" aria-hidden>›</span>}
                          {g.line
                            ? <Link className="ixs-group-line" to={linePath(g.line.id)}>{g.line.name}</Link>
                            : <span className="ixs-group-line">{UNPUBLISHED_LINE}</span>}
                          <span className="ixs-group-count">{fmt.plural(g.items.length, 'product')}</span>
                        </div>
                      </td>
                    </tr>
                    {g.items.map((p) => <ProductRow key={p.pid} p={p} />)}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
