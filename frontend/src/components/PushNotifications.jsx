import { useCallback, useEffect, useState } from 'react';
import api, { formatApiError } from '../api';
import { useToast } from './Toast';

// Web push, the other half of the PWA (the installable shell already shipped).
//
// Free by construction: the browser's own push relay is used once
// pushManager.subscribe() runs. No paid service, no Firebase project — only a
// VAPID key pair, whose public half is served by /api/push/config rather than
// baked into this build, so rotating it does not need a redeploy of the SPA.
//
// The capability panel reads the browser, not the server, and says so. That
// matters most on iOS: Safari delivers push ONLY to a PWA added to the home
// screen, never to a plain tab, so a subscribe button that looks like it
// worked and then never delivers is the default failure here unless the UI
// says why.

function urlBase64ToUint8Array(base64String) {
  // The VAPID public key travels as URL-safe base64; subscribe() wants bytes.
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString() : '—');

function CapRow({ label, ok, detail }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0',
                  borderBottom: '1px solid var(--row-divider)' }}>
      <span aria-hidden="true" style={{
        width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
        background: ok ? 'var(--accent)' : 'var(--accent2)',
      }} />
      <span style={{ fontSize: 12 }}>{label}</span>
      <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted)' }}>{detail}</span>
    </div>
  );
}

export default function PushNotifications() {
  const { addToast } = useToast();
  const [caps, setCaps] = useState(null);
  const [config, setConfig] = useState(null);
  const [devices, setDevices] = useState([]);
  const [subscribedHere, setSubscribedHere] = useState(false);
  const [busy, setBusy] = useState(false);

  const readCaps = useCallback(async () => {
    const sw = 'serviceWorker' in navigator;
    const push = 'PushManager' in window;
    const permission = typeof Notification !== 'undefined' ? Notification.permission : 'unsupported';
    const standalone = window.matchMedia?.('(display-mode: standalone)')?.matches
      || window.navigator.standalone === true;
    let existing = false;
    if (sw && push) {
      try {
        const reg = await navigator.serviceWorker.ready;
        existing = !!(await reg.pushManager.getSubscription());
      } catch { /* a browser that refuses to answer is simply not subscribed */ }
    }
    setCaps({ sw, push, permission, standalone });
    setSubscribedHere(existing);
  }, []);

  const loadDevices = useCallback(() => {
    api.get('/api/push/subscriptions')
      .then(({ data }) => setDevices(data))
      .catch(() => setDevices([]));
  }, []);

  useEffect(() => {
    readCaps();
    api.get('/api/push/config')
      .then(({ data }) => setConfig(data))
      .catch(() => setConfig({ enabled: false, public_key: null }));
    loadDevices();
  }, [readCaps, loadDevices]);

  const enable = async () => {
    setBusy(true);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        addToast('Notifications were not allowed for this site.', 'info');
        await readCaps();
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        // Required by every browser: a push that shows nothing to the user is
        // not permitted, which is also why the service worker always calls
        // showNotification.
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config.public_key),
      });
      const json = sub.toJSON();
      await api.post('/api/push/subscriptions', {
        endpoint: json.endpoint,
        p256dh: json.keys.p256dh,
        auth: json.keys.auth,
        user_agent: navigator.userAgent.slice(0, 255),
      });
      addToast('This device will now receive alerts.', 'success');
      await readCaps();
      loadDevices();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not enable notifications on this device.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const { endpoint } = sub.toJSON();
        await sub.unsubscribe();
        // Unsubscribing in the browser stops delivery but leaves the server
        // row, so it is removed too — otherwise the device list grows a
        // permanent ghost. Matched by endpoint, which the list endpoint never
        // returns (it is a capability URL) but this browser already holds.
        await api.post('/api/push/unsubscribe', { endpoint });
      }
      await readCaps();
      loadDevices();
      addToast('This device will no longer receive alerts.', 'info');
    } catch (e) {
      addToast(formatApiError(e) || 'Could not turn notifications off.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setBusy(true);
    try {
      const { data } = await api.post('/api/push/test');
      addToast(
        data.delivered
          ? `Sent to ${data.delivered} device${data.delivered === 1 ? '' : 's'}.`
          : 'Nothing was delivered — no device accepted the push.',
        data.delivered ? 'success' : 'info',
      );
      loadDevices();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not send a test.', 'error');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/api/push/subscriptions/${id}`);
      loadDevices();
      readCaps();
    } catch (e) {
      addToast(formatApiError(e) || 'Could not remove that device.', 'error');
    }
  };

  if (!caps || !config) return null;

  const blocked = caps.permission === 'denied';
  const unsupported = !caps.sw || !caps.push;
  const iosTabProblem = /iPad|iPhone|iPod/.test(navigator.userAgent) && !caps.standalone;

  return (
    <div className="ca-card">
      <div className="ca-card-title" style={{ marginBottom: 12 }}>Push notifications</div>

      {!config.enabled ? (
        <p style={{ fontSize: 12, color: 'var(--muted)' }}>
          Push is not configured on this server yet. It needs a VAPID key pair — free, no third-party
          service — set as <code>VAPID_PUBLIC_KEY</code> and <code>VAPID_PRIVATE_KEY</code>.
        </p>
      ) : (
        <>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 14 }}>
            Get index moves, new gaps and buy windows on this device instead of only in an inbox. Choose
            which alerts use push on the Alerts page.
          </p>

          <div style={{ marginBottom: 14 }}>
            <CapRow label="Service worker" ok={caps.sw} detail={caps.sw ? 'available' : 'missing'} />
            <CapRow label="Push API" ok={caps.push} detail={caps.push ? 'available' : 'missing'} />
            <CapRow label="Permission" ok={caps.permission === 'granted'} detail={caps.permission} />
            <CapRow label="Subscribed on this device" ok={subscribedHere}
                    detail={subscribedHere ? 'yes' : 'no'} />
          </div>

          {iosTabProblem && (
            <p style={{ fontSize: 11, color: 'var(--accent3)', marginBottom: 12 }}>
              On iOS, notifications only reach CostAdvisor once it has been added to the home screen.
              Subscribing from a Safari tab appears to work and then never delivers.
            </p>
          )}
          {blocked && (
            <p style={{ fontSize: 11, color: 'var(--accent2)', marginBottom: 12 }}>
              Notifications are blocked for this site in your browser settings — that has to be changed
              there, this page cannot ask again.
            </p>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {subscribedHere ? (
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={disable} disabled={busy}>
                Turn off on this device
              </button>
            ) : (
              <button className="ca-btn ca-btn-primary ca-btn-sm" onClick={enable}
                      disabled={busy || unsupported || blocked}>
                {busy ? 'Working…' : 'Enable on this device'}
              </button>
            )}
            {devices.length > 0 && (
              <button className="ca-btn ca-btn-ghost ca-btn-sm" onClick={sendTest} disabled={busy}>
                Send a test
              </button>
            )}
          </div>

          {devices.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <div className="ca-card-title" style={{ marginBottom: 8 }}>Registered devices</div>
              {devices.map(d => (
                <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 10,
                                         padding: '8px 0', borderBottom: '1px solid var(--row-divider)' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 11, wordBreak: 'break-word' }}>
                      {d.user_agent || 'Unknown browser'}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
                      added {fmt(d.created_at)} · last delivered {fmt(d.last_delivered_at)}
                    </div>
                  </div>
                  <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }}
                          onClick={() => remove(d.id)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
