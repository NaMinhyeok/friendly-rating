const CACHE_PREFIX = "woorisai-";
const STATIC_CACHE = `${CACHE_PREFIX}static-v2`;
const OFFLINE_URL = "/static/ratings/offline.html";
const PRECACHE_URLS = [
  OFFLINE_URL,
  "/static/ratings/manifest.webmanifest",
  "/static/ratings/icons/icon-192.png",
  "/static/ratings/icons/icon-512.png",
  "/static/ratings/icons/maskable-icon-512.png",
  "/static/ratings/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(CACHE_PREFIX) && key !== STATIC_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then(
          (response) =>
            response ||
            new Response("오프라인 상태예요. 인터넷 연결을 확인해 주세요.", {
              headers: { "Content-Type": "text/plain; charset=utf-8" },
              status: 503,
            }),
        ),
      ),
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(cacheStaticAsset(request));
  }
});

async function cacheStaticAsset(request) {
  const cached = await caches.match(request);
  if (cached) {
    return cached;
  }

  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

const FIREBASE_CONFIG = {{ firebase_config_json|safe }};

if (Object.keys(FIREBASE_CONFIG).length > 0) {
  try {
    importScripts("https://www.gstatic.com/firebasejs/12.16.0/firebase-app-compat.js");
    importScripts("https://www.gstatic.com/firebasejs/12.16.0/firebase-messaging-compat.js");

    firebase.initializeApp(FIREBASE_CONFIG);
    firebase.messaging();
  } catch (error) {
    console.warn("Firebase Messaging을 초기화하지 못했어요.", error);
  }
}
