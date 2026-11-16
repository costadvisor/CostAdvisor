import { useState } from 'react';
import api, { formatApiError } from '../api';

export default function QuarterPriceList({ year, quarter, products, onDataChanged }) {
  // products: [{ cost_model_id, product_name, product_reference, region, currency, price, volume }]
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);
  // A refused save (e.g. a price of zero) used to go to the console only.
  const [error, setError] = useState(null);

  const isEditing = (cmId) => cmId in edits;

  const startEdit = (p) => {
    setEdits(prev => ({
      ...prev,
      [p.cost_model_id]: {
        price: p.price != null ? String(p.price) : '',
        volume: p.volume != null ? String(p.volume) : '',
      },
    }));
  };

  const cancelEdit = (cmId) => {
    setEdits(prev => { const next = { ...prev }; delete next[cmId]; return next; });
  };

  const updateEdit = (cmId, field, value) => {
    setEdits(prev => ({ ...prev, [cmId]: { ...prev[cmId], [field]: value } }));
  };

  const saveOne = async (cmId) => {
    const e = edits[cmId];
    if (!e) return;
    setSaving(true);
    setError(null);
    try {
      const calls = [];
      if (e.price !== '') {
        calls.push(api.put(`/api/prices/${cmId}/${year}/${quarter}`, {
          year, quarter, price: parseFloat(e.price),
        }));
      }
      if (e.volume !== '') {
        calls.push(api.put(`/api/volumes/${cmId}/${year}/${quarter}`, {
          year, quarter, volume: parseFloat(e.volume),
        }));
      }
      await Promise.all(calls);
      cancelEdit(cmId);
      onDataChanged?.();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const saveAll = async () => {
    setSaving(true);
    setError(null);
    try {
      const calls = [];
      for (const [cmId, e] of Object.entries(edits)) {
        if (e.price !== '') {
          calls.push(api.put(`/api/prices/${cmId}/${year}/${quarter}`, {
            year, quarter, price: parseFloat(e.price),
          }));
        }
        if (e.volume !== '') {
          calls.push(api.put(`/api/volumes/${cmId}/${year}/${quarter}`, {
            year, quarter, volume: parseFloat(e.volume),
          }));
        }
      }
      await Promise.all(calls);
      setEdits({});
      onDataChanged?.();
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  };

  const editAll = () => {
    const all = {};
    products.forEach(p => {
      all[p.cost_model_id] = {
        price: p.price != null ? String(p.price) : '',
        volume: p.volume != null ? String(p.volume) : '',
      };
    });
    setEdits(all);
  };

  const handleKeyDown = (e, cmId) => {
    if (e.key === 'Enter') saveOne(cmId);
    if (e.key === 'Escape') cancelEdit(cmId);
  };

  const hasEdits = Object.keys(edits).length > 0;
  const allEditing = Object.keys(edits).length === products.length;
  const inputStyle = { fontSize: 11, padding: '3px 6px', width: 90 };

  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6, gap: 6 }}>
        {!allEditing && (
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={editAll}>Edit All</button>
        )}
        {hasEdits && (
          <>
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={saveAll} disabled={saving}>
              {saving ? 'Saving...' : 'Save All'}
            </button>
            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setEdits({})}>Cancel All</button>
          </>
        )}
      </div>
      {error && (
        <div role="alert" style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 6 }}>Couldn't save — {error}</div>
      )}
      <table className="ca-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th>Product</th>
            <th>Reference</th>
            <th>Producing Region</th>
            <th>Price</th>
            <th>Volume</th>
            <th className="center" style={{ width: 100 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {products.map(p => {
            const editing = isEditing(p.cost_model_id);
            const e = edits[p.cost_model_id];
            return (
              <tr key={p.cost_model_id}>
                <td style={{ fontWeight: 500, fontSize: 11 }}>{p.product_name}</td>
                <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{p.product_reference || '\u2014'}</td>
                <td style={{ color: 'var(--muted)', fontSize: 11 }}>{p.region}</td>
                <td>
                  {editing ? (
                    <input className="ca-input" style={inputStyle} value={e.price}
                      onChange={ev => updateEdit(p.cost_model_id, 'price', ev.target.value)}
                      onKeyDown={ev => handleKeyDown(ev, p.cost_model_id)}
                      placeholder={p.currency} autoFocus />
                  ) : (
                    <span style={{ fontSize: 11 }}>
                      {p.price != null ? `${p.price.toLocaleString()} ${p.currency}` : '\u2014'}
                    </span>
                  )}
                </td>
                <td>
                  {editing ? (
                    <input className="ca-input" style={inputStyle} value={e.volume}
                      onChange={ev => updateEdit(p.cost_model_id, 'volume', ev.target.value)}
                      onKeyDown={ev => handleKeyDown(ev, p.cost_model_id)}
                      placeholder="Volume" />
                  ) : (
                    <span style={{ fontSize: 11 }}>
                      {p.volume != null ? p.volume.toLocaleString() : '\u2014'}
                    </span>
                  )}
                </td>
                <td className="center">
                  {editing ? (
                    <>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => saveOne(p.cost_model_id)}
                        disabled={saving}>{saving ? '...' : 'Save'}</button>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 4 }}
                        onClick={() => cancelEdit(p.cost_model_id)}>Cancel</button>
                    </>
                  ) : (
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => startEdit(p)}>Edit</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
