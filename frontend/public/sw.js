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

// ── Web push ──────────────────────────────────────────────────────────────
// The payload is {title, body, url}, sent by services/push.py. `url` is what
// makes a notification worth tapping: an alert about one product should open
// that product, not the dashboard and a search.

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    // A relay can deliver an empty or non-JSON push (some browsers send one
    // to verify a subscription). Showing something generic is better than
    // throwing inside the handler, which some browsers punish by dropping
    // the subscription.
    payload = {};
  }
  const title = payload.title || 'CostAdvisor';
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || '',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      // Collapses repeats of the same alert on the device rather than
      // stacking them, matching the dedup the alert ledger already does
      // server-side.
      tag: payload.tag || payload.url || 'costadvisor',
      data: { url: payload.url || '/' },
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Focus an already-open tab rather than opening a second one — an app
      // that spawns a new window per notification is quickly unusable.
      for (const client of clients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
