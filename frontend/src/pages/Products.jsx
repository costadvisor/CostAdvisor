import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useConfirm, useAlert } from '../components/ConfirmDialog';
import exportCsv from '../utils/exportCsv';

export default function Products() {
  const { activeTeamId } = useAuth();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);

  // Form fields
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('');
  const [activeContent, setActiveContent] = useState(1.0);
  const [unit, setUnit] = useState('kg');
  const [chemicalFamilyId, setChemicalFamilyId] = useState('');
  const [formulaTemplateId, setFormulaTemplateId] = useState('');

  const fetchData = () => {
    if (!activeTeamId) return;
    setLoading(true);
    Promise.all([
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/chemical-families'),
      api.get('/api/formulas/', { params: { team_id: activeTeamId } }),
    ])
      .then(([pRes, fRes, tRes]) => {
        setProducts(pRes.data);
        setFamilies(fRes.data);
        setTemplates(tRes.data);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(fetchData, [activeTeamId]);

  const resetForm = () => {
    setName(''); setFormula(''); setActiveContent(1.0); setUnit('kg'); setChemicalFamilyId('');
    setFormulaTemplateId('');
    setEditing(null); setShowForm(false);
  };

  const startEdit = (p) => {
    setName(p.name);
    setFormula(p.formula || '');
    setActiveContent(p.active_content ?? 1.0);
    setUnit(p.unit || 'kg');
    setChemicalFamilyId(p.chemical_family_id ?? '');
    setFormulaTemplateId(p.formula_template_id ?? '');
    setEditing(p.id);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    const body = {
      name: name.trim(),
      formula: formula.trim() || null,
      active_content: activeContent,
      unit,
      chemical_family_id: chemicalFamilyId ? Number(chemicalFamilyId) : null,
      formula_template_id: formulaTemplateId || null,
    };
    try {
      if (editing) {
        await api.put(`/api/products/${editing}`, body);
      } else {
        await api.post(`/api/products?team_id=${activeTeamId}`, body);
      }
      resetForm();
      fetchData();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    const ok = await confirm({
      title: 'Delete this product?',
      message: 'This will also delete all associated cost models.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(`/api/products/${id}`);
      fetchData();
    } catch (err) {
      showAlert({ title: 'Error', message: formatApiError(err) });
    }
  };

  const getFamilyName = (fid) => {
    const f = families.find(f => f.id === fid);
    return f ? f.name : null;
  };

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">Products</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {products.length > 0 && (
            <button
              className="ca-btn ca-btn-ghost"
              onClick={() => exportCsv('products.csv',
                ['Name', 'Chemical Formula', 'Family', 'Unit', 'Active Content'],
                products.map(p => [
                  p.name,
                  p.formula || '',
                  getFamilyName(p.chemical_family_id) || '',
                  p.unit,
                  p.active_content != null ? (p.active_content * 100).toFixed(0) + '%' : '',
                ])
              )}
            >
              Export CSV
            </button>
          )}
          <button className="ca-btn ca-btn-primary" onClick={() => { resetForm(); setShowForm(!showForm); }}>
            {showForm && !editing ? 'Cancel' : '+ Add Product'}
          </button>
        </div>
      </div>
      <p className="ca-subtitle">Manage products for your team. Products are linked to cost models.</p>

      {showForm && (
        <div className="ca-card" style={{ marginBottom: 16 }}>
          <div className="ca-card-title">{editing ? 'Edit Product' : 'New Product'}</div>
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
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="ca-btn ca-btn-primary" onClick={handleSave} disabled={saving || !name.trim()}>
              {saving ? 'Saving...' : (editing ? 'Update' : 'Create')}
            </button>
            {editing && <button className="ca-btn ca-btn-ghost" onClick={resetForm}>Cancel</button>}
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ padding: 20, color: 'var(--muted)' }}>Loading...</div>
      ) : products.length === 0 ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>
          No products yet. Create one above or via the Cost Model Builder.
        </div>
      ) : (
        <div className="ca-card">
          <table className="ca-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Formula</th>
                <th>Family</th>
                <th>Catalog Formula</th>
                <th className="center">Unit</th>
                <th className="center">Active Content</th>
                <th className="center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {products.map(p => (
                <tr key={p.id}>
                  <td style={{ fontWeight: 600 }}>{p.name}</td>
                  <td style={{ color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>{p.formula || '\u2014'}</td>
                  <td>
                    {getFamilyName(p.chemical_family_id)
                      ? <span className="ca-tag">{getFamilyName(p.chemical_family_id)}</span>
                      : <span style={{ color: 'var(--muted)' }}>{'\u2014'}</span>
                    }
                  </td>
                  <td>
                    {p.formula_template_id ? (
                      <span title={p.formula_template_name || ''} style={{
                        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                        color: 'var(--text-secondary)', background: 'var(--surface2)',
                        padding: '2px 7px', borderRadius: 4, whiteSpace: 'nowrap',
                      }}>
                        {p.formula_template_code || p.formula_template_name || 'linked'}
                      </span>
                    ) : <span style={{ color: 'var(--muted)' }}>{'\u2014'}</span>}
                  </td>
                  <td className="center">{p.unit}</td>
                  <td className="center">{p.active_content ? `${(p.active_content * 100).toFixed(0)}%` : '\u2014'}</td>
                  <td className="center">
                    <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => startEdit(p)}>Edit</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)' }}
                        onClick={() => handleDelete(p.id)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
