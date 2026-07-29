/* Functions the advanced-formula evaluator (`safe_eval_expr`) supports natively.
 * Kept in sync with backend `_SAFE_FUNCS` in costing_engine.py. These names are
 * NOT variables — variable detection must exclude them so the user isn't asked
 * to map `min`/`max`/etc. */
export const RESERVED_FN_NAMES = new Set(['min', 'max', 'abs', 'round', 'clamp', 'step']);

/** Strip reserved function names from a detected-token list. */
export const stripReservedFns = (tokens) => tokens.filter((t) => !RESERVED_FN_NAMES.has(t));
