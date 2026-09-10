import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { OVC_ITEMS, RM_ITEMS, PIE_COLORS, INCOTERMS } from '../utils/constants';
import DonutChart from '../components/DonutChart';
import IncotermAdjustments from '../components/IncotermAdjustments';
import RegionSelect from '../components/RegionSelect';
import api, { formatApiError } from '../api';
import { useConfirm, useAlert } from '../components/ConfirmDialog';
import { useToast } from '../components/Toast';
import { useAuth } from '../AuthContext';
import { stripReservedFns } from '../utils/formulaFns';
import NumberInput from '../components/NumberInput';
import { normalizeVarMap } from '../components/VariableMapEditor';
import IndexCombo from '../components/IndexCombo';

export default function CostModelBuilder() {
  const { costModelId } = useParams();
  const navigate = useNavigate();
  const { activeTeamId, user } = useAuth();
  const confirm = useConfirm();
  const showAlert = useAlert();
  const { addToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(!costModelId);
  const [editing, setEditing] = useState(!costModelId);
  const [loadError, setLoadError] = useState(null);
  const [justCreated, setJustCreated] = useState(false);

  const [products, setProducts] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [commodities, setCommodities] = useState([]);

  // Product fields
  const [productId, setProductId] = useState('');
  const [productName, setProductName] = useState('');
  const [formula, setFormula] = useState('');
  const [activeContent, setActiveContent] = useState(0.65);
  const [unit, setUnit] = useState('kg');

  // Cost model fields
  const [supplierId, setSupplierId] = useState('');
  const [newSupplierName, setNewSupplierName] = useState('');
  const [destinationCountry, setDestinationCountry] = useState('');
  const [destinationRegion, setDestinationRegion] = useState('');
  const [region, setRegion] = useState('Europe');
  const [currency, setCurrency] = useState('USD');
  const [incoterm, setIncoterm] = useState('');

  // Formula version fields
  const [basePrice, setBasePrice] = useState(3.0);
  const [baseYear, setBaseYear] = useState(2024);
  const [baseQuarter, setBaseQuarter] = useState(1);
  const [marginType, setMarginType] = useState('pct');
  const [marginValue, setMarginValue] = useState(20);
  const [versionIncoterm, setVersionIncoterm] = useState('');
  const [versionNamedPlace, setVersionNamedPlace] = useState('');
  const [landedCostAdjustments, setLandedCostAdjustments] = useState(null);
  const [components, setComponents] = useState([]);

  // Catalog link (Scrum 28b) — which combo this version was priced from, and
  // whether it's frozen ("pinned") or recomputed live from the catalog recipe
  // on every evaluation ("tracking"). Both null = not catalog-linked.
  const [sourceCoverageId, setSourceCoverageId] = useState(null);
  const [linkMode, setLinkMode] = useState(null);
  const [linkedTemplateName, setLinkedTemplateName] = useState(null);

  // Advanced formula mode
  const [formulaMode, setFormulaMode] = useState('simple'); // 'simple' | 'advanced'
  const [advancedExpression, setAdvancedExpression] = useState('');
  const [advancedVars, setAdvancedVars] = useState({});
  const [showLandedCost, setShowLandedCost] = useState(true);

  // Formula template picker / saver
  const [formulaTemplates, setFormulaTemplates] = useState([]);
  const [showTemplateDropdown, setShowTemplateDropdown] = useState(false);
  // Last catalog template loaded into this model — persisted onto the product
  // at save so future models for it auto-load the same recipe.
  const loadedTemplateIdRef = useRef(null);
  const autoLoadedTemplateRef = useRef(false);
  const [showSaveTemplate, setShowSaveTemplate] = useState(false);
  const [canEditPlatform, setCanEditPlatform] = useState(false);
  const templateDropdownRef = useRef(null);

  const membership = user?.memberships?.find(m => m.team_id === activeTeamId);
  const canEditTeam = membership?.role === 'owner' || membership?.role === 'admin';

  // Snapshot for cancel
  const [snapshot, setSnapshot] = useState(null);

  // Resolved supplier name for view mode
  const supplierName = useMemo(() => {
    if (!supplierId) return newSupplierName || null;
    const s = suppliers.find(s => String(s.id) === String(supplierId));
    return s ? s.name : null;
  }, [supplierId, newSupplierName, suppliers]);

  // Load reference data
  useEffect(() => {
    if (!activeTeamId) return;
    Promise.all([
      api.get('/api/products', { params: { team_id: activeTeamId } }),
      api.get('/api/suppliers', { params: { team_id: activeTeamId } }),
      // Full index list — catalog formulas reference commodities that don't
      // carry values yet; filtering to has_data would blank their rows.
      api.get('/api/indexes'),
      api.get('/api/formulas/', { params: { team_id: activeTeamId } }).catch(() => ({ data: [] })),
      api.get('/api/formulas/can-edit-platform').catch(() => ({ data: { can_edit: false } })),
    ]).then(([pRes, sRes, iRes, tmplRes, permRes]) => {
      setProducts(pRes.data);
      setSuppliers(sRes.data);
      setCommodities(iRes.data);
      setFormulaTemplates(tmplRes.data);
      setCanEditPlatform(permRes.data.can_edit);
    }).catch(() => setLoadError('Could not load reference data. Try reloading the page.'));
  }, [activeTeamId]);

  // Preselect a product when arriving from Portfolio's draft "Complete formula"
  // action (route state), so completing a draft attaches to the existing product
  // instead of creating a near-duplicate.
  useEffect(() => {
    if (costModelId) return;
    const pid = location.state?.productId;
    if (!pid || !products.length) return;
    const p = products.find(pp => pp.id === pid);
    if (!p) return;
    setProductId(p.id);
    setProductName(p.name);
    setFormula(p.formula || '');
    setUnit(p.unit || 'kg');
    setActiveContent(p.active_content ?? 0.65);
    // A catalog-linked product auto-loads its recipe at this model's region
    // (Scrum 58: "creating a product auto-loads the template by formula × region").
    if (p.formula_template_id && formulaTemplates.length && !autoLoadedTemplateRef.current) {
      const t = formulaTemplates.find(ft => ft.id === p.formula_template_id);
      if (t) {
        autoLoadedTemplateRef.current = true;
        loadTemplateIntoModel(t);
      }
    }
  }, [products, formulaTemplates, location.state, costModelId]);

  // Close template dropdown on outside click
  useEffect(() => {
    if (!showTemplateDropdown) return;
    const handler = (e) => {
      if (templateDropdownRef.current && !templateDropdownRef.current.contains(e.target)) {
        setShowTemplateDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showTemplateDropdown]);

  // Load existing cost model
  useEffect(() => {
    if (!costModelId) return;
    api.get(`/api/cost-models/${costModelId}`)
      .then(({ data }) => {
        setProductId(data.product_id);
        setProductName(data.product_name || '');
        setFormula(data.product_reference || '');
        setUnit(data.product_unit || 'kg');
        setActiveContent(data.product_active_content ?? 0.65);
        setSupplierId(data.supplier_id || '');
        setDestinationCountry(data.destination_country || '');
        setDestinationRegion(data.destination_region || '');
        setRegion(data.region);
        setCurrency(data.currency);
        setIncoterm(data.incoterm || '');

        const currentFv = data.formula_versions?.[0];
        if (currentFv) {
          setBasePrice(currentFv.base_price);
          setBaseYear(currentFv.base_year);
          setBaseQuarter(currentFv.base_quarter);
          setMarginType(currentFv.margin_type === 'pct' || currentFv.margin_type === 'fixed' ? currentFv.margin_type : 'pct');
          setMarginValue(currentFv.margin_value ?? 20);
          setVersionIncoterm(currentFv.incoterm || '');
          setVersionNamedPlace(currentFv.named_place || '');
          setLandedCostAdjustments(currentFv.landed_cost_adjustments || null);

          setComponents(currentFv.components.map(c => ({
            label: c.label,
            commodity_name: c.commodity_name || '',
            commodity_id: c.commodity_id,
            parts: Math.round(c.weight * 100),
            component_type: c.component_type,
            depth: c.depth,
            via_template_id: c.via_template_id,
            line_region: c.line_region,
            is_proxy: c.is_proxy,
          })));
          const ftype = currentFv.formula_type || 'simple';
          setFormulaMode(ftype);
          setAdvancedExpression(currentFv.expression || '');
          setAdvancedVars(currentFv.variables || {});
          setShowLandedCost(ftype === 'simple');
          setSourceCoverageId(currentFv.source_coverage_id || null);
          setLinkMode(currentFv.link_mode || null);
          // The name isn't carried on FormulaVersionOut — a generic
          // "Catalog-linked" label is shown until the recipe is reloaded.
          setLinkedTemplateName(null);
        }
        setLoaded(true);
      })
      .catch(err => { console.error(err); setLoaded(true); });
  }, [costModelId]);

  // Computed
  const totalParts = components.reduce((s, c) => s + (c.parts || 0), 0);
  const marginCost = marginType === 'pct'
    ? basePrice * (marginValue || 0) / 100
    : (marginValue || 0);
  const componentPool = basePrice - marginCost;
  const marginWeight = basePrice > 0 ? marginCost / basePrice : 0;
  const isValid = formulaMode === 'advanced'
    ? (advancedExpression.trim().length > 0 && basePrice > 0)
    : (totalParts > 0 && componentPool >= 0 && basePrice > 0);

  const compWeight = (c) => totalParts > 0 ? (c.parts / totalParts) * (componentPool / basePrice) : 0;
  const compCost = (c) => totalParts > 0 ? (c.parts / totalParts) * componentPool : 0;

  const shouldCost = basePrice;

  const donutSegs = useMemo(() => {
    const segs = components
      .filter(c => c.parts > 0)
      .map((c, i) => ({
        label: c.label,
        pct: totalParts > 0 ? (c.parts / totalParts) * (1 - marginWeight) : 0,
        color: PIE_COLORS[i % PIE_COLORS.length],
      }));
    if (marginWeight > 0) {
      segs.push({ label: 'Margin', pct: marginWeight, color: 'var(--accent2)' });
    }
    return segs;
  }, [components, totalParts, marginWeight]);

  const startEditing = () => {
    setSnapshot({
      productName, formula, activeContent, unit,
      supplierId, newSupplierName, destinationCountry, destinationRegion, region, currency, incoterm,
      basePrice, baseYear, baseQuarter, marginType, marginValue,
      versionIncoterm, versionNamedPlace, landedCostAdjustments,
      formulaMode, advancedExpression, advancedVars: { ...advancedVars }, showLandedCost,
      components: components.map(c => ({ ...c })),
      sourceCoverageId, linkMode, linkedTemplateName,
    });
    setEditing(true);
  };

  const cancelEditing = () => {
    if (snapshot) {
      setProductName(snapshot.productName);
      setFormula(snapshot.formula);
      setActiveContent(snapshot.activeContent);
      setUnit(snapshot.unit);
      setSupplierId(snapshot.supplierId);
      setNewSupplierName(snapshot.newSupplierName);
      setDestinationCountry(snapshot.destinationCountry);
      setDestinationRegion(snapshot.destinationRegion);
      setRegion(snapshot.region);
      setCurrency(snapshot.currency);
      setIncoterm(snapshot.incoterm);
      setBasePrice(snapshot.basePrice);
      setBaseYear(snapshot.baseYear);
      setBaseQuarter(snapshot.baseQuarter);
      setMarginType(snapshot.marginType);
      setMarginValue(snapshot.marginValue);
      setVersionIncoterm(snapshot.versionIncoterm);
      setVersionNamedPlace(snapshot.versionNamedPlace);
      setLandedCostAdjustments(snapshot.landedCostAdjustments);
      setFormulaMode(snapshot.formulaMode);
      setAdvancedExpression(snapshot.advancedExpression);
      setAdvancedVars(snapshot.advancedVars);
      setShowLandedCost(snapshot.showLandedCost);
      setComponents(snapshot.components);
      setSourceCoverageId(snapshot.sourceCoverageId ?? null);
      setLinkMode(snapshot.linkMode ?? null);
      setLinkedTemplateName(snapshot.linkedTemplateName ?? null);
    }
    setEditing(false);
    setSnapshot(null);
  };

  const save = async () => {
    setSaving(true);
    try {
      let pid = productId;
      if (!pid) {
        const { data } = await api.post(`/api/products?team_id=${activeTeamId}`, {
          name: productName,
          formula,
          active_content: activeContent,
          unit,
          // Remember which catalog recipe priced this product, so its next
          // cost model auto-loads the template at that model's region.
          formula_template_id: loadedTemplateIdRef.current || null,
        });
        pid = data.id;
      } else if (loadedTemplateIdRef.current) {
        await api.put(`/api/products/${pid}`, {
          formula_template_id: loadedTemplateIdRef.current,
        });
      }

      let sid = supplierId || null;
      if (!sid && newSupplierName) {
        const { data } = await api.post(`/api/suppliers?team_id=${activeTeamId}`, {
          name: newSupplierName,
        });
        sid = data.id;
        setSuppliers(prev => [...prev, data]);
        setSupplierId(String(data.id));
        setNewSupplierName('');
      }

      const formulaPayload = {
        formula_type: formulaMode,
        base_price: basePrice,
        base_year: baseYear,
        base_quarter: baseQuarter,
        incoterm: versionIncoterm || null,
        named_place: versionNamedPlace || null,
        landed_cost_adjustments: landedCostAdjustments,
        // Scrum 28b — advanced (expression) formulas are never catalog-linked;
        // sourceCoverageId/linkMode are only ever set by loadTemplateIntoModel
        // on a weighted-recipe template, so this is naturally null for them.
        source_coverage_id: sourceCoverageId || null,
        link_mode: sourceCoverageId ? (linkMode || 'pinned') : null,
        ...(formulaMode === 'advanced' ? {
          expression: advancedExpression,
          variables: normalizeVarMap(advancedVars),
        } : {
          margin_type: marginType,
          margin_value: marginValue,
          components: components.map(c => ({
            label: c.label,
            commodity_name: c.commodity_name || null,
            // An explicit id (already resolved via /resolve or a prior load)
            // wins over a fragile exact-name re-match on the backend.
            commodity_id: c.commodity_id ?? null,
            weight: totalParts > 0 ? c.parts / totalParts : 0,
            component_type: c.component_type ?? null,
            depth: c.depth ?? null,
            via_template_id: c.via_template_id ?? null,
            line_region: c.line_region ?? null,
            is_proxy: c.is_proxy ?? null,
          })),
        }),
      };

      const payload = {
        product_id: pid,
        supplier_id: sid ? Number(sid) : null,
        destination_country: destinationCountry || null,
        destination_region: destinationRegion || null,
        region,
        currency,
        incoterm: incoterm || null,
        formula: formulaPayload,
      };

      if (costModelId) {
        if (pid) {
          await api.put(`/api/products/${pid}`, {
            name: productName,
            formula: formula || null,
            active_content: activeContent,
            unit,
          });
        }
        await api.put(`/api/cost-models/${costModelId}`, {
          supplier_id: sid ? Number(sid) : null,
          destination_country: destinationCountry || null,
          destination_region: destinationRegion || null,
          region,
          currency,
          incoterm: incoterm || null,
        });
        await api.post(`/api/cost-models/${costModelId}/renegotiate`, formulaPayload);
        setEditing(false);
        setSnapshot(null);
      } else {
        const { data } = await api.post(`/api/cost-models?team_id=${activeTeamId}`, payload);
        setEditing(false);
        setSnapshot(null);
        setJustCreated(true);
        navigate(`/cost-models/${data.id}`, { replace: true });
      }
    } catch (err) {
      showAlert({ title: 'Error saving', message: formatApiError(err) });
    } finally {
      setSaving(false);
    }
  };

  const detectVars = () => {
    const expr = advancedExpression.replace(/[[\]]/g, '').replace(/\s/g, '');
    const tokens = expr.match(/[a-zA-Z_][a-zA-Z0-9_]*/g) || [];
    const unique = stripReservedFns([...new Set(tokens)]);
    setAdvancedVars(prev => {
      const next = {};
      unique.forEach(name => { next[name] = prev[name] || { type: 'fixed', value: 0 }; });
      return next;
    });
  };

  const updateAdvancedVar = (name, key, val) => {
    setAdvancedVars(prev => ({ ...prev, [name]: { ...prev[name], [key]: val } }));
  };

  const removeAdvancedVar = (name) => {
    setAdvancedVars(prev => { const n = { ...prev }; delete n[name]; return n; });
  };

  const updateComp = (i, key, val) => {
    const next = [...components];
    next[i] = { ...next[i], [key]: val };
    setComponents(next);
  };
  const addComp = () => setComponents([...components, { label: '', commodity_name: '', parts: 0 }]);
  const removeComp = (i) => setComponents(components.filter((_, j) => j !== i));

  // Instantiate a library template into this model. Weighted templates load
  // as simple-mode components (the resolver flattens chained formulas and
  // picks the recipe for this model's region); expression templates keep the
  // existing advanced-mode prefill.
  const loadTemplateIntoModel = async (t) => {
    setShowTemplateDropdown(false);
    try {
      const res = await api.get(`/api/formulas/${t.id}/resolve`, {
        params: { team_id: activeTeamId, region },
      });
      const { lines, coverage: cov, region_resolved } = res.data;
      loadedTemplateIdRef.current = t.id;
      if (lines.length > 0) {
        setFormulaMode('simple');
        setShowLandedCost(true);
        setComponents(lines.map(l => ({
          label: l.name,
          commodity_name: l.commodity_name || '',
          commodity_id: l.commodity_id,
          // Full precision — a prior 1-decimal rounding here fought the
          // parts/totalParts renormalization at save for no reason.
          parts: l.effective_weight_pct,
          component_type: l.component_type,
          depth: l.depth,
          via_template_id: l.via_template_id,
          line_region: l.line_region,
          is_proxy: l.is_proxy,
        })));
        // Catalog recipes carry margin as a fixed line inside the weights —
        // a separate margin on top would double-count it.
        setMarginType('pct');
        setMarginValue(0);
        // Default to pinned — a should-cost estimate can opt into tracking,
        // but a saved formula must not drift out from under you by default.
        setSourceCoverageId(cov?.id ?? null);
        setLinkMode(cov?.id ? 'pinned' : null);
        setLinkedTemplateName(t.name);
        if (cov) {
          if (cov.base_price != null) setBasePrice(cov.base_price);
          if (cov.base_year) { setBaseYear(cov.base_year); setBaseQuarter(cov.base_quarter); }
          if (cov.currency) setCurrency(cov.currency);
        }
        const fallback = region_resolved && region_resolved !== region
          ? ` — pricing from ${region_resolved}` : '';
        addToast(`Loaded ${t.name}${fallback}`, 'success');
        if (cov?.needs_review) {
          addToast('This recipe is a placeholder pending expert review — treat the number as directional', 'info');
        }
        if (!cov || cov.base_price == null) {
          addToast('No base price anchor on this formula — set the starting price manually', 'info');
        }
      } else {
        setFormulaMode('advanced');
        setAdvancedExpression(t.expression || '');
        setAdvancedVars(t.variables || {});
        // Pinned/tracking only applies to weighted-recipe templates.
        setSourceCoverageId(null);
        setLinkMode(null);
        setLinkedTemplateName(null);
      }
    } catch (e) {
      addToast(formatApiError(e), 'error');
    }
  };

  if (!loaded) return <div className="ca-page" style={{ color: 'var(--muted)' }}>Loading...</div>;

  const sym = currency === 'EUR' ? '\u20AC' : '$';

  return (
    <>
    <div className="ca-page ca-fade-in">
      {loadError && (
        <div style={{ background: 'var(--accent2-dim)', border: '1px solid var(--accent2)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13, color: 'var(--accent2)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{loadError}</span>
          <button className="ca-btn-icon" onClick={() => setLoadError(null)} style={{ marginLeft: 12, fontWeight: 700 }}>✕</button>
        </div>
      )}
      {justCreated && (
        <div style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13, color: 'var(--accent)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Model created. Next: go to <button className="ca-btn-link" onClick={() => navigate(`/cost-models/${costModelId}/pricing`)}>Pricing</button> to upload actual prices and see the gap.</span>
          <button className="ca-btn-icon" onClick={() => setJustCreated(false)} style={{ marginLeft: 12, fontWeight: 700 }}>✕</button>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="ca-h1">
          {!costModelId ? 'New Cost Model' : editing ? 'Edit Cost Model' : 'Cost Model'}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {costModelId && (
            <>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/pricing`)}>
                Pricing
              </button>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/evolution`)}>
                Evolution
              </button>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/brief`)}>
                Brief
              </button>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate(`/cost-models/${costModelId}/squeeze`)}>
                Squeeze
              </button>
            </>
          )}
          {editing ? (
            <>
              {costModelId && (
                <button className="ca-btn ca-btn-ghost" onClick={cancelEditing}>Cancel</button>
              )}
              <button className="ca-btn ca-btn-primary" onClick={save} disabled={saving || !isValid}>
                {saving ? 'Saving...' : (costModelId ? 'Save' : 'Create')}
              </button>
            </>
          ) : (
            <button className="ca-btn ca-btn-primary" onClick={startEditing}>Edit</button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22, alignItems: 'start' }}>
        {/* LEFT COLUMN */}
        <div>
          {/* Supplier & Destination */}
          <div className="ca-card" style={{ marginBottom: 16 }}>
            <div className="ca-card-title">Supplier & Destination</div>
            {editing ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label className="ca-label">Supplier</label>
                  <select className="ca-select" value={supplierId} onChange={e => setSupplierId(e.target.value)}>
                    <option value="">— New supplier —</option>
                    {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </div>
                {!supplierId && (
                  <div>
                    <label className="ca-label">New Supplier Name</label>
                    <input className="ca-input" value={newSupplierName} onChange={e => setNewSupplierName(e.target.value)} />
                  </div>
                )}
                <div>
                  <label className="ca-label">Destination Country</label>
                  <input className="ca-input" value={destinationCountry} onChange={e => setDestinationCountry(e.target.value)} />
                </div>
                <div>
                  <label className="ca-label">Destination Region</label>
                  <RegionSelect value={destinationRegion} onChange={setDestinationRegion} includeEmpty />
                </div>
                <div>
                  <label className="ca-label">Producing Region</label>
                  <RegionSelect value={region} onChange={setRegion} />
                </div>
                <div>
                  <label className="ca-label">Currency</label>
                  <select className="ca-select" value={currency} onChange={e => setCurrency(e.target.value)}>
                    {['USD','EUR'].map(c => <option key={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label className="ca-label">Default Incoterm</label>
                  <select className="ca-select" value={incoterm} onChange={e => setIncoterm(e.target.value)}>
                    <option value="">—</option>
                    {INCOTERMS.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label className="ca-label">Supplier</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{supplierName || '\u2014'}</div>
                </div>
                <div>
                  <label className="ca-label">Destination Country</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{destinationCountry || '\u2014'}</div>
                </div>
                <div>
                  <label className="ca-label">Destination Region</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{destinationRegion || '\u2014'}</div>
                </div>
                <div>
                  <label className="ca-label">Producing Region</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{region}</div>
                </div>
                <div>
                  <label className="ca-label">Currency</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{currency}</div>
                </div>
                <div>
                  <label className="ca-label">Default Incoterm</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{incoterm || '\u2014'}</div>
                </div>
              </div>
            )}
          </div>

          {/* Formula */}
          <div className="ca-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div className="ca-card-title" style={{ marginBottom: 0 }}>Formula</div>
              {editing && (
                <div style={{ display: 'flex', gap: 0, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
                  {['simple', 'advanced'].map(mode => (
                    <button
                      key={mode}
                      onClick={() => { setFormulaMode(mode); setShowLandedCost(mode === 'simple'); }}
                      style={{
                        padding: '4px 14px', fontSize: 11, fontWeight: 600, border: 'none', cursor: 'pointer',
                        background: formulaMode === mode ? 'var(--accent)' : 'transparent',
                        color: formulaMode === mode ? '#fff' : 'var(--muted)',
                        textTransform: 'capitalize',
                      }}
                    >{mode}</button>
                  ))}
                </div>
              )}
              {!editing && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {sourceCoverageId && (
                    <span style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 600 }}>
                      {linkedTemplateName ? `Linked to ${linkedTemplateName}` : 'Catalog-linked'} · {linkMode === 'tracking' ? 'Tracking' : 'Pinned'}
                    </span>
                  )}
                  <span style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', fontWeight: 600 }}>
                    {formulaMode}
                  </span>
                </span>
              )}
            </div>
            {editing ? (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                  <div>
                    <label className="ca-label">Total Price ({sym}/unit)</label>
                    <input className="ca-input" type="number" value={basePrice} min={0} step={0.01}
                      onChange={e => setBasePrice(+e.target.value)} />
                  </div>
                  <div>
                    <label className="ca-label">Base Year</label>
                    <select className="ca-select" value={baseYear} onChange={e => setBaseYear(+e.target.value)}>
                      {[2022,2023,2024,2025,2026].map(y => <option key={y} value={y}>{y}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="ca-label">Base Quarter</label>
                    <select className="ca-select" value={baseQuarter} onChange={e => setBaseQuarter(+e.target.value)}>
                      {[1,2,3,4].map(q => <option key={q} value={q}>Q{q}</option>)}
                    </select>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 12, marginBottom: 12 }}>
                  <div>
                    <label className="ca-label">Incoterm (this version)</label>
                    <select className="ca-select" value={versionIncoterm} onChange={e => setVersionIncoterm(e.target.value)}>
                      <option value="">— use default ({incoterm || 'none'}) —</option>
                      {INCOTERMS.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="ca-label">Named Place</label>
                    <input className="ca-input" placeholder="e.g. Rotterdam, Houston" value={versionNamedPlace}
                      onChange={e => setVersionNamedPlace(e.target.value)} />
                  </div>
                </div>

                <div className="ca-card" style={{ marginBottom: 12, padding: 10, background: 'var(--bg)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: showLandedCost ? 8 : 0 }}>
                    <div className="ca-card-title" style={{ fontSize: 11, marginBottom: 0 }}>Landed-Cost Adjustments</div>
                    {formulaMode === 'advanced' && (
                      <span
                        onClick={() => setShowLandedCost(v => !v)}
                        style={{ fontSize: 10, color: 'var(--muted)', cursor: 'pointer', userSelect: 'none', letterSpacing: '0.04em' }}
                      >
                        {showLandedCost ? '▲ hide' : '▼ ex-works'}
                      </span>
                    )}
                  </div>
                  {showLandedCost ? (
                    <IncotermAdjustments
                      value={landedCostAdjustments}
                      onChange={setLandedCostAdjustments}
                      editing={true}
                      originRegion={region}
                      destinationRegion={destinationRegion}
                      currencySym={sym}
                    />
                  ) : null}
                </div>

                {formulaMode === 'simple' ? (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <label className="ca-label" style={{ marginBottom: 0 }}>Components</label>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {sourceCoverageId && (
                          <div style={{ display: 'flex', gap: 0, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}
                            title={`Linked to ${linkedTemplateName || 'a catalog formula'}`}>
                            {['pinned', 'tracking'].map(mode => (
                              <button
                                key={mode}
                                onClick={() => setLinkMode(mode)}
                                style={{
                                  padding: '4px 10px', fontSize: 10, fontWeight: 600, border: 'none', cursor: 'pointer',
                                  background: (linkMode || 'pinned') === mode ? 'var(--accent)' : 'transparent',
                                  color: (linkMode || 'pinned') === mode ? '#fff' : 'var(--muted)',
                                  textTransform: 'capitalize',
                                }}
                              >{mode}</button>
                            ))}
                          </div>
                        )}
                        <div style={{ position: 'relative' }} ref={templateDropdownRef}>
                          <button
                            className="ca-btn ca-btn-ghost ca-btn-sm"
                            style={{ fontSize: 10 }}
                            onClick={() => setShowTemplateDropdown(v => !v)}
                          >
                            Load Catalog Formula ▾
                          </button>
                          {showTemplateDropdown && (
                            <div style={{
                              position: 'absolute', right: 0, top: 'calc(100% + 4px)',
                              background: 'var(--surface)', border: '1px solid var(--border)',
                              borderRadius: 8, boxShadow: 'var(--shadow-popover)',
                              zIndex: 50, minWidth: 280, maxHeight: 300, overflowY: 'auto', padding: 6,
                            }}>
                              {formulaTemplates.length === 0 ? (
                                <div style={{ padding: '8px 10px', fontSize: 11, color: 'var(--muted)' }}>
                                  No templates available
                                </div>
                              ) : (
                                [
                                  { label: 'Default', items: formulaTemplates.filter(t => !t.team_id) },
                                  { label: 'Team', items: formulaTemplates.filter(t => t.team_id) },
                                ].map(group => group.items.length > 0 && (
                                  <div key={group.label}>
                                    <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', padding: '6px 8px 2px' }}>
                                      {group.label}
                                    </div>
                                    {group.items.map(t => (
                                      <button
                                        key={t.id}
                                        style={{
                                          display: 'block', width: '100%', textAlign: 'left',
                                          padding: '6px 10px', borderRadius: 4, fontSize: 12,
                                          border: 'none', cursor: 'pointer', background: 'transparent',
                                          color: 'var(--text)',
                                        }}
                                        onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface2)'; }}
                                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                                        onClick={() => loadTemplateIntoModel(t)}
                                      >
                                        <span style={{ fontWeight: 600 }}>{t.name}</span>
                                        {t.code && (
                                          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: 'var(--muted)', marginLeft: 6 }}>
                                            {t.code}
                                          </span>
                                        )}
                                        {t.description && (
                                          <span style={{ fontSize: 10, color: 'var(--muted)', display: 'block' }}>{t.description}</span>
                                        )}
                                      </button>
                                    ))}
                                  </div>
                                ))
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 60px 80px 90px 30px', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Label</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Reference Index</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Parts</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase', textAlign: 'right' }}>Weight</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase', textAlign: 'right' }}>Est. Cost</span>
                      <span></span>
                    </div>
                    {components.map((c, i) => (
                      <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 60px 80px 90px 30px', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                        <input className="ca-input" value={c.label} placeholder="Component label"
                          onChange={e => updateComp(i, 'label', e.target.value)} style={{ padding: '7px 8px' }} />
                        <select className="ca-select" value={c.commodity_name || ''} style={{ fontSize: 11, padding: '7px 8px' }}
                          onChange={e => {
                            const name = e.target.value;
                            // Keep commodity_id (which save() now sends and the
                            // backend trusts ahead of a name re-match) in sync —
                            // otherwise switching the dropdown here would
                            // silently keep pointing at whatever was loaded
                            // before. component_type follows the same edit so
                            // a manual "None" reads as deliberately fixed, not
                            // a broken link.
                            const match = commodities.find(ci => ci.name === name);
                            const next = [...components];
                            next[i] = {
                              ...next[i], commodity_name: name,
                              commodity_id: match ? match.id : null,
                              component_type: name ? 'index' : 'fixed',
                            };
                            setComponents(next);
                          }}>
                          <option value="">None</option>
                          {commodities.map(ci => <option key={ci.id} value={ci.name}>{ci.name}</option>)}
                        </select>
                        <NumberInput value={c.parts} allowNegative={false}
                          style={{ textAlign: 'right', padding: '7px 6px' }}
                          onChange={v => updateComp(i, 'parts', v ?? 0)} />
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, textAlign: 'right', color: 'var(--muted)' }}>
                          {(compWeight(c) * 100).toFixed(1)}%
                        </span>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, textAlign: 'right' }}>
                          {sym}{compCost(c).toFixed(3)}
                        </span>
                        <button className="ca-btn-danger" onClick={() => removeComp(i)}>x</button>
                      </div>
                    ))}
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginTop: 6, marginBottom: 12 }} onClick={addComp}>
                      + Add Component
                    </button>
                    {components.length === 0 && (
                      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
                        Add at least one commodity component to enable should-cost calculation.
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ marginBottom: 12 }}>
                    {/* Expression label row with Load Template dropdown */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <label className="ca-label" style={{ marginBottom: 0 }}>Expression</label>
                      <div style={{ position: 'relative' }} ref={templateDropdownRef}>
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          style={{ fontSize: 10 }}
                          onClick={() => setShowTemplateDropdown(v => !v)}
                        >
                          Load Template ▾
                        </button>
                        {showTemplateDropdown && (
                          <div style={{
                            position: 'absolute', right: 0, top: 'calc(100% + 4px)',
                            background: 'var(--surface)', border: '1px solid var(--border)',
                            borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
                            zIndex: 50, minWidth: 260, maxHeight: 300, overflowY: 'auto', padding: 6,
                          }}>
                            {formulaTemplates.length === 0 ? (
                              <div style={{ padding: '8px 10px', fontSize: 11, color: 'var(--muted)' }}>
                                No templates available
                              </div>
                            ) : (
                              [
                                { label: 'Default', items: formulaTemplates.filter(t => !t.team_id) },
                                { label: 'Team', items: formulaTemplates.filter(t => t.team_id) },
                              ].map(group => group.items.length > 0 && (
                                <div key={group.label}>
                                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', padding: '6px 8px 2px' }}>
                                    {group.label}
                                  </div>
                                  {group.items.map(t => (
                                    <button
                                      key={t.id}
                                      style={{
                                        display: 'block', width: '100%', textAlign: 'left',
                                        padding: '6px 10px', borderRadius: 4, fontSize: 12,
                                        border: 'none', cursor: 'pointer', background: 'transparent',
                                        color: 'var(--text)',
                                      }}
                                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface2)'; }}
                                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                                      onClick={() => loadTemplateIntoModel(t)}
                                    >
                                      <span style={{ fontWeight: 600 }}>{t.name}</span>
                                      {t.description && (
                                        <span style={{ fontSize: 10, color: 'var(--muted)', display: 'block' }}>{t.description}</span>
                                      )}
                                    </button>
                                  ))}
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
                      The result is the should-cost directly — embed any margin in the expression. Use square or round brackets.
                    </div>
                    <textarea
                      className="ca-input"
                      rows={3}
                      style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, width: '100%', resize: 'vertical', boxSizing: 'border-box' }}
                      placeholder="e.g. 0.92*[(0.75*ACN+1500)*(1-h)+h*AA/0.8]+FC"
                      value={advancedExpression}
                      onChange={e => setAdvancedExpression(e.target.value)}
                    />
                    <div style={{ display: 'flex', gap: 8, marginTop: 8, marginBottom: 14, alignItems: 'center' }}>
                      <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={detectVars}>
                        Detect Variables
                      </button>
                      {advancedExpression.trim() && (
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          style={{ fontSize: 10, color: 'var(--accent)' }}
                          onClick={() => setShowSaveTemplate(true)}
                        >
                          Save as Template
                        </button>
                      )}
                    </div>
                    {Object.keys(advancedVars).length > 0 && (
                      <div>
                        <div style={{ display: 'grid', gridTemplateColumns: '90px 90px 1fr 28px', gap: 8, marginBottom: 6 }}>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Variable</span>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Type</span>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Index / Value</span>
                          <span></span>
                        </div>
                        {Object.entries(advancedVars).map(([name, def]) => (
                          <div key={name} style={{ display: 'grid', gridTemplateColumns: '90px 90px 1fr 28px', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 600 }}>{name}</span>
                            <select className="ca-select" value={def.type || 'fixed'} style={{ fontSize: 11, padding: '6px 8px' }}
                              onChange={e => updateAdvancedVar(name, 'type', e.target.value)}>
                              <option value="fixed">Fixed</option>
                              <option value="index">Index</option>
                            </select>
                            {def.type === 'index' ? (
                              <IndexCombo
                                value={def.commodity_id ?? null}
                                commodities={commodities}
                                onChange={id => updateAdvancedVar(name, 'commodity_id', id)}
                              />
                            ) : (
                              <NumberInput
                                style={{ padding: '6px 8px', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}
                                value={def.value}
                                onChange={v => updateAdvancedVar(name, 'value', v)} />
                            )}
                            <button className="ca-btn-danger" onClick={() => removeAdvancedVar(name)}>x</button>
                          </div>
                        ))}
                      </div>
                    )}
                    {Object.keys(advancedVars).length === 0 && advancedExpression.trim() && (
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                        Click "Detect Variables" to extract variable names from your expression.
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                  <div>
                    <label className="ca-label">Total Price ({sym}/unit)</label>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13, padding: '7px 0' }}>{sym}{basePrice.toFixed(3)}</div>
                  </div>
                  <div>
                    <label className="ca-label">Base Period</label>
                    <div style={{ fontSize: 13, padding: '7px 0' }}>Q{baseQuarter}-{baseYear}</div>
                  </div>
                  <div>
                    <label className="ca-label">Pricing Basis</label>
                    <div style={{ fontSize: 13, padding: '7px 0' }}>
                      {(versionIncoterm || incoterm) ? `${versionIncoterm || incoterm}${versionNamedPlace ? ' ' + versionNamedPlace : ''}` : '—'}
                    </div>
                  </div>
                </div>

                {formulaMode === 'advanced' ? (
                  <div>
                    <label className="ca-label">Expression</label>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, background: 'var(--bg)', borderRadius: 6, padding: '8px 10px', marginBottom: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                      {advancedExpression || '\u2014'}
                    </div>
                    {Object.keys(advancedVars).length > 0 && (
                      <>
                        <div style={{ display: 'grid', gridTemplateColumns: '90px 90px 1fr', gap: 8, marginBottom: 4 }}>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Variable</span>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Type</span>
                          <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Index / Value</span>
                        </div>
                        {Object.entries(advancedVars).map(([name, def]) => {
                          const idxName = def.type === 'index'
                            ? (commodities.find(c => c.id === def.commodity_id)?.name || `ID ${def.commodity_id}`)
                            : null;
                          return (
                            <div key={name} style={{ display: 'grid', gridTemplateColumns: '90px 90px 1fr', gap: 8, marginBottom: 5, alignItems: 'center' }}>
                              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 600 }}>{name}</span>
                              <span style={{ fontSize: 11, color: 'var(--muted)' }}>{def.type === 'index' ? 'Index' : 'Fixed'}</span>
                              <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                                {def.type === 'index' ? idxName : String(def.value)}
                              </span>
                            </div>
                          );
                        })}
                      </>
                    )}
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px 90px', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Label</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Reference Index</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase', textAlign: 'right' }}>Weight</span>
                      <span style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase', textAlign: 'right' }}>Est. Cost</span>
                    </div>
                    {components.map((c, i) => (
                      <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px 90px', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                        <span style={{ fontSize: 13 }}>{c.label}</span>
                        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{c.commodity_name || '\u2014'}</span>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, textAlign: 'right', color: 'var(--muted)' }}>
                          {(compWeight(c) * 100).toFixed(1)}%
                        </span>
                        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, textAlign: 'right' }}>
                          {sym}{compCost(c).toFixed(3)}
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}

            {formulaMode === 'simple' && (
              <>
                <hr className="ca-sep" />
                <div className="ca-card-title" style={{ marginTop: 8 }}>Margin</div>
                {editing ? (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 70px 70px', gap: 12, marginBottom: 12, alignItems: 'flex-end' }}>
                    <div>
                      <label className="ca-label">Type</label>
                      <select className="ca-select" value={marginType} onChange={e => setMarginType(e.target.value)}>
                        <option value="pct">Percentage</option>
                        <option value="fixed">Fixed Amount</option>
                      </select>
                    </div>
                    <div>
                      <label className="ca-label">{marginType === 'pct' ? 'Margin %' : `Fixed Amount (${sym})`}</label>
                      <input className="ca-input" type="number" value={marginValue} min={0}
                        step={marginType === 'pct' ? 1 : 0.01}
                        onChange={e => setMarginValue(+e.target.value)} />
                    </div>
                    <div>
                      <label className="ca-label" style={{ fontSize: 9 }}>Weight</label>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, textAlign: 'right', padding: '7px 0', color: 'var(--muted)' }}>
                        {(marginWeight * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div>
                      <label className="ca-label" style={{ fontSize: 9 }}>Est. Cost</label>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, textAlign: 'right', padding: '7px 0' }}>
                        {sym}{marginCost.toFixed(3)}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 70px 70px', gap: 12, marginBottom: 12, alignItems: 'flex-end' }}>
                    <div>
                      <label className="ca-label">Type</label>
                      <div style={{ fontSize: 13, padding: '7px 0' }}>{marginType === 'pct' ? 'Percentage' : 'Fixed Amount'}</div>
                    </div>
                    <div>
                      <label className="ca-label">Value</label>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13, padding: '7px 0' }}>
                        {marginType === 'pct' ? `${marginValue}%` : `${sym}${marginValue}`}
                      </div>
                    </div>
                    <div>
                      <label className="ca-label" style={{ fontSize: 9 }}>Weight</label>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, textAlign: 'right', padding: '7px 0', color: 'var(--muted)' }}>
                        {(marginWeight * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div>
                      <label className="ca-label" style={{ fontSize: 9 }}>Est. Cost</label>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, textAlign: 'right', padding: '7px 0' }}>
                        {sym}{marginCost.toFixed(3)}
                      </div>
                    </div>
                  </div>
                )}
                {editing && componentPool < 0 && (
                  <div style={{ fontSize: 11, color: 'var(--accent2)', marginBottom: 8 }}>
                    Margin exceeds total price.
                  </div>
                )}
              </>
            )}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--bg)', borderRadius: 8, marginTop: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                {formulaMode === 'advanced'
                  ? `Base price anchor: ${sym}${basePrice.toFixed(3)} - should-cost from expression`
                  : `Components: ${sym}${componentPool >= 0 ? componentPool.toFixed(3) : '—'} + Margin: ${sym}${marginCost.toFixed(3)}`}
              </span>
              <span style={{
                fontFamily: "'Syne', sans-serif", fontSize: 16, fontWeight: 700,
                color: isValid ? 'var(--accent)' : 'var(--accent2)'
              }}>
                {sym}{shouldCost.toFixed(3)}
              </span>
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN */}
        <div>
          {/* Product Family */}
          <div className="ca-card" style={{ marginBottom: 16 }}>
            <div className="ca-card-title">Product Family</div>
            {editing ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label className="ca-label">Product Family</label>
                  <input className="ca-input" placeholder="e.g. Active Carbon" value={productName}
                    onChange={e => setProductName(e.target.value)} />
                </div>
                <div>
                  <label className="ca-label">Product Reference</label>
                  <input className="ca-input" placeholder="e.g. Mineral or Recycled" value={formula} onChange={e => setFormula(e.target.value)} />
                </div>
                <div>
                  <label className="ca-label">Unit</label>
                  <select className="ca-select" value={unit} onChange={e => setUnit(e.target.value)}>
                    {['kg', 't', 'lb'].map(u => <option key={u} value={u}>{u}</option>)}
                  </select>
                </div>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label className="ca-label">Product Family</label>
                  <div style={{ fontSize: 13, padding: '7px 0', fontWeight: 600 }}>{productName || '\u2014'}</div>
                </div>
                <div>
                  <label className="ca-label">Product Reference</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{formula || '\u2014'}</div>
                </div>
                <div>
                  <label className="ca-label">Unit</label>
                  <div style={{ fontSize: 13, padding: '7px 0' }}>{unit}</div>
                </div>
              </div>
            )}
          </div>

          {/* Should-Cost result */}
          <div className="ca-result" style={{ marginBottom: 16 }}>
            <div className="ca-result-label">Estimated Should-Cost</div>
            <div className="ca-result-big">{sym}{shouldCost.toFixed(3)}</div>
            <div style={{ marginTop: 4, fontSize: 11, color: 'var(--muted)' }}>
              per {unit} · {region}
            </div>
            {formulaMode === 'simple' ? (
              <>
                <hr className="ca-sep" />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 20, fontWeight: 700, color: 'var(--accent3)' }}>
                      {sym}{componentPool >= 0 ? componentPool.toFixed(3) : '\u2014'}
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Indexed Cost</div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 20, fontWeight: 700, color: 'var(--accent2)' }}>
                      {sym}{marginCost.toFixed(3)}
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--muted)', textTransform: 'uppercase' }}>Margin</div>
                  </div>
                </div>
              </>
            ) : (
              <div style={{ marginTop: 10, fontSize: 11, color: 'var(--muted)', fontStyle: 'italic' }}>
                Should-cost computed from expression at runtime.
              </div>
            )}
          </div>

          {/* Donut chart — simple mode only; advanced formulas have no component breakdown */}
          {formulaMode === 'simple' ? (
          <div className="ca-card">
            <div className="ca-card-title">Cost Composition</div>
            <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
              <div style={{ position: 'relative' }}>
                <DonutChart segments={donutSegs} size={150} />
                <div style={{
                  position: 'absolute', top: '50%', left: '50%',
                  transform: 'translate(-50%, -50%)', textAlign: 'center', pointerEvents: 'none'
                }}>
                  <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 16, fontWeight: 700 }}>100%</div>
                  <div style={{ fontSize: 8, color: 'var(--muted)' }}>TOTAL</div>
                </div>
              </div>
              <div style={{ flex: 1 }}>
                {donutSegs.map((s, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5, fontSize: 11 }}>
                    <div style={{ width: 9, height: 9, borderRadius: 2, flexShrink: 0, background: s.color }} />
                    <span style={{ color: 'var(--muted)', flex: 1 }}>{s.label}</span>
                    <span style={{ color: 'var(--text)' }}>{(s.pct * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          ) : (
          <div className="ca-card">
            <div className="ca-card-title">Cost Composition</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', padding: '4px 0' }}>
              No breakdown available — the expression evaluates directly to a should-cost value.
            </div>
          </div>
          )}

          {costModelId && (
            <VersionHistory costModelId={costModelId} editing={editing} onLoadVersion={(v) => {
              setBasePrice(v.base_price);
              setBaseYear(v.base_year);
              setBaseQuarter(v.base_quarter);
              setMarginType(v.margin_type === 'pct' || v.margin_type === 'fixed' ? v.margin_type : 'pct');
              setMarginValue(v.margin_value ?? 20);
              setVersionIncoterm(v.incoterm || '');
              setVersionNamedPlace(v.named_place || '');
              setLandedCostAdjustments(v.landed_cost_adjustments || null);
              setComponents(v.components.map(c => ({
                label: c.label,
                commodity_name: c.commodity_name || '',
                commodity_id: c.commodity_id,
                parts: Math.round(c.weight * 100),
                component_type: c.component_type,
                depth: c.depth,
                via_template_id: c.via_template_id,
                line_region: c.line_region,
                is_proxy: c.is_proxy,
              })));
              const vtype = v.formula_type || 'simple';
              setFormulaMode(vtype);
              setAdvancedExpression(v.expression || '');
              setAdvancedVars(v.variables || {});
              setShowLandedCost(vtype === 'simple');
              setSourceCoverageId(v.source_coverage_id || null);
              setLinkMode(v.link_mode || null);
              setLinkedTemplateName(null);
            }} />
          )}
        </div>
      </div>
    </div>

    {showSaveTemplate && (
      <SaveTemplateModal
        expression={advancedExpression}
        variables={advancedVars}
        activeTeamId={activeTeamId}
        canEditPlatform={canEditPlatform}
        canEditTeam={canEditTeam}
        addToast={addToast}
        onClose={() => setShowSaveTemplate(false)}
        onSaved={(t) => {
          setFormulaTemplates(prev => [...prev, t]);
          setShowSaveTemplate(false);
        }}
      />
    )}
    </>
  );
}

function SaveTemplateModal({ expression, variables, activeTeamId, canEditPlatform, canEditTeam, addToast, onClose, onSaved }) {
  const showScopeToggle = canEditPlatform && canEditTeam;
  const defaultScope = !canEditTeam && canEditPlatform ? 'platform' : 'team';
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scope, setScope] = useState(defaultScope);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!name.trim()) { addToast('Name is required', 'error'); return; }
    setSaving(true);
    try {
      const res = await api.post('/api/formulas/', {
        team_id: scope === 'platform' ? null : activeTeamId,
        name: name.trim(),
        description: description.trim() || null,
        expression,
        variables: Object.keys(variables).length > 0 ? normalizeVarMap(variables) : null,
      });
      addToast('Saved to Formula Library', 'success');
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
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
    }}>
      <div style={{
        background: 'var(--surface)', borderRadius: 12, padding: 28,
        width: '100%', maxWidth: 440, boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
        margin: '0 16px',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Save as Template</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: 'var(--muted)' }}>✕</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label className="ca-label">Name *</label>
            <input className="ca-input" value={name} onChange={e => setName(e.target.value)} placeholder="Template name" autoFocus />
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
                  <button key={s} onClick={() => setScope(s)} style={{
                    padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                    border: `1px solid ${scope === s ? 'var(--accent)' : 'var(--border)'}`,
                    background: scope === s ? 'var(--accent)' : 'transparent',
                    color: scope === s ? '#fff' : 'var(--text)',
                    fontWeight: scope === s ? 600 : 400,
                  }}>
                    {s === 'platform' ? 'Default (all teams)' : 'Team only'}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
          <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

function VersionHistory({ costModelId, editing, onLoadVersion }) {
  const [versions, setVersions] = useState([]);
  const [open, setOpen] = useState(false);
  const confirm = useConfirm();
  const showAlert = useAlert();

  const fetchVersions = () => {
    api.get(`/api/cost-models/${costModelId}/versions`)
      .then(({ data }) => setVersions(data))
      .catch(console.error);
  };

  useEffect(fetchVersions, [costModelId]);

  const deleteVersion = async (v) => {
    const ok = await confirm({
      title: `Delete formula for Q${v.base_quarter}-${v.base_year}?`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    api.delete(`/api/cost-models/${costModelId}/versions/${v.id}`)
      .then(fetchVersions)
      .catch(err => showAlert({ title: 'Error', message: formatApiError(err) }));
  };

  if (versions.length === 0) return null;

  return (
    <div style={{ marginTop: 16 }}>
      <button
        className="ca-btn ca-btn-ghost"
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px' }}
        onClick={() => setOpen(!open)}
      >
        <span style={{ fontSize: 12, fontWeight: 600 }}>Version History ({versions.length})</span>
        <span style={{ fontSize: 10, transition: 'transform .15s', transform: open ? 'rotate(180deg)' : 'rotate(0)' }}>{'\u25BC'}</span>
      </button>
      {open && (
        <div style={{ border: '1px solid var(--border)', borderTop: 'none', borderRadius: '0 0 8px 8px', maxHeight: 220, overflowY: 'auto' }}>
          <table className="ca-table" style={{ margin: 0 }}>
            <thead>
              <tr>
                <th>Quarter</th>
                <th>Base Price</th>
                <th>Margin</th>
                <th>Last Updated</th>
                {editing && <th className="center" style={{ width: 90 }}>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {versions.map(v => (
                <tr key={v.id}>
                  <td>Q{v.base_quarter}-{v.base_year}</td>
                  <td>${v.base_price.toFixed(2)}</td>
                  <td>{v.margin_type === 'pct' ? `${v.margin_value}%` : v.margin_type === 'fixed' ? `$${v.margin_value}` : 'Unknown'}</td>
                  <td style={{ fontSize: 11, color: 'var(--muted)' }}>{new Date(v.updated_at || v.created_at).toLocaleDateString()}</td>
                  {editing && (
                    <td className="center">
                      <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          onClick={() => onLoadVersion(v)}
                          title="Load into editor"
                        >Load</button>
                        <button
                          className="ca-btn ca-btn-ghost ca-btn-sm"
                          style={{ color: 'var(--accent2)' }}
                          onClick={() => deleteVersion(v)}
                          disabled={versions.length <= 1}
                          title={versions.length <= 1 ? 'Cannot delete the only version' : 'Delete version'}
                        >Del</button>
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
