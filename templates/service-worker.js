const CACHE_PREFIX = "woorisai-";
const STATIC_CACHE = `${CACHE_PREFIX}static-v4`;
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

function readScoreThreadUrl(value) {
  if (typeof value !== "string") {
    return null;
  }

  try {
    const url = new URL(value, self.location.origin);
    if (
      url.origin !== self.location.origin ||
      !/^\/history\/[1-9]\d*\/$/.test(url.pathname)
    ) {
      return null;
    }
    return `${url.pathname}${url.search}`;
  } catch {
    return null;
  }
}

function readClientPath(value) {
  try {
    const url = new URL(value, self.location.origin);
    if (url.origin !== self.location.origin) {
      return null;
    }
    return `${url.pathname}${url.search}`;
  } catch {
    return null;
  }
}

function isAppClient(value) {
  const clientPath = readClientPath(value);
  return (
    clientPath !== null &&
    /^(?:\/|\/login\/|\/history\/|\/history\/[1-9]\d*\/)(?:\?.*)?$/.test(
      clientPath,
    )
  );
}

async function openScoreThread(threadUrl) {
  const windowClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  const threadClient = windowClients.find(
    (client) => readClientPath(client.url) === threadUrl,
  );
  if (threadClient) {
    try {
      await threadClient.focus();
      return;
    } catch (error) {
      console.warn("열린 점수 대화를 활성화하지 못했어요.", error);
    }
  }

  const appClient = windowClients.find(
    (client) => client !== threadClient && isAppClient(client.url),
  );
  if (appClient && typeof appClient.navigate === "function") {
    try {
      const navigatedClient = await appClient.navigate(threadUrl);
      if (navigatedClient) {
        await navigatedClient.focus();
        return;
      }
    } catch (error) {
      console.warn("열린 앱을 점수 대화로 이동하지 못했어요.", error);
    }
  }

  await self.clients.openWindow(threadUrl);
}

// Firebase's listener focuses an existing same-host window without navigating it.
// Register first so notification clicks always open the intended score thread.
self.addEventListener("notificationclick", (event) => {
  const firebaseMessage = event.notification?.data?.FCM_MSG;
  if (event.action || !firebaseMessage || typeof firebaseMessage !== "object") {
    return;
  }

  event.stopImmediatePropagation();
  event.notification.close();
  const threadUrl = readScoreThreadUrl(firebaseMessage.fcmOptions?.link);
  if (!threadUrl) {
    return;
  }

  event.waitUntil(openScoreThread(threadUrl));
});

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
