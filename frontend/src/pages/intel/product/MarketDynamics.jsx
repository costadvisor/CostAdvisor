import { Fragment, useState } from 'react';
import { Panel, StatusBadge, fmt } from '../../../components/intel';

/* Tab 1: market dynamics (3M / 24M, the mockup's buildDynamicsFor) and the
 * dated current-events outlook. */

const dirClass = (v) => {
  if (v == null || !Number.isFinite(Number(v))) return 'ix-flat';
  const n = Math.round(Number(v) * 10) / 10;
  if (n > 0) return 'ix-up';
  if (n < 0) return 'ix-down';
  return 'ix-flat';
};

const SIGNAL_3M = { up: '↑', down: '↓', flat: '→' };
const SIGNAL_24M = { open: '○', peak: '▲', trough: '▼', mover: '◆', close: '●' };
const SIGNAL_24M_CLASS = { peak: 'ix-up', trough: 'ix-down' };

function monthRange(from, to) {
  const a = fmt.month(from);
  const b = fmt.month(to);
  if (a === '—' || b === '—') return null;
  const [am, ay] = a.split(' ');
  const [bm, by] = b.split(' ');
  return ay === by ? `${am}–${bm} ${by}` : `${a} – ${b}`;
}

function shiftMonth(period, delta) {
  const m = String(period || '').match(/^(\d{4})-(\d{2})/);
  if (!m) return null;
  const k = Number(m[1]) * 12 + Number(m[2]) - 1 + delta;
  return `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, '0')}`;
}

/* The window's start and end levels, as the narrative prints them. */
function LevelMove({ from, to }) {
  if (from == null || to == null) return null;
  return (
    <div className="ixd-dyn-move" title="Index level at the start and end of the window (base 100 = Jan 2023)">
      {fmt.index(from)} → {fmt.index(to)}
    </div>
  );
}

export function MarketDynamics({ data }) {
  const d = data.dynamics;
  const [win, setWin] = useState('3m');
  if (!d) return null;
  // The window ends at the last actual month of the source data.
  const asOf = data.data_as_of || data.snapshot?.index_latest_period || data.timeline?.actual_to;
  const shortFrom = shiftMonth(asOf, -(d.short_window_months || 3));
  const driver = d.top_driver;
  // The _html variant is the API's escaped text with <strong> around line
  // labels; the plain variant is rendered as text, never as markup.
  const narrativeHtml = win === '3m' ? d.narrative_3m_html : d.narrative_24m_html;
  const narrativeText = win === '3m' ? d.narrative_3m : d.narrative_24m;
  const signals = win === '3m' ? (d.signals_3m || []) : (d.signals_24m || []);
  const longLabel = d.long_window?.label || `${d.long_window_months || 24} months`;

  return (
    <Panel
      title="Market dynamics"
      actions={(
        <>
          <div className="ixd-seg" role="group" aria-label="Window">
            <button type="button" aria-pressed={win === '3m'} onClick={() => setWin('3m')}>
              {d.short_window_months || 3} months
            </button>
            <button type="button" aria-pressed={win === '24m'} onClick={() => setWin('24m')}>
              {d.long_window_months || 24} months
            </button>
          </div>
          <span className="ix-panel-caption">{data.region}</span>
        </>
      )}
    >
      <div className="ixd-dyn-grid">
        <button type="button" className="ixd-dyn-stat" aria-pressed={win === '3m'} onClick={() => setWin('3m')}>
          <div className="ixd-dyn-label">Last {d.short_window_months || 3} months</div>
          <div className={`ixd-dyn-val ${dirClass(d.short_points)}`}>
            {fmt.signed(d.short_points)} pts<small>{fmt.pct(d.short_pct)}</small>
          </div>
          <LevelMove from={d.short_from} to={d.short_to} />
          {shortFrom && asOf && <div className="ixd-dyn-sub">{monthRange(shortFrom, asOf)}</div>}
        </button>
        <button type="button" className="ixd-dyn-stat" aria-pressed={win === '24m'} onClick={() => setWin('24m')}>
          <div className="ixd-dyn-label">{d.long_window_months || 24} months · {longLabel}</div>
          <div className={`ixd-dyn-val ${dirClass(d.long_points)}`}>
            {fmt.signed(d.long_points)} pts<small>{fmt.pct(d.long_pct)}</small>
          </div>
          <LevelMove from={d.long_from} to={d.long_to} />
          {(d.long_low || d.long_high) && (
            <div className="ixd-dyn-sub">
              {d.long_low && <>Low {fmt.index(d.long_low.level)} ({fmt.month(d.long_low.period)})</>}
              {d.long_low && d.long_high && ' · '}
              {d.long_high && <>high {fmt.index(d.long_high.level)} ({fmt.month(d.long_high.period)})</>}
            </div>
          )}
        </button>
        <div className="ixd-dyn-stat">
          <div className="ixd-dyn-label">Top driver since Jan 2023</div>
          {driver ? (
            <>
              <div className="ixd-dyn-driver">{driver.label}</div>
              <div className="ixd-dyn-sub">
                {fmt.share(driver.weight_pct)} weight · <span className={dirClass(driver.vs_base_pct)}>{fmt.pct(driver.vs_base_pct)}</span> own level
                {' '}· <span className={dirClass(driver.weighted_impact_pct)}>{fmt.signed(driver.weighted_impact_pct)} pts</span> on the index
              </div>
            </>
          ) : <div className="ixd-dyn-sub">Not in the source data</div>}
        </div>
      </div>

      {(narrativeHtml || narrativeText) && (
        <div className="ixd-narr">
          <div className="ixd-narr-label">What drove the index · {win === '3m' ? `last ${d.short_window_months || 3} months` : longLabel}</div>
          {narrativeHtml
            ? <div dangerouslySetInnerHTML={{ __html: narrativeHtml }} />
            : <div>{narrativeText}</div>}
        </div>
      )}

      {signals.length > 0 && (
        <>
          <div className="ix-section-label">{win === '3m' ? 'Forward signals' : 'Window markers'}</div>
          <ul className="ixd-signals">
            {signals.map((s, i) => {
              const ico = win === '3m' ? SIGNAL_3M[s.direction] || '·' : SIGNAL_24M[s.kind] || '·';
              const cls = win === '3m' ? dirClass(s.direction === 'up' ? 1 : s.direction === 'down' ? -1 : 0) : SIGNAL_24M_CLASS[s.kind] || 'ix-muted';
              return (
                <li key={`${s.text}-${i}`} className="ixd-signal">
                  <span className={`ixd-signal-ico ${cls}`} aria-hidden>{ico}</span>
                  <span>{s.text}</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Panel>
  );
}

/* The outlook text is short markdown: paragraphs and **bold**, no lists. */
function renderInline(text) {
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => (p.startsWith('**') && p.endsWith('**') && p.length > 4
    ? <strong key={i}>{p.slice(2, -2)}</strong>
    : <Fragment key={i}>{p}</Fragment>));
}

export function MarketOutlook({ data }) {
  const o = data.outlook;
  if (!o || !o.text) return null;
  const paras = String(o.text).split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
  const caption = [
    o.region ? `${o.region}` : 'All regions',
    o.expires_at ? `review by ${fmt.date(o.expires_at)}` : null,
  ].filter(Boolean).join(' · ');
  return (
    <Panel
      title="Outlook"
      actions={(
        <>
          {o.is_stale && <StatusBadge status="Stale" tone="warn" />}
          <span className="ix-panel-caption">{caption}</span>
        </>
      )}
    >
      <div className="ix-prose ixd-outlook">
        {paras.map((p, i) => <p key={i}>{renderInline(p)}</p>)}
      </div>
    </Panel>
  );
}
