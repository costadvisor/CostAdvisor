import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';

// Cost-structure templates: a named % breakdown ("Raw Materials 68%,
// Energy 4%...") a team can reuse when sketching a should-cost before a
// full formula exists. Had a working backend (/api/scenarios) since
// early on with no UI anywhere in the app — this closes that gap
// (Scrum 16's "Scenarios still to do" empty-state item).
function pctSum(breakdown) {
  return Object.values(breakdown).reduce((s, v) => s + (Number(v) || 0), 0);
}

function NewScenarioForm({ teamId, onCreated, onCancel }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [rows, setRows] = useState([{ label: 'Raw Materials', pct: '' }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const updateRow = (i, field, value) => {
    setRows(rs => rs.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  };
  const addRow = () => setRows(rs => [...rs, { label: '', pct: '' }]);
  const removeRow = (i) => setRows(rs => rs.filter((_, idx) => idx !== i));

  const breakdown = Object.fromEntries(
    rows.filter(r => r.label.trim() && r.pct !== '').map(r => [r.label.trim(), Number(r.pct) / 100]),
  );
  const total = pctSum(breakdown) * 100;

  const submit = async () => {
    if (!name.trim() || Object.keys(breakdown).length === 0) return;
    setBusy(true);
    setErr(null);
    try {
      const { data } = await api.post('/api/scenarios/', { name, description, breakdown }, { params: { team_id: teamId } });
      onCreated(data);
    } catch (e) {
      setErr(formatApiError(e) || 'Could not save that scenario.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ca-card" style={{ marginBottom: 16 }}>
      <div className="ca-card-title" style={{ marginBottom: 8 }}>New cost-structure scenario</div>
      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 8 }}>{err}</div>}
      <input className="ca-input" placeholder="Name (e.g. Specialty Chemical — typical mix)" value={name}
             onChange={e => setName(e.target.value)} style={{ width: '100%', marginBottom: 8 }} />
      <input className="ca-input" placeholder="Description (optional)" value={description}
             onChange={e => setDescription(e.target.value)} style={{ width: '100%', marginBottom: 10 }} />
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <input className="ca-input" placeholder="Category" value={r.label}
                 onChange={e => updateRow(i, 'label', e.target.value)} style={{ flex: 1 }} />
          <input className="ca-input" type="number" placeholder="%" value={r.pct}
                 onChange={e => updateRow(i, 'pct', e.target.value)} style={{ width: 80 }} />
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => removeRow(i)} disabled={rows.length === 1}>×</button>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, marginBottom: 10 }}>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={addRow}>+ Category</button>
        <span style={{ fontSize: 11, color: Math.round(total) === 100 ? 'var(--muted)' : 'var(--accent2)' }}>
          Total: {total.toFixed(1)}%{Math.round(total) !== 100 && ' (should be 100%)'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={submit}
                disabled={busy || !name.trim() || Object.keys(breakdown).length === 0}>
          {busy ? 'Saving…' : 'Save scenario'}
        </button>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function Scenarios() {
  const { activeTeamId } = useAuth();
  const [scenarios, setScenarios] = useState(null);
  const [err, setErr] = useState(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(() => {
    if (!activeTeamId) return;
    api.get('/api/scenarios/', { params: { team_id: activeTeamId } })
      .then(({ data }) => setScenarios(data))
      .catch(e => setErr(formatApiError(e) || 'Could not load scenarios.'));
  }, [activeTeamId]);

  useEffect(() => { load(); }, [load]);

  const remove = async (id) => {
    try {
      await api.delete(`/api/scenarios/${id}`);
      load();
    } catch (e) {
      setErr(formatApiError(e) || 'Could not delete that scenario.');
    }
  };

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 16px' }}>
      <h2 style={{ marginBottom: 4 }}>Scenarios</h2>
      <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
        Reusable cost-structure templates — a named % breakdown you can start a should-cost
        sketch from before a product has a full formula.
      </p>
      {err && <div style={{ fontSize: 12, color: 'var(--accent2)', marginBottom: 10 }}>{err}</div>}

      {showForm && (
        <NewScenarioForm
          teamId={activeTeamId}
          onCancel={() => setShowForm(false)}
          onCreated={() => { setShowForm(false); load(); }}
        />
      )}

      {!scenarios ? (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>Loading…</div>
      ) : scenarios.length === 0 && !showForm ? (
        <div className="ca-card" style={{ textAlign: 'center', padding: '32px 20px' }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>No scenarios yet</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>
            Build a reusable cost-structure template — e.g. "Specialty Chemical: 68% raw materials,
            4% energy, 12% labor, 16% margin" — to sketch a should-cost before a real formula exists.
          </div>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setShowForm(true)}>
            + Add your first scenario
          </button>
        </div>
      ) : (
        <>
          {!showForm && (
            <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => setShowForm(true)} style={{ marginBottom: 12 }}>
              + New scenario
            </button>
          )}
          {scenarios.map(s => (
            <div key={s.id} className="ca-card" style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
                <div style={{ fontWeight: 700 }}>{s.name}</div>
                {s.is_system && <span className="ca-badge" style={{ marginLeft: 8 }}>Platform</span>}
                {!s.is_system && (
                  <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
                          onClick={() => remove(s.id)}>Delete</button>
                )}
              </div>
              {s.description && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>{s.description}</div>}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {Object.entries(s.breakdown).map(([label, pct]) => (
                  <span key={label} className="ca-badge" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    {label} {(pct * 100).toFixed(0)}%
                  </span>
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
