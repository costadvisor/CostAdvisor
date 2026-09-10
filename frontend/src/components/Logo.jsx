/* The single source for the CostAdvisor brand mark — every place the logo
 * appears in the app renders it through this component. Swapping the logo
 * later means replacing frontend/logo.png; nothing here needs to change.
 *
 * The PNG itself is a rounded-square white card with the mark centered on
 * it (not a bare transparent cutout) — deliberately, so it reads correctly
 * everywhere it's placed: the browser tab (favicon area has no guaranteed
 * background color across browsers/OS light-dark settings) and the navbar
 * across every app theme (Mint's near-black background would otherwise
 * swallow the mark's navy half). One asset, one fixed look, no per-theme
 * variants to keep in sync.
 *
 * Imported (not a hardcoded "/logo.png" src) so Vite's build actually finds
 * it — a bare string path silently 404s in production because the build
 * moves the file into a hashed dist/assets/ path. The favicon link in
 * index.html gets rewritten too, but only because Vite's HTML transform
 * processes <link href>; it never rewrites plain string literals in JS. */
import logoUrl from '../../logo.png';

export default function Logo({ size = 32, style, className }) {
  return (
    <img
      src={logoUrl}
      alt="CostAdvisor"
      height={size}
      width={size}
      style={{ display: 'block', ...style }}
      className={className}
    />
  );
}
