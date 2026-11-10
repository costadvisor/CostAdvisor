// A live-number token (Jira 210), e.g. {{index:brent|last|fallback=about 130}}.
// The server fills these in before any text reaches the browser, so an export
// built from API data never meets one. This is the last line of defence: an
// export must hold plain values, never a token — so a raw one becomes its
// fallback (or "n/a"), and the console says which export let it through.
const LIVE_TOKEN = /\{\{(?:index|recipe|bar):([^{}]*)\}\}/g;

export function plainValue(s) {
  if (!s.includes('{{')) return s;
  return s.replace(LIVE_TOKEN, (_raw, body) => {
    const at = body.indexOf('|fallback=');
    return at === -1 ? 'n/a' : body.slice(at + '|fallback='.length).trim() || 'n/a';
  });
}

export default function exportCsv(filename, headers, rows) {
  let leaked = false;
  const escape = (v) => {
    if (v === null || v === undefined) return '';
    const raw = String(v);
    const s = plainValue(raw);
    if (s !== raw) leaked = true;
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers.map(escape).join(','), ...rows.map(r => r.map(escape).join(','))].join('\n');
  if (leaked) console.warn(`${filename}: a live-number token reached the export unfilled; its fallback was written`);
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
