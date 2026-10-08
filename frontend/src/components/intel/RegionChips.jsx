import { REGION_CODES, REGION_LABELS, normalizeRegion, regionColor, sortRegions } from './palette';

export { REGION_CODES, REGION_COLORS, REGION_VARS, REGION_LABELS, regionColor, normalizeRegion } from './palette';

/* Region chips in the mockup's colours.
 *
 *   <RegionChips regions={['EU','CN','NA']} />                       // all on, static
 *   <RegionChips regions={REGION_CODES} available={p.regions} size="sm" />   // card strip
 *   <RegionChips label="Viewing region" regions={p.regions} active={region} onSelect={setRegion} />
 *
 * regions:   codes to show (sorted into the house order unless ordered=false)
 * active:    code or array of codes shown filled; omit → every available chip on
 * available: codes that have data; the rest render greyed and disabled
 * onSelect:  makes the chips toggle buttons (aria-pressed); called with the code */
export default function RegionChips({
  regions = REGION_CODES, active, available, onSelect, size, label, ordered = true, ariaLabel,
}) {
  const list = ordered ? sortRegions(regions) : regions;
  const avail = available ? new Set(available.map(normalizeRegion)) : null;
  const act = active == null ? null : new Set((Array.isArray(active) ? active : [active]).map(normalizeRegion));

  return (
    <div className="ix-region-chips" role={onSelect ? 'group' : undefined}
      aria-label={ariaLabel || label || (onSelect ? 'Regions' : undefined)}>
      {label && <span className="ix-region-chips-label">{label}</span>}
      {list.map((raw) => {
        const code = normalizeRegion(raw);
        const isAvail = !avail || avail.has(code);
        const isOn = isAvail && (act ? act.has(code) : true);
        const cls = `ix-rchip${size === 'sm' ? ' sm' : ''}${isOn ? '' : ' off'}${isAvail ? '' : ' unavailable'}`;
        const style = { '--rg': regionColor(code) };
        const title = `${REGION_LABELS[code] || code}${isAvail ? '' : ' — no data'}`;
        if (onSelect) {
          return (
            <button key={code} type="button" className={cls} style={style} title={title}
              aria-pressed={isOn} disabled={!isAvail} onClick={() => onSelect(code)}>
              {code}
            </button>
          );
        }
        return <span key={code} className={cls} style={style} title={title}>{code}</span>;
      })}
    </div>
  );
}

/* A small coloured dot for a region (legends, table cells). */
export function RegionDot({ region, title }) {
  const code = normalizeRegion(region);
  return <span className="ix-rdot" style={{ '--rg': regionColor(code) }} title={title || REGION_LABELS[code] || code} />;
}
