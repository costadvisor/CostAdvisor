import { useNavigate } from 'react-router-dom';

export default function Home() {
  const navigate = useNavigate();

  return (
    <div className="ca-page ca-fade-in">
      {/* Hero */}
      <div style={{ padding: '48px 0 40px', borderBottom: '1px solid var(--border)', marginBottom: 40 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 28 }}>
          <div style={{ maxWidth: 620 }}>
            <div style={{ fontSize: 10, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 2.5, marginBottom: 14, fontWeight: 600 }}>
              Chemical Cost Intelligence
            </div>
            <h1 style={{ fontFamily: "'Syne', sans-serif", fontSize: 42, fontWeight: 800, lineHeight: 1.1, marginBottom: 18, letterSpacing: -1 }}>
              Should-Cost Estimator<br />
              <span style={{ color: 'var(--muted)' }}>for Chemical Products</span>
            </h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.9, marginBottom: 28 }}>
              CostAdvisor estimates the <strong style={{ color: 'var(--text)' }}>should-cost</strong> of chemical products — the price
              a product <em>ought</em> to cost given its raw material composition and current market conditions.
              It combines a reference cost breakdown with real commodity index evolutions to benchmark pricing over time.
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="ca-btn ca-btn-primary" onClick={() => navigate('/cost-models/new')}>New Cost Model</button>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate('/dashboard')}>View Dashboard</button>
              <button className="ca-btn ca-btn-ghost" onClick={() => navigate('/index-library')}>Index Library</button>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 210 }}>
            {[
              { label: 'Coverage', value: 'Multi-Index', sub: 'across all regions', color: 'var(--accent)', border: 'var(--accent)' },
              { label: 'Tracking', value: 'Dynamic', sub: 'quarterly & monthly', color: 'var(--accent3)', border: 'var(--accent3)' },
              { label: 'Analysis', value: 'Full Stack', sub: 'evolve \u00B7 brief \u00B7 pricing', color: 'var(--accent4)', border: 'var(--accent4)' },
            ].map((s, i) => (
              <div key={i} style={{
                padding: '16px 18px', background: 'var(--surface)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', borderLeft: `3px solid ${s.border}`
              }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3, textTransform: 'uppercase', letterSpacing: 0.8 }}>{s.label}</div>
                <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 20, fontWeight: 700, color: s.color }}>{s.value}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{s.sub}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* What is Should-Cost */}
      <div style={{ marginBottom: 32 }}>
        <div className="ca-h2" style={{ fontSize: 20, marginBottom: 6 }}>What is Should-Cost?</div>
        <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 22 }}>Understanding the methodology behind the estimates.</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="ca-card">
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 14, fontWeight: 700, marginBottom: 8 }}>The Concept</div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.85 }}>
              Should-cost is a <strong style={{ color: 'var(--text)' }}>cost engineering technique</strong> that determines
              what a product should cost based on its material inputs, manufacturing processes, and market conditions —
              independently of the supplier's quoted price.
            </p>
          </div>
          <div className="ca-card">
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 14, fontWeight: 700, marginBottom: 8 }}>How It's Calculated</div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.85 }}>
              You provide a <strong style={{ color: 'var(--text)' }}>reference cost</strong> at a known point in time, along
              with the product's <strong style={{ color: 'var(--text)' }}>cost breakdown</strong> and which commodity indexes
              drive each component. CostAdvisor then applies index evolution factors to project cost over time.
            </p>
          </div>
        </div>
      </div>

      {/* Modules */}
      <div style={{ marginBottom: 32 }}>
        <div className="ca-h2" style={{ fontSize: 20, marginBottom: 6 }}>How the Tool Works</div>
        <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 22 }}>Five core modules in the should-cost workflow.</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {[
            { title: 'Cost Model Builder', step: 'Step 1 \u2014 Define', color: 'var(--accent)', path: '/cost-models/new',
              desc: 'Define your product, select a supplier and destination, assign raw materials to commodity indexes with weights, and set the margin type. Versioned formulas support renegotiation history.' },
            { title: 'Cost Evolution', step: 'Step 2 \u2014 Track', color: 'var(--accent4)', path: '/dashboard',
              desc: 'Compare how cost should have evolved (theoretical) vs. how it actually evolved (observed prices). The gap highlights pricing drift for renegotiations.' },
            { title: 'Squeeze Analysis', step: 'Step 3 \u2014 Quantify', color: 'var(--accent3)', path: '/dashboard',
              desc: 'Multiply the pricing gap by actual or projected volumes to compute the financial impact per period, with cumulative totals showing total over/under-payment.' },
            { title: 'Negotiation Brief', step: 'Step 4 \u2014 Act', color: 'var(--accent2)', path: '/dashboard',
              desc: 'Auto-generated one-pager with verdict, top cost drivers, evolution chart, and narrative text \u2014 ready to export as PDF for supplier meetings.' },
            { title: 'Portfolio Dashboard', step: 'Overview', color: 'var(--accent)', path: '/dashboard',
              desc: 'All cost models ranked by financial exposure with flag indicators for index moves and price drift. Table and card views with sortable columns.' },
          ].map((m, i) => (
            <div key={i} className="ca-card" style={{ cursor: 'pointer', transition: 'border-color .2s' }}
              onClick={() => navigate(m.path)}
              onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent)'}
              onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}>
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontFamily: "'Syne', sans-serif", fontWeight: 700, fontSize: 14 }}>{m.title}</div>
                <div style={{ fontSize: 10, color: m.color, textTransform: 'uppercase', letterSpacing: 0.6, marginTop: 1 }}>{m.step}</div>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.8 }}>{m.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
