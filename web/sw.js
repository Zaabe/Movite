/* Movite — service worker.
   - File di Movite (pagina, script, stile): prima la rete, ma se non risponde entro 1,5 s
     si apre la copia salvata sul telefono; così pagina e script restano sempre della stessa versione.
   - Librerie e font con versione fissa (unpkg, Google Fonts): dalla cache, aggiornati in background.
   - API e tile della mappa: sempre dalla rete. */
"use strict";

const VERSION = "movite-v1";
const SHELL = [
  "/", "/app.js", "/style.css", "/manifest.webmanifest",
  "/icons/icon-180.png", "/icons/icon-192.png", "/icons/icon-512.png",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function networkFirst(req, key) {
  return caches.open(VERSION).then((cache) => new Promise((resolve) => {
    let done = false;
    const fromCache = () => cache.match(key).then((c) => {
      if (!done && c) { done = true; resolve(c); }
      return c;
    });
    const timer = setTimeout(fromCache, 1500);
    fetch(req).then((res) => {
      clearTimeout(timer);
      if (res.ok) cache.put(key, res.clone());
      if (!done) { done = true; resolve(res); }
    }).catch(() => {
      clearTimeout(timer);
      fromCache().then((c) => { if (!done) { done = true; resolve(c || Response.error()); } });
    });
  }));
}

function cacheFirst(req) {
  return caches.open(VERSION).then(async (cache) => {
    const cached = await cache.match(req);
    const fresh = fetch(req).then((res) => {
      if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
      return res;
    }).catch(() => cached);
    return cached || fresh;
  });
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    if (url.pathname.startsWith("/api/")) return;
    const key = url.pathname === "/index.html" ? "/" : url.pathname;
    e.respondWith(networkFirst(req, key));
  } else if (url.hostname === "unpkg.com" || url.hostname.endsWith("fonts.googleapis.com") ||
             url.hostname.endsWith("fonts.gstatic.com")) {
    e.respondWith(cacheFirst(req));
  }
});
