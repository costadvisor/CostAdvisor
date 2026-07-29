import { Fragment } from 'react';
import { stripReservedFns } from '../utils/formulaFns';

/* Reusable advanced-expression + variable-map editor. Same shape and behaviour as
 * the advanced-formula editors in CostModelBuilder/Formulas: an expression textarea,
 * a "Detect variables" action, and per-variable rows binding each name to either a
 * commodity index or a fixed value. Var map shape:
 *   { "Graphite": {type:"index", commodity_id:N}, "FC": {type:"fixed", value:X} } */
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
      unique.forEach(n => { next[n] = prev[n] || { type: 'fixed', value: 0 }; });
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
            const def = vars[name] || { type: 'fixed', value: 0 };
            return (
              <Fragment key={name}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 600 }}>{name}</div>
                <select className="ca-select" value={def.type} onChange={e => updateVar(name, 'type', e.target.value)}>
                  <option value="fixed">fixed</option>
                  <option value="index">index</option>
                </select>
                {def.type === 'index' ? (
                  <select className="ca-select" value={def.commodity_id || ''}
                    onChange={e => updateVar(name, 'commodity_id', e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Select index…</option>
                    {commodities.map(ci => <option key={ci.id} value={ci.id}>{ci.name}</option>)}
                  </select>
                ) : (
                  <input className="ca-input" type="number" value={def.value ?? 0}
                    onChange={e => updateVar(name, 'value', Number(e.target.value))} />
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
