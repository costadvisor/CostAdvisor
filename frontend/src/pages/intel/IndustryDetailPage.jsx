import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  DetailHeader, Panel, EmptyState, ErrorState, LoadingPage, StatusBadge, useApi, fmt,
} from '../../components/intel';
import CategoryTable from './categories/CategoryTable';
import OutPanel from './categories/OutPanel';
import { ReferenceBuyerPanel, BoundariesPanel, SharedObjectsPanel } from './categories/ReferenceBuyer';
import { STATUSES, STATUS_DEF, STATUS_LABEL, industryIndex } from './categories/status';
import '../../styles/intel-categories.css';

/* Intelligence › Categories › <industry>: the reference buyer, its categories
 * (→ product lines → products, shared objects, what is still to build) and the
 * products it deliberately does not buy.
 *
 * `?open=MW-01,MW-04` expands those categories and scrolls to the first. */

const SCOPE_LABEL = {
  existing: 'Carried over',
  split: 'Split from an earlier industry',
  new: 'New in September 2026',
};

function parseOpen(raw) {
  return new Set(String(raw || '').split(',').map((s) => s.trim()).filter(Boolean));
}

export default function IndustryDetailPage() {
  const { industrySlug } = useParams();
  const { data, error, status, loading, reload } = useApi(
    industrySlug ? `/api/intel/industries/${encodeURIComponent(industrySlug)}` : null,
  );
  // The list gives name → slug, so industry names in the text can link.
  const { data: list } = useApi('/api/intel/industries');
  const index = useMemo(() => industryIndex(list?.items), [list]);

  const [params, setParams] = useSearchParams();
  const open = useMemo(() => parseOpen(params.get('open')), [params]);
  const [statusFilter, setStatusFilter] = useState(null);

  const setOpen = useCallback((next) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (next.size) p.set('open', Array.from(next).join(','));
      else p.delete('open');
      return p;
    }, { replace: true });
  }, [setParams]);

  const toggle = useCallback((code) => {
    const next = new Set(open);
    if (next.has(code)) next.delete(code); else next.add(code);
    setOpen(next);
  }, [open, setOpen]);

  // Deep link: scroll the first opened category into view once the data is in.
  const scrolledFor = useRef(null);
  useEffect(() => {
    if (!data || scrolledFor.current === data.slug) return;
    scrolledFor.current = data.slug;
    const first = data.categories?.find((c) => open.has(c.code));
    if (!first) return;
    const el = document.getElementById(`cat-${first.code}`);
    if (!el) return;
    window.requestAnimationFrame(() => {
      // Leave the page where it is when the row is already on screen below
      // the sticky header (e.g. the first category).
      const header = document.querySelector('.ix-dh');
      const top = el.getBoundingClientRect().top;
      const headerBottom = header ? header.getBoundingClientRect().bottom : 0;
      if (top > headerBottom && top < window.innerHeight * 0.6) return;
      el.scrollIntoView({ block: 'start' });
    });
  }, [data, open]);

  const categories = useMemo(() => data?.categories || [], [data]);
  const shown = useMemo(
    () => (statusFilter ? categories.filter((c) => c.status === statusFilter) : categories),
    [categories, statusFilter],
  );
  const allOpen = shown.length > 0 && shown.every((c) => open.has(c.code));

  if (status === 404) {
    return (
      <div className="ix-page ca-fade-in">
        <EmptyState
          title="Industry not found"
          body={`There is no industry called "${industrySlug}" in the demand tree.`}
          action={<Link className="ca-btn ca-btn-ghost ca-btn-sm" to="/intelligence/categories">All industries</Link>}
        />
      </div>
    );
  }
  if (error) {
    return (
      <div className="ix-page ca-fade-in">
        <ErrorState error={error} onRetry={reload} title="Could not load this industry" />
      </div>
    );
  }
  if (loading || !data) {
    return <div className="ix-detail-body"><LoadingPage panels={2} /></div>;
  }

  const counts = data.categories_by_status || {};
  const rb = data.reference_buyer || {};
  const prefix = categories[0]?.code?.split('-')[0];

  return (
    <div className="ca-fade-in">
      <DetailHeader
        backTo="/intelligence/categories"
        crumbs={[
          { label: 'Intelligence', to: '/intelligence' },
          { label: 'Categories', to: '/intelligence/categories' },
        ]}
        title={data.name}
        subtitle={prefix ? `${prefix} · ${fmt.plural(data.category_count, 'category', 'categories')}` : null}
        meta={(
          <>
            {STATUSES.map((k) => (
              <StatusBadge key={k} status={`${fmt.num(counts[k] || 0)} ${k}`}
                tone={k === 'servable' ? 'good' : k === 'partial' ? 'warn' : 'neutral'}
                title={STATUS_DEF[k]} />
            ))}
            <span className="ix-chip"><strong>{fmt.num(data.line_count)}</strong> product lines</span>
            <span className="ix-chip"><strong>{fmt.num(data.product_count)}</strong> products</span>
            {data.scope_status && SCOPE_LABEL[data.scope_status] && (
              <span className="ix-chip">{SCOPE_LABEL[data.scope_status]}</span>
            )}
            {data.ratified_on && <span className="ix-chip">Ratified {fmt.date(data.ratified_on)}</span>}
          </>
        )}
      >
        {rb.buyer_one_line && (
          <p className="ixc-dh-buyer"><span className="ixc-dh-buyer-label">Reference buyer</span> {rb.buyer_one_line}</p>
        )}
      </DetailHeader>

      <div className="ix-detail-body">
        <div className="ix-two-col ixc-layout">
          <div className="ix-col">
            <Panel
              title="Categories"
              caption="one function bought in this industry · select a row to see what is held"
              flush
            >
              <div className="ixc-cat-toolbar">
                <div className="ixc-seg-ctrl" role="group" aria-label="Filter by status">
                  <button type="button" className="ix-pill" aria-pressed={!statusFilter}
                    onClick={() => setStatusFilter(null)}>
                    All <span className="ixc-pill-n">{categories.length}</span>
                  </button>
                  {STATUSES.map((k) => (
                    <button key={k} type="button" className={`ix-pill ixc-pill-${k}`}
                      aria-pressed={statusFilter === k} disabled={!counts[k]}
                      onClick={() => setStatusFilter(statusFilter === k ? null : k)}
                      title={STATUS_DEF[k]}>
                      <i className={`ixc-dot s-${k}`} aria-hidden />
                      {STATUS_LABEL[k]} <span className="ixc-pill-n">{counts[k] || 0}</span>
                    </button>
                  ))}
                </div>
                <span className="ix-spacer" />
                <button type="button" className="ix-link ix-small"
                  onClick={() => {
                    const next = new Set(open);
                    shown.forEach((c) => (allOpen ? next.delete(c.code) : next.add(c.code)));
                    setOpen(next);
                  }}>
                  {allOpen ? 'Collapse all' : 'Expand all'}
                </button>
              </div>
              {shown.length > 0 ? (
                <CategoryTable categories={shown} open={open} onToggle={toggle}
                  sharedObjects={data.shared_objects} />
              ) : (
                <div className="ixc-pad">
                  <EmptyState variant="compact" title="No categories with this status" />
                </div>
              )}
            </Panel>

            <OutPanel rows={data.out} />
          </div>

          <aside className="ix-rail" aria-label="Reference buyer">
            <ReferenceBuyerPanel buyer={rb} />
            <BoundariesPanel buyer={rb} index={index} />
            <SharedObjectsPanel objects={data.shared_objects} index={index} />
          </aside>
        </div>
      </div>
    </div>
  );
}
