import { useMemo } from 'react';
import { Panel, fmt } from '../../../components/intel';
import { StackedBars } from '../../../components/charts';

/* Tab 1: the recipe's index components (table) and the monthly cost build-up
 * (stacked bars). Both read the market payload for the viewing region. */

const dirClass = (v) => {
  if (v == null || !Number.isFinite(Number(v))) return 'ix-flat';
  const n = Math.round(Number(v) * 10) / 10;
  if (n > 0) return 'ix-up';
  if (n < 0) return 'ix-down';
  return 'ixd-zero';
};

function lineSub(c) {
  if (c.is_margin) return { text: 'Margin, inside the 100 — held flat', flat: true };
  if (!c.indexed) return { text: 'No public index — held flat at its weight', flat: true };
  const series = c.series_name || c.series_key;
  return { text: series ? `${series}${c.series_key && c.series_name ? ` (${c.series_key})` : ''}` : c.source_label, flat: false };
}

function agencyCell(c) {
  if (c.is_margin) return { main: 'Margin', sub: null };
  if (!c.indexed) return { main: 'Fixed', sub: null };
  return { main: c.agency || '—', sub: c.freq || null };
}

export function ComponentsTable({ data }) {
  const comps = data.components || [];
  const region = data.region;
  const agencies = useMemo(() => {
    const seen = [];
    comps.forEach((c) => {
      if (c.indexed && c.agency && !/^unverified/i.test(c.agency) && !seen.includes(c.agency)) seen.push(c.agency);
    });
    return seen;
  }, [comps]);
  if (!comps.length) return null;

  const indexLatest = data.snapshot?.index_latest;
  // The rows' weighted impacts add up to index − 100 (in index points). The
  // total is that exact sum; each row is rounded to 0.1 on its own, so the
  // displayed rows can differ from it by a tenth — hence "unrounded".
  const impactSum = indexLatest != null
    ? indexLatest - 100
    : comps.reduce((s, c) => s + (Number(c.weighted_impact_pct) || 0), 0);
  const caption = agencies.length
    ? `${region} feeds · ${agencies.slice(0, 3).join(' / ')}${agencies.length > 3 ? ` +${agencies.length - 3}` : ''}`
    : `${region} recipe`;

  return (
    <Panel title={`Index components — ${region}${data.variant ? ` · ${data.variant}` : ''}`} caption={caption} flush
      footer={data.data_gaps?.length ? (
        <>Data gaps: {data.data_gaps.map((g) => `${g.line} (${g.reason})`).join('; ')}.</>
      ) : null}>
      <div className="ix-table-wrap">
        <table className="ix-table ixd-comp">
          <thead>
            <tr>
              <th scope="col">Cost line</th>
              <th scope="col">Category</th>
              <th scope="col">Source</th>
              <th scope="col">Weight</th>
              <th scope="col" className="num">Current level</th>
              <th scope="col" className="num">vs Jan 2023</th>
              <th scope="col" className="num" title="Weight × (level − 100), in index points. The rows add up to the index minus 100.">
                Weighted impact (pts)
              </th>
            </tr>
          </thead>
          <tbody>
            {comps.map((c, i) => {
              const sub = lineSub(c);
              const ag = agencyCell(c);
              return (
                <tr key={`${c.label}-${i}`} title={c.source_label || undefined}>
                  <td>
                    <div className="ixd-line">{c.label}</div>
                    <div className={`ixd-line-sub${sub.flat ? ' is-flat' : ''}`}>{sub.text}</div>
                  </td>
                  <td><span className="ixd-cat">{c.cost_category || '—'}</span></td>
                  <td>
                    <div className="ixd-agency">{ag.main}</div>
                    {ag.sub && <div className="ixd-agency-sub">{ag.sub}</div>}
                  </td>
                  <td>
                    <span className="ix-wbar">
                      <span className="ix-wbar-track ixd-wbar-track">
                        <span className="ix-wbar-fill" style={{ width: `${Math.max(0, Math.min(100, Number(c.weight_pct) || 0))}%` }} />
                      </span>
                      <span className="ix-muted ix-mono">{fmt.share(c.weight_pct)}</span>
                    </span>
                  </td>
                  <td className="num ixd-level">{fmt.index(c.current_level)}</td>
                  <td className={`num ${dirClass(c.vs_base_pct)}`}>{fmt.pct(c.vs_base_pct)}</td>
                  <td className={`num ${dirClass(c.weighted_impact_pct)}`}>{fmt.signed(c.weighted_impact_pct)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>Total (incl. supplier margin)</td>
              <td className="ixd-total-w ix-mono">{fmt.share(data.total_weight_pct)}</td>
              <td className="num">{fmt.index(indexLatest)}</td>
              <td className={`num ${dirClass(indexLatest == null ? null : indexLatest - 100)}`}>
                {indexLatest == null ? '—' : fmt.pct(indexLatest - 100)}
              </td>
              <td className={`num ${dirClass(impactSum)}`}
                title="Sum of the unrounded row values: the latest index minus 100. The rounded rows can differ from it by 0.1.">
                {fmt.signed(impactSum)}
                <div className="ixd-total-note">unrounded</div>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Panel>
  );
}

/* Indexed lines take the categorical palette; lines with no public index and
 * the margin ride flat, so they are drawn in greys (the mockup's convention). */
const INDEXED_COLORS = [
  'var(--pie-2)', 'var(--pie-1)', 'var(--pie-3)', 'var(--pie-5)', 'var(--pie-4)',
  'var(--pie-6)', 'var(--pie-8)', 'var(--pie-9)', 'var(--pie-7)',
];
const FLAT_COLORS = { no_index: 'var(--ixd-flat-noindex)', margin: 'var(--ixd-flat-margin)' };

export function CostBuildUp({ data }) {
  const stack = data.stack || [];
  const lines = data.stack_lines || [];
  const actual = useMemo(() => stack.filter((s) => s.kind !== 'forecast'), [stack]);
  const categories = useMemo(() => {
    const keys = stack.length ? Object.keys(stack[0].by_line || {}) : [];
    let n = 0;
    return keys.map((key, i) => {
      const role = lines[i]?.role || (lines[i]?.indexed === false ? 'no_index' : 'indexed');
      const color = FLAT_COLORS[role] || INDEXED_COLORS[(n++) % INDEXED_COLORS.length];
      return { key, label: role === 'no_index' ? `${key} (no index)` : key, color };
    });
  }, [stack, lines]);
  const rows = useMemo(() => actual.map((s) => ({ label: fmt.monthShort(s.period), by_line: s.by_line })), [actual]);
  if (!rows.length) return null;

  return (
    <Panel
      title={`How the should-cost index builds up — ${rows.length}-month history`}
      caption={data.region}
      footer="Grey = cost lines with no public index (held flat at their weight) and the supplier margin. A band that grows means that line's own price is rising, not that its weight changed."
    >
      <div className="ixd-sub">
        Each bar is one month: every cost line's weight × its own index level. The bar top is the
        {' '}{data.region} index in the chart above (base 100 = Jan 2023).
      </div>
      <StackedBars
        data={rows}
        categories={categories}
        height={230}
        formatValue={(v) => v.toFixed(1)}
        totalLabel="Index"
        ariaLabel={`Monthly cost build-up of the ${data.region} should-cost index`}
      />
    </Panel>
  );
}
