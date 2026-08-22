import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { registerToastFn } from '../api';

const ToastContext = createContext(null);

const TYPE_STYLES = {
  success: { bg: 'var(--accent)', border: 'var(--accent)', icon: '✓' },
  error:   { bg: 'var(--accent2)', border: 'var(--accent2)', icon: '✕' },
  warning: { bg: '#f59e0b', border: '#f59e0b', icon: '!' },
  info:    { bg: 'var(--accent4)', border: 'var(--accent4)', icon: 'i' },
};

function Toast({ id, message, type = 'info', onDismiss }) {
  const style = TYPE_STYLES[type] || TYPE_STYLES.info;
  const timerRef = useRef(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => onDismiss(id), 5000);
    return () => clearTimeout(timerRef.current);
  }, [id, onDismiss]);

  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: '12px 14px',
        borderRadius: 'var(--radius, 8px)',
        background: 'var(--surface, #fff)',
        // Status colour carried by a full border + the coloured icon, not a thick
        // left stripe (a side-stripe accent is a house-style ban).
        border: `1px solid ${style.bg}`,
        boxShadow: 'var(--shadow-popover, 0 4px 12px rgba(0,0,0,0.12))',
        minWidth: 260,
        maxWidth: 380,
        fontSize: 13,
        animation: 'toast-in 0.2s ease',
      }}
    >
      <span style={{
        flexShrink: 0,
        width: 18,
        height: 18,
        borderRadius: '50%',
        background: style.bg,
        color: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 10,
        fontWeight: 700,
        marginTop: 1,
      }}>
        {style.icon}
      </span>
      <span style={{ flex: 1, color: 'var(--text)', lineHeight: 1.4 }}>{message}</span>
      <button
        onClick={() => onDismiss(id)}
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--muted)',
          fontSize: 14,
          padding: 0,
          lineHeight: 1,
          flexShrink: 0,
          marginTop: 1,
        }}
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);

  const addToast = useCallback((message, type = 'info') => {
    const id = nextId.current++;
    setToasts(prev => [...prev, { id, message, type }]);
  }, []);

  const dismiss = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Wire into the api.js 403 interceptor on mount
  useEffect(() => {
    registerToastFn(addToast);
    return () => registerToastFn(null);
  }, [addToast]);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {toasts.length > 0 && (
        <div
          aria-live="polite"
          style={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            zIndex: 9999,
            pointerEvents: 'none',
          }}
        >
          {toasts.map(t => (
            <div key={t.id} style={{ pointerEvents: 'auto' }}>
              <Toast {...t} onDismiss={dismiss} />
            </div>
          ))}
        </div>
      )}
      <style>{`
        @keyframes toast-in {
          from { opacity: 0; transform: translateX(20px); }
          to   { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside ToastProvider');
  return ctx;
}
