import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import api from '../api';
import exportCsv from '../utils/exportCsv';
import FormulaDetailModal from '../components/FormulaDetailModal';
import FileUpload from '../components/FileUpload';
import { stripReservedFns } from '../utils/formulaFns';
import NumberInput from '../components/NumberInput';
import { normalizeVarMap } from '../components/VariableMapEditor';
import IndexCombo from '../components/IndexCombo';

// data_confidence → badge treatment. CONF-LOW is a placeholder pending expert
// review — it must read as a caution, not as fact.
const CONF_BADGE = {
  'CONF-HIGH': {
    bg: 'var(--success-bg)', color: 'var(--accent)', label: 'HIGH',
    title: 'Verified against real process chemistry',
  },
  'CONF-MED': {
    bg: 'var(--info-bg)', color: 'var(--accent4)', label: 'MED',
    title: 'Missing lines added; dominant feedstock proportionally scaled',
  },
  'CONF-LOW': {
    bg: 'var(--warn-bg)', color: 'var(--accent3)', label: 'LOW · REVIEW',
    title: 'Placeholder — proportional scaling only, pending expert review',
  },
};

const TIER_LABEL = {
  free: 'free', good_proxy: 'good proxy', weak_proxy: 'weak proxy', blocked: 'blocked',
};

// Coverage tier = the worst retrieval tier among the recipe's index inputs —
// a formula is only as strong as its weakest feed.
const TIER_TITLE = {
  free: 'Every index input has a direct, free public feed (World Bank, EIA, Eurostat…)',
  good_proxy: 'At least one input is derived via a reliable stand-in relationship (e.g. Brent + a stable spread) — directionally trustworthy, not the exact traded price',
  weak_proxy: 'At least one input leans on a loose stand-in — treat movements as indicative only',
  blocked: 'An input has no viable free source (subscription-only) — this formula cannot be priced without licensed data',
};

export default function Formulas() {
  const { activeTeamId, user } = useAuth();
  const { addToast } = useToast();

  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [canEditPlatform, setCanEditPlatform] = useState(false);
  const [canEditTeam, setCanEditTeam] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState(null);
  const [detailTemplate, setDetailTemplate] = useState(null);
  const [commodities, setCommodities] = useState([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const platformTemplates = templates.filter(t => !t.team_id);
  const teamTemplates = templates.filter(t => t.team_id);
  // Platform templates this team has already forked (so we don't offer a duplicate fork).
  const forkedOriginIds = new Set(teamTemplates.map(t => t.origin_id).filter(Boolean));

  const fetchData = async () => {
    if (!activeTeamId) return;
    setLoading(true);
    try {
      const [tmplRes, permRes, cmRes] = await Promise.all([
        api.get('/api/formulas/', { params: { team_id: activeTeamId } }),
        api.get('/api/formulas/can-edit-platform'),
        // Full index list (not just has_data) — weighted lines may target
        // catalog commodities that don't carry values yet.
        api.get('/api/indexes'),
      ]);
      setTemplates(tmplRes.data);
      setCanEditPlatform(permRes.data.can_edit);
      setCommodities(cmRes.data);

      // Team edit: any owner-level check via membership role
      const meRes = await api.get('/auth/me');
      const membership = meRes.data?.memberships?.find(m => m.team_id === activeTeamId);
      setCanEditTeam(membership?.role === 'owner' || membership?.role === 'admin');
    } catch {
      // silently fail — page shows empty state
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, [activeTeamId]);

  const handleDelete = async (id) => {
    try {
      await api.delete(`/api/formulas/${id}`);
      setTemplates(prev => prev.filter(t => t.id !== id));
      addToast('Formula deleted', 'success');
    } catch (e) {
      addToast(e?.response?.data?.detail || 'Delete failed', 'error');
    } finally {
      setConfirmDeleteId(null);
    }
  };

  const handleFork = async (id) => {
    try {
      await api.post(`/api/formulas/${id}/fork`, { team_id: activeTeamId });
      addToast('Forked into your team — edit it under Team Formulas.', 'success');
      fetchData();
    } catch (e) {
      addToast(e?.response?.data?.detail || 'Fork failed', 'error');
    }
  };

  const openCreate = (isPlatform) => {
    setEditingTemplate({ _new: true, _platform: isPlatform });
    setShowModal(true);
  };

  const openEdit = (t) => {
    setEditingTemplate(t);
    setShowModal(true);
  };

  const handleSaved = (saved) => {
    setTemplates(prev => {
      const idx = prev.findIndex(t => t.id === saved.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = saved;
        return next;
      }
      return [...prev, saved];
    });
    setShowModal(false);
    setEditingTemplate(null);
  };

  if (!activeTeamId) {
    return (
      <div className="ca-page">
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
          Select a team to view formulas.
        </div>
      </div>
    );
  }

  return (
    <div className="ca-page">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 className="ca-page-title" style={{ marginBottom: 4 }}>Formula Library</h1>
          <p style={{ fontSize: 12, color: 'var(--muted)', margin: 0 }}>
            Reusable advanced formula templates for cost models
          </p>
        </div>
        {(canEditTeam || canEditPlatform) && (
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={() => openCreate(false)}>
            + Add Formula
          </button>
        )}
      </div>

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>Loading…</div>
      ) : (
        <>
          <CatalogSection
            rows={platformTemplates}
            canEdit={canEditPlatform}
            canFork={canEditTeam}
            forkedOriginIds={forkedOriginIds}
            onFork={handleFork}
            onEdit={openEdit}
            onDelete={id => setConfirmDeleteId(id)}
            onOpen={setDetailTemplate}
          />

          <FormulaSection
            title="Team Formulas"
            subtitle="Managed by your team — visible only to your team"
            rows={teamTemplates}
            canEdit={canEditTeam}
            onEdit={openEdit}
            onDelete={id => setConfirmDeleteId(id)}
            onOpen={setDetailTemplate}
          />

          {platformTemplates.length === 0 && teamTemplates.length === 0 && (
            <div style={{
              marginTop: 40, padding: 40, textAlign: 'center',
              border: '1px dashed var(--border)', borderRadius: 12,
              color: 'var(--muted)', fontSize: 13,
            }}>
              No formulas yet.
              {(canEditPlatform || canEditTeam) && (
                <span> Use the buttons above to create your first template.</span>
              )}
            </div>
          )}
        </>
      )}

      {confirmDeleteId && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }}>
          <div style={{
            background: 'var(--surface)', borderRadius: 12, padding: 28,
            minWidth: 320, boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
          }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Delete formula?</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 20 }}>
              This cannot be undone. Cost models using this expression will keep their saved values.
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => setConfirmDeleteId(null)}>
                Cancel
              </button>
              <button
                className="ca-btn ca-btn-sm"
                style={{ background: 'var(--accent2)', color: '#fff', border: 'none' }}
                onClick={() => handleDelete(confirmDeleteId)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {showModal && (
        <FormulaModal
          template={editingTemplate}
          activeTeamId={activeTeamId}
          canEditPlatform={canEditPlatform}
          canEditTeam={canEditTeam}
          commodities={commodities}
          templates={templates}
          onSaved={handleSaved}
          onClose={() => { setShowModal(false); setEditingTemplate(null); }}
          addToast={addToast}
        />
      )}

      {detailTemplate && (
        <FormulaDetailModal
          template={detailTemplate}
          activeTeamId={activeTeamId}
          canEdit={detailTemplate.team_id ? canEditTeam : canEditPlatform}
          onClose={() => setDetailTemplate(null)}
          addToast={addToast}
        />
      )}
    </div>
  );
}

function ConfidenceBadge({ confidence }) {
  const style = CONF_BADGE[confidence];
  if (!style) return <span style={{ color: 'var(--muted)', fontSize: 10 }}>—</span>;
  return (
    <span className="ca-badge" title={style.title}
      style={{ background: style.bg, color: style.color, fontWeight: 600 }}>
      {style.label}
    </span>
  );
}

function NameButton({ template, onOpen }) {
  return (
    <button
      onClick={() => onOpen(template)}
      title="View weighted recipe & regional pricing"
      style={{
        background: 'none', border: 'none', padding: 0, cursor: 'pointer',
        color: 'var(--text)', fontWeight: 600, fontSize: 12, textAlign: 'left',
        fontFamily: 'inherit',
        // Persistent muted underline: the name is the door to the recipe —
        // it has to read as a link, not as plain text.
        textDecoration: 'underline',
        textDecorationColor: 'var(--border-light)',
        textUnderlineOffset: 3,
      }}
      onMouseEnter={e => { e.currentTarget.style.textDecorationColor = 'var(--accent)'; }}
      onMouseLeave={e => { e.currentTarget.style.textDecorationColor = 'var(--border-light)'; }}
    >
      {template.name}
    </button>
  );
}

function CatalogSection({ rows, canEdit, onEdit, onDelete, onOpen, canFork = false, forkedOriginIds, onFork }) {
  const [query, setQuery] = useState('');
  const [confFilter, setConfFilter] = useState('all');
  const [expanded, setExpanded] = useState(() => new Set());
  const [showPriceImport, setShowPriceImport] = useState(false);

  const catalogRows = rows.filter(t => t.code);
  const otherRows = rows.filter(t => !t.code);

  // family code → { code, name, subfamilies: Map(subName → rows[]) }, sorted
  const families = useMemo(() => {
    const q = query.trim().toLowerCase();
    const map = new Map();
    for (const t of catalogRows) {
      if (confFilter !== 'all' && t.catalog_meta?.data_confidence !== confFilter) continue;
      if (q) {
        const hay = `${t.name} ${t.code} ${t.family_name || ''} ${t.subfamily_name || ''}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      const fcode = t.family_code || '—';
      if (!map.has(fcode)) {
        map.set(fcode, { code: fcode, name: t.family_name || 'Uncategorised', subs: new Map(), count: 0, review: 0 });
      }
      const fam = map.get(fcode);
      const sub = t.subfamily_name || '—';
      if (!fam.subs.has(sub)) fam.subs.set(sub, []);
      fam.subs.get(sub).push(t);
      fam.count += 1;
      if (t.catalog_meta?.data_confidence === 'CONF-LOW') fam.review += 1;
    }
    const list = [...map.values()].sort((a, b) => a.code.localeCompare(b.code));
    for (const fam of list) {
      fam.subs = new Map([...fam.subs.entries()].sort((a, b) => a[0].localeCompare(b[0])));
      for (const rowsInSub of fam.subs.values()) rowsInSub.sort((a, b) => a.name.localeCompare(b.name));
    }
    return list;
  }, [catalogRows, query, confFilter]);

  const filtering = query.trim() !== '' || confFilter !== 'all';
  const shown = families.reduce((n, f) => n + f.count, 0);
  const reviewTotal = catalogRows.filter(t => t.catalog_meta?.data_confidence === 'CONF-LOW').length;
  const isOpen = (fam) => filtering || expanded.has(fam.code);
  const allOpen = families.length > 0 && families.every(isOpen);

  const toggle = (code) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(code)) next.delete(code); else next.add(code);
    return next;
  });

  const handleExport = () => {
    exportCsv(
      'formulas_default_catalog.csv',
      ['Family', 'Subfamily', 'Formula', 'Code', 'Confidence', 'Coverage', 'Regions'],
      families.flatMap(fam => [...fam.subs.entries()].flatMap(([sub, list]) =>
        list.map(t => [
          `${fam.code} ${fam.name}`, sub, t.name, t.code,
          t.catalog_meta?.data_confidence || '', t.catalog_meta?.coverage_tier || '',
          t.catalog_meta?.region_count ?? '',
        ])
      ))
    );
  };

  if (catalogRows.length === 0 && otherRows.length === 0 && !canEdit) return null;

  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Default Formulas</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            Managed by platform Chemists and Super Admins — visible to all teams
          </div>
          {catalogRows.length > 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, fontFamily: "'JetBrains Mono', monospace" }}>
              {families.length} families · {shown} formulas
              {reviewTotal > 0 && (
                <span style={{ color: 'var(--accent3)' }}> · {reviewTotal} pending expert review</span>
              )}
            </div>
          )}
        </div>
        {catalogRows.length > 0 && (
          <div style={{ display: 'flex', gap: 6 }}>
            {canEdit && (
              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 11 }}
                onClick={() => setShowPriceImport(v => !v)}>
                Import Prices
              </button>
            )}
            <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 11 }} onClick={handleExport}>
              Export CSV
            </button>
          </div>
        )}
      </div>

      {showPriceImport && canEdit && (
        <div style={{
          border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px',
          marginBottom: 12, background: 'var(--surface)',
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
            Import base-price anchors
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
            Columns: formula, region, base_price — optional currency, base_period (Q1-2025), margin_pct.
            Rows attach to existing combos only; recipes and review state are never touched.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <FileUpload endpoint="/api/formulas/coverage/upload" />
            <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
              onClick={() => exportCsv(
                'coverage_price_template.csv',
                ['formula', 'region', 'base_price', 'currency', 'base_period', 'margin_pct'],
                [['OLE-FAC-SAT', 'Europe', '1250', 'EUR', 'Q1-2025', '9'],
                 ['SUR-AES-3EO', 'NA', '1480', 'USD', 'Q1-2025', '']],
              )}>
              Download template
            </button>
          </div>
        </div>
      )}

      {catalogRows.length > 0 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            className="ca-input"
            style={{ maxWidth: 300, padding: '7px 10px', fontSize: 12 }}
            placeholder="Search name, code, family…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            aria-label="Search default formulas"
          />
          {['all', 'CONF-HIGH', 'CONF-MED', 'CONF-LOW'].map(c => (
            <button
              key={c}
              onClick={() => setConfFilter(c)}
              style={{
                padding: '5px 12px', borderRadius: 20, fontSize: 10, cursor: 'pointer',
                fontFamily: "'JetBrains Mono', monospace", letterSpacing: 0.5,
                border: `1px solid ${confFilter === c ? 'var(--border-light)' : 'var(--border)'}`,
                background: confFilter === c ? 'var(--surface3)' : 'transparent',
                color: confFilter === c ? 'var(--text)' : 'var(--text-secondary)',
                fontWeight: confFilter === c ? 600 : 400,
              }}
            >
              {c === 'all' ? 'ALL' : c.replace('CONF-', '')}
            </button>
          ))}
          {!filtering && (
            <button
              className="ca-btn ca-btn-ghost ca-btn-sm"
              style={{ fontSize: 10, marginLeft: 'auto' }}
              onClick={() => setExpanded(allOpen ? new Set() : new Set(families.map(f => f.code)))}
            >
              {allOpen ? 'Collapse all' : 'Expand all'}
            </button>
          )}
        </div>
      )}

      {catalogRows.length === 0 ? (
        <div style={{
          padding: '20px 16px', border: '1px dashed var(--border)',
          borderRadius: 8, fontSize: 12, color: 'var(--muted)', textAlign: 'center',
        }}>
          No default formulas yet.
        </div>
      ) : families.length === 0 ? (
        <div style={{
          padding: '20px 16px', border: '1px dashed var(--border)',
          borderRadius: 8, fontSize: 12, color: 'var(--muted)', textAlign: 'center',
        }}>
          No formulas match {query.trim() ? <>“{query.trim()}”</> : 'this filter'}.
        </div>
      ) : (
        <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
          {families.map((fam, fi) => (
            <div key={fam.code} style={{ borderTop: fi > 0 ? '1px solid var(--border)' : 'none' }}>
              <button
                onClick={() => !filtering && toggle(fam.code)}
                aria-expanded={isOpen(fam)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                  padding: '11px 16px', background: 'transparent', border: 'none',
                  cursor: filtering ? 'default' : 'pointer', textAlign: 'left', color: 'var(--text)',
                }}
              >
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"
                  style={{
                    transform: isOpen(fam) ? 'rotate(90deg)' : 'none',
                    transition: 'transform 0.15s ease-out', flexShrink: 0,
                    opacity: filtering ? 0.3 : 1,
                  }}>
                  <path d="M3 1l4 4-4 4" fill="none" stroke="var(--text-secondary)" strokeWidth="1.5" />
                </svg>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: 'var(--muted)', width: 28, flexShrink: 0 }}>
                  {fam.code}
                </span>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{fam.name}</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-secondary)', fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'nowrap' }}>
                  {fam.subs.size} subfamilies · {fam.count} formulas
                  {fam.review > 0 && <span style={{ color: 'var(--accent3)' }}> · {fam.review} review</span>}
                </span>
              </button>

              {isOpen(fam) && (
                <table className="ca-table" style={{ margin: 0 }}>
                  <tbody>
                    {[...fam.subs.entries()].map(([sub, list]) => (
                      <SubfamilyRows key={sub} sub={sub} list={list}
                        canEdit={canEdit} onEdit={onEdit} onDelete={onDelete} onOpen={onOpen}
                        canFork={canFork} forkedOriginIds={forkedOriginIds} onFork={onFork} />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </div>
      )}

      {otherRows.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <FormulaSection
            title="Other platform formulas"
            subtitle="Hand-authored templates outside the catalog taxonomy"
            rows={otherRows}
            canEdit={canEdit}
            onEdit={onEdit}
            onDelete={onDelete}
            onOpen={onOpen}
            canFork={canFork}
            forkedOriginIds={forkedOriginIds}
            onFork={onFork}
          />
        </div>
      )}
    </div>
  );
}

function SubfamilyRows({ sub, list, canEdit, onEdit, onDelete, onOpen, canFork = false, forkedOriginIds, onFork }) {
  const showActions = canEdit || canFork;
  return (
    <>
      <tr>
        <td colSpan={showActions ? 5 : 4} style={{
          padding: '8px 16px 4px 44px', fontSize: 10, fontWeight: 600,
          textTransform: 'uppercase', letterSpacing: 0.8, color: 'var(--muted)',
          borderBottom: 'none', background: 'var(--neutral-bg-soft)',
        }}>
          {sub}
        </td>
      </tr>
      {list.map(t => (
        <tr key={t.id}>
          <td style={{ paddingLeft: 44 }}>
            <NameButton template={t} onOpen={onOpen} />
          </td>
          <td style={{ width: 130 }}>
            <span style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
              color: 'var(--text-secondary)', background: 'var(--surface2)',
              padding: '2px 7px', borderRadius: 4, whiteSpace: 'nowrap',
            }}>
              {t.code}
            </span>
          </td>
          <td style={{ width: 110 }}>
            <ConfidenceBadge confidence={t.catalog_meta?.data_confidence} />
          </td>
          <td style={{ width: 150, fontFamily: "'JetBrains Mono', monospace", fontSize: 11, whiteSpace: 'nowrap' }}>
            <span
              title={TIER_TITLE[t.catalog_meta?.coverage_tier]}
              style={{ color: t.catalog_meta?.coverage_tier === 'blocked' ? 'var(--accent2)' : 'var(--text-secondary)' }}
            >
              {TIER_LABEL[t.catalog_meta?.coverage_tier] || '—'}
            </span>
            <span style={{ color: 'var(--muted)' }}>
              {' '}· {t.catalog_meta?.region_count ?? 0} {t.catalog_meta?.region_count === 1 ? 'region' : 'regions'}
            </span>
          </td>
          {showActions && (
            <td style={{ width: 160, textAlign: 'center' }}>
              <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                {canEdit && (
                  <>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => onEdit(t)}>Edit</button>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm"
                      style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                      onClick={() => onDelete(t.id)}>Delete</button>
                  </>
                )}
                {canFork && (
                  forkedOriginIds?.has(t.id)
                    ? <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: 'var(--muted)' }}>Forked</span>
                    : <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => onFork(t.id)} title="Copy into your team as an editable formula">Fork</button>
                )}
              </div>
            </td>
          )}
        </tr>
      ))}
    </>
  );
}

function FormulaSection({ title, subtitle, rows, canEdit, onEdit, onDelete, onOpen, canFork = false, forkedOriginIds, onFork }) {
  const showActions = canEdit || canFork;
  if (rows.length === 0 && !showActions) return null;

  const handleExport = () => {
    exportCsv(
      `formulas_${title.toLowerCase().replace(/\s+/g, '_')}.csv`,
      ['Name', 'Description', 'Expression', 'Variables', 'Created By'],
      rows.map(t => [
        t.name,
        t.description || '',
        t.expression || '',
        t.variables ? Object.keys(t.variables).join(', ') : '',
        t.creator_email || '',
      ])
    );
  };

  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{subtitle}</div>
        </div>
        {rows.length > 0 && (
          <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 11 }} onClick={handleExport}>
            Export CSV
          </button>
        )}
      </div>
      {rows.length === 0 ? (
        <div style={{
          padding: '20px 16px', border: '1px dashed var(--border)',
          borderRadius: 8, fontSize: 12, color: 'var(--muted)', textAlign: 'center',
        }}>
          No formulas yet.
        </div>
      ) : (
        <div className="ca-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="ca-table" style={{ margin: 0 }}>
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th style={{ textAlign: 'center' }}>Variables</th>
                <th>Created by</th>
                {showActions && <th style={{ textAlign: 'center' }}>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map(t => (
                <tr key={t.id}>
                  <td>
                    <NameButton template={t} onOpen={onOpen} />
                    {t.origin_id && (
                      <span className="ca-badge" title="Forked from a platform formula"
                        style={{ marginLeft: 6, background: 'var(--info-bg)', color: 'var(--accent4)' }}>fork</span>
                    )}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{t.description || '—'}</td>
                  <td style={{ textAlign: 'center', fontFamily: 'monospace', fontSize: 11 }}>
                    {t.variables ? Object.keys(t.variables).length : 0}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{t.creator_email || '—'}</td>
                  {showActions && (
                    <td style={{ textAlign: 'center' }}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                        {canEdit && (
                          <>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => onEdit(t)}>Edit</button>
                            <button className="ca-btn ca-btn-ghost ca-btn-sm"
                              style={{ color: 'var(--accent2)', borderColor: 'var(--accent2)' }}
                              onClick={() => onDelete(t.id)}>Delete</button>
                          </>
                        )}
                        {canFork && (
                          forkedOriginIds?.has(t.id)
                            ? <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: 'var(--muted)' }}>Forked</span>
                            : <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => onFork(t.id)} title="Copy into your team as an editable formula">Fork</button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FormulaModal({ template, activeTeamId, canEditPlatform, canEditTeam, commodities, templates, onSaved, onClose, addToast }) {
  const isNew = !!template?._new;
  // Default scope: platform-only users → platform; everyone else → team
  const defaultScope = !canEditTeam && canEditPlatform ? 'platform' : 'team';
  const [name, setName] = useState(isNew ? '' : (template?.name || ''));
  const [description, setDescription] = useState(isNew ? '' : (template?.description || ''));
  const [scope, setScope] = useState(
    isNew ? defaultScope : (template?.team_id ? 'team' : 'platform')
  );
  // Toggle shown only when user has both permissions
  const showScopeToggle = canEditPlatform && canEditTeam;
  const [expression, setExpression] = useState(isNew ? '' : (template?.expression || ''));
  const [vars, setVars] = useState(isNew ? {} : (template?.variables || {}));
  const [mode, setMode] = useState('expression'); // 'expression' | 'weighted'
  const [lines, setLines] = useState([]);
  const [saving, setSaving] = useState(false);

  // Load the template-level (region-NULL) weighted lines when editing; a
  // template that has lines but no expression opens in weighted mode.
  useEffect(() => {
    if (isNew) return;
    let alive = true;
    api.get(`/api/formulas/${template.id}/components`, { params: { team_id: activeTeamId } })
      .then(res => {
        if (!alive) return;
        setLines(res.data.map(l => ({
          name: l.name, component_type: l.component_type,
          commodity_id: l.commodity_id, input_template_id: l.input_template_id,
          weight_pct: l.weight_pct, is_proxy: l.is_proxy,
        })));
        if (res.data.length > 0 && !template.expression) setMode('weighted');
      })
      .catch(() => { /* lines stay empty; expression mode still works */ });
    return () => { alive = false; };
  }, [template?.id]);

  const linesSum = lines.reduce((s, l) => s + (parseFloat(l.weight_pct) || 0), 0);
  const sumOk = Math.abs(linesSum - 100) <= 0.01;

  const updateLine = (i, patch) => {
    setLines(prev => prev.map((l, idx) => idx === i ? { ...l, ...patch } : l));
  };

  const chainableTemplates = (templates || []).filter(t =>
    t.id !== template?.id &&
    // A platform formula may only chain platform inputs (the backend
    // enforces this too — any team resolving it must not miss lines).
    (scope === 'platform' ? !t.team_id : true)
  );

  const detectVars = () => {
    const expr = expression.replace(/[[\]]/g, '').replace(/\s/g, '');
    const tokens = expr.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
    const unique = stripReservedFns([...new Set(tokens)]);
    setVars(prev => {
      const next = { ...prev };
      unique.forEach(n => { if (!next[n]) next[n] = { type: 'fixed', value: 0 }; });
      return next;
    });
  };

  const updateVar = (varName, key, val) => {
    setVars(prev => ({ ...prev, [varName]: { ...prev[varName], [key]: val } }));
  };

  const removeVar = (varName) => {
    setVars(prev => { const n = { ...prev }; delete n[varName]; return n; });
  };

  const handleSave = async () => {
    if (!name.trim()) { addToast('Name is required', 'error'); return; }
    if (mode === 'expression' && !expression.trim() && lines.length === 0) {
      addToast('Expression is required', 'error'); return;
    }
    if (mode === 'weighted') {
      if (lines.length === 0) { addToast('Add at least one weighted line', 'error'); return; }
      if (lines.some(l => !l.name.trim())) { addToast('Every line needs a name', 'error'); return; }
      if (lines.some(l => l.component_type === 'index' && !l.commodity_id)) {
        addToast('Index lines need a commodity index', 'error'); return;
      }
      if (lines.some(l => l.component_type === 'formula' && !l.input_template_id)) {
        addToast('Formula lines need an input formula', 'error'); return;
      }
      if (!sumOk) { addToast(`Weights must sum to 100 (currently ${linesSum.toFixed(2)})`, 'error'); return; }
    }
    setSaving(true);
    try {
      const payload = {
        team_id: scope === 'platform' ? null : activeTeamId,
        name: name.trim(),
        description: description.trim() || null,
        expression: expression.trim() || null,
        variables: Object.keys(vars).length > 0 ? normalizeVarMap(vars) : null,
      };
      let res;
      if (isNew) {
        res = await api.post('/api/formulas/', payload);
      } else {
        res = await api.put(`/api/formulas/${template.id}`, payload);
      }
      if (mode === 'weighted') {
        await api.put(`/api/formulas/${res.data.id}/components`, {
          components: lines.map((l, i) => ({
            name: l.name.trim(),
            component_type: l.component_type,
            commodity_id: l.component_type === 'index' ? l.commodity_id : null,
            input_template_id: l.component_type === 'formula' ? l.input_template_id : null,
            weight_pct: parseFloat(l.weight_pct) || 0,
            is_proxy: !!l.is_proxy,
            sort_order: i,
          })),
        });
      }
      addToast(isNew ? 'Formula created' : 'Formula updated', 'success');
      onSaved(res.data);
    } catch (e) {
      addToast(e?.response?.data?.detail || 'Save failed', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
      zIndex: 100, paddingTop: 60, overflowY: 'auto',
    }}>
      <div style={{
        background: 'var(--surface)', borderRadius: 12, padding: 28,
        width: '100%', maxWidth: 560, boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
        margin: '0 16px 60px',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>
            {isNew ? 'New Formula' : 'Edit Formula'}
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 16, color: 'var(--muted)',
          }}>✕</button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label className="ca-label">Name *</label>
            <input className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Resin cost model" />
          </div>

          <div>
            <label className="ca-label">Description</label>
            <input className="ca-input" value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional description" />
          </div>

          {showScopeToggle && (
            <div>
              <label className="ca-label">Scope</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {['team', 'platform'].map(s => (
                  <button
                    key={s}
                    onClick={() => setScope(s)}
                    style={{
                      padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                      border: `1px solid ${scope === s ? 'var(--accent)' : 'var(--border)'}`,
                      background: scope === s ? 'var(--accent)' : 'transparent',
                      color: scope === s ? '#fff' : 'var(--text)',
                      fontWeight: scope === s ? 600 : 400,
                    }}
                  >
                    {s === 'platform' ? 'Default (all teams)' : 'Team only'}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div>
            <label className="ca-label">Defined by</label>
            <div style={{ display: 'flex', gap: 8 }}>
              {[['expression', 'Expression'], ['weighted', 'Weighted lines']].map(([m, label]) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  style={{
                    padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                    border: `1px solid ${mode === m ? 'var(--accent)' : 'var(--border)'}`,
                    background: mode === m ? 'var(--accent)' : 'transparent',
                    color: mode === m ? 'var(--on-accent)' : 'var(--text)',
                    fontWeight: mode === m ? 600 : 400,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {mode === 'weighted' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <label className="ca-label" style={{ marginBottom: 0 }}>Weighted lines *</label>
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 600,
                  color: lines.length === 0 ? 'var(--muted)' : sumOk ? 'var(--accent)' : 'var(--accent3)',
                }}>
                  Σ {linesSum.toFixed(2)}%
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
                Each line explains a share of the cost — weights must sum to 100.
                Mark a line proxy when it leans on a stand-in index.
              </div>
              {lines.map((l, i) => (
                <div key={i} style={{
                  display: 'grid', gridTemplateColumns: '1fr 82px 1fr 64px 24px 20px',
                  gap: 6, marginBottom: 6, alignItems: 'center',
                }}>
                  <input className="ca-input" style={{ padding: '6px 8px', fontSize: 11 }}
                    placeholder="Line name" value={l.name}
                    onChange={e => updateLine(i, { name: e.target.value })} />
                  <select className="ca-select" style={{ fontSize: 11, padding: '6px 6px' }}
                    value={l.component_type}
                    onChange={e => updateLine(i, {
                      component_type: e.target.value, commodity_id: null, input_template_id: null,
                    })}>
                    <option value="index">Index</option>
                    <option value="fixed">Fixed</option>
                    <option value="formula">Formula</option>
                  </select>
                  {l.component_type === 'index' ? (
                    <select className="ca-select" style={{ fontSize: 11, padding: '6px 6px' }}
                      value={l.commodity_id ?? ''}
                      onChange={e => updateLine(i, { commodity_id: parseInt(e.target.value) || null })}>
                      <option value="">Select index…</option>
                      {commodities.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  ) : l.component_type === 'formula' ? (
                    <select className="ca-select" style={{ fontSize: 11, padding: '6px 6px' }}
                      value={l.input_template_id ?? ''}
                      onChange={e => updateLine(i, { input_template_id: e.target.value || null })}>
                      <option value="">Select formula…</option>
                      {chainableTemplates.map(t => (
                        <option key={t.id} value={t.id}>{t.name}{t.code ? ` (${t.code})` : ''}</option>
                      ))}
                    </select>
                  ) : (
                    <span style={{ fontSize: 10, color: 'var(--muted)', paddingLeft: 4 }}>
                      flat share (margin / conversion)
                    </span>
                  )}
                  <input className="ca-input" type="number" step="0.1" style={{ padding: '6px 8px', fontSize: 11 }}
                    value={l.weight_pct}
                    onChange={e => updateLine(i, { weight_pct: e.target.value })} />
                  <input type="checkbox" checked={!!l.is_proxy} title="Proxy — stand-in index"
                    onChange={e => updateLine(i, { is_proxy: e.target.checked })}
                    style={{ accentColor: 'var(--accent3)', cursor: 'pointer' }} />
                  <button onClick={() => setLines(prev => prev.filter((_, idx) => idx !== i))} style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--accent2)', fontSize: 14, fontWeight: 700,
                  }} aria-label={`Remove line ${l.name || i + 1}`}>×</button>
                </div>
              ))}
              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
                onClick={() => setLines(prev => [...prev, {
                  name: '', component_type: 'index', commodity_id: null,
                  input_template_id: null, weight_pct: '', is_proxy: false,
                }])}>
                + Add line
              </button>
            </div>
          )}

          {mode === 'expression' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <label className="ca-label" style={{ marginBottom: 0 }}>Expression *</label>
              <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }} onClick={detectVars}>
                Detect Variables
              </button>
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
              The result is the should-cost directly — embed margin in the expression. Use square or round brackets.
            </div>
            <textarea
              className="ca-input"
              rows={3}
              style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, width: '100%', resize: 'vertical', boxSizing: 'border-box' }}
              placeholder="e.g. 0.92*[(0.75*ACN+1500)*(1-h)+h*AA/0.8]+FC"
              value={expression}
              onChange={e => setExpression(e.target.value)}
            />
          </div>
          )}

          {mode === 'expression' && Object.keys(vars).length > 0 && (
            <div>
              <label className="ca-label">Variables</label>
              <div style={{ display: 'grid', gridTemplateColumns: '90px 80px 1fr 28px', gap: 6, marginBottom: 6 }}>
                <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Variable</span>
                <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Type</span>
                <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Index / Value</span>
                <span></span>
              </div>
              {Object.entries(vars).map(([varName, def]) => (
                <div key={varName} style={{ display: 'grid', gridTemplateColumns: '90px 80px 1fr 28px', gap: 6, marginBottom: 6, alignItems: 'center' }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{varName}</span>
                  <select className="ca-select" style={{ fontSize: 11, padding: '6px 8px' }}
                    value={def.type} onChange={e => updateVar(varName, 'type', e.target.value)}>
                    <option value="fixed">Fixed</option>
                    <option value="index">Index</option>
                  </select>
                  {def.type === 'fixed' ? (
                    <NumberInput style={{ padding: '6px 8px', fontSize: 11 }}
                      value={def.value}
                      onChange={v => updateVar(varName, 'value', v)} />
                  ) : (
                    <IndexCombo
                      value={def.commodity_id ?? null}
                      commodities={commodities}
                      onChange={id => updateVar(varName, 'commodity_id', id)}
                    />
                  )}
                  <button onClick={() => removeVar(varName)} style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--accent2)', fontSize: 14, fontWeight: 700,
                  }}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 24 }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : (isNew ? 'Create' : 'Save')}
          </button>
        </div>
      </div>
    </div>
  );
}
