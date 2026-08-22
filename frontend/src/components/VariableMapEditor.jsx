import { Fragment } from 'react';
import { stripReservedFns } from '../utils/formulaFns';
import NumberInput from './NumberInput';
import IndexCombo from './IndexCombo';

/* Reusable advanced-expression + variable-map editor. Same shape and behaviour as
 * the advanced-formula editors in CostModelBuilder/Formulas: an expression textarea,
 * a "Detect variables" action, and per-variable rows binding each name to either a
 * commodity index or a fixed value. Var map shape:
 *   { "Graphite": {type:"index", commodity_id:N}, "FC": {type:"fixed", value:X} } */
/**
 * Blank fixed values mean zero. New variables start EMPTY rather than pre-filled
 * with `0` — a box showing "0" is what made users type in front of it and get
 * "052" — so the zero is applied here, at the point of submission, instead of
 * being shown as a value the user never chose. The backend requires a real
 * number (`isinstance(value, (int, float))`), so this must run before any POST/PUT
 * carrying a variable map.
 */
export function normalizeVarMap(vars) {
  const out = {};
  Object.entries(vars || {}).forEach(([name, def]) => {
    if (def?.type === 'fixed') {
      const v = def.value;
      // Drop any region left behind by switching a variable from index to fixed.
      const { region, ...rest } = def;
      out[name] = { ...rest, value: v == null || v === '' ? 0 : Number(v) };
    } else if (def?.type === 'index') {
      // Omit `region` entirely when unpinned — the backend treats absent/None as
      // "follow the requested region", and sending null says the same thing less
      // clearly. Everything else on the spec is passed through untouched.
      const { region, ...rest } = def;
      out[name] = region ? { ...rest, region } : rest;
    } else {
      out[name] = def;
    }
  });
  return out;
}

export default function VariableMapEditor({
  expression, setExpression, vars, setVars, commodities = [],
  exprLabel = 'Expression', exprPlaceholder = 'e.g. 0.6*Graphite + 0.3*Wood + FC',
}) {
  const detectVars = () => {
    const expr = (expression || '').replace(/[[\]]/g, '').replace(/\s/g, '');
    const tokens = expr.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
    const unique = stripReservedFns([...new Set(tokens)]);
    setVars(prev => {
      const next = {};
      // value: null (not 0) so the box renders empty with a "0" placeholder.
      unique.forEach(n => { next[n] = prev[n] || { type: 'fixed', value: null }; });
      return next;
    });
  };
  const updateVar = (name, key, val) => setVars(prev => ({ ...prev, [name]: { ...prev[name], [key]: val } }));
  const removeVar = (name) => setVars(prev => { const n = { ...prev }; delete n[name]; return n; });

  const names = Object.keys(vars || {});

  return (
    <div>
      <label className="ca-label">{exprLabel}</label>
      <textarea
        className="ca-input"
        style={{ width: '100%', minHeight: 64, resize: 'vertical', fontFamily: "'JetBrains Mono', monospace" }}
        value={expression}
        onChange={e => setExpression(e.target.value)}
        placeholder={exprPlaceholder}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '6px 0' }}>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>Map each variable to an index or a fixed value.</span>
        <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={detectVars}>Detect variables</button>
      </div>
      {names.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 84px 1.4fr 24px', gap: 8, alignItems: 'center' }}>
          {names.map(name => {
            const def = vars[name] || { type: 'fixed', value: null };
            return (
              <Fragment key={name}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 600 }}>{name}</div>
                <select className="ca-select" value={def.type} onChange={e => updateVar(name, 'type', e.target.value)}>
                  <option value="fixed">fixed</option>
                  <option value="index">index</option>
                </select>
                {def.type === 'index' ? (
                  /* byRegion — a composite variable CAN store a region, so
                     "Iron · Europe" and "Iron · GLOBAL" are picked separately. */
                  <IndexCombo
                    byRegion
                    value={def.commodity_id ?? null}
                    region={def.region ?? null}
                    commodities={commodities}
                    onChange={(id, reg) => setVars(prev => ({
                      ...prev,
                      [name]: { ...prev[name], commodity_id: id, region: reg },
                    }))}
                  />
                ) : (
                  <NumberInput value={def.value}
                    onChange={v => updateVar(name, 'value', v)} />
                )}
                <button type="button" className="ca-btn-link" title="Remove"
                  style={{ color: 'var(--accent2)' }} onClick={() => removeVar(name)}>×</button>
              </Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
}
