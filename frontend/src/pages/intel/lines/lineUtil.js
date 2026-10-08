/* Helpers for the Product lines pages. Lines are addressed by id. */
export { asOfLabel } from '../../../components/intel/report/reportUtil';
export {
  lineHref, linesHref, productHref, supplierHref, industryHref, strategyHref as playbookHref,
} from '../../../components/intel';

export const LINES_PATH = '/intelligence/lines';

/** "3 products", "1 product". */
export function count(n, one, many = `${one}s`) {
  const v = Number(n) || 0;
  return `${v.toLocaleString('en-GB')} ${v === 1 ? one : many}`;
}

/** Sentence-case a lower-case authored fragment ("an oxidation stage…"). */
export function sentence(text) {
  if (!text) return text;
  const t = String(text).trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/* Sub-family filter value: its id, or 'none' for a line the axis does not
   place on one. */
export const ssfValue = (item) => String(item?.subfamily?.id ?? 'none');
export const famValue = (item) => String(item?.family?.id ?? 'none');
/* Products tracked on a line: its listed cards, whatever their status. */
export const listedOf = (item) => Number(item?.counts?.listed ?? item?.product_count ?? 0);
export const UNPLACED_LABEL = 'Not on a named sub-family';
