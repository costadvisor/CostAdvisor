import { useEffect, useRef } from 'react';

// Escape closes a dialog while `active`. For hand-rolled dialogs that do not
// use <Modal>; pass active=false while a child dialog is open so one press
// closes only the top one. onClose sits in a ref so an inline callback does
// not re-subscribe on every render.
export function useEscapeKey(active, onClose) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!active) return undefined;
    const handleKey = (e) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [active]);
}

export default function Modal({ isOpen, onClose, title, children, width = 420 }) {
  const modalRef = useRef(null);

  useEscapeKey(isOpen, onClose);

  // Focus the modal when it opens (only then: re-focusing on every render
  // would pull focus out of a field being typed in).
  useEffect(() => {
    if (isOpen) modalRef.current?.focus();
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="ca-modal-backdrop" onClick={onClose}>
      <div
        className="ca-modal"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
        ref={modalRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
      >
        <div className="ca-modal-header">
          <span style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 15 }}>
            {title}
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: 'var(--muted)',
              cursor: 'pointer', fontSize: 18, lineHeight: 1, padding: 4,
            }}
          >
            &times;
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
