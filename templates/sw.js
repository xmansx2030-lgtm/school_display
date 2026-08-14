/* Offline runtime cache for school display pages, assets and media.
 *
 * Versioned-asset contract
 * ------------------------
 * Every asset the display page loads carries a `?v=<release>` cache buster.
 * The cache key therefore MUST include the query string: a release that bumps
 * `?v=` has to miss the cache and reach the network, otherwise a screen keeps
 * running the previous build forever and no deploy ever lands on it.
 *
 * The offline lifeline is the only place a version-agnostic lookup is allowed:
 * when the network is unreachable, serving last release's file beats serving
 * nothing. It never runs while the screen is online.
 */

const RELEASE = 'v7';
const SHELL_CACHE = 'school-display-shell-' + RELEASE;
const RUNTIME_CACHE = 'school-display-runtime-' + RELEASE;
const EXPECTED_CACHES = [SHELL_CACHE, RUNTIME_CACHE];

/* Offline fallbacks only. These are unversioned URLs, so they are never what a
 * live screen actually loads — the page always requests `?v=<release>`. Keep
 * this list aligned with the assets `templates/website/display.html` links. */
const SHELL_ASSETS = [
  '/static/css/tailwind.generated.css',
  '/static/css/app.css',
  '/static/css/display-board.css',
  '/static/css/display-legacy.css',
  '/static/css/display-controls.css',
  '/static/css/fonts.css',
  '/static/js/display-controls.js',
  '/static/js/display.min.js',
  '/static/js/display-sw-register.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      // `addAll` is all-or-nothing; a single 404 would discard the whole shell.
      .then((cache) => Promise.all(
        SHELL_ASSETS.map((asset) => cache.add(asset).catch(() => undefined))
      ))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith('school-display-') && EXPECTED_CACHES.indexOf(key) === -1)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

/**
 * Drop every cached entry that shares this request's path but carries a
 * different `?v=`. Without this the runtime cache would keep one copy per
 * release forever and slowly fill the device's storage quota.
 */
async function dropSupersededVersions(cache, request) {
  try {
    const keepUrl = request.url;
    const keepPath = new URL(keepUrl).pathname;
    const keys = await cache.keys();
    await Promise.all(keys.map((key) => {
      if (key.url === keepUrl) return undefined;
      let path = '';
      try {
        path = new URL(key.url).pathname;
      } catch (_) {
        return undefined;
      }
      return path === keepPath ? cache.delete(key) : undefined;
    }));
  } catch (_) {
    /* Pruning is housekeeping; never let it fail a response. */
  }
}

/**
 * Assets are immutable per release, so an exact-URL hit is served straight from
 * cache. A miss means a new release: go to the network, store it under the new
 * key, and retire the previous one. Only a network failure falls back to a
 * version-agnostic match, which keeps an offline screen booting.
 */
async function handleAsset(request) {
  const cache = await caches.open(RUNTIME_CACHE);

  const exact = await cache.match(request);
  if (exact) return exact;

  try {
    const response = await fetch(request);
    if (response && response.ok) {
      await cache.put(request, response.clone());
      await dropSupersededVersions(cache, request);
    }
    return response;
  } catch (offline) {
    const anyVersion =
      (await cache.match(request, { ignoreSearch: true })) ||
      (await caches.match(request, { ignoreSearch: true, cacheName: SHELL_CACHE }));
    if (anyVersion) return anyVersion;
    throw offline;
  }
}

/**
 * The display page is data-bearing HTML, so it is always network-first; the
 * cached copy exists purely so a screen that boots without a network still
 * shows its last known board instead of a browser error page.
 */
async function handleNavigation(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok) await cache.put(request, response.clone());
    return response;
  } catch (offline) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw offline;
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  /* Media elements use byte-range requests for playback and seeking. Cache
   * Storage cannot store a 206 Partial Content response; trying to do so makes
   * handleAsset fall into its offline branch and turns a successful audio
   * response into a playback error. Leave range requests to the browser's
   * normal HTTP cache, which understands 206 responses. */
  if (request.headers.has('range')) return;

  let url;
  try {
    url = new URL(request.url);
  } catch (_) {
    return;
  }

  if (url.origin !== self.location.origin) return;
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/dashboard/') ||
    url.pathname.startsWith('/admin/')
  ) {
    return;
  }

  const isAsset =
    url.pathname.startsWith('/static/') ||
    url.pathname.startsWith('/media/') ||
    /\.(png|jpe?g|webp|gif|svg|css|js|woff2?)$/i.test(url.pathname);

  if (isAsset) {
    event.respondWith(handleAsset(request));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(handleNavigation(request));
  }
});
