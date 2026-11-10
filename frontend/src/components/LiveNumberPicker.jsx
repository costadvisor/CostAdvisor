import { useEffect, useState } from 'react';
import Modal from './Modal';
import api, { formatApiError } from '../api';

/* "Insert live number" — the picker for text editors (Jira 210 S7).
 *
 *   <LiveNumberPicker isOpen teamId={teamId} onClose={…} onInsert={(token) => …} />
 *
 * Builds a token such as `{{index:brent|change12m|fallback=about +20%}}`
 * (grammar: backend/app/services/live_tokens.py), shows what readers will see
 * right now (POST /api/live/preview) and hands the token to `onInsert`. The
 * text stores the token; the server fills it in each time the text is read.
 */

const MEASURES = [
  { key: 'last', label: 'Latest value' },
  { key: 'change12m', label: '% change 12 mo' },
  { key: 'asof', label: 'As-of date' },
  { key: 'weight', label: 'Recipe weight' },
];

const STATE_NOTE = {
  current: 'Current data',
  late: 'Data is late: readers see it marked "(late)"',
  forecast: 'Only forecast data: readers see it marked "(forecast)"',
  missing: 'No data: readers see the fallback with "as of"',
};

// A fallback cannot hold braces (they would end the token).
const cleanFallback = (text) => text.replace(/[{}]/g, '').trim();

export function buildToken({ measure, series, recipeCode, line, fallback }) {
  const fb = cleanFallback(fallback || '');
  const tail = fb ? `|fallback=${fb}` : '';
  if (measure === 'weight') {
    if (!recipeCode || !line) return null;
    return `{{recipe:${recipeCode.trim()}|${line.region}|${line.name}|weight${tail}}}`;
  }
  if (!series) return null;
  return `{{index:${series.key}|${measure}${tail}}}`;
}

export default function LiveNumberPicker({ isOpen, onClose, onInsert, teamId }) {
  const [measure, setMeasure] = useState('last');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [series, setSeries] = useState(null);
  const [recipeCode, setRecipeCode] = useState('');
  const [lines, setLines] = useState([]);
  const [line, setLine] = useState(null);
  const [fallback, setFallback] = useState('');
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState(null);

  const token = buildToken({ measure, series, recipeCode, line, fallback });

  // Series search, debounced.
  useEffect(() => {
    if (!isOpen || measure === 'weight') return undefined;
    const t = setTimeout(() => {
      api.get('/api/live/series', { params: { team_id: teamId, q: query, limit: 12 } })
        .then(({ data }) => { setResults(data); setErr(null); })
        .catch((e) => setErr(formatApiError(e) || 'Could not search series.'));
    }, 250);
    return () => clearTimeout(t);
  }, [isOpen, measure, query, teamId]);

  // Live preview of the token being built, debounced.
  useEffect(() => {
    if (!token) { setPreview(null); return undefined; }
    const t = setTimeout(() => {
      api.post('/api/live/preview', { token }, { params: { team_id: teamId } })
        .then(({ data }) => setPreview(data))
        .catch((e) => setErr(formatApiError(e) || 'Could not preview.'));
    }, 250);
    return () => clearTimeout(t);
  }, [token, teamId]);

  const findRecipe = () => {
    setLines([]); setLine(null);
    api.get('/api/live/recipe-lines', { params: { team_id: teamId, code: recipeCode.trim() } })
      .then(({ data }) => { setLines(data); setErr(null); })
      .catch((e) => setErr(formatApiError(e) || 'No such recipe.'));
  };

  const insert = () => {
    if (!token || !preview?.valid) return;
    onInsert(token);
    onClose();
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Insert live number" width={520}>
      <div style={{ display: 'grid', gap: 12, fontSize: 12 }}>
        <div role="radiogroup" aria-label="Show" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {MEASURES.map((m) => (
            <button key={m.key} type="button" role="radio" aria-checked={measure === m.key}
              className={`ca-btn ca-btn-sm ${measure === m.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
              onClick={() => setMeasure(m.key)}>
              {m.label}
            </button>
          ))}
        </div>

        {measure !== 'weight' ? (
          <div>
            <label className="ca-label" htmlFor="live-search">Series</label>
            <input id="live-search" className="ca-input" style={{ width: '100%' }} value={query}
              placeholder="Search by name or key, e.g. brent" onChange={(e) => setQuery(e.target.value)} />
            <div style={{ maxHeight: 180, overflowY: 'auto', marginTop: 6 }}>
              {results.map((r) => (
                <button key={r.key} type="button" onClick={() => setSeries(r)}
                  className={`ca-btn ca-btn-sm ${series?.key === r.key ? 'ca-btn-primary' : 'ca-btn-ghost'}`}
                  style={{ display: 'flex', width: '100%', justifyContent: 'space-between', marginBottom: 2 }}>
                  <span>{r.name} <span style={{ opacity: 0.7 }}>· {r.key}</span></span>
                  <span style={{ opacity: 0.7 }}>{r.last_actual || r.state}</span>
                </button>
              ))}
              {results.length === 0 && <div style={{ color: 'var(--muted)' }}>No series match.</div>}
            </div>
          </div>
        ) : (
          <div>
            <label className="ca-label" htmlFor="live-recipe">Recipe code</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input id="live-recipe" className="ca-input" style={{ flex: 1 }} value={recipeCode}
                placeholder="e.g. ACR-HEMA-LIQ" onChange={(e) => setRecipeCode(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') findRecipe(); }} />
              <button type="button" className="ca-btn ca-btn-sm ca-btn-ghost" onClick={findRecipe}
                disabled={!recipeCode.trim()}>Find</button>
            </div>
            {lines.length > 0 && (
              <select className="ca-input" style={{ width: '100%', marginTop: 6 }} aria-label="Recipe line"
                value={line ? `${line.region}|${line.name}` : ''}
                onChange={(e) => setLine(lines.find((l) => `${l.region}|${l.name}` === e.target.value) || null)}>
                <option value="">Pick a line…</option>
                {lines.map((l) => (
                  <option key={`${l.region}|${l.variant}|${l.name}`} value={`${l.region}|${l.name}`}>
                    {l.region} · {l.name} · {l.weight_pct}%
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div>
          <label className="ca-label" htmlFor="live-fallback">If missing, show</label>
          <input id="live-fallback" className="ca-input" style={{ width: '100%' }} value={fallback}
            placeholder="e.g. ~€400/t (shown with “as of” when there is no data)"
            onChange={(e) => setFallback(e.target.value)} />
        </div>

        <div className="ca-card" style={{ padding: 10 }}>
          <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase' }}>Preview</div>
          {preview ? (
            <>
              <div style={{ fontSize: 14, fontWeight: 600, margin: '4px 0' }}>{preview.display}</div>
              <div style={{ color: preview.valid ? 'var(--muted)' : 'var(--accent2)' }}>
                {preview.valid ? STATE_NOTE[preview.state] : `Cannot be read: ${preview.error}`}
              </div>
              <code style={{ display: 'block', marginTop: 6, fontSize: 10, wordBreak: 'break-all' }}>{token}</code>
            </>
          ) : (
            <div style={{ color: 'var(--muted)', marginTop: 4 }}>
              {measure === 'weight' ? 'Find a recipe and pick a line.' : 'Pick a series.'}
            </div>
          )}
        </div>

        {err && <div style={{ color: 'var(--accent2)' }}>{err}</div>}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="ca-btn ca-btn-sm ca-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="ca-btn ca-btn-sm ca-btn-primary" onClick={insert}
            disabled={!token || !preview?.valid}>Insert</button>
        </div>
      </div>
    </Modal>
  );
}
