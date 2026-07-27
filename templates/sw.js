/* Offline runtime cache for school display pages, assets and media. */

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open('school-display-shell-v3')
      .then((cache) => cache.addAll([
        '/static/css/tailwind.generated.css',
        '/static/css/display-controls.css',
        '/static/css/app.css',
        '/static/js/display-controls.js',
        '/static/js/display.js'
      ]))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith('school-display-') || !['school-display-shell-v3', 'school-display-runtime-v3'].includes(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/dashboard/') || url.pathname.startsWith('/admin/')) return;

  const isAsset = url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/') || /\.(png|jpe?g|webp|gif|svg|css|js|woff2?)$/i.test(url.pathname);
  if (isAsset) {
    event.respondWith(
      caches.open('school-display-runtime-v3').then(async (cache) => {
        const cached = await cache.match(request, {ignoreSearch: true});
        const network = fetch(request).then((response) => {
          if (response && response.ok) cache.put(request, response.clone());
          return response;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      caches.open('school-display-runtime-v3').then(async (cache) => {
        try {
          const response = await fetch(request);
          if (response && response.ok) await cache.put(request, response.clone());
          return response;
        } catch (_) {
          return (await cache.match(request)) || Response.error();
        }
      })
    );
  }
});
