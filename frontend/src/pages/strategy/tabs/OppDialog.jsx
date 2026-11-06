import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useConfirm } from '../../../components/ConfirmDialog';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/* Modal shell on the app's .ca-modal classes. Tab stays inside it. Escape and
 * a backdrop click close it, but ask first once something has been typed: a
 * stray key or click must not drop a half-written form (Cancel and × close at
 * once). Focus returns to whatever opened it. Rendered in a portal so no
 * transformed ancestor can re-anchor the fixed backdrop. */
export default function OppDialog({ title, onClose, width = 560, children }) {
  const titleId = useId();
  const boxRef = useRef(null);
  const confirm = useConfirm();
  // Taken while rendering, before a field inside takes focus (autoFocus).
  const [opener] = useState(() => document.activeElement);
  const dirty = useRef(false);
  const asking = useRef(false);
  // Read through refs: the parents pass inline callbacks, and re-running the
  // effect below on every render would hand focus back to the opener.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const closeAsking = async () => {
    if (asking.current) return;
    if (!dirty.current) { onCloseRef.current(); return; }
    asking.current = true;
    const ok = await confirm({
      title: 'Discard your changes?',
      message: 'What you entered in this form has not been saved.',
      confirmLabel: 'Discard',
      danger: true,
    });
    asking.current = false;
    if (ok) onCloseRef.current();
  };
  const closeAskingRef = useRef(closeAsking);
  closeAskingRef.current = closeAsking;

  useEffect(() => {
    const onKey = (e) => {
      if (asking.current) return;               // the confirm dialog has the keys
      if (e.key === 'Escape') { closeAskingRef.current(); return; }
      if (e.key !== 'Tab' || !boxRef.current) return;
      const focusable = boxRef.current.querySelectorAll(FOCUSABLE);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const inside = boxRef.current.contains(document.activeElement);
      if (!inside || (e.shiftKey && document.activeElement === first)
          || (!e.shiftKey && document.activeElement === last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      // Not while the dialog is still up (StrictMode's rehearsal unmount).
      if (document.activeElement?.closest?.('.so-dialog')) return;
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
    };
  }, [opener]);

  const markDirty = () => { dirty.current = true; };

  return createPortal(
    <div className="ca-modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) closeAsking(); }}>
      <div ref={boxRef} className="ca-modal so-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}
        style={{ width: `min(${width}px, 92vw)` }} onInputCapture={markDirty} onChangeCapture={markDirty}>
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
