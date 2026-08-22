import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { registerAlertFn } from '../api';

const ConfirmCtx = createContext(null);

export function ConfirmProvider({ children }) {
  const [dialog, setDialog] = useState(null);

  const open = useCallback((opts) => new Promise((resolve) => {
    setDialog({ ...opts, resolve });
  }), []);

  const confirm = useCallback((opts) => open(opts), [open]);
  const showAlert = useCallback((opts) => open({ ...opts, alertOnly: true, confirmLabel: 'OK' }), [open]);

  // Wire the api.js interceptor so 429 rate-limit messages use this dialog too
  useEffect(() => {
    registerAlertFn((msg) => showAlert({ title: 'Notice', message: msg }));
  }, [showAlert]);

  // Close on Escape
  useEffect(() => {
    if (!dialog) return;
    const handler = (e) => { if (e.key === 'Escape') handle(false); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [dialog]);

  const handle = (result) => {
    dialog?.resolve(result);
    setDialog(null);
  };

  return (
    <ConfirmCtx.Provider value={{ confirm, alert: showAlert }}>
      {children}
      {dialog && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 9000,
            background: 'rgba(0,0,0,0.55)',
            backdropFilter: 'blur(4px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onMouseDown={(e) => { if (e.target === e.currentTarget) handle(false); }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ca-confirm-title"
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 14,
              padding: '28px 28px 22px',
              minWidth: 360,
              maxWidth: 500,
              width: '90vw',
              boxShadow: '0 32px 80px rgba(0,0,0,0.6)',
            }}
          >
            {dialog.title && (
              <div
                id="ca-confirm-title"
                style={{
                  fontFamily: "'Syne', sans-serif",
                  fontWeight: 700,
                  fontSize: 15,
                  color: dialog.danger ? 'var(--accent2)' : 'var(--text)',
                  marginBottom: dialog.message ? 10 : 22,
                }}
              >
                {dialog.title}
              </div>
            )}
            {dialog.message && (
              <p style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                lineHeight: 1.75,
                marginBottom: 24,
                whiteSpace: 'pre-line',
              }}>
                {dialog.message}
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              {!dialog.alertOnly && (
                <button className="ca-btn ca-btn-ghost" onClick={() => handle(false)}>
                  Cancel
                </button>
              )}
              <button
                className="ca-btn"
                style={dialog.danger
                  ? { background: 'var(--accent2-dim)', color: 'var(--accent2)', border: '1px solid var(--accent2)' }
                  : { background: 'var(--accent)', color: '#060c09', border: '1px solid var(--accent)' }
                }
                onClick={() => handle(true)}
                autoFocus
              >
                {dialog.confirmLabel || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmCtx).confirm;
}

export function useAlert() {
  return useContext(ConfirmCtx).alert;
}
