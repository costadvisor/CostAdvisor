import { useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/* URL-synced tabs (?tab=…), WAI-ARIA tablist with automatic activation.
 *
 *   const TABS = [{ id: 'market', label: 'Market & Costs' }, { id: 'intel', label: 'Product Intelligence' }];
 *   const [tab, setTab] = useTabParam(TABS.map(t => t.id));
 *   <DetailHeader … tabs={<Tabs tabs={TABS} value={tab} onChange={setTab} idBase="product" />} />
 *   <TabPanel id="market" active={tab} idBase="product">…</TabPanel>
 *
 * Leave out value/onChange and <Tabs> drives the URL itself; the page can still
 * read the active id with useTabParam — both read the same search param. */

export function useTabParam(ids, defaultId, param = 'tab') {
  const [params, setParams] = useSearchParams();
  const fallback = defaultId ?? ids[0];
  const raw = params.get(param);
  const active = raw && ids.includes(raw) ? raw : fallback;
  const setActive = useCallback((id) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set(param, id);
      return next;
    }, { replace: true });
  }, [setParams, param]);
  return [active, setActive];
}

export function Tabs({
  tabs = [], value, onChange, param = 'tab', idBase = 'ix', ariaLabel = 'Sections', className = '',
}) {
  const ids = tabs.map((t) => t.id);
  const [urlActive, setUrlActive] = useTabParam(ids, ids[0], param);
  const controlled = value !== undefined;
  const active = controlled ? value : urlActive;
  const refs = useRef({});

  const select = (id) => {
    if (!controlled) setUrlActive(id);
    onChange?.(id);
  };

  const enabled = tabs.filter((t) => !t.disabled);
  const move = (fromId, delta) => {
    const i = enabled.findIndex((t) => t.id === fromId);
    const next = enabled[(i + delta + enabled.length) % enabled.length];
    if (!next) return;
    select(next.id);
    refs.current[next.id]?.focus();
  };

  const onKeyDown = (e, id) => {
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); move(id, 1); break;
      case 'ArrowLeft': e.preventDefault(); move(id, -1); break;
      case 'Home': e.preventDefault(); if (enabled[0]) { select(enabled[0].id); refs.current[enabled[0].id]?.focus(); } break;
      case 'End': {
        e.preventDefault();
        const last = enabled[enabled.length - 1];
        if (last) { select(last.id); refs.current[last.id]?.focus(); }
        break;
      }
      default: break;
    }
  };

  return (
    <div role="tablist" aria-label={ariaLabel} className={`ix-tabs ${className}`}>
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            ref={(el) => { refs.current[t.id] = el; }}
            type="button"
            role="tab"
            id={`${idBase}-tab-${t.id}`}
            aria-controls={`${idBase}-panel-${t.id}`}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            disabled={t.disabled}
            className="ix-tab"
            onClick={() => select(t.id)}
            onKeyDown={(e) => onKeyDown(e, t.id)}
          >
            {t.label}
            {t.count != null && <span className="ix-tab-count">{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* Renders only when active (unless keepMounted, which hides instead). */
export function TabPanel({ id, active, idBase = 'ix', keepMounted = false, className = '', children }) {
  const isActive = active === id;
  if (!isActive && !keepMounted) return null;
  return (
    <div
      role="tabpanel"
      id={`${idBase}-panel-${id}`}
      aria-labelledby={`${idBase}-tab-${id}`}
      hidden={!isActive}
      tabIndex={0}
      className={`ix-tabpanel ${className}`}
    >
      {children}
    </div>
  );
}

export default Tabs;
