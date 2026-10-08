/* Small helpers shared by the report viewer pieces. */

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

/** "2026-08" → "August 2026". Anything unparseable comes back as given. */
export function asOfLabel(asOf) {
  const m = String(asOf || '').match(/^(\d{4})-(\d{1,2})/);
  if (!m) return asOf || '';
  const month = MONTH_NAMES[Number(m[2]) - 1];
  return month ? `${month} ${m[1]}` : asOf;
}

/** Where a product line lives in the app: by id (a key also opens it). */
export function lineHref(lineRef) {
  return `/intelligence/lines/${encodeURIComponent(lineRef)}`;
}

/** DOM id for a report section, namespaced so "overview" / "supply" never
 *  collide with another element on the page. */
export function sectionDomId(slug, sectionId) {
  return `rpt-${slug}-${sectionId}`.replace(/[^A-Za-z0-9_-]/g, '-');
}
