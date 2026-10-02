import { Fragment, useEffect, useMemo, useState } from 'react';
import api, { formatApiError } from '../api';
import exportCsv from '../utils/exportCsv';

// Per-region sourcing for the index library (Scrum 57 follow-up).
//
// What this page exists to show, and why it is not the page the ticket
// originally described: the follow-up assumed the series was showing ONE
// region's sourcing for all of its regions. On the drop-loaded series it is
// showing none — access, frequency and retrieval status are null on every one
// of them, while the cards underneath carry real per-region facts that nothing
// read until now.
//
// The Index Library's data-trust chip still reads `retrieval_status`, which
// lives on the series and not on a card, so it stays series-level. That is a
// property of the data, not an omission here, and the page says so rather than
// leaving somebody to rediscover it.

const CONFLICT_FIELDS = { access: 'access', frequency: 'frequency', agency: 'agency' };

function Stat({ value, label, color, title }) {
  return (
    <div className="ca-metric" style={{ flex: '1 1 160px' }} title={title}>
      <div className="ca-metric-val" style={color ? { color } : undefined}>{value}</div>
      <div className="ca-metric-lbl">{label}</div>
    </div>
  );
}

function RegionChip({ card }) {
  if (card.region_is_span) {
    return (
      <span className="ca-badge" style={{ background: 'var(--neutral-bg)', color: 'var(--muted)' }}
            title="This card deliberately spans several regions — not a region that failed to map.">
        multi-region
      </span>
    );
  }
  if (!card.region) {
    return <span style={{ fontSize: 11, color: 'var(--muted)' }}>no region stated</span>;
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontWeight: 600 }}>{card.region}</span>
      {card.mapped_region ? (
        <span className="ca-tag" title="How this maps onto the app's own region vocabulary">
          {card.mapped_region}
        </span>
      ) : (
        <span className="ca-badge" style={{ background: 'var(--accent2-dim)', color: 'var(--accent2)' }}
              title="This region code has no mapping onto the app's regions.">
          unmapped
        </span>
      )}
    </span>
  );
}

export default function IndexSourcing() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [search, setSearch] = useState('');
  const [only, setOnly] = useState('all'); // all | conflicts | duplicates | silent | multi_card
  const [open, setOpen] = useState(null);

  useEffect(() => {
    api.get('/api/indexes/region-coverage')
      .then(({ data: d }) => setData(d))
      .catch(e => setErr(formatApiError(e) || 'Could not load sourcing coverage.'));
  }, []);

  const entries = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.entries.filter(e => {
      if (only === 'conflicts' && e.sibling_conflicts.length === 0) return false;
      if (only === 'duplicates' && !e.duplicate_default_regions) return false;
      if (only === 'silent' && e.series_silent_fields.length === 0) return false;
      if (only === 'multi_card' && e.n_cards < 2) return false;
      if (!q) return true;
      return (e.name || '').toLowerCase().includes(q)
        || (e.commodity_key || '').toLowerCase().includes(q)
        || e.cards.some(c => (c.region || '').toLowerCase().includes(q)
          || (c.agency || '').toLowerCase().includes(q));
    });
  }, [data, search, only]);

  const download = () => {
    if (!data) return;
    exportCsv(
      `index-sourcing-${new Date().toISOString().slice(0, 10)}.csv`,
      ['Series', 'Series key', 'Card region', 'Mapped region', 'Access', 'Frequency', 'Agency',
        'Default region', 'Disagrees with series', 'Sibling conflicts', 'Sourcing note'],
      data.entries.flatMap(e => (e.cards.length ? e.cards : [null]).map(c => [
        e.name, e.commodity_key, c?.region ?? '', c?.mapped_region ?? '', c?.access ?? '',
        c?.frequency ?? '', c?.agency ?? '', c?.is_default_region ?? '',
        (c?.disagrees_with_series || []).join(' '), e.sibling_conflicts.join(' '),
        c?.sourcing_note ?? '',
      ])),
    );
  };

  if (err) {
    return <div className="ca-page ca-fade-in"><div className="ca-card" style={{ color: 'var(--accent2)' }}>{err}</div></div>;
  }
  if (!data) {
    return (
      <div className="ca-page ca-fade-in">
        <h1 className="ca-h1">Index sourcing</h1>
        {[0, 1, 2, 3, 4].map(i => <div key={i} className="ca-skeleton" style={{ height: 34, marginBottom: 8 }} />)}
      </div>
    );
  }

  const s = data.summary;

  return (
    <div className="ca-page ca-fade-in">
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <h1 className="ca-h1">Index sourcing</h1>
          <p className="ca-subtitle" style={{ marginBottom: 0 }}>
            Where each index is actually sourced, region by region — and where two cards on one series
            disagree about it.
          </p>
        </div>
        <button className="ca-btn ca-btn-ghost ca-btn-sm" style={{ marginLeft: 'auto' }} onClick={download}>
          Export CSV
        </button>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '28px 0 16px' }}>
        <Stat value={s.series} label="Series" />
        <Stat value={s.cards} label="Regional cards" />
        <Stat value={s.cards_carrying_sourcing_facts} label="Cards with sourcing facts" color="var(--accent)"
              title="Cards stating at least one of access, frequency or agency." />
        <Stat value={s.series_with_no_series_level_sourcing} label="Series stating none themselves"
              color="var(--accent3)"
              title="These series carry no sourcing metadata of their own, so their cards are the only place it exists." />
        <Stat value={s.series_with_sibling_conflicts} label="Series whose cards disagree"
              color={s.series_with_sibling_conflicts ? 'var(--accent2)' : undefined} />
        <Stat value={s.series_with_duplicate_defaults} label="Several default regions"
              color={s.series_with_duplicate_defaults ? 'var(--accent3)' : undefined} />
      </div>

      <div className="ca-card" style={{ marginBottom: 16, borderColor: 'var(--accent4)' }}>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          <strong>The data-trust chip in the Index Library stays series-level.</strong> It reads{' '}
          <code>retrieval_status</code>, which lives on the series; a card carries access, frequency and
          agency but no retrieval status. Making that chip per-region needs the per-card field the drop does
          not supply — it is a property of the data, not an omission here.
        </p>
      </div>

      <div className="ca-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 240px' }}>
            <label className="ca-label" htmlFor="src-search">Search</label>
            <input id="src-search" className="ca-input" value={search} placeholder="series, key, region or agency"
                   onChange={e => setSearch(e.target.value)} style={{ width: '100%' }} />
          </div>
          {[
            ['all', 'All'],
            ['multi_card', 'Several regions'],
            ['conflicts', 'Cards disagree'],
            ['duplicates', 'Duplicate defaults'],
            ['silent', 'No series-level facts'],
          ].map(([k, label]) => (
            <button key={k} className={`ca-btn ca-btn-sm ${only === k ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                    aria-pressed={only === k} onClick={() => setOnly(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="ca-card">
        <div className="ca-card-title" style={{ marginBottom: 12 }}>
          {entries.length} series{entries.length !== data.entries.length ? ` of ${data.entries.length}` : ''}
        </div>
        {entries.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '28px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Nothing matches</div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>Widen the search or clear the filter.</div>
          </div>
        ) : (
          <div className="ca-scroll-x">
            <table className="ca-table">
              <caption className="ca-sr-only">
                Index series and the regional cards beneath them. A row expands to each card&apos;s own sourcing.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Series</th>
                  <th scope="col">Key</th>
                  <th scope="col" style={{ textAlign: 'right' }}>Regions</th>
                  <th scope="col">Series states</th>
                  <th scope="col">Flags</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(e => {
                  const isOpen = open === e.commodity_id;
                  const stated = Object.entries({
                    access: e.series.access_tier, frequency: e.series.frequency, agency: e.series.provider,
                  }).filter(([, v]) => v);
                  return (
                    // Key on the Fragment, not the inner <tr> — a keyed child of an
                    // unkeyed fragment is exactly the console warning this repo
                    // has fixed before.
                    <Fragment key={e.commodity_id}>
                      <tr
                        onClick={() => setOpen(isOpen ? null : e.commodity_id)}
                        style={{ cursor: 'pointer' }}
                      >
                        <td>
                          <button
                            type="button"
                            className="ca-btn ca-btn-ghost ca-btn-sm"
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${e.name}`}
                            onClick={ev => { ev.stopPropagation(); setOpen(isOpen ? null : e.commodity_id); }}
                            style={{ padding: '0 6px', minWidth: 22, marginRight: 8 }}
                          >
                            {isOpen ? '-' : '+'}
                          </button>
                          {e.name}
                        </td>
                        <td style={{ color: 'var(--muted)' }}>{e.commodity_key}</td>
                        <td style={{ textAlign: 'right' }}>{e.n_cards}</td>
                        <td style={{ fontSize: 11, color: stated.length ? 'var(--text-secondary)' : 'var(--muted)' }}>
                          {stated.length
                            ? stated.map(([k, v]) => `${k}: ${v}`).join(' · ')
                            : 'nothing — its cards are the only source'}
                        </td>
                        <td>
                          <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                            {e.sibling_conflicts.map(f => (
                              <span key={f} className="ca-badge"
                                    style={{ background: 'var(--accent2-dim)', color: 'var(--accent2)' }}
                                    title={`Two cards on this series state different ${CONFLICT_FIELDS[f] || f}.`}>
                                {f} conflict
                              </span>
                            ))}
                            {e.duplicate_default_regions && (
                              <span className="ca-badge" style={{ background: 'var(--accent3-dim)', color: 'var(--accent3)' }}
                                    title="More than one card on this series claims to be the default region.">
                                several defaults
                              </span>
                            )}
                          </span>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td colSpan={5} style={{ background: 'var(--bg)' }}>
                            {e.cards.length === 0 ? (
                              <div style={{ padding: 14, fontSize: 11, color: 'var(--muted)' }}>
                                No regional card describes this series.
                              </div>
                            ) : (
                              <table className="ca-table" style={{ margin: '6px 0 12px' }}>
                                <caption className="ca-sr-only">Regional cards for {e.name}.</caption>
                                <thead>
                                  <tr>
                                    <th scope="col">Region</th>
                                    <th scope="col">Access</th>
                                    <th scope="col">Frequency</th>
                                    <th scope="col">Agency</th>
                                    <th scope="col">Default</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {e.cards.map(c => (
                                    <tr key={c.feed_key}>
                                      <td><RegionChip card={c} /></td>
                                      <td style={{ color: c.disagrees_with_series.includes('access') ? 'var(--accent2)' : undefined }}>
                                        {c.access || <span style={{ color: 'var(--muted)' }}>—</span>}
                                      </td>
                                      <td style={{ color: c.disagrees_with_series.includes('frequency') ? 'var(--accent2)' : undefined }}>
                                        {c.frequency || <span style={{ color: 'var(--muted)' }}>—</span>}
                                      </td>
                                      <td title={c.sourcing_note || undefined}
                                          style={{ color: c.disagrees_with_series.includes('agency') ? 'var(--accent2)' : undefined }}>
                                        {c.agency || <span style={{ color: 'var(--muted)' }}>—</span>}
                                      </td>
                                      <td>
                                        {c.is_default_region
                                          ? <span className="ca-badge" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>default</span>
                                          : <span style={{ color: 'var(--muted)' }}>—</span>}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
