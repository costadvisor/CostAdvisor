import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import RegionSelect from './RegionSelect';

// Mirrors CONF_BADGE on the Formulas page — kept local so the modal stays
// drop-in usable from other pages later.
const CONF_BADGE = {
  'CONF-HIGH': { bg: 'var(--success-bg)', color: 'var(--accent)', label: 'HIGH' },
  'CONF-MED': { bg: 'var(--info-bg)', color: 'var(--accent4)', label: 'MED' },
  'CONF-LOW': { bg: 'var(--warn-bg)', color: 'var(--accent3)', label: 'LOW · REVIEW' },
};

const TIER_LABEL = {
  free: 'free', good_proxy: 'good proxy', weak_proxy: 'weak proxy', blocked: 'blocked',
};

// Coverage tier = the worst retrieval tier among the recipe's index inputs.
const TIER_TITLE = {
  free: 'Every index input has a direct, free public feed (World Bank, EIA, Eurostat…)',
  good_proxy: 'At least one input is derived via a reliable stand-in relationship (e.g. Brent + a stable spread) — directionally trustworthy, not the exact traded price',
  weak_proxy: 'At least one input leans on a loose stand-in — treat movements as indicative only',
  blocked: 'An input has no viable free source (subscription-only) — this formula cannot be priced without licensed data',
};

const mono = { fontFamily: "'JetBrains Mono', monospace" };

function Stat({ label, children }) {
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', minWidth: 90 }}>
      <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: 0.8, color: 'var(--muted)', marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ ...mono, fontSize: 12, color: 'var(--text)' }}>{children}</div>
    </div>
  );
}

/**
 * Read-and-review view of one formula: the resolved weighted recipe per
 * region (chained formulas flattened, proxy lines marked), the combo pricing
 * (margin / base price / confidence / coverage tier), and the expert-review
 * flow for CONF-LOW placeholder rows. Editors can also manage the per-region
 * pricing rows here.
 */
export default function FormulaDetailModal({ template, activeTeamId, canEdit, onClose, addToast }) {
  const now = new Date();
  const [coverage, setCoverage] = useState([]);
  const [region, setRegion] = useState(null);
  const [resolved, setResolved] = useState(null);
  const [evalPeriod, setEvalPeriod] = useState({
    year: now.getFullYear(), quarter: Math.floor(now.getMonth() / 3) + 1,
  });
  const [evaluation, setEvaluation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [resolving, setResolving] = useState(false);
  const [editingPricing, setEditingPricing] = useState(false);
  const [pricingForm, setPricingForm] = useState({});
  const [addRegionCode, setAddRegionCode] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [busy, setBusy] = useState(false);

  const teamParam = { team_id: activeTeamId };

  const loadCoverage = useCallback(async (selectFirst) => {
    const res = await api.get(`/api/formulas/${template.id}/coverage`, { params: teamParam });
    setCoverage(res.data);
    if (selectFirst) {
      // Europe is the catalog's most complete region — a sensible default view.
      const codes = res.data.map(c => c.region);
      setRegion(codes.includes('Europe') ? 'Europe' : (codes[0] || 'Europe'));
    }
    return res.data;
  }, [template.id, activeTeamId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await loadCoverage(true);
      } catch (e) {
        if (alive) addToast(formatApiError(e), 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [loadCoverage]);

  useEffect(() => {
    if (!region) return;
    let alive = true;
    setResolving(true);
    api.get(`/api/formulas/${template.id}/resolve`, { params: { ...teamParam, region } })
      .then(res => { if (alive) setResolved(res.data); })
      .catch(e => { if (alive) addToast(formatApiError(e), 'error'); })
      .finally(() => { if (alive) setResolving(false); });
    return () => { alive = false; };
  }, [region, template.id]);

  useEffect(() => {
    if (!region) return;
    let alive = true;
    api.get(`/api/formulas/${template.id}/evaluate`, {
      params: { ...teamParam, region, year: evalPeriod.year, quarter: evalPeriod.quarter },
    })
      .then(res => { if (alive) setEvaluation(res.data); })
      .catch(() => { if (alive) setEvaluation(null); });
    return () => { alive = false; };
  }, [region, template.id, evalPeriod, coverage]);

  const cov = coverage.find(c => c.region === region) || null;
  const meta = template.catalog_meta || {};
  const confidence = cov?.data_confidence || meta.data_confidence;
  const confStyle = CONF_BADGE[confidence];
  const lines = resolved?.lines || [];
  const total = lines.reduce((s, l) => s + l.effective_weight_pct, 0);
  const linesRegion = lines.length > 0 ? lines[0].line_region : null;
  const showEval = !!(evaluation?.evaluable);
  const evalByComponent = showEval
    ? Object.fromEntries(evaluation.lines.map(l => [l.component_id, l]))
    : {};

  const refreshCoverage = async () => {
    const data = await loadCoverage(false);
    if (!data.some(c => c.region === region)) {
      setRegion(data[0]?.region || 'Europe');
    }
  };

  const startEditPricing = () => {
    setPricingForm({
      base_price: cov?.base_price ?? '',
      currency: cov?.currency ?? '',
      margin_pct: cov?.margin_pct ?? '',
      base_year: cov?.base_year ?? '',
      base_quarter: cov?.base_quarter ?? '',
    });
    setEditingPricing(true);
  };

  const savePricing = async () => {
    setBusy(true);
    try {
      const f = pricingForm;
      await api.put(`/api/formulas/${template.id}/coverage/${region}`, {
        base_price: f.base_price === '' ? null : parseFloat(f.base_price),
        currency: f.currency ? f.currency.toUpperCase() : null,
        margin_pct: f.margin_pct === '' ? null : parseFloat(f.margin_pct),
        base_year: f.base_year === '' ? null : parseInt(f.base_year, 10),
        base_quarter: f.base_quarter === '' ? null : parseInt(f.base_quarter, 10),
      });
      await refreshCoverage();
      setEditingPricing(false);
      addToast('Pricing saved', 'success');
    } catch (e) {
      addToast(formatApiError(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const addRegion = async () => {
    if (!addRegionCode) return;
    setBusy(true);
    try {
      await api.put(`/api/formulas/${template.id}/coverage/${addRegionCode}`, {});
      await loadCoverage(false);
      setRegion(addRegionCode);
      setAddRegionCode('');
      addToast('Region added — set its pricing', 'success');
    } catch (e) {
      addToast(formatApiError(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const removeRegion = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/formulas/${template.id}/coverage/${region}`);
      setConfirmRemove(false);
      await refreshCoverage();
      addToast('Region pricing removed', 'success');
    } catch (e) {
      addToast(formatApiError(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const markReviewed = async () => {
    setBusy(true);
    try {
      await api.post(`/api/formulas/${template.id}/coverage/${region}/review`);
      await loadCoverage(false);
      addToast('Marked as reviewed', 'success');
    } catch (e) {
      addToast(formatApiError(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const correction = cov?.review_metadata?.correction_plan;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'var(--backdrop)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        zIndex: 100, paddingTop: 48, overflowY: 'auto',
      }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Formula detail: ${template.name}`}
        style={{
          background: 'var(--surface)', borderRadius: 12, padding: 24,
          width: '100%', maxWidth: 720, boxShadow: 'var(--shadow-popover)',
          margin: '0 16px 60px', border: '1px solid var(--border)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 15, fontWeight: 700 }}>{template.name}</span>
              {template.code && (
                <span style={{
                  ...mono, fontSize: 10, color: 'var(--text-secondary)',
                  background: 'var(--surface2)', padding: '2px 7px', borderRadius: 4,
                }}>
                  {template.code}
                </span>
              )}
              {confStyle && (
                <span className="ca-badge" style={{ background: confStyle.bg, color: confStyle.color, fontWeight: 600 }}>
                  {confStyle.label}
                </span>
              )}
            </div>
            {(template.family_name || template.subfamily_name) && (
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>
                {[template.family_name, template.subfamily_name].filter(Boolean).join(' → ')}
              </div>
            )}
          </div>
          <button onClick={onClose} aria-label="Close" style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 16, color: 'var(--muted)', padding: 4,
          }}>✕</button>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>Loading…</div>
        ) : (
          <>
            {/* Region pills */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
              {coverage.map(c => (
                <button
                  key={c.region}
                  onClick={() => { setRegion(c.region); setEditingPricing(false); setConfirmRemove(false); }}
                  style={{
                    ...mono, padding: '5px 12px', borderRadius: 20, fontSize: 11, cursor: 'pointer',
                    border: `1px solid ${region === c.region ? 'var(--border-light)' : 'var(--border)'}`,
                    background: region === c.region ? 'var(--surface3)' : 'transparent',
                    color: region === c.region ? 'var(--text)' : 'var(--text-secondary)',
                    fontWeight: region === c.region ? 600 : 400,
                  }}
                >
                  {c.region}
                  {c.needs_review && <span style={{ color: 'var(--accent3)' }}> •</span>}
                </button>
              ))}
              {coverage.length === 0 && (
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>No regional pricing yet.</span>
              )}
              {canEdit && (
                <span style={{ display: 'inline-flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
                  <RegionSelect
                    value={addRegionCode}
                    onChange={setAddRegionCode}
                    includeEmpty
                    emptyLabel="Add region…"
                    className="ca-select"
                  />
                  <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={addRegion}
                    disabled={!addRegionCode || busy} style={{ fontSize: 10 }}>
                    Add
                  </button>
                </span>
              )}
            </div>

            {/* Combo pricing */}
            {cov && !editingPricing && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14, alignItems: 'stretch' }}>
                <Stat label="Margin">{cov.margin_pct != null ? `${cov.margin_pct}%` : '—'}</Stat>
                <Stat label="Base price">
                  {cov.base_price != null ? `${cov.base_price} ${cov.currency || ''}`.trim() : '—'}
                </Stat>
                <Stat label="Base period">
                  {cov.base_year ? `Q${cov.base_quarter} ${cov.base_year}` : '—'}
                </Stat>
                <Stat label="Coverage">
                  <span
                    title={TIER_TITLE[cov.coverage_tier]}
                    style={{ color: cov.coverage_tier === 'blocked' ? 'var(--accent2)' : 'var(--text)' }}
                  >
                    {TIER_LABEL[cov.coverage_tier] || '—'}
                  </span>
                </Stat>
                {canEdit && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }} onClick={startEditPricing}>
                      Edit pricing
                    </button>
                    {confirmRemove ? (
                      <button className="ca-btn ca-btn-sm" disabled={busy}
                        style={{ fontSize: 10, background: 'var(--accent2)', color: '#fff', border: 'none' }}
                        onClick={removeRegion}>
                        Confirm remove
                      </button>
                    ) : (
                      <button className="ca-btn ca-btn-ghost ca-btn-sm"
                        style={{ fontSize: 10, color: 'var(--accent2)' }}
                        onClick={() => setConfirmRemove(true)}>
                        Remove
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}

            {cov && editingPricing && (
              <div style={{
                display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14, alignItems: 'flex-end',
                background: 'var(--surface2)', borderRadius: 8, padding: 12,
              }}>
                {[
                  { key: 'base_price', label: 'Base price', width: 110, type: 'number' },
                  { key: 'currency', label: 'Currency', width: 70, type: 'text', placeholder: 'EUR' },
                  { key: 'margin_pct', label: 'Margin %', width: 90, type: 'number' },
                  { key: 'base_year', label: 'Base year', width: 90, type: 'number', placeholder: '2025' },
                  { key: 'base_quarter', label: 'Base Q', width: 70, type: 'number', placeholder: '1' },
                ].map(f => (
                  <div key={f.key} style={{ width: f.width }}>
                    <label className="ca-label" style={{ fontSize: 9 }}>{f.label}</label>
                    <input className="ca-input" type={f.type} placeholder={f.placeholder}
                      style={{ padding: '6px 8px', fontSize: 11 }}
                      value={pricingForm[f.key]}
                      onChange={e => setPricingForm(prev => ({ ...prev, [f.key]: e.target.value }))} />
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ fontSize: 10 }}
                    onClick={() => setEditingPricing(false)} disabled={busy}>
                    Cancel
                  </button>
                  <button className="ca-btn ca-btn-primary ca-btn-sm" style={{ fontSize: 10 }}
                    onClick={savePricing} disabled={busy}>
                    {busy ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </div>
            )}

            {/* Review panel — a CONF-LOW combo is a placeholder, not a fact */}
            {cov?.needs_review && (
              <div style={{
                background: 'var(--warn-bg)', border: '1px solid var(--accent3)',
                borderRadius: 8, padding: '12px 14px', marginBottom: 14,
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent3)', marginBottom: 4 }}>
                  Placeholder recipe — pending expert review
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: correction ? 8 : 0 }}>
                  Weights were closed by proportional scaling, not verified process chemistry.
                  Don't quote this number in a negotiation until it's signed off.
                </div>
                {correction && (
                  <div style={{ ...mono, fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8 }}>
                    <span style={{ color: 'var(--muted)' }}>correction · </span>
                    {correction.action} “{correction.label}” at {correction.weight}%
                    {correction.note && (
                      <div style={{ marginTop: 4, fontSize: 10, color: 'var(--muted)', fontFamily: 'inherit' }}>
                        {correction.note}
                      </div>
                    )}
                  </div>
                )}
                {canEdit && (
                  <button className="ca-btn ca-btn-sm" disabled={busy} onClick={markReviewed}
                    style={{ background: 'var(--accent3)', color: 'var(--on-accent)', border: 'none', fontSize: 10, fontWeight: 700 }}>
                    {busy ? '…' : 'Mark as reviewed'}
                  </button>
                )}
              </div>
            )}
            {cov && !cov.needs_review && cov.reviewed_by && (
              <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 12, ...mono }}>
                Reviewed by {cov.reviewed_by}
                {cov.reviewed_at ? ` · ${new Date(cov.reviewed_at).toLocaleDateString()}` : ''}
              </div>
            )}

            {/* Should-cost evaluation */}
            <div style={{
              background: 'var(--surface2)', borderRadius: 8, padding: '12px 14px', marginBottom: 14,
              display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
            }}>
              <div style={{ flex: 1, minWidth: 160 }}>
                <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: 0.8, color: 'var(--muted)', marginBottom: 3 }}>
                  Should-cost
                </div>
                {evaluation?.evaluable && evaluation.should_cost != null ? (
                  <div>
                    <span style={{ ...mono, fontSize: 20, fontWeight: 700 }}>
                      {evaluation.should_cost.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </span>
                    <span style={{ ...mono, fontSize: 12, color: 'var(--text-secondary)', marginLeft: 6 }}>
                      {evaluation.currency || ''}
                    </span>
                  </div>
                ) : evaluation?.evaluable ? (
                  <div style={{ ...mono, fontSize: 13, color: 'var(--text-secondary)' }}>
                    index only — no base price anchor
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                    {evaluation?.reason || 'Not evaluable'}
                    {canEdit && evaluation?.reason?.includes('base') && (
                      <span> — set it under “Edit pricing”.</span>
                    )}
                  </div>
                )}
                {evaluation?.evaluable && (
                  <div style={{ ...mono, fontSize: 10, marginTop: 3 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      index {evaluation.index_level_pct.toFixed(1)}
                    </span>
                    <span style={{
                      marginLeft: 6,
                      color: evaluation.index_level_pct > 100 ? 'var(--accent2)'
                        : evaluation.index_level_pct < 100 ? 'var(--accent)' : 'var(--muted)',
                    }}>
                      {evaluation.index_level_pct === 100 ? '=' : (evaluation.index_level_pct > 100 ? '+' : '')}
                      {(evaluation.index_level_pct - 100).toFixed(1)}%
                    </span>
                    <span style={{ color: 'var(--muted)' }}>
                      {' '}vs base Q{evaluation.base_quarter} {evaluation.base_year}
                    </span>
                  </div>
                )}
                {evaluation?.data_gaps?.length > 0 && (
                  <div title={evaluation.data_gaps.map(g => `${g.line}: ${g.reason}`).join('\n')}
                    style={{ fontSize: 10, color: 'var(--accent3)', marginTop: 4 }}>
                    ⚠ {evaluation.data_gaps.length} line{evaluation.data_gaps.length > 1 ? 's' : ''} without
                    index data — riding flat
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select className="ca-select" style={{ fontSize: 11, padding: '6px 8px', width: 'auto' }}
                  aria-label="Evaluation quarter"
                  value={evalPeriod.quarter}
                  onChange={e => setEvalPeriod(p => ({ ...p, quarter: parseInt(e.target.value, 10) }))}>
                  {[1, 2, 3, 4].map(q => <option key={q} value={q}>Q{q}</option>)}
                </select>
                <select className="ca-select" style={{ fontSize: 11, padding: '6px 8px', width: 'auto' }}
                  aria-label="Evaluation year"
                  value={evalPeriod.year}
                  onChange={e => setEvalPeriod(p => ({ ...p, year: parseInt(e.target.value, 10) }))}>
                  {Array.from({ length: now.getFullYear() + 2 - 2020 }, (_, i) => 2020 + i).map(y => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Resolved recipe */}
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.8, color: 'var(--muted)' }}>
                Weighted recipe
              </div>
              {linesRegion && linesRegion !== region && (
                <span style={{ fontSize: 10, color: 'var(--accent3)', ...mono }}>
                  resolved at {linesRegion} (no {region} recipe)
                </span>
              )}
            </div>

            {resolving ? (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>Resolving…</div>
            ) : lines.length === 0 ? (
              <div style={{
                padding: '16px 14px', border: '1px dashed var(--border)', borderRadius: 8,
                fontSize: 11, color: 'var(--muted)',
              }}>
                No weighted lines for this formula{template.expression ? ' — it uses an expression:' : '.'}
                {template.expression && (
                  <div style={{ ...mono, fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>
                    {template.expression}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                <table className="ca-table" style={{ margin: 0 }}>
                  <thead>
                    <tr>
                      <th>Line</th>
                      <th>Source</th>
                      <th style={{ textAlign: 'right' }}>Weight</th>
                      <th style={{ textAlign: 'right' }}>Effective</th>
                      {showEval && <th style={{ textAlign: 'right' }}>× Index</th>}
                      {showEval && (
                        <th style={{ textAlign: 'right' }}>
                          {evaluation.should_cost != null ? `Contribution ${evaluation.currency || ''}`.trim() : 'Contribution'}
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map(l => {
                      const ev = evalByComponent[l.component_id];
                      return (
                      <tr key={l.component_id}>
                        <td style={{ paddingLeft: l.depth > 0 ? 28 : 10 }}>
                          <span style={{ fontSize: 12, fontWeight: l.depth === 0 ? 600 : 400 }}>
                            {l.depth > 0 && <span style={{ color: 'var(--muted)' }}>↳ </span>}
                            {l.name}
                          </span>
                          {l.depth > 0 && l.via_template_name && (
                            <span style={{ fontSize: 10, color: 'var(--muted)' }}> via {l.via_template_name}</span>
                          )}
                        </td>
                        <td style={{ ...mono, fontSize: 11 }}>
                          <span style={{ color: 'var(--text-secondary)' }}>
                            {l.component_type === 'fixed' ? 'fixed' : (l.commodity_name || '—')}
                          </span>
                          {l.is_proxy && (
                            <span title="Priced via a stand-in index — a softer signal than an exact feed"
                              style={{
                                marginLeft: 6, fontSize: 9, fontWeight: 700, color: 'var(--accent3)',
                                background: 'var(--warn-bg)', padding: '1px 5px', borderRadius: 4,
                              }}>
                              PROXY
                            </span>
                          )}
                        </td>
                        <td style={{ ...mono, fontSize: 11, textAlign: 'right', color: 'var(--text-secondary)' }}>
                          {l.weight_pct.toFixed(1)}%
                        </td>
                        <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>
                          {l.effective_weight_pct.toFixed(2)}%
                        </td>
                        {showEval && (
                          <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}
                            title={ev && ev.base_value != null
                              ? `${ev.base_value} → ${ev.current_value}` : undefined}>
                            {ev ? (
                              ev.has_data || l.component_type === 'fixed' ? (
                                <span style={{
                                  color: ev.ratio > 1 ? 'var(--accent2)'
                                    : ev.ratio < 1 ? 'var(--accent)' : 'var(--text-secondary)',
                                }}>
                                  {ev.ratio.toFixed(3)}
                                </span>
                              ) : (
                                <span title="No index data — line rides flat"
                                  style={{ color: 'var(--accent3)' }}>flat</span>
                              )
                            ) : '—'}
                          </td>
                        )}
                        {showEval && (
                          <td style={{ ...mono, fontSize: 11, textAlign: 'right' }}>
                            {ev
                              ? (ev.contribution_abs != null
                                ? ev.contribution_abs.toLocaleString(undefined, { maximumFractionDigits: 2 })
                                : `${ev.contribution_pct.toFixed(2)}%`)
                              : '—'}
                          </td>
                        )}
                      </tr>
                      );
                    })}
                    <tr>
                      <td colSpan={3} style={{ fontSize: 11, fontWeight: 600, textAlign: 'right', color: 'var(--text-secondary)' }}>
                        Σ{cov?.margin_pct != null ? ` (margin ${cov.margin_pct}% is a line above)` : ''}
                      </td>
                      <td style={{ ...mono, fontSize: 12, fontWeight: 700, textAlign: 'right' }}>
                        {total.toFixed(2)}%
                      </td>
                      {showEval && <td />}
                      {showEval && (
                        <td style={{ ...mono, fontSize: 12, fontWeight: 700, textAlign: 'right' }}>
                          {evaluation.should_cost != null
                            ? evaluation.should_cost.toLocaleString(undefined, { maximumFractionDigits: 2 })
                            : `${evaluation.index_level_pct.toFixed(2)}%`}
                        </td>
                      )}
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
