import { useState, useEffect } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useConfirm, useAlert } from '../components/ConfirmDialog';
import ProductFormModal from '../components/ProductFormModal';
import exportCsv from '../utils/exportCsv';

export default function Products() {
  const { activeTeamId } = useAuth();
  const confirm = useConfirm();
  const showAlert = useAlert();
  const [products, setProducts] = useState([]);
  const [families, setFamilies] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);

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
    setEditing(null); setShowForm(false);
  };

  // `editing` now holds the full product (ProductFormModal prefills from it
  // directly) rather than just its id.
  const startEdit = (p) => {
    setEditing(p);
    setShowForm(true);
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
          <button className="ca-btn ca-btn-primary" onClick={() => { setEditing(null); setShowForm(true); }}>
            + Add Product
          </button>
        </div>
      </div>
      <p className="ca-subtitle">Manage products for your team. Products are linked to cost models.</p>

      <ProductFormModal
        isOpen={showForm}
        editing={editing}
        families={families}
        templates={templates}
        activeTeamId={activeTeamId}
        onClose={resetForm}
        onSaved={() => { resetForm(); fetchData(); }}
      />

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
