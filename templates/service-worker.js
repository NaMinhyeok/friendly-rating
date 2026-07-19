const CACHE_PREFIX = "woorisai-";
const STATIC_CACHE = `${CACHE_PREFIX}static-v10`;
const PUSH_HANDOFF_CACHE = `${CACHE_PREFIX}push-handoff-v1`;
const PUSH_HANDOFF_KEY = "/__woorisai/pending-push-navigation";
const PUSH_HANDOFF_TTL_MS = 5 * 60 * 1000;
const PUSH_HANDOFF_TARGET_LEASE_MS = 30 * 1000;
const PUSH_HANDOFF_VERSION = 1;
const PUSH_NAVIGATION_READY = "woorisai:push-navigation-ready";
const PUSH_NAVIGATION_AVAILABLE = "woorisai:push-navigation-available";
const PUSH_NAVIGATION_OPEN = "woorisai:push-navigation-open";
const PUSH_NAVIGATION_CONSUMED = "woorisai:push-navigation-consumed";
const RETAINED_CACHES = new Set([STATIC_CACHE, PUSH_HANDOFF_CACHE]);
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
            .filter(
              (key) => key.startsWith(CACHE_PREFIX) && !RETAINED_CACHES.has(key),
            )
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

// WebKit can open the PWA root and drop early messages. Keep one route until
// the selected client reaches the thread (or preserves it through login) and ACKs.
let pushHandoffStorageTail = Promise.resolve();
const pendingPushNavigationSelections = new Set();

function runPushHandoffStorageOperation(operation) {
  const result = pushHandoffStorageTail.then(operation, operation);
  pushHandoffStorageTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

function createPushHandoffResponse(record) {
  return new Response(JSON.stringify(record), {
    headers: { "Content-Type": "application/json" },
  });
}

async function savePendingPushNavigation(path) {
  const record = {
    createdAt: Date.now(),
    id: crypto.randomUUID(),
    path,
    targetClientId: null,
    targetedAt: null,
    version: PUSH_HANDOFF_VERSION,
  };
  pendingPushNavigationSelections.add(record.id);
  try {
    await runPushHandoffStorageOperation(async () => {
      const cache = await caches.open(PUSH_HANDOFF_CACHE);
      const previousRecord = await readPendingPushNavigation(cache);
      try {
        await cache.put(PUSH_HANDOFF_KEY, createPushHandoffResponse(record));
      } catch (error) {
        try {
          const currentRecord = await readPendingPushNavigation(cache);
          if (
            previousRecord &&
            currentRecord?.id === previousRecord.id
          ) {
            await cache.delete(PUSH_HANDOFF_KEY);
          }
        } catch (cleanupError) {
          console.warn(
            "교체하지 못한 이전 푸시 이동을 정리하지 못했어요.",
            cleanupError,
          );
        }
        throw error;
      }
    });
  } catch (error) {
    pendingPushNavigationSelections.delete(record.id);
    throw error;
  }
  return record;
}

function normalizePendingPushNavigation(value) {
  if (
    !value ||
    typeof value !== "object" ||
    value.version !== PUSH_HANDOFF_VERSION ||
    !Number.isFinite(value.createdAt) ||
    typeof value.id !== "string" ||
    value.id.length === 0 ||
    value.id.length > 128 ||
    (value.targetClientId !== null &&
      (typeof value.targetClientId !== "string" ||
        value.targetClientId.length === 0 ||
        value.targetClientId.length > 128)) ||
    (value.targetedAt !== null && !Number.isFinite(value.targetedAt)) ||
    (value.targetClientId === null) !== (value.targetedAt === null)
  ) {
    return null;
  }

  const age = Date.now() - value.createdAt;
  if (age < 0 || age > PUSH_HANDOFF_TTL_MS) {
    return null;
  }

  const path = readScoreThreadUrl(value.path);
  return path
    ? {
        createdAt: value.createdAt,
        id: value.id,
        path,
        targetClientId: value.targetClientId,
        targetedAt: value.targetedAt,
        version: value.version,
      }
    : null;
}

async function readPendingPushNavigation(cache) {
  const response = await cache.match(PUSH_HANDOFF_KEY);
  if (!response) {
    return null;
  }

  let record = null;
  try {
    record = normalizePendingPushNavigation(await response.json());
  } catch {
    // Invalid records are removed below.
  }
  if (!record) {
    await cache.delete(PUSH_HANDOFF_KEY);
  }
  return record;
}

async function targetPendingPushNavigation(recordId, clientId) {
  return runPushHandoffStorageOperation(async () => {
    const cache = await caches.open(PUSH_HANDOFF_CACHE);
    const record = await readPendingPushNavigation(cache);
    if (!record || record.id !== recordId) {
      return false;
    }

    record.targetClientId = clientId;
    record.targetedAt = Date.now();
    await cache.put(PUSH_HANDOFF_KEY, createPushHandoffResponse(record));
    return true;
  });
}

async function clearPendingPushNavigationTarget(recordId, clientId) {
  return runPushHandoffStorageOperation(async () => {
    const cache = await caches.open(PUSH_HANDOFF_CACHE);
    const record = await readPendingPushNavigation(cache);
    if (
      !record ||
      record.id !== recordId ||
      record.targetClientId !== clientId
    ) {
      return false;
    }

    record.targetClientId = null;
    record.targetedAt = null;
    await cache.put(PUSH_HANDOFF_KEY, createPushHandoffResponse(record));
    return true;
  });
}

async function readPendingPushNavigationForClient(client) {
  return runPushHandoffStorageOperation(async () => {
    const cache = await caches.open(PUSH_HANDOFF_CACHE);
    const record = await readPendingPushNavigation(cache);
    if (
      !record ||
      pendingPushNavigationSelections.has(record.id) ||
      typeof client.id !== "string" ||
      client.id.length === 0
    ) {
      return null;
    }

    const clientPath = readClientPath(client.url);
    const isDestination = clientPath === record.path;
    const isLoginContinuation = readLoginNextThreadUrl(client.url) === record.path;
    const isTarget = record.targetClientId === client.id;
    let isOrphanedTarget = false;
    if (
      record.targetClientId !== null &&
      !isTarget &&
      !isDestination &&
      !isLoginContinuation
    ) {
      const targetLeaseAge = Date.now() - record.targetedAt;
      isOrphanedTarget =
        targetLeaseAge > PUSH_HANDOFF_TARGET_LEASE_MS ||
        !(await self.clients.get(record.targetClientId));
    }
    if (
      !isDestination &&
      !isLoginContinuation &&
      !isTarget &&
      record.targetClientId !== null &&
      !isOrphanedTarget
    ) {
      return null;
    }

    if (!isTarget) {
      record.targetClientId = client.id;
      record.targetedAt = Date.now();
      try {
        await cache.put(PUSH_HANDOFF_KEY, createPushHandoffResponse(record));
      } catch (error) {
        console.warn("푸시 이동 대상을 다시 저장하지 못했어요.", error);
      }
    }
    return record;
  });
}

async function consumePendingPushNavigation(recordId, client) {
  return runPushHandoffStorageOperation(async () => {
    const cache = await caches.open(PUSH_HANDOFF_CACHE);
    const record = await readPendingPushNavigation(cache);
    if (
      !record ||
      record.id !== recordId
    ) {
      return false;
    }

    const clientPath = readClientPath(client.url);
    const isDestination = clientPath === record.path;
    const isLogin =
      clientPath !== null && /^\/login\/(?:\?.*)?$/.test(clientPath);
    const isTarget = record.targetClientId === client.id;
    if (!isTarget && !isDestination && !isLogin) {
      return false;
    }
    return cache.delete(PUSH_HANDOFF_KEY);
  });
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

function readLoginNextThreadUrl(value) {
  try {
    const url = new URL(value, self.location.origin);
    if (url.origin !== self.location.origin || url.pathname !== "/login/") {
      return null;
    }
    return readScoreThreadUrl(url.searchParams.get("next"));
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

function announcePendingPushNavigation(client) {
  try {
    client.postMessage({ type: PUSH_NAVIGATION_AVAILABLE });
    return true;
  } catch (error) {
    console.warn("열려 있는 앱에 푸시 이동을 알리지 못했어요.", error);
    return false;
  }
}

async function findPreferredAppClient(excludedClientIds) {
  const windowClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  const appClients = windowClients.filter(
    (client) =>
      isAppClient(client.url) && !excludedClientIds.has(client.id),
  );
  return (
    appClients.find((client) => client.focused === true) ||
    appClients.find((client) => client.visibilityState === "visible") ||
    (appClients.length === 1 ? appClients[0] : null)
  );
}

async function deliverPendingPushNavigation(client) {
  let pendingNavigation;
  try {
    pendingNavigation = await readPendingPushNavigationForClient(client);
  } catch (error) {
    console.warn("저장된 푸시 이동을 확인하지 못했어요.", error);
    return;
  }
  if (!pendingNavigation) {
    return;
  }

  try {
    client.postMessage({
      id: pendingNavigation.id,
      path: pendingNavigation.path,
      type: PUSH_NAVIGATION_OPEN,
    });
  } catch (error) {
    console.warn("앱에 푸시 이동을 전달하지 못했어요.", error);
    try {
      await clearPendingPushNavigationTarget(
        pendingNavigation.id,
        client.id,
      );
    } catch (releaseError) {
      console.warn("실패한 푸시 이동 대상을 해제하지 못했어요.", releaseError);
    }
  }
}

async function openScoreThread(
  threadUrl,
  excludedClientIds,
  retryClients,
) {
  const windowClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  const threadClient = windowClients.find(
    (client) => readClientPath(client.url) === threadUrl,
  );
  if (threadClient) {
    try {
      return await threadClient.focus();
    } catch (error) {
      excludedClientIds.add(threadClient.id);
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
        try {
          return await navigatedClient.focus();
        } catch (error) {
          excludedClientIds.add(navigatedClient.id);
          console.warn("이동한 점수 대화를 활성화하지 못했어요.", error);
        }
      } else {
        retryClients.push(appClient);
      }
    } catch (error) {
      excludedClientIds.add(appClient.id);
      retryClients.push(appClient);
      console.warn("열린 앱을 점수 대화로 이동하지 못했어요.", error);
    }
  }

  return self.clients.openWindow(threadUrl);
}

self.addEventListener("message", (event) => {
  if (!event.source || !isAppClient(event.source.url)) {
    return;
  }

  if (event.data?.type === PUSH_NAVIGATION_READY) {
    event.waitUntil(deliverPendingPushNavigation(event.source));
    return;
  }
  if (
    event.data?.type === PUSH_NAVIGATION_CONSUMED &&
    typeof event.data.id === "string"
  ) {
    event.waitUntil(
      consumePendingPushNavigation(event.data.id, event.source),
    );
  }
});

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

  event.waitUntil(
    (async () => {
      const pendingNavigationPromise = savePendingPushNavigation(threadUrl).catch(
        (error) => {
          console.warn("푸시 이동을 보관하지 못했어요.", error);
          return null;
        },
      );
      const excludedClientIds = new Set();
      const retryClients = [];
      const clientPromise = openScoreThread(
        threadUrl,
        excludedClientIds,
        retryClients,
      ).catch((error) => {
        console.warn("점수 대화를 열지 못했어요.", error);
        return null;
      });
      const [pendingNavigation, openedClient] = await Promise.all([
        pendingNavigationPromise,
        clientPromise,
      ]);

      if (!pendingNavigation && openedClient) {
        try {
          openedClient.postMessage({
            path: threadUrl,
            type: PUSH_NAVIGATION_OPEN,
          });
        } catch (error) {
          console.warn("앱에 푸시 이동을 바로 전달하지 못했어요.", error);
        }
        return;
      }

      if (!pendingNavigation) {
        return;
      }

      let targetClient = openedClient;
      if (!targetClient) {
        try {
          targetClient = await findPreferredAppClient(excludedClientIds);
        } catch (error) {
          console.warn("푸시 이동을 받을 앱을 확인하지 못했어요.", error);
        }
      }

      if (targetClient && typeof targetClient.id === "string") {
        let wasAnnounced = false;
        try {
          const isTargeted = await targetPendingPushNavigation(
            pendingNavigation.id,
            targetClient.id,
          );
          pendingPushNavigationSelections.delete(pendingNavigation.id);
          if (isTargeted) {
            wasAnnounced = announcePendingPushNavigation(targetClient);
            if (!wasAnnounced) {
              await clearPendingPushNavigationTarget(
                pendingNavigation.id,
                targetClient.id,
              );
            }
          }
        } catch (error) {
          pendingPushNavigationSelections.delete(pendingNavigation.id);
          console.warn("푸시 이동 대상을 저장하지 못했어요.", error);
          wasAnnounced = announcePendingPushNavigation(targetClient);
        }
        if (!wasAnnounced) {
          for (const retryClient of retryClients) {
            if (retryClient.id !== targetClient.id) {
              announcePendingPushNavigation(retryClient);
            }
          }
        }
        return;
      }

      pendingPushNavigationSelections.delete(pendingNavigation.id);
      for (const retryClient of retryClients) {
        announcePendingPushNavigation(retryClient);
      }
    })(),
  );
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
