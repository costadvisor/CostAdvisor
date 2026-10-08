import { memo, useCallback } from 'react';
import ProductCard from './ProductCard';

/* One family: uppercase heading, rule, count, then its card grid. While
 * `deferred`, the grid is a placeholder of about the same height (see
 * ProgressiveGroups). */
function FamilyGroup({
  groupId, family, items, sparkWidth, deferred = false, placeholderHeight = 0, registerPlaceholder,
}) {
  const n = items.length;
  const ref = useCallback((el) => registerPlaceholder?.(groupId, el), [groupId, registerPlaceholder]);
  return (
    <section className="ix-group" aria-label={`${family}, ${n} product${n === 1 ? '' : 's'}`}>
      <div className="ix-group-head">
        <h2 className="ix-group-name ixp-group-name">{family}</h2>
        <div className="ix-group-rule" aria-hidden />
        <div className="ix-group-count">{n} product{n === 1 ? '' : 's'}</div>
      </div>
      {deferred ? (
        <div ref={ref} className="ixp-placeholder" data-group={groupId}
          style={{ height: placeholderHeight }} aria-busy="true" />
      ) : (
        <div className="ix-grid ixp-grid">
          {items.map((item) => (
            <ProductCard key={item.pid} item={item} sparkWidth={sparkWidth} />
          ))}
        </div>
      )}
    </section>
  );
}

export default memo(FamilyGroup);
