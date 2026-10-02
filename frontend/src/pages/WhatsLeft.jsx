// What is still outstanding, and who can unblock it.
//
// This page used to be the index of six clickable mockups. All six are built,
// so what remains is the part no coding session closes: an account, a
// signature, a dashboard, or a dataset. That distinction is the only reason
// this page still exists — a flat TODO hides it, and these items are the ones
// actually standing between the product and a paying customer.
//
// Long form, with the reasoning: jvpdocs/remaining-work-plan.md.

const BLOCKED = [
  {
    title: 'Sync the deploy repo',
    who: 'Whoever holds the Railway and Cloudflare dashboards',
    detail: 'The Railway-connected repo is far behind this branch. Nothing from Scrum 26 onward, none of '
      + 'the 12 Index Data Layer units, and none of this work is live. Verify anything infra-related '
      + 'against the live domains only after that sync — not before.',
    impact: 'Everything built since the go-live merge',
  },
  {
    title: 'SMTP credentials',
    who: 'Whoever can open an account with a mail provider',
    detail: 'No provider chosen. Until one is, invites, welcome mails, demo confirmations and every alert '
      + 'email fail silently — the code paths are all best-effort by design.',
    impact: 'Invites · alerts · demo scheduling',
  },
  {
    title: 'VAPID keys in production',
    who: 'Whoever sets Railway environment variables',
    detail: 'Web push is built and works locally. Without VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY the app '
      + 'reports push as unconfigured rather than failing — deliberately, but it also means nobody gets a '
      + 'notification.',
    impact: 'Push notifications',
  },
  {
    title: 'Vendor DPAs and named incident contacts',
    who: 'Whoever can sign',
    detail: 'The last two open items on the security posture doc. Until they land it cannot go to a '
      + 'prospect, which is the only reason it was written.',
    impact: 'Enterprise IT review',
  },
  {
    title: 'Base-price anchors',
    who: 'Whoever can source real prices',
    detail: '199 of 200 platform combos have no base-period price, so they produce an index level and never '
      + 'a currency figure. The per-region editor and the bulk CSV import are both built and waiting.',
    impact: 'The catalog showing money rather than index levels',
  },
  {
    title: 'FD-1 feed mapping',
    who: 'An engineer, but one feed at a time against live sources',
    detail: 'The 2026-07 drop renamed commodities to short type-codes that do not match the old scraper '
      + 'registry keys. That mismatch, not a missing scraper, is why the library shows "No data". Verify '
      + 'each series ID live before committing to it — two candidates failed that check and were correctly '
      + 'left out rather than guessed.',
    impact: 'Most catalog commodities having any values at all',
  },
  {
    title: 'Search Console verification and field Core Web Vitals',
    who: 'Whoever owns the domains, once there is traffic',
    detail: 'Both need real production traffic first, so neither can be closed early.',
    impact: 'SEO and the landing page',
  },
];

export default function WhatsLeft() {
  return (
    <div className="ca-page ca-fade-in">
      <h1 className="ca-h1">What is still to build</h1>
      <p className="ca-subtitle">
        Nothing, in code. What is left needs an account, a signature, a dashboard or a dataset.
      </p>

      <div className="ca-card" style={{ marginBottom: 24, borderColor: 'var(--accent)' }}>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          Every feature that was mocked here has been built and wired: nested cost models, the AI cost
          modeler, supplier price-list import, negotiation prep, per-region sourcing, push notifications, the
          data-quality console and real index forecasts. The reasoning behind each is in{' '}
          <code>CLAUDE.md</code>; the forward view is <code>jvpdocs/remaining-work-plan.md</code>.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
        {BLOCKED.map(item => (
          <div key={item.title} className="ca-card">
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 15, fontWeight: 700, marginBottom: 8 }}>
              {item.title}
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 12 }}>{item.detail}</p>
            <div style={{ borderTop: '1px solid var(--row-divider)', paddingTop: 10 }}>
              <div className="ca-card-title" style={{ marginBottom: 3 }}>Who</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>{item.who}</div>
              <div className="ca-card-title" style={{ marginBottom: 3 }}>Blocks</div>
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>{item.impact}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
