/* Vocabulary for the demand axis (Intelligence › Categories).
 *
 * A category's status is authored in the ratified tree, not computed from its
 * members (CATEGORY_TREE_SPEC): never derive it from product counts. */

export const STATUSES = ['servable', 'partial', 'build'];

export const STATUS_LABEL = {
  servable: 'Servable',
  partial: 'Partial',
  build: 'Build',
};

// One line each, used for the legend and as badge tooltips.
export const STATUS_DEF = {
  servable: 'The catalogue holds the right grade of everything this buyer buys for the function.',
  partial: 'The catalogue holds some of it; the rest is listed under "To build".',
  build: 'Nothing is held in the right grade yet; the products to author are listed.',
};

// `out` rows: the API sends `reason{code, label}`; these one-line meanings
// are the badge tooltips (and the label when the API sends none).
export const OUT_CODES = {
  OUT_WRONG_BUYER: { label: 'Wrong buyer', def: 'Another defined buyer procures it directly.' },
  OUT_WRONG_GRADE: { label: 'Wrong grade', def: 'The function is valid but this product or grade cannot serve it.' },
  OUT_UPSTREAM: { label: 'Upstream input', def: 'The buyer purchases the downstream material instead.' },
  OUT_BUYER_OUTPUT: { label: "Buyer's own output", def: 'This reference buyer produces it rather than purchases it.' },
  OUT_SCOPE: { label: 'Out of scope', def: 'Outside the written reference buyer.' },
  PENDING_EVIDENCE: { label: 'Pending evidence', def: 'A plausible buy; the evidence is missing. A question, not a ruling.' },
  PENDING_BOUNDARY: { label: 'Boundary under review', def: 'Which buyer owns it is not resolved yet. A question, not a ruling.' },
  BUILD: { label: 'To be built', def: 'Demand accepted; the correct article does not exist in the catalogue yet.' },
};

export function outCode(code) {
  return OUT_CODES[code] || { label: 'Not bought here', def: '' };
}

export { lineHref, productHref, industryHref } from '../../../components/intel';

/* Authored member rows can name the same line twice (with different products);
 * show one line with the union of its products, in first-seen order. Rows on
 * a line that is not published (no id) merge per family, never across. */
export function mergeLines(rows = []) {
  const byKey = new Map();
  rows.forEach((r) => {
    const key = r.line_id != null ? `id:${r.line_id}` : `unpublished:${r.family || ''}`;
    let line = byKey.get(key);
    if (!line) {
      line = { ...r, products: [] };
      byKey.set(key, line);
    }
    if (r.is_whole_line) line.is_whole_line = true;
    (r.products || []).forEach((p) => {
      if (!line.products.some((q) => q.pid === p.pid)) line.products.push(p);
    });
  });
  return Array.from(byKey.values());
}

/* name → slug for the 50 industries, so free-text references (goes_to, with,
 * to_industry) link only when they are an exact industry name. */
export function industryIndex(items = []) {
  const m = new Map();
  items.forEach((it) => m.set(String(it.name).toLowerCase(), it.slug));
  return m;
}
