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
  const [threshold, setThreshold] = useState(5);
  const [channel, setChannel] = useState('email');

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
    ])
      .then(([s, h, cm, idx, sw]) => {
        setSubs(s.data); setHistory(h.data); setCostModels(cm.data);
        setCommodities(idx.data); setSlack(sw.data); setSlackUrl(sw.data.slack_webhook_url || '');
      })
      .catch(err => addToast(formatApiError(err), 'error'))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId]);

  useEffect(() => { load(); }, [load]);

  const scope = TRIGGERS[trigger].scope;

  const addSub = () => {
    const body = { trigger_type: trigger, threshold_pct: Number(threshold), channel };
    if (scopeId) {
      if (scope === 'product') body.cost_model_id = scopeId;
      else body.commodity_id = Number(scopeId);
    }
    api.post('/api/alerts/subscriptions', body, { params: { team_id: activeTeamId } })
      .then(() => { setScopeId(''); load(); addToast('Alert subscription added', 'success'); })
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
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>Get notified on index moves, new gaps, and buy-window flips — by email or Slack.</p>
        </div>
        <button className="ca-btn ca-btn-ghost" onClick={runNow} disabled={running}>{running ? 'Evaluating…' : '⟳ Run now'}</button>
      </div>

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
          <div>
            <label className="ca-label">Scope</label>
            <select className="ca-input" value={scopeId} onChange={e => setScopeId(e.target.value)}>
              <option value="">{scope === 'product' ? 'All products' : 'All indexes'}</option>
              {scope === 'product'
                ? costModels.map(cm => <option key={cm.id} value={cm.id}>{cm.product_name}{cm.supplier_name ? ` · ${cm.supplier_name}` : ''}</option>)
                : commodities.map(ci => <option key={ci.id} value={ci.id}>{ci.name}</option>)}
            </select>
          </div>
          {trigger !== 'buy_window' && (
            <div>
              <label className="ca-label">Threshold %</label>
              <input className="ca-input" type="number" min="0" max="100" style={{ width: 90 }} value={threshold} onChange={e => setThreshold(e.target.value)} />
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
                  <td className="center">{s.trigger_type === 'buy_window' ? '—' : `${s.threshold_pct}%`}</td>
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
