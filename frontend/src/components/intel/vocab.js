/* Words the Intelligence pages share, so each one is written once.
 *
 * - The supply axis reads family › sub-family › product line › product. The
 *   sub-family tier's label is one constant (design §4.1, Laurent question L5).
 * - A product line that is not published is never named by its key or its
 *   old name; it reads UNPUBLISHED_LINE.
 * - Supply status is a badge with no number (house rules 4 and 10b). The API
 *   sends `status{code,label,tone}`; the labels here are only a fallback. */

export const SUBFAMILY_LABEL = 'Sub-family';
export const SUBFAMILY_LABEL_PLURAL = 'Sub-families';
export const UNPUBLISHED_LINE = 'Product line not yet published';

export const STATUS_CODES = ['live', 'supply_exception', 'supply_pending', 'not_audited'];
/* The grid opens on these two: verified makers, plus concentrated supply with
 * a documented structural exception. */
export const DEFAULT_STATUSES = ['live', 'supply_exception'];
export const STATUS_LABELS = {
  live: 'Verified makers',
  supply_exception: 'Concentrated supply',
  supply_pending: 'Supply pending',
  not_audited: 'Makers not yet verified',
};
export const STATUS_TONES = {
  live: 'green',
  supply_exception: 'green-amber',
  supply_pending: 'amber',
  not_audited: 'grey',
};

const isDefault = (codes) => codes.length === DEFAULT_STATUSES.length
  && DEFAULT_STATUSES.every((c) => codes.includes(c));

/* `?status=` in a page URL: absent = the default view, `all` = every status,
 * else a comma list. Unknown codes are dropped (the API would answer 422). */
export function parseStatusParam(raw) {
  if (raw == null || raw === '') return [...DEFAULT_STATUSES];
  if (String(raw).trim().toLowerCase() === 'all') return [...STATUS_CODES];
  const picked = String(raw).split(',').map((s) => s.trim().toLowerCase());
  const codes = STATUS_CODES.filter((c) => picked.includes(c));
  return codes.length ? codes : [...DEFAULT_STATUSES];
}

/* The URL value for a set of codes: null for the default (keeps URLs short),
 * `all` for every status, else the codes in rank order. */
export function statusParamOf(codes) {
  const list = STATUS_CODES.filter((c) => (codes || []).includes(c));
  if (!list.length || isDefault(list)) return null;
  if (list.length === STATUS_CODES.length) return 'all';
  return list.join(',');
}

/* The `status` query value the API gets: always explicit, so the sidebar
 * (/facets) and the grid (/products) count the same cards. */
export function statusQuery(codes) {
  const list = STATUS_CODES.filter((c) => (codes || []).includes(c));
  if (!list.length || list.length === STATUS_CODES.length) return 'all';
  return list.join(',');
}

export const isDefaultStatus = (codes) => isDefault(STATUS_CODES.filter((c) => (codes || []).includes(c)));
export const isAllStatuses = (codes) => STATUS_CODES.every((c) => (codes || []).includes(c));

/* Line flags come as codes on the product payload (`['pending']`) and as
 * `{code, label}` on the line payloads. Only fixed labels are shown; a
 * `do_not_publish` line never reaches the page (the API treats it as not
 * published). */
const LINE_FLAG_LABELS = {
  pending: 'Supplier validation pending',
  validation_pending: 'Supplier validation pending',
};
export function lineFlags(flags) {
  const out = [];
  const seen = new Set();
  (flags || []).forEach((f) => {
    const code = typeof f === 'string' ? f : f?.code;
    const label = LINE_FLAG_LABELS[code];
    if (!label || seen.has(label)) return;
    seen.add(label);
    out.push({ code, label });
  });
  return out;
}

/* Where things live in the app. Lines are addressed by id; the line page
 * also accepts a current or former key, so older links keep working. */
export const productHref = (pid) => `/intelligence/products/${encodeURIComponent(pid)}`;
export const lineHref = (ref) => `/intelligence/lines/${encodeURIComponent(ref)}`;
export const supplierHref = (id) => `/intelligence/suppliers/${encodeURIComponent(id)}`;
export const industryHref = (slug) => `/intelligence/categories/${encodeURIComponent(slug)}`;
export const strategyHref = (slug) => `/strategy/${encodeURIComponent(slug)}`;

/* `/intelligence/products?family_id=…` and friends. */
export function productsHref(params = {}) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null && v !== '') q.set(k, String(v)); });
  const s = q.toString();
  return `/intelligence/products${s ? `?${s}` : ''}`;
}

/* `/intelligence/lines?family_id=…&subfamily_id=…` */
export function linesHref({ familyId, subfamilyId } = {}) {
  const q = new URLSearchParams();
  if (familyId != null) q.append('family_id', String(familyId));
  if (subfamilyId != null) q.append('subfamily_id', String(subfamilyId));
  const s = q.toString();
  return `/intelligence/lines${s ? `?${s}` : ''}`;
}
