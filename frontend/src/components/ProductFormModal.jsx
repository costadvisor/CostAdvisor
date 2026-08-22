import { useState, useEffect } from 'react';
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
 * open, so callers don't have to manage six controlled fields themselves.
 */
export default function ProductFormModal({
  isOpen,
  onClose,
  onSaved,          // (product) => void — called after a successful create/update
  activeTeamId,
  families = [],
  templates = [],
  editing = null,    // existing product to edit, or null to create
}) {
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('');
  const [activeContent, setActiveContent] = useState(1.0);
  const [unit, setUnit] = useState('kg');
  const [chemicalFamilyId, setChemicalFamilyId] = useState('');
  const [formulaTemplateId, setFormulaTemplateId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    if (editing) {
      setName(editing.name);
      setFormula(editing.formula || '');
      setActiveContent(editing.active_content ?? 1.0);
      setUnit(editing.unit || 'kg');
      setChemicalFamilyId(editing.chemical_family_id ?? '');
      setFormulaTemplateId(editing.formula_template_id ?? '');
    } else {
      setName(''); setFormula(''); setActiveContent(1.0); setUnit('kg');
      setChemicalFamilyId(''); setFormulaTemplateId('');
    }
  }, [isOpen, editing]);

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    const body = {
      name: name.trim(),
      formula: formula.trim() || null,
      active_content: activeContent,
      unit,
      chemical_family_id: chemicalFamilyId ? Number(chemicalFamilyId) : null,
      formula_template_id: formulaTemplateId || null,
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

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={editing ? 'Edit Product' : 'New Product'} width={560}>
      <div className="ca-modal-body">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label">Name *</label>
            <input className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="Product name" />
          </div>
          <div>
            <label className="ca-label">Chemical Formula</label>
            <input className="ca-input" value={formula} onChange={e => setFormula(e.target.value)} placeholder="e.g. NaOH" />
          </div>
          <div>
            <label className="ca-label">Chemical Family</label>
            <select className="ca-select" value={chemicalFamilyId} onChange={e => setChemicalFamilyId(e.target.value)}>
              <option value="">None</option>
              {families.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </div>
          <div>
            <label className="ca-label">Unit</label>
            <select className="ca-select" value={unit} onChange={e => setUnit(e.target.value)}>
              {['kg', 't', 'lb'].map(u => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
          <div>
            <label className="ca-label">Active Content (0-1)</label>
            <input className="ca-input" type="number" value={activeContent} min={0} max={1} step={0.01}
              onChange={e => setActiveContent(+e.target.value)} />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label className="ca-label">Catalog Formula</label>
            <select className="ca-select" value={formulaTemplateId}
              onChange={e => setFormulaTemplateId(e.target.value)}>
              <option value="">None — model by hand</option>
              {templates.map(t => (
                <option key={t.id} value={t.id}>
                  {t.name}{t.code ? ` (${t.code})` : ''}{t.team_id ? ' · team' : ''}
                </option>
              ))}
            </select>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
              New cost models for this product auto-load the linked recipe at their region.
            </div>
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
