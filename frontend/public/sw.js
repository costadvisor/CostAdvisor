// Hand-written, no build-time generation (no vite-plugin-pwa/Workbox) — keeps
// the dependency footprint at zero, same choice as the reference implementation
// this was modeled on. Only makes the app installable and its static build
// output fast on repeat loads; deliberately does NOT cache /api or /auth
// responses or the HTML shell itself — this is a live, authenticated app, not
// a content site, and serving stale should-cost data or a stale index.html
// forever from cache would be a worse failure mode than no offline support.
const VERSION = 'v1';
const ASSET_CACHE = `ca-assets-${VERSION}`;
const IMAGE_CACHE = `ca-images-${VERSION}`;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  const keep = new Set([ASSET_CACHE, IMAGE_CACHE]);
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => !keep.has(n)).map((n) => caches.delete(n))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Vite's build output is content-hashed (a new deploy gets new filenames),
  // so cache-first is always safe here -- there's no "stale asset" case.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.open(ASSET_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok) cache.put(request, response.clone());
        return response;
      }),
    );
    return;
  }

  // Images: show the cached one instantly if there is one, refresh in the
  // background either way -- fine to be a request stale by one load.
  if (request.destination === 'image') {
    event.respondWith(
      caches.open(IMAGE_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const fetchPromise = fetch(request).then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        }).catch(() => cached);
        return cached || fetchPromise;
      }),
    );
    return;
  }

  // Everything else (HTML shell, /api, /auth) -- untouched, straight to network.
});
