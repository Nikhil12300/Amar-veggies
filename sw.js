/* ============================================================
   Amar Veggies — Service Worker
   Strategy:
     • App shell (HTML, fonts, CDN scripts) → Cache First
     • API calls (/api/*) → Network First with offline fallback
     • Images → Cache First with background refresh
   ============================================================ */

const CACHE_NAME   = "amar-veggies-v1";
const API_ORIGIN   = "https://amar-veggies.onrender.com";

// Resources to pre-cache on install (app shell)
const PRECACHE = [
  "/",
  "/index.html",
  "/manifest.json",
  "/icon-192.png",
  "/icon-512.png",
  "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400;1,600&family=Outfit:wght@300;400;500;600;700&display=swap",
  "https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.23.2/babel.min.js"
];

// ── INSTALL: pre-cache app shell ──────────────────────────────
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: clear old caches ────────────────────────────────
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── FETCH ─────────────────────────────────────────────────────
self.addEventListener("fetch", event => {
  const { request } = event;
  const url = new URL(request.url);

  // 1. API calls → Network First, fall back to a JSON error
  if (url.origin === API_ORIGIN || url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request));
    return;
  }

  // 2. Google Fonts CSS → Cache First (changes rarely)
  if (url.origin === "https://fonts.googleapis.com" ||
      url.origin === "https://fonts.gstatic.com") {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 3. CDN scripts → Cache First
  if (url.origin === "https://cdnjs.cloudflare.com") {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 4. Same-origin requests (app shell, icons) → Cache First
  if (url.origin === self.location.origin) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 5. Everything else → Network with cache fallback
  event.respondWith(networkFirst(request));
});

/* ── Strategies ── */

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return offlineFallback(request);
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok && request.method === "GET") {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    return offlineFallback(request);
  }
}

function offlineFallback(request) {
  const url = new URL(request.url);
  // API request while offline → return a helpful JSON error
  if (url.pathname.startsWith("/api/")) {
    return new Response(
      JSON.stringify({ detail: "You appear to be offline. Please check your connection." }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    );
  }
  // Navigation request while offline → serve cached index.html
  if (request.mode === "navigate") {
    return caches.match("/index.html");
  }
  return new Response("Offline", { status: 503 });
}

// ── BACKGROUND SYNC: retry failed orders ─────────────────────
self.addEventListener("sync", event => {
  if (event.tag === "retry-order") {
    event.waitUntil(retryPendingOrders());
  }
});

async function retryPendingOrders() {
  // Reads pending orders stored by the app in IndexedDB/localStorage
  // and retries them. The app sets these before going offline.
  // (Full implementation requires the app to write to IndexedDB on submit failure)
  console.log("[SW] Background sync: retrying pending orders");
}

// ── PUSH NOTIFICATIONS (ready for future use) ─────────────────
self.addEventListener("push", event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || "Amar Veggies", {
      body: data.body || "Your order has been updated!",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.tag || "order-update",
      data: { url: data.url || "/" }
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window" }).then(list => {
      const url = event.notification.data?.url || "/";
      for (const client of list) {
        if (client.url === url && "focus" in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
