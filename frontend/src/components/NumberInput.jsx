import { useState, useEffect } from 'react';

/**
 * Controlled numeric field that emits a NUMBER but lets the user type freely.
 *
 * WHY THIS EXISTS — two bugs kept recurring in the numeric fields:
 *
 * 1. **The sticky leading zero.** Fields defaulted to `0`, so typing "52" into a
 *    box already showing "0" produced "052". `Number("052")` is 52, so state was
 *    correct while the display was wrong — React's `type="number"` DOM sync
 *    compares loosely (`"052" == 52` is true), decides nothing changed, and leaves
 *    the stale text on screen. The number was right and the screen lied.
 *
 * 2. **Decimals were unenterable mid-keystroke.** `<input type="number">` reports
 *    `.value` as `""` for any transient non-numeric string, so "1." on the way to
 *    "1.5" read as empty and wiped the entry.
 *
 * Both go away by holding the in-progress TEXT locally and only lifting a parsed
 * number to the parent. `type="text"` + `inputMode="decimal"` sidesteps React's
 * number-input quirk entirely and still shows a numeric keypad on mobile; the
 * trade is losing the native spinner arrows, which these fields don't need.
 *
 * Empty input emits `null` rather than `0` — "no value yet" and "zero" are
 * different answers, and defaulting to 0 is what created bug 1.
 */
export default function NumberInput({
  value,
  onChange,
  placeholder = '0',
  className = 'ca-input',
  allowNegative = true,
  ...rest
}) {
  const asText = (v) => (v == null || v === '' ? '' : String(v));
  // Local text is always what's displayed. Gating on focus made the field
  // sensitive to blur and to any parent re-render that reset local state, which
  // could swallow a trailing "." mid-entry and turn "1.5" into "15".
  const [text, setText] = useState(() => asText(value));

  // Adopt an external change only when the parent genuinely disagrees with what's
  // typed. "1." and "1.50" both parse to the number the parent already holds, so
  // they survive; a real reset (parent clears or loads a record) shows through.
  useEffect(() => {
    const typed = text === '' ? null : Number(text);
    const incoming = value == null || value === '' ? null : Number(value);
    if (typed !== incoming) setText(asText(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const handleChange = (raw) => {
    let next = raw.replace(allowNegative ? /[^\d.-]/g : /[^\d.]/g, '');
    // Collapse a leading run of zeros: "052" -> "52", "-007" -> "-7".
    // Keeps a lone "0" and preserves "0.5".
    next = next.replace(/^(-?)0+(?=\d)/, '$1');
    setText(next);

    if (next === '' || next === '-' || next === '.' || next === '-.') {
      onChange(null);
      return;
    }
    const n = Number(next);
    if (Number.isFinite(n)) onChange(n);
  };

  return (
    <input
      type="text"
      inputMode="decimal"
      className={className}
      value={text}
      placeholder={placeholder}
      onChange={(e) => handleChange(e.target.value)}
      {...rest}
    />
  );
}
