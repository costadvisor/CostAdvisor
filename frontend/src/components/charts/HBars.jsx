/* Horizontal bars with labels and values — products by family or industry, spend by
 * product/supplier/site, any ranked share.
 *
 *   <HBars items={families.map(f => ({ label: f.name, value: f.count }))}
 *          formatValue={(v) => fmt.num(v)} />
 *   <HBars items={byProduct.map(p => ({ label: p.name, sub: p.pid, value: p.spend }))}
 *          formatValue={(v) => fmt.money(v, 'EUR')} color="var(--accent)" labelWidth={180} />
 *
 * items: [{ key?, label, value (number|null), sub?, color?, display? }]
 * `display` replaces the value text (e.g. "not disclosed"); a null value draws
 * an empty track. max defaults to the largest value.
 *
 * onItemClick: each row gets a real <button> (inside its list item, so the
 * list keeps its listitem semantics and the button keeps its role) styled
 * by `button.ix-hbar-row` in styles/intel.css — same size and layout as a
 * plain row, plus hover and focus states. */
export default function HBars({
  items = [], max, formatValue = (v) => String(v), color = 'var(--accent4)', labelWidth = 150,
  onItemClick, emptyText = 'No data.', ariaLabel,
}) {
  const vals = items.map((i) => Number(i.value)).filter((v) => Number.isFinite(v));
  if (!items.length) return <div className="ix-chart-note">{emptyText}</div>;
  const top = max ?? Math.max(0, ...vals);

  return (
    <div className={`ix-hbars${onItemClick ? ' is-clickable' : ''}`} style={{ '--ix-hbar-label': `${labelWidth}px` }}
      role="list" aria-label={ariaLabel}>
      {items.map((it, idx) => {
        const v = Number(it.value);
        const has = it.value != null && Number.isFinite(v);
        const w = has && top > 0 ? Math.max(0, Math.min(100, (v / top) * 100)) : 0;
        const text = it.display ?? (has ? formatValue(v) : '—');
        const key = it.key ?? `${it.label}-${idx}`;
        const cells = (
          <>
            <span className="ix-hbar-label">
              {it.label}
              {it.sub && <span className="sub">{it.sub}</span>}
            </span>
            <span className="ix-hbar-track" aria-hidden>
              <span className="ix-hbar-fill" style={{ width: `${w}%`, background: it.color || color, display: 'block' }} />
            </span>
            <span className={`ix-hbar-val${has ? '' : ' muted'}`}>{text}</span>
          </>
        );
        if (!onItemClick) {
          return (
            <div key={key} role="listitem" className="ix-hbar-row" title={`${it.label}: ${text}`}>{cells}</div>
          );
        }
        return (
          <div key={key} role="listitem" className="ix-hbar-item">
            <button type="button" className="ix-hbar-row" onClick={() => onItemClick(it)}
              title={`${it.label}: ${text}`}>
              {cells}
            </button>
          </div>
        );
      })}
    </div>
  );
}
