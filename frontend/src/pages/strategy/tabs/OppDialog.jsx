import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';

/* Modal shell on the app's .ca-modal classes. Escape and a backdrop click
 * close it; focus returns to whatever opened it. Rendered in a portal so no
 * transformed ancestor can re-anchor the fixed backdrop. */
export default function OppDialog({ title, onClose, width = 560, children }) {
  const titleId = useId();
  const boxRef = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (opener && typeof opener.focus === 'function') opener.focus();
    };
  }, [onClose]);

  return createPortal(
    <div className="ca-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={boxRef} className="ca-modal so-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}
        style={{ width: `min(${width}px, 92vw)` }}>
        <div className="ca-modal-header">
          <div className="ca-modal-title" id={titleId}>{title}</div>
          <button type="button" className="ca-modal-close" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="ca-modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
