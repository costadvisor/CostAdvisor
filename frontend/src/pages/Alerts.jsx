import { useState, useEffect, useCallback } from 'react';
import api, { formatApiError } from '../api';
import { useAuth } from '../AuthContext';
import { useToast } from '../components/Toast';
import { useConfirm } from '../components/ConfirmDialog';

/* Scrum 24 — Alerts. Subscribe to index moves, new gaps, or buy-window flips
 * (per product / per index / portfolio-wide), delivered by email or the team's
 * Slack webhook. Shows alert history and lets an owner/admin run evaluation on
 * demand (the nightly Celery task does the same). */

const TRIGGERS = {
  gap: { label: 'New gap vs should-cost', scope: 'product' },
  index_move: { label: 'Index move', scope: 'index' },
  buy_window: { label: 'Buy-window flip', scope: 'product' },
  // SCRUM-79. The only trigger that can scope a supplier or a contract, which
  // is why those two columns exist on a subscription at all.
  negotiation_window: { label: 'Negotiation window opens', scope: 'window' },
};

// Which scopes each trigger accepts. The API rejects the wrong pairing with a
// 422 rather than silently ignoring it, so the picker offers only what will be
// accepted.
const WINDOW_SCOPES = [
  ['', 'Any window'],
  ['product', 'One product'],
  ['supplier', 'One supplier'],
  ['contract', 'One contract'],
  ['index', 'One index'],
];

const SCOPE_ALL_LABEL = {
  product: 'All products', index: 'All indexes',
  supplier: 'All suppliers', contract: 'All contracts', '': 'Any scope',
};

const fmtTime = (iso) => new Date(iso).toLocaleString(undefined,
  { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });

export default function Alerts() {
  const { activeTeamId } = useAuth();
  const { addToast } = useToast();
  const confirm = useConfirm();

  const [subs, setSubs] = useState([]);
  const [history, setHistory] = useState([]);
  const [costModels, setCostModels] = useState([]);
  const [commodities, setCommodities] = useState([]);
  const [slack, setSlack] = useState({ configured: false, slack_webhook_url: null });
  const [slackUrl, setSlackUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  // New-subscription form
  const [trigger, setTrigger] = useState('gap');
  const [scopeId, setScopeId] = useState('');       // '' = all; else cost_model_id or commodity_id
  const [threshold, setThreshold] = useState('');   // '' = inherit the team default
  const [channel, setChannel] = useState('email');
  const [windowScope, setWindowScope] = useState('');

  // The team default fire boundary. It lives here rather than only on a
  // subscription because a threshold set per-subscription and a threshold set
  // per-team were two live values with no rule for which won — one accessor
  // now reconciles them, and this is the half a person can see and change.
  const [teamThreshold, setTeamThreshold] = useState(null);
  const [thresholdDraft, setThresholdDraft] = useState('');
  const [savingThreshold, setSavingThreshold] = useState(false);
  const [suppliers, setSuppliers] = useState([]);
  const [contracts, setContracts] = useState([]);

  const load = useCallback(() => {
    if (!activeTeamId) return;
    setLoading(true);
    const p = { team_id: activeTeamId };
    Promise.all([
      api.get('/api/alerts/subscriptions', { params: p }),
      api.get('/api/alerts/history', { params: p }),
      api.get('/api/cost-models', { params: p }),
      api.get('/api/indexes'),
      api.get('/api/alerts/slack-webhook', { params: p }),
      api.get('/api/alerts/threshold', { params: p }),
      api.get('/api/suppliers', { params: p }).catch(() => ({ data: [] })),
      api.get('/api/contracts', { params: p }).catch(() => ({ data: [] })),
    ])
      .then(([s, h, cm, idx, sw, th, sup, con]) => {
        setSubs(s.data); setHistory(h.data); setCostModels(cm.data);
        setCommodities(idx.data); setSlack(sw.data); setSlackUrl(sw.data.slack_webhook_url || '');
        setTeamThreshold(th.data); setThresholdDraft(String(th.data.default_threshold_pct));
        setSuppliers(sup.data || []); setContracts(con.data || []);
      })
      .catch(err => addToast(formatApiError(err), 'error'))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId]);

  useEffect(() => { load(); }, [load]);

  const scope = TRIGGERS[trigger].scope;

  const addSub = () => {
    // An empty threshold box means "inherit the team default" — sending 0 would
    // instead mean "fire on any movement at all", which is a different alert.
    const body = {
      trigger_type: trigger, channel,
      threshold_pct: threshold === '' ? null : Number(threshold),
    };
    if (scopeId) {
      const kind = scope === 'window' ? windowScope : scope;
      if (kind === 'product') body.cost_model_id = scopeId;
      else if (kind === 'supplier') body.supplier_id = Number(scopeId);
      else if (kind === 'contract') body.contract_id = scopeId;
      else body.commodity_id = Number(scopeId);
    }
    api.post('/api/alerts/subscriptions', body, { params: { team_id: activeTeamId } })
      .then(() => { setScopeId(''); setThreshold(''); load(); addToast('Alert subscription added', 'success'); })
      .catch(err => addToast(formatApiError(err), 'error'));
  };

  const toggle = (s) => api.put(`/api/alerts/subscriptions/${s.id}`, { active: !s.active })
    .then(load).catch(err => addToast(formatApiError(err), 'error'));

  const del = async (s) => {
    if (!(await confirm({ title: 'Delete this alert?', confirmLabel: 'Delete', danger: true }))) return;
    api.delete(`/api/alerts/subscriptions/${s.id}`).then(load)
      .catch(err => addToast(formatApiError(err), 'error'));
  };

  const saveSlack = () => {
    api.put('/api/alerts/slack-webhook', { slack_webhook_url: slackUrl || null }, { params: { team_id: activeTeamId } })
      .then(({ data }) => { setSlack(data); addToast('Slack webhook saved', 'success'); })
      .catch(err => addToast(formatApiError(err), 'error'));
  };

  const saveThreshold = () => {
    setSavingThreshold(true);
    api.put('/api/alerts/threshold',
      { default_threshold_pct: Number(thresholdDraft), default_threshold_unit: teamThreshold.default_threshold_unit },
      { params: { team_id: activeTeamId } })
      .then(({ data }) => { setTeamThreshold(data); load(); addToast('Team default threshold saved', 'success'); })
      .catch(err => addToast(formatApiError(err), 'error'))
      .finally(() => setSavingThreshold(false));
  };

  const runNow = () => {
    setRunning(true);
    api.post('/api/alerts/evaluate', null, { params: { team_id: activeTeamId } })
      .then(({ data }) => { addToast(`Evaluation done — ${data.alerts_created} new alert(s)`, 'success'); load(); })
      .catch(err => addToast(formatApiError(err), 'error'))
      .finally(() => setRunning(false));
  };

  if (loading) return <div className="ca-page ca-fade-in"><div className="ca-h1">Alerts</div><div style={{ padding: 20, color: 'var(--muted)' }}>Loading…</div></div>;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="ca-h1">Alerts</div>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>Get notified on index moves, new gaps, buy-window flips, and negotiation windows opening — by email or Slack.</p>
        </div>
        <button className="ca-btn ca-btn-ghost" onClick={runNow} disabled={running}>{running ? 'Evaluating…' : '⟳ Run now'}</button>
      </div>

      {/* Team default threshold. One accessor reconciles this with the
          per-subscription override, so the two can never both be "the"
          threshold with no rule for which applies. */}
      {teamThreshold && (
        <div className="ca-card" style={{ marginTop: 14 }}>
          <div className="ca-card-title">Team default threshold</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label className="ca-label">Fires at</label>
              <input className="ca-input" type="number" min="0" max="100" step="0.5" style={{ width: 90 }}
                value={thresholdDraft} onChange={e => setThresholdDraft(e.target.value)} />
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', paddingBottom: 8 }}>
              {teamThreshold.default_threshold_unit === 'pct' ? '%' : teamThreshold.default_threshold_unit}
            </div>
            <button className="ca-btn ca-btn-sm ca-btn-ghost" onClick={saveThreshold}
              disabled={savingThreshold || thresholdDraft === String(teamThreshold.default_threshold_pct)}>
              {savingThreshold ? 'Saving…' : 'Save'}
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 8, lineHeight: 1.55 }}>
            Applies to every subscription that has not set its own. Changing it moves the
            boundary for all of them at once. Owner/admin only — saving 403s otherwise.
            {/* The unit travels with the value: a currency threshold only means
                something where both sides are money, and a platform index level
                is base 100, where nothing is. */}
          </div>
        </div>
      )}

      {/* New subscription */}
      <div className="ca-card" style={{ marginTop: 14 }}>
        <div className="ca-card-title">New alert</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <label className="ca-label">Trigger</label>
            <select className="ca-input" value={trigger} onChange={e => { setTrigger(e.target.value); setScopeId(''); }}>
              {Object.entries(TRIGGERS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          {scope === 'window' && (
            <div>
              <label className="ca-label">Scope by</label>
              <select className="ca-input" value={windowScope}
                onChange={e => { setWindowScope(e.target.value); setScopeId(''); }}>
                {WINDOW_SCOPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
          )}
          {!(scope === 'window' && !windowScope) && (
            <div>
              <label className="ca-label">Scope</label>
              <select className="ca-input" value={scopeId} onChange={e => setScopeId(e.target.value)}>
                <option value="">{SCOPE_ALL_LABEL[scope === 'window' ? windowScope : scope]}</option>
                {(scope === 'window' ? windowScope : scope) === 'product'
                  && costModels.map(cm => <option key={cm.id} value={cm.id}>{cm.product_name}{cm.supplier_name ? ` · ${cm.supplier_name}` : ''}</option>)}
                {(scope === 'window' ? windowScope : scope) === 'index'
                  && commodities.map(ci => <option key={ci.id} value={ci.id}>{ci.name}</option>)}
                {windowScope === 'supplier'
                  && suppliers.map(sp => <option key={sp.id} value={sp.id}>{sp.name}</option>)}
                {windowScope === 'contract'
                  && contracts.map(c => <option key={c.id} value={c.id}>{c.reference || c.id.slice(0, 8)}</option>)}
              </select>
            </div>
          )}
          {trigger !== 'buy_window' && (
            <div>
              <label className="ca-label">Threshold %</label>
              <input className="ca-input" type="number" min="0" max="100" style={{ width: 130 }}
                placeholder={teamThreshold ? `${teamThreshold.default_threshold_pct} (team)` : 'team default'}
                title="Leave blank to inherit the team default. Typing 0 is not the same thing — it means fire on any movement at all."
                value={threshold} onChange={e => setThreshold(e.target.value)} />
            </div>
          )}
          <div>
            <label className="ca-label">Channel</label>
            <select className="ca-input" value={channel} onChange={e => setChannel(e.target.value)}>
              <option value="email">Email</option>
              <option value="slack">Slack</option>
            </select>
          </div>
          <button className="ca-btn ca-btn-primary" onClick={addSub}>Add alert</button>
        </div>
        {channel === 'slack' && !slack.configured && (
          <div style={{ fontSize: 11, color: 'var(--accent2)', marginTop: 6 }}>No Slack webhook configured yet — set one below (owner/admin).</div>
        )}
      </div>

      {/* Subscriptions */}
      <div className="ca-card" style={{ marginTop: 14 }}>
        <div className="ca-card-title">My subscriptions</div>
        {subs.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>No alerts yet — add one above to get notified.</div>
        ) : (
          <table className="ca-table" style={{ width: '100%' }}>
            <thead><tr><th>Trigger</th><th>Scope</th><th className="center">Threshold</th><th className="center">Channel</th><th className="center">Active</th><th className="center">Actions</th></tr></thead>
            <tbody>
              {subs.map(s => (
                <tr key={s.id}>
                  <td>{TRIGGERS[s.trigger_type]?.label || s.trigger_type}</td>
                  <td style={{ color: 'var(--muted)' }}>{s.scope_label}</td>
                  {/* The raw override and what actually applies are different
                      facts; showing only one of them is how a person ends up
                      surprised by when an alert fired. */}
                  <td className="center">
                    {s.trigger_type === 'buy_window' ? '—' : (
                      <span title={s.threshold_source === 'team_default'
                        ? 'Inheriting the team default — change it above to move this one too'
                        : 'This subscription overrides the team default'}>
                        {s.effective_threshold_pct ?? s.threshold_pct}
                        {s.effective_threshold_unit === 'currency' ? '' : '%'}
                        {s.threshold_source === 'team_default' && (
                          <span style={{ color: 'var(--muted)', fontSize: 10 }}> (team)</span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="center"><span className="ca-badge">{s.channel}</span></td>
                  <td className="center">
                    <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={() => toggle(s)} style={{ color: s.active ? 'var(--accent)' : 'var(--muted)' }}>
                      {s.active ? 'On' : 'Off'}
                    </button>
                  </td>
                  <td className="center"><button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ color: 'var(--accent2)' }} onClick={() => del(s)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Slack webhook (owner/admin — save 403s otherwise) */}
      <div className="ca-card" style={{ marginTop: 14 }}>
        <div className="ca-card-title">Team Slack webhook {slack.configured && <span className="ca-badge" style={{ background: 'var(--success-bg)', color: 'var(--accent)' }}>configured</span>}</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="ca-input" style={{ flex: 1, minWidth: 260 }} placeholder="https://hooks.slack.com/services/…" value={slackUrl} onChange={e => setSlackUrl(e.target.value)} />
          <button className="ca-btn ca-btn-primary" onClick={saveSlack}>Save</button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6 }}>Owner/admin only. Slack-channel alerts post here.</div>
      </div>

      {/* History */}
      <div className="ca-card" style={{ marginTop: 14 }}>
        <div className="ca-card-title">Alert history</div>
        {history.length === 0 ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>No alerts have fired yet. Add a subscription and hit “Run now”.</div>
        ) : (
          <div className="ca-scroll-x">
            <table className="ca-table" style={{ width: '100%' }}>
              <thead><tr><th className="center">When</th><th>Alert</th><th className="center">Channel</th><th className="center">Delivered</th></tr></thead>
              <tbody>
                {history.map(e => (
                  <tr key={e.id}>
                    <td className="center" style={{ color: 'var(--muted)', fontSize: 11, whiteSpace: 'nowrap' }}>{fmtTime(e.triggered_at)}</td>
                    <td>{e.message}</td>
                    <td className="center"><span className="ca-badge">{e.channel}</span></td>
                    <td className="center" style={{ color: e.delivered ? 'var(--accent)' : 'var(--muted)' }}>{e.delivered ? '✓' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
