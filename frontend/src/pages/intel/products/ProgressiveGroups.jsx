import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FamilyGroup from './FamilyGroup';

/* The catalogue is up to every listed card (1,750 today) in a few dozen family groups. Rendering
 * them in one go is slow, so the grid renders progressively:
 *
 *  1. the first families, up to EAGER_CARDS cards, render at once;
 *  2. every other family renders its heading and a placeholder of about its
 *     real height; an IntersectionObserver renders a family as it nears the
 *     viewport (so a fast scroll never lands on an empty band);
 *  3. in idle time the remaining families fill in one at a time, in order, so
 *     the page is soon complete for find-in-page, keyboard tabbing and print.
 *
 * The layout is the mockup's (families stacked, no pagination); only the order
 * in which the browser builds it changes. */
const EAGER_CARDS = 24;
const CARD_H = 304; // a typical card at 1280–1440 wide (status badge row included)
const GAP = 12;

const idle = typeof window !== 'undefined' && window.requestIdleCallback
  ? (cb) => window.requestIdleCallback(cb, { timeout: 250 })
  : (cb) => window.setTimeout(cb, 16);
const cancelIdle = typeof window !== 'undefined' && window.cancelIdleCallback
  ? (id) => window.cancelIdleCallback(id)
  : (id) => window.clearTimeout(id);

function eagerSet(groups) {
  const out = new Set();
  let n = 0;
  for (const g of groups) {
    if (out.size > 0 && n >= EAGER_CARDS) break;
    out.add(String(g.id));
    n += g.items.length;
  }
  return out;
}

export default function ProgressiveGroups({ groups, sparkWidth, cols }) {
  const [shown, setShown] = useState(() => eagerSet(groups));
  // New results (a filter change) start over from the eager slice.
  const [prevGroups, setPrevGroups] = useState(groups);
  if (prevGroups !== groups) {
    setPrevGroups(groups);
    setShown(eagerSet(groups));
  }

  const pending = useMemo(() => groups.filter((g) => !shown.has(String(g.id))), [groups, shown]);
  const placeholders = useRef(new Map());
  const registerPlaceholder = useCallback((groupId, el) => {
    if (el) placeholders.current.set(groupId, el);
    else placeholders.current.delete(groupId);
  }, []);

  // Render a family as it comes within reach of the viewport.
  useEffect(() => {
    if (!pending.length || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver((entries) => {
      const near = entries.filter((e) => e.isIntersecting).map((e) => e.target.dataset.group);
      if (near.length) setShown((prev) => new Set([...prev, ...near]));
    }, { rootMargin: '900px 0px' });
    pending.forEach((g) => {
      const el = placeholders.current.get(String(g.id));
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, [pending]);

  // Fill in the rest, one family per idle slot.
  useEffect(() => {
    if (!pending.length) return undefined;
    const id = idle(() => setShown((prev) => new Set([...prev, String(pending[0].id)])));
    return () => cancelIdle(id);
  }, [pending]);

  return groups.map((g) => {
    const deferred = !shown.has(String(g.id));
    const rows = Math.ceil(g.items.length / Math.max(1, cols || 4));
    return (
      <FamilyGroup
        key={g.id}
        groupId={String(g.id)}
        family={g.family}
        items={g.items}
        sparkWidth={sparkWidth}
        deferred={deferred}
        placeholderHeight={rows * CARD_H + (rows - 1) * GAP}
        registerPlaceholder={registerPlaceholder}
      />
    );
  });
}
