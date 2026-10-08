import { useState, useEffect, useMemo, useRef } from 'react';
import Modal from './Modal';
import api, { formatApiError } from '../api';

/* Shared add/edit product form, extracted from Products.jsx.
 *
 * It used to be an inline `.ca-card` living only inside Products.jsx, so getting
 * to it from anywhere else meant navigating to /products first. That's the two-page
 * detour Portfolio's "+ Add product" button forced: leave Portfolio, land on an
 * unrelated list page, click again to reveal the form. Modalizing it — on the same
 * `Modal` primitive EditCellModal/FxCustomEditModal already use — lets both pages
 * render the same form in place, with no route change.
 *
 * Self-contained: owns its own field state and resets/prefills from `editing` on
 * open, so callers don't have to manage the fields themselves.
 *
 * Where a product sits in the supply taxonomy (family › sub-family › product
 * line) is not typed in here. A catalogue product brings its own line. Only a
 * custom product (no catalogue product, or a team formula with no line) picks a
 * line, from GET /api/taxonomy/lines, and the form stores the line's id. There
 * is no family or sub-family dropdown: both follow from the line.
 */

const UNPUBLISHED_LINE = 'Product line not yet published';
const MAX_RESULTS = 50;
const LINE_SEARCH_DELAY_MS = 200;

const mono = { fontFamily: "'JetBrains Mono', monospace" };

// "Family › Sub-family" for a line or template; an unnamed sub-family is skipped.
const familyPath = (family, subfamily) =>
  [family?.name, subfamily?.name].filter(Boolean).join(' › ');

const templateLabel = (t) => `${t.name}${t.code ? ` (${t.code})` : ''}${t.team_id ? ' · team' : ''}`;

function PickedValue({ title, detail, onClear, clearLabel }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px',
      border: '1px solid var(--border)', borderRadius: 6, background: 'var(--surface2)',
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {title}
        </div>
        {detail && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 1 }}>{detail}</div>}
      </div>
      <button type="button" className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClear} aria-label={clearLabel}>
        Change
      </button>
    </div>
  );
}

/* A search box with its result list inline below it. Inline (not a floating
   popover) because the modal body scrolls: a popover would be clipped. */
function SearchList({ id, label, placeholder, query, onQuery, items, onPick, renderItem, footer }) {
  const [active, setActive] = useState(0);
  useEffect(() => { setActive(0); }, [items]);

  const onKeyDown = (e) => {
    if (!items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, items.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); onPick(items[active]); }
  };

  return (
    <div>
      <input
        id={id}
        className="ca-input"
        type="search"
        role="combobox"
        aria-expanded={items.length > 0}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        value={query}
        placeholder={placeholder}
        onChange={e => onQuery(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {(items.length > 0 || footer) && (
        <div style={{
          marginTop: 4, border: '1px solid var(--border)', borderRadius: 6,
          maxHeight: 220, overflowY: 'auto', background: 'var(--surface)',
        }}>
          <div id={`${id}-list`} role="listbox" aria-label={label}>
            {items.map((it, i) => (
              <button
                type="button"
                key={it.id}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => onPick(it)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left', padding: '6px 10px',
                  border: 'none', borderBottom: '1px solid var(--border)', cursor: 'pointer',
                  background: i === active ? 'var(--surface2)' : 'transparent', color: 'var(--text)',
                  font: 'inherit',
                }}
              >
                {renderItem(it)}
              </button>
            ))}
          </div>
          {footer && (
            <div style={{ padding: '6px 10px', fontSize: 10, color: 'var(--muted)' }}>{footer}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ProductFormModal({
  isOpen,
  onClose,
  onSaved,          // (product) => void — called after a successful create/update
  activeTeamId,
  templates = [],   // GET /api/formulas/: listed catalogue cards + the team's own formulas
  editing = null,   // existing product to edit, or null to create
}) {
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('');
  const [activeContent, setActiveContent] = useState(1.0);
  const [unit, setUnit] = useState('kg');
  const [formulaTemplateId, setFormulaTemplateId] = useState('');
  const [templateQuery, setTemplateQuery] = useState('');
  // The custom product's line: { id, name, family, subfamily } or null.
  const [line, setLine] = useState(null);
  const [lineQuery, setLineQuery] = useState('');
  const [lineResults, setLineResults] = useState({ items: [], total: 0 });
  const [lineError, setLineError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const lineSeq = useRef(0);

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setTemplateQuery('');
    setLineQuery('');
    setLineResults({ items: [], total: 0 });
    setLineError(null);
    if (editing) {
      setName(editing.name);
      setFormula(editing.formula || '');
      setActiveContent(editing.active_content ?? 1.0);
      setUnit(editing.unit || 'kg');
      setFormulaTemplateId(editing.formula_template_id ?? '');
      // `product_line_id` is the stored manual line (custom products only);
      // `product_line` is the resolved one, which names it when it is manual.
      const manualId = editing.product_line_id ?? null;
      setLine(manualId == null ? null : {
        id: manualId,
        name: editing.product_line?.id === manualId ? editing.product_line.name : null,
        family: editing.family || null,
        subfamily: editing.subfamily || null,
      });
    } else {
      setName(''); setFormula(''); setActiveContent(1.0); setUnit('kg');
      setFormulaTemplateId(''); setLine(null);
    }
  }, [isOpen, editing]);

  const templateById = useMemo(
    () => Object.fromEntries(templates.map(t => [t.id, t])), [templates]);
  const selectedTemplate = formulaTemplateId ? templateById[formulaTemplateId] || null : null;
  // An edited product can stay linked to a card the list no longer shows (for
  // example one the catalogue merged away); its own payload still names it.
  const linkedFromEditing = !selectedTemplate && !!editing
    && String(editing.formula_template_id ?? '') === String(formulaTemplateId) && !!formulaTemplateId;

  // A catalogue product brings its own line, so the picker is for custom
  // products: no catalogue product, or a team formula that sits on no line.
  const linkedToCatalogue = !!formulaTemplateId;
  const templateGivesLine = !!selectedTemplate?.product_line;
  const isCustom = !linkedToCatalogue || (!!selectedTemplate?.team_id && !templateGivesLine);

  const templateMatches = useMemo(() => {
    const q = templateQuery.trim().toLowerCase();
    if (!q) return [];
    const hits = [];
    for (const t of templates) {
      const hay = `${t.name} ${t.code || ''} ${t.family?.name || ''} ${t.product_line?.name || ''}`.toLowerCase();
      if (hay.includes(q)) hits.push(t);
    }
    // Exact code or name-prefix hits first, then by name.
    const rank = (t) => ((t.code || '').toLowerCase() === q ? 0 : t.name.toLowerCase().startsWith(q) ? 1 : 2);
    hits.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
    return hits;
  }, [templates, templateQuery]);

  // Line search runs on the server (every line, former names included).
  useEffect(() => {
    if (!isOpen || !isCustom || line) return undefined;
    const q = lineQuery.trim();
    if (!q) { setLineResults({ items: [], total: 0 }); setLineError(null); return undefined; }
    const seq = ++lineSeq.current;
    const timer = setTimeout(() => {
      api.get('/api/taxonomy/lines', { params: { q, limit: MAX_RESULTS } })
        .then(({ data }) => {
          if (seq !== lineSeq.current) return;
          setLineResults({ items: data.items || [], total: data.total ?? 0 });
          setLineError(null);
        })
        .catch(err => { if (seq === lineSeq.current) setLineError(formatApiError(err)); });
    }, LINE_SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [isOpen, isCustom, line, lineQuery]);

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    const body = {
      name: name.trim(),
      formula: formula.trim() || null,
      active_content: activeContent,
      unit,
      formula_template_id: formulaTemplateId || null,
      // Sent every time, so linking a catalogue product unlinks a stale
      // manual line (an explicit null unlinks on update).
      product_line_id: isCustom && line ? line.id : null,
    };
    try {
      const res = editing
        ? await api.put(`/api/products/${editing.id}`, body)
        : await api.post(`/api/products?team_id=${activeTeamId}`, body);
      onSaved(res.data);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const shownTemplates = useMemo(() => templateMatches.slice(0, MAX_RESULTS), [templateMatches]);
  const templateFooter = !templateQuery.trim() ? null
    : templateMatches.length === 0 ? 'No catalogue product matches.'
      : templateMatches.length > MAX_RESULTS
        ? `First ${MAX_RESULTS} of ${templateMatches.length} matches. Type more to narrow.`
        : null;

  const lineFooter = lineError
    ? lineError
    : !lineQuery.trim() ? null
      : lineResults.items.length === 0 ? 'No product line matches.'
        : lineResults.total > lineResults.items.length
          ? `First ${lineResults.items.length} of ${lineResults.total} matches. Type more to narrow.`
          : null;

  // What the linked catalogue product says about itself.
  let linkedTitle = 'Linked catalogue product';
  let linkedDetail = null;
  let linkedLine = UNPUBLISHED_LINE;
  if (selectedTemplate) {
    linkedTitle = templateLabel(selectedTemplate);
    linkedLine = selectedTemplate.product_line?.name || UNPUBLISHED_LINE;
    linkedDetail = [familyPath(selectedTemplate.family, selectedTemplate.subfamily),
      selectedTemplate.team_id && !selectedTemplate.product_line ? null : linkedLine]
      .filter(Boolean).join(' › ');
  } else if (linkedFromEditing) {
    linkedTitle = `${editing.formula_template_name || 'Linked catalogue product'}`
      + (editing.formula_template_code ? ` (${editing.formula_template_code})` : '');
    linkedLine = editing.product_line?.name || UNPUBLISHED_LINE;
    linkedDetail = [familyPath(editing.family, editing.subfamily), linkedLine].filter(Boolean).join(' › ');
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={editing ? 'Edit Product' : 'New Product'} width={560}>
      <div className="ca-modal-body">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label" htmlFor="pf-name">Name *</label>
            <input id="pf-name" className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="Product name" />
          </div>
          <div>
            <label className="ca-label" htmlFor="pf-formula">Chemical Formula</label>
            <input id="pf-formula" className="ca-input" value={formula} onChange={e => setFormula(e.target.value)} placeholder="e.g. NaOH" />
          </div>
          <div>
            <label className="ca-label" htmlFor="pf-unit">Unit</label>
            <select id="pf-unit" className="ca-select" value={unit} onChange={e => setUnit(e.target.value)}>
              {['kg', 't', 'lb'].map(u => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
          <div>
            <label className="ca-label" htmlFor="pf-active">Active Content (0-1)</label>
            <input id="pf-active" className="ca-input" type="number" value={activeContent} min={0} max={1} step={0.01}
              onChange={e => setActiveContent(+e.target.value)} />
          </div>

          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label" htmlFor="pf-template">Catalogue product</label>
            {linkedToCatalogue ? (
              <PickedValue
                title={linkedTitle}
                detail={linkedDetail}
                clearLabel="Change the catalogue product"
                onClear={() => { setFormulaTemplateId(''); setTemplateQuery(''); }}
              />
            ) : (
              <SearchList
                id="pf-template"
                label="Catalogue products"
                placeholder="Search by name or code (leave empty for a custom product)"
                query={templateQuery}
                onQuery={setTemplateQuery}
                items={shownTemplates}
                onPick={(t) => { setFormulaTemplateId(t.id); setTemplateQuery(''); }}
                footer={templateFooter}
                renderItem={(t) => (
                  <>
                    <div style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'baseline' }}>
                      <span style={{ fontWeight: 600 }}>{t.name}</span>
                      {t.code && <span style={{ ...mono, fontSize: 10, color: 'var(--text-secondary)' }}>{t.code}</span>}
                      {t.team_id && <span style={{ fontSize: 10, color: 'var(--muted)' }}>team</span>}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                      {[t.family?.name, t.product_line?.name].filter(Boolean).join(' › ') || '—'}
                    </div>
                  </>
                )}
              />
            )}
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              New cost models for this product auto-load the linked recipe at their region.
            </div>
          </div>

          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label" htmlFor="pf-line">Product line</label>
            {!isCustom ? (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '4px 0' }}>
                {linkedLine}
                <span style={{ fontSize: 10, color: 'var(--muted)' }}> · from the catalogue product</span>
              </div>
            ) : line ? (
              <PickedValue
                title={line.name || 'Selected product line'}
                detail={familyPath(line.family, line.subfamily) || null}
                clearLabel="Change the product line"
                onClear={() => { setLine(null); setLineQuery(''); }}
              />
            ) : (
              <SearchList
                id="pf-line"
                label="Product lines"
                placeholder="Search product lines (optional)"
                query={lineQuery}
                onQuery={setLineQuery}
                items={lineResults.items}
                onPick={(l) => { setLine({ id: l.id, name: l.name, family: l.family, subfamily: l.subfamily }); setLineQuery(''); }}
                footer={lineFooter}
                renderItem={(l) => (
                  <>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{l.name}</div>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>{familyPath(l.family, l.subfamily) || '—'}</div>
                  </>
                )}
              />
            )}
            {isCustom && (
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                For a custom product. Its family follows from the line.
              </div>
            )}
          </div>
        </div>
        {error && (
          <div style={{ padding: '8px 12px', borderRadius: 6, fontSize: 11, marginBottom: 12, background: 'var(--accent2-dim)', color: 'var(--accent2)' }}>
            {error}
          </div>
        )}
      </div>
      <div className="ca-modal-footer">
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose}>Cancel</button>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleSave} disabled={saving || !name.trim()}>
          {saving ? 'Saving…' : (editing ? 'Update' : 'Create')}
        </button>
      </div>
    </Modal>
  );
}
