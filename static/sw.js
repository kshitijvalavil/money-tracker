// Money Tracker service worker — makes the app installable and gives it an
// offline app shell. The server (app.py, /sw.js) replaces "money-tracker-v1"
// with a hash of the static files, so each deploy gets a fresh cache and old
// caches are cleared on activate.
const CACHE = "money-tracker-v1";

// The app shell: static assets and the offline fallback page. Page routes
// themselves are not precached — they need a signed-in, fresh fetch — so
// navigations go to the network first and fall back to /offline.
const SHELL = [
  "/offline",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/demo.js",
  "/static/js/recurring.js",
  "/static/vendor/supabase.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                       // never touch writes
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;        // let Supabase etc. pass through

  // Page navigations: network first (so data is fresh), offline -> /offline.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/offline", { ignoreSearch: true })
        .then((r) => r || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } })))
    );
    return;
  }

  // Same-origin static assets: cache first, then network (and cache it).
  event.respondWith(
    caches.match(req).then((hit) =>
      hit || fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      }).catch(() => hit)
    )
  );
});
