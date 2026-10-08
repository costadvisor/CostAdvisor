import { useState } from 'react';
import { Link } from 'react-router-dom';
import api, { formatApiError } from '../../api';
import { KraljicBadge, fmt } from '../../components/intel';

/* "Suggested from your portfolio": playbooks the team's products reach but
 * the team has not started. "Start strategy" → POST …/adopt (idempotent),
 * then the caller opens the category. */

function SuggestionCard({ row, teamId, currency, onAdopted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const href = `/strategy/${encodeURIComponent(row.playbook_slug)}`;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/strategy/categories/${encodeURIComponent(row.playbook_slug)}/adopt`, null, {
        params: { team_id: teamId },
      });
      onAdopted(row.playbook_slug);
    } catch (err) {
      setError(formatApiError(err));
      setBusy(false);
    }
  };

  return (
    <article className="st-sugg" aria-labelledby={`sugg-${row.playbook_slug}`}>
      <div className="st-sugg-top">
        <div style={{ minWidth: 0 }}>
          <div className="st-sugg-family">{row.family || '—'}</div>
          <h3 className="st-sugg-name" id={`sugg-${row.playbook_slug}`}>{row.name}</h3>
        </div>
        <KraljicBadge kraljic={row.kraljic} />
      </div>
      <div>
        <div className="st-sugg-label">From your portfolio</div>
        <div className="st-sugg-products">
          {(row.products || []).map((p) => (
            <Link key={p.product_id} className="st-product-chip" title={p.pid || undefined}
              to={p.pid ? `/intelligence/products/${encodeURIComponent(p.pid)}` : '/products'}>
              {p.name}
            </Link>
          ))}
        </div>
      </div>
      <div className="st-sugg-stats">
        <div>
          <div className="st-sugg-stat-l">Annual spend</div>
          <div className="st-sugg-stat-v">{fmt.money(row.annual_spend, row.currency || currency)}</div>
        </div>
        <div>
          <div className="st-sugg-stat-l">Open opportunities</div>
          <div className="st-sugg-stat-v">{fmt.num(row.open_opportunities)}</div>
        </div>
      </div>
      <div className="st-sugg-foot">
        <Link className="ix-link ix-small" to={href}>
          {row.has_report ? 'Read the analysis' : 'Open the playbook'} →
        </Link>
        <button type="button" className="ca-btn ca-btn-primary ca-btn-sm" onClick={start} disabled={busy}>
          {busy ? 'Starting…' : 'Start strategy'}
        </button>
      </div>
      {error && <div className="st-sugg-error" role="alert">{error}</div>}
    </article>
  );
}

export default function LandingSuggestions({ rows = [], teamId, currency, onAdopted }) {
  if (!rows.length) return null;
  return (
    <section aria-labelledby="st-suggested-title">
      <div className="st-section-head">
        <h2 className="st-section-title" id="st-suggested-title" tabIndex={-1}>Suggested from your portfolio</h2>
        <span className="ix-muted ix-small">{fmt.plural(rows.length, 'playbook')}</span>
      </div>
      <p className="st-section-sub">
        Your products reach these playbooks through their product lines. Start a strategy to set objectives,
        score the opportunities and track actions.
      </p>
      <div className="st-sugg-grid">
        {rows.map((r) => (
          <SuggestionCard key={r.playbook_slug} row={r} teamId={teamId} currency={currency} onAdopted={onAdopted} />
        ))}
      </div>
    </section>
  );
}
