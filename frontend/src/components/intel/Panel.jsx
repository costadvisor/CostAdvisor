import { useState, useId } from 'react';

/* Card with an uppercase small title and a right-aligned muted caption.
 *
 *   <Panel title="Index components — EU" caption="EU feeds · Eurostat" flush>
 *     <table className="ix-table">…</table>
 *   </Panel>
 *
 * collapsible: the title becomes a disclosure button (defaultOpen, or control
 * it with open + onToggle). flush: no body padding (tables, charts that bleed).
 * footer: a muted strip under the body (source notes). */
export default function Panel({
  title, caption, actions, collapsible = false, defaultOpen = true, open: openProp, onToggle,
  flush = false, footer, className = '', bodyClassName = '', id, children,
}) {
  const [openState, setOpenState] = useState(defaultOpen);
  const open = openProp ?? openState;
  const bodyId = useId();
  const toggle = () => {
    const next = !open;
    if (openProp === undefined) setOpenState(next);
    onToggle?.(next);
  };
  const hasHead = title || caption || actions;

  return (
    <section id={id} className={`ix-panel${open ? '' : ' is-collapsed'} ${className}`}>
      {hasHead && (
        <div className="ix-panel-head">
          <h2 className="ix-panel-title">
            {collapsible ? (
              <button type="button" className="ix-panel-toggle" aria-expanded={open} aria-controls={bodyId}
                onClick={toggle}>
                <span className="ix-chev" aria-hidden>▾</span>
                <span>{title}</span>
              </button>
            ) : title}
          </h2>
          {(caption || actions) && (
            <div className="ix-panel-headright">
              {caption && <span className="ix-panel-caption">{caption}</span>}
              {actions}
            </div>
          )}
        </div>
      )}
      {open && (
        <div id={bodyId} className={`ix-panel-body${flush ? ' flush' : ''} ${bodyClassName}`}>
          {children}
        </div>
      )}
      {open && footer && <div className="ix-panel-foot">{footer}</div>}
    </section>
  );
}
