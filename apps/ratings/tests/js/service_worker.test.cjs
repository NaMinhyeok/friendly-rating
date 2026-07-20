const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const serviceWorkerTemplatePath = path.resolve(
  __dirname,
  "../../../../templates/service-worker.js",
);
const serviceWorkerTemplate = fs.readFileSync(serviceWorkerTemplatePath, "utf8");
const firebaseConfigMarker = "{{ firebase_config_json|safe }}";
assert.equal(serviceWorkerTemplate.split(firebaseConfigMarker).length - 1, 1);

const HANDOFF_CACHE = "woorisai-push-handoff-v1";
const HANDOFF_KEY = "https://friendly.test/__woorisai/pending-push-navigation";
const READY_MESSAGE = "woorisai:push-navigation-ready";
const AVAILABLE_MESSAGE = "woorisai:push-navigation-available";
const OPEN_MESSAGE = "woorisai:push-navigation-open";
const CONSUMED_MESSAGE = "woorisai:push-navigation-consumed";
let nextClientId = 0;

function renderServiceWorker(firebaseConfig = {}) {
  return serviceWorkerTemplate.replace(
    firebaseConfigMarker,
    JSON.stringify(firebaseConfig),
  );
}

function createWindowClient(
  url,
  {
    focusOutcome = "client",
    focused = false,
    id = `client-${++nextClientId}`,
    messageOutcome = "success",
    navigateOutcome = "client",
    visibilityState = "hidden",
  } = {},
) {
  const calls = { actions: [], focus: 0, messages: [], navigate: [] };
  const client = {
    focused,
    id,
    messageOutcome,
    url,
    visibilityState,
    async focus() {
      calls.actions.push("focus");
      calls.focus += 1;
      if (focusOutcome === "reject") {
        throw new TypeError("This window cannot be focused.");
      }
      client.focused = true;
      client.visibilityState = "visible";
      return client;
    },
    async navigate(targetUrl) {
      calls.actions.push(`navigate:${targetUrl}`);
      calls.navigate.push(targetUrl);
      if (navigateOutcome === "reject") {
        throw new TypeError("This client is not controlled by the service worker.");
      }
      if (navigateOutcome === "null") {
        return null;
      }
      client.url = new URL(targetUrl, "https://friendly.test").href;
      return client;
    },
    postMessage(message) {
      if (client.messageOutcome === "reject") {
        throw new TypeError("This client cannot receive messages.");
      }
      if (client.messageOutcome === "drop") {
        return;
      }
      calls.messages.push(message);
    },
  };
  return { calls, client };
}

function normalizeCacheKey(value) {
  const rawValue = typeof value === "string" ? value : value.url;
  return new URL(rawValue, "https://friendly.test").href;
}

function createCacheStorage({
  beforeHandoffPut,
  failHandoffPuts = new Set(),
  failPut = false,
} = {}) {
  const stores = new Map();
  const deletedCaches = [];
  let handoffPutCount = 0;

  const open = async (name) => {
    if (!stores.has(name)) {
      stores.set(name, new Map());
    }
    const entries = stores.get(name);
    return {
      async addAll() {},
      async delete(key) {
        return entries.delete(normalizeCacheKey(key));
      },
      async match(key) {
        return entries.get(normalizeCacheKey(key))?.clone();
      },
      async put(key, response) {
        if (name === HANDOFF_CACHE) {
          handoffPutCount += 1;
          if (failPut || failHandoffPuts.has(handoffPutCount)) {
            throw new TypeError("Pending navigation cannot be stored.");
          }
          if (beforeHandoffPut) {
            await beforeHandoffPut();
          }
        }
        entries.set(normalizeCacheKey(key), response.clone());
      },
    };
  };

  return {
    deletedCaches,
    stores,
    async delete(name) {
      deletedCaches.push(name);
      return stores.delete(name);
    },
    async keys() {
      return [...stores.keys()];
    },
    async match(key) {
      for (const entries of stores.values()) {
        const response = entries.get(normalizeCacheKey(key));
        if (response) {
          return response.clone();
        }
      }
      return undefined;
    },
    open,
  };
}

function createHarness({
  cacheStorage = createCacheStorage(),
  firebaseConfig = {},
  openWindowClient = null,
  openWindowOutcome = "client",
  windowClients = [],
} = {}) {
  const importStates = [];
  const listeners = new Map();
  const openedUrls = [];
  const matchAllCalls = [];
  const warnings = [];
  let uuidCounter = 0;
  const self = {
    clients: {
      claim: async () => undefined,
      async get(id) {
        return windowClients.find((client) => client.id === id);
      },
      async matchAll(options) {
        matchAllCalls.push(options);
        return windowClients;
      },
      async openWindow(url) {
        openedUrls.push(url);
        if (openWindowOutcome === "reject") {
          throw new TypeError("A window cannot be opened.");
        }
        if (openWindowClient && !windowClients.includes(openWindowClient)) {
          windowClients.push(openWindowClient);
        }
        return openWindowOutcome === "null" ? null : openWindowClient;
      },
    },
    location: {
      href: "https://friendly.test/service-worker.js",
      origin: "https://friendly.test",
    },
    addEventListener(type, listener) {
      const handlers = listeners.get(type) || [];
      handlers.push(listener);
      listeners.set(type, handlers);
    },
    skipWaiting: async () => undefined,
  };
  const sandbox = {
    crypto: {
      randomUUID() {
        return `push-navigation-${++uuidCounter}`;
      },
    },
    Date,
    Response,
    URL,
    caches: cacheStorage,
    console: {
      warn(...items) {
        warnings.push(items);
      },
    },
    fetch: async () => undefined,
    firebase: {
      initializeApp() {},
      messaging() {},
    },
    importScripts(url) {
      importStates.push({
        notificationClickHandlers: listeners.get("notificationclick")?.length || 0,
        url,
      });
    },
    self,
  };

  vm.runInNewContext(renderServiceWorker(firebaseConfig), sandbox, {
    filename: serviceWorkerTemplatePath,
  });

  return {
    cacheStorage,
    importStates,
    listeners,
    matchAllCalls,
    openedUrls,
    warnings,
  };
}

function createNotificationClickEvent(link) {
  let pendingWork = null;
  const state = {
    closed: false,
    propagationStopped: false,
  };
  const event = {
    notification: {
      data: {
        FCM_MSG: {
          fcmOptions: { link },
        },
      },
      close() {
        state.closed = true;
      },
    },
    stopImmediatePropagation() {
      state.propagationStopped = true;
    },
    waitUntil(promise) {
      pendingWork = promise;
    },
  };

  return {
    event,
    state,
    async settle() {
      assert.ok(pendingWork, "notification click should register pending work");
      await pendingWork;
    },
  };
}

function createServiceWorkerMessageEvent(source, data) {
  let pendingWork = null;
  const event = {
    data,
    source,
    waitUntil(promise) {
      pendingWork = promise;
    },
  };

  return {
    event,
    async settle() {
      assert.ok(pendingWork, "service worker message should register pending work");
      await pendingWork;
    },
  };
}

function clickHandler(harness) {
  const handlers = harness.listeners.get("notificationclick") || [];
  assert.equal(handlers.length, 1);
  return handlers[0];
}

function messageHandler(harness) {
  const handlers = harness.listeners.get("message") || [];
  assert.equal(handlers.length, 1);
  return handlers[0];
}

function activateHandler(harness) {
  const handlers = harness.listeners.get("activate") || [];
  assert.equal(handlers.length, 1);
  return handlers[0];
}

function createExtendableEvent() {
  let pendingWork = null;
  const event = {
    waitUntil(promise) {
      pendingWork = promise;
    },
  };

  return {
    event,
    async settle() {
      assert.ok(pendingWork, "event should register pending work");
      await pendingWork;
    },
  };
}

async function seedPendingNavigation(cacheStorage, value) {
  const cache = await cacheStorage.open(HANDOFF_CACHE);
  const storedValue =
    typeof value === "string"
      ? value
      : {
          createdAt: Date.now(),
          id: "seeded-push-navigation",
          path: "/history/31/",
          targetClientId: null,
          targetedAt: null,
          version: 1,
          ...value,
        };
  if (
    typeof storedValue !== "string" &&
    storedValue.targetClientId !== null &&
    storedValue.targetedAt === null
  ) {
    storedValue.targetedAt = Date.now();
  }
  await cache.put(
    HANDOFF_KEY,
    new Response(
      typeof storedValue === "string"
        ? storedValue
        : JSON.stringify(storedValue),
    ),
  );
}

async function readPendingNavigation(cacheStorage) {
  const cache = await cacheStorage.open(HANDOFF_CACHE);
  const response = await cache.match(HANDOFF_KEY);
  return response ? response.json() : null;
}

test("background push navigates an open app window to the score thread", async () => {
  const existing = createWindowClient("https://friendly.test/");
  const harness = createHarness({ windowClients: [existing.client] });
  const click = createNotificationClickEvent(
    "https://friendly.test/history/31/",
  );

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(click.state.propagationStopped, true);
  assert.equal(click.state.closed, true);
  assert.deepEqual(existing.calls.actions, ["navigate:/history/31/", "focus"]);
  assert.deepEqual(existing.calls.navigate, ["/history/31/"]);
  assert.equal(existing.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, []);
  assert.deepEqual(JSON.parse(JSON.stringify(harness.matchAllCalls)), [
    { includeUncontrolled: true, type: "window" },
  ]);
});

test("background push navigates the diary list to a diary conversation", async () => {
  const existing = createWindowClient("https://friendly.test/diary/");
  const harness = createHarness({ windowClients: [existing.client] });
  const click = createNotificationClickEvent(
    "https://friendly.test/diary/44/?from=push",
  );

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(click.state.propagationStopped, true);
  assert.equal(click.state.closed, true);
  assert.deepEqual(existing.calls.actions, [
    "navigate:/diary/44/?from=push",
    "focus",
  ]);
  assert.deepEqual(existing.calls.navigate, ["/diary/44/?from=push"]);
  assert.equal(existing.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, []);
});

test("background push focuses an app window already showing the score thread", async () => {
  const existing = createWindowClient("https://friendly.test/history/31/");
  const harness = createHarness({ windowClients: [existing.client] });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(existing.calls.navigate, []);
  assert.equal(existing.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, []);
});

test("pending handoff remains available when the target thread cannot focus", async () => {
  const existing = createWindowClient("https://friendly.test/history/31/", {
    focusOutcome: "reject",
  });
  const opened = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    openWindowClient: opened.client,
    windowClients: [existing.client],
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(existing.calls.navigate, []);
  assert.equal(existing.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, AVAILABLE_MESSAGE);
  assert.equal(harness.warnings.length, 1);

  const ready = createServiceWorkerMessageEvent(opened.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();

  assert.equal(opened.calls.messages.length, 2);
  assert.equal(opened.calls.messages[1].type, OPEN_MESSAGE);
  assert.equal(opened.calls.messages[1].path, "/history/31/");
});

test("background push opens the score thread when no app window exists", async () => {
  const harness = createHarness();
  const click = createNotificationClickEvent(
    "https://friendly.test/history/31/?from=push",
  );

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(harness.openedUrls, ["/history/31/?from=push"]);
});

test("pending route survives a worker restart when iOS opens the app root", async () => {
  const cacheStorage = createCacheStorage();
  const opened = createWindowClient("https://friendly.test/");
  const firstWorker = createHarness({
    cacheStorage,
    openWindowClient: opened.client,
  });
  const click = createNotificationClickEvent("/history/31/?from=push");

  clickHandler(firstWorker)(click.event);
  await click.settle();

  assert.deepEqual(firstWorker.openedUrls, ["/history/31/?from=push"]);
  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, AVAILABLE_MESSAGE);
  const pendingNavigation = await readPendingNavigation(cacheStorage);
  assert.equal(pendingNavigation.path, "/history/31/?from=push");
  assert.equal(pendingNavigation.version, 1);
  assert.equal(typeof pendingNavigation.createdAt, "number");

  opened.calls.messages.length = 0;
  const restartedWorker = createHarness({
    cacheStorage,
    windowClients: [opened.client],
  });
  const ready = createServiceWorkerMessageEvent(opened.client, {
    type: READY_MESSAGE,
  });
  messageHandler(restartedWorker)(ready.event);
  await ready.settle();

  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(opened.calls.messages[0].path, "/history/31/?from=push");
  const pendingAfterDelivery = await readPendingNavigation(cacheStorage);
  assert.equal(pendingAfterDelivery.id, opened.calls.messages[0].id);

  const destination = createWindowClient(
    "https://friendly.test/history/31/?from=push",
  );
  const destinationReady = createServiceWorkerMessageEvent(destination.client, {
    type: READY_MESSAGE,
  });
  messageHandler(restartedWorker)(destinationReady.event);
  await destinationReady.settle();
  assert.equal(destination.calls.messages.length, 1);
  assert.equal(destination.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(destination.calls.messages[0].id, pendingAfterDelivery.id);

  const consumed = createServiceWorkerMessageEvent(destination.client, {
    id: pendingAfterDelivery.id,
    type: CONSUMED_MESSAGE,
  });
  messageHandler(restartedWorker)(consumed.event);
  await consumed.settle();
  assert.equal(await readPendingNavigation(cacheStorage), null);

  const duplicateReady = createServiceWorkerMessageEvent(opened.client, {
    type: READY_MESSAGE,
  });
  messageHandler(restartedWorker)(duplicateReady.event);
  await duplicateReady.settle();
  assert.equal(opened.calls.messages.length, 1);
});

test("page-first ready retries after the click announces a pending route", async () => {
  const existing = createWindowClient("https://friendly.test/", {
    navigateOutcome: "reject",
  });
  const harness = createHarness({ windowClients: [existing.client] });
  const earlyReady = createServiceWorkerMessageEvent(existing.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(earlyReady.event);
  await earlyReady.settle();
  assert.deepEqual(existing.calls.messages, []);

  const click = createNotificationClickEvent("/history/31/");
  clickHandler(harness)(click.event);
  await click.settle();
  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  assert.ok(
    existing.calls.messages.some(
      (message) => message.type === AVAILABLE_MESSAGE,
    ),
  );

  const retryReady = createServiceWorkerMessageEvent(existing.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(retryReady.event);
  await retryReady.settle();
  assert.ok(
    existing.calls.messages.some(
      (message) =>
        message.type === OPEN_MESSAGE && message.path === "/history/31/",
    ),
  );
});

test("concurrent ready messages claim a pending route only once", async () => {
  const first = createWindowClient("https://friendly.test/");
  const second = createWindowClient("https://friendly.test/history/");
  const harness = createHarness({
    windowClients: [first.client, second.client],
  });
  await seedPendingNavigation(harness.cacheStorage, {
    createdAt: Date.now(),
    path: "/history/31/",
    version: 1,
  });
  const firstReady = createServiceWorkerMessageEvent(first.client, {
    type: READY_MESSAGE,
  });
  const secondReady = createServiceWorkerMessageEvent(second.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(firstReady.event);
  messageHandler(harness)(secondReady.event);
  await Promise.all([firstReady.settle(), secondReady.settle()]);

  const deliveredMessages = [...first.calls.messages, ...second.calls.messages].filter(
    (message) => message.type === OPEN_MESSAGE,
  );
  assert.equal(deliveredMessages.length, 1);
  assert.equal(first.calls.messages.length, 1);
  assert.equal(second.calls.messages.length, 0);
  const pendingNavigation = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingNavigation.targetClientId, first.client.id);
});

test("expired and malformed pending routes are discarded", async () => {
  const client = createWindowClient("https://friendly.test/");
  const harness = createHarness({ windowClients: [client.client] });
  const invalidRecords = [
    {
      createdAt: Date.now() - 5 * 60 * 1000 - 1,
      path: "/history/31/",
      version: 1,
    },
    { createdAt: Date.now() + 60_000, path: "/history/31/", version: 1 },
    { createdAt: Date.now(), path: "https://evil.test/history/31/", version: 1 },
    { createdAt: Date.now(), path: "/history/0/", version: 1 },
    { createdAt: Date.now(), path: "/history/31/", version: 2 },
    { id: "", path: "/history/31/" },
    { path: "/history/31/", targetClientId: 31 },
    "not-json",
  ];

  for (const record of invalidRecords) {
    await seedPendingNavigation(harness.cacheStorage, record);
    const ready = createServiceWorkerMessageEvent(client.client, {
      type: READY_MESSAGE,
    });
    messageHandler(harness)(ready.event);
    await ready.settle();
    assert.equal(await readPendingNavigation(harness.cacheStorage), null);
  }

  assert.deepEqual(client.calls.messages, []);
});

test("storage failure keeps a best-effort direct thread message", async () => {
  const cacheStorage = createCacheStorage({ failPut: true });
  const opened = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    cacheStorage,
    openWindowClient: opened.client,
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(opened.calls.messages[0].path, "/history/31/");
  assert.equal(harness.warnings.length, 1);
});

test("a failed replacement cannot resurrect an older pending route", async () => {
  const cacheStorage = createCacheStorage({
    failHandoffPuts: new Set([2]),
  });
  await seedPendingNavigation(cacheStorage, {
    id: "older-push-navigation",
    path: "/history/30/",
  });
  const opened = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    cacheStorage,
    openWindowClient: opened.client,
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(await readPendingNavigation(harness.cacheStorage), null);
  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(opened.calls.messages[0].path, "/history/31/");

  const ready = createServiceWorkerMessageEvent(opened.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();
  assert.equal(opened.calls.messages.length, 1);
});

test("a target binding write failure still lets the opened root claim the route", async () => {
  const cacheStorage = createCacheStorage({
    failHandoffPuts: new Set([2]),
  });
  const opened = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    cacheStorage,
    openWindowClient: opened.client,
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(opened.calls.messages.length, 1);
  assert.equal(opened.calls.messages[0].type, AVAILABLE_MESSAGE);
  assert.equal(harness.warnings.length, 1);

  const ready = createServiceWorkerMessageEvent(opened.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();
  assert.equal(opened.calls.messages.length, 2);
  assert.equal(opened.calls.messages[1].type, OPEN_MESSAGE);
  assert.equal(opened.calls.messages[1].path, "/history/31/");
});

test("pending handoff survives when an existing client cannot navigate", async () => {
  for (const navigateOutcome of ["null", "reject"]) {
    const existing = createWindowClient("https://friendly.test/", {
      navigateOutcome,
    });
    const opened = createWindowClient("https://friendly.test/");
    const harness = createHarness({
      openWindowClient: opened.client,
      windowClients: [existing.client],
    });
    const click = createNotificationClickEvent("/history/31/");

    clickHandler(harness)(click.event);
    await click.settle();

    assert.deepEqual(existing.calls.navigate, ["/history/31/"]);
    assert.equal(existing.calls.focus, 0);
    assert.deepEqual(harness.openedUrls, ["/history/31/"]);
    assert.equal(existing.calls.messages.length, 0);
    assert.equal(opened.calls.messages.length, 1);
    assert.equal(opened.calls.messages[0].type, AVAILABLE_MESSAGE);
    assert.equal(harness.warnings.length, navigateOutcome === "reject" ? 1 : 0);

    const ready = createServiceWorkerMessageEvent(opened.client, {
      type: READY_MESSAGE,
    });
    messageHandler(harness)(ready.event);
    await ready.settle();

    assert.equal(opened.calls.messages.length, 2);
    assert.equal(opened.calls.messages[1].type, OPEN_MESSAGE);
    assert.equal(opened.calls.messages[1].path, "/history/31/");
  }
});

test("a failed handoff message releases the route for another root client", async () => {
  const client = createWindowClient("https://friendly.test/", {
    messageOutcome: "reject",
  });
  const harness = createHarness({ windowClients: [client.client] });
  await seedPendingNavigation(harness.cacheStorage, {
    createdAt: Date.now(),
    path: "/history/31/",
    version: 1,
  });
  const ready = createServiceWorkerMessageEvent(client.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(ready.event);
  await ready.settle();

  const pendingNavigation = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingNavigation.path, "/history/31/");
  assert.equal(pendingNavigation.targetClientId, null);
  assert.equal(pendingNavigation.targetedAt, null);
  assert.equal(harness.warnings.length, 1);

  const replacement = createWindowClient("https://friendly.test/");
  const replacementReady = createServiceWorkerMessageEvent(
    replacement.client,
    { type: READY_MESSAGE },
  );
  messageHandler(harness)(replacementReady.event);
  await replacementReady.settle();

  assert.equal(replacement.calls.messages.length, 1);
  assert.equal(replacement.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(replacement.calls.messages[0].path, "/history/31/");
});

test("a failed availability message releases the opened route", async () => {
  const opened = createWindowClient("https://friendly.test/history/31/", {
    messageOutcome: "reject",
  });
  const harness = createHarness({ openWindowClient: opened.client });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  const pendingNavigation = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingNavigation.targetClientId, null);
  assert.equal(pendingNavigation.targetedAt, null);
  assert.equal(harness.warnings.length, 1);

  const replacement = createWindowClient("https://friendly.test/");
  const replacementReady = createServiceWorkerMessageEvent(
    replacement.client,
    { type: READY_MESSAGE },
  );
  messageHandler(harness)(replacementReady.event);
  await replacementReady.settle();

  assert.equal(replacement.calls.messages.length, 1);
  assert.equal(replacement.calls.messages[0].type, OPEN_MESSAGE);
});

test("a failed availability message wakes a known retry client", async () => {
  const retryClient = createWindowClient("https://friendly.test/", {
    navigateOutcome: "reject",
  });
  const opened = createWindowClient("https://friendly.test/history/31/", {
    messageOutcome: "reject",
  });
  const harness = createHarness({
    openWindowClient: opened.client,
    windowClients: [retryClient.client],
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(retryClient.calls.messages.length, 1);
  assert.equal(retryClient.calls.messages[0].type, AVAILABLE_MESSAGE);
  const pendingNavigation = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingNavigation.targetClientId, null);

  const ready = createServiceWorkerMessageEvent(retryClient.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();

  assert.equal(retryClient.calls.messages.length, 2);
  assert.equal(retryClient.calls.messages[1].type, OPEN_MESSAGE);
  assert.equal(retryClient.calls.messages[1].path, "/history/31/");
});

test("a silently dropped handoff stays pending until the destination acknowledges", async () => {
  const rootClient = createWindowClient("https://friendly.test/", {
    messageOutcome: "drop",
  });
  const harness = createHarness({ windowClients: [rootClient.client] });
  await seedPendingNavigation(harness.cacheStorage, {
    path: "/history/31/",
  });
  const rootReady = createServiceWorkerMessageEvent(rootClient.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(rootReady.event);
  await rootReady.settle();

  const pendingAfterDrop = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingAfterDrop.targetClientId, rootClient.client.id);
  assert.deepEqual(rootClient.calls.messages, []);

  const destination = createWindowClient("https://friendly.test/history/31/");
  const destinationReady = createServiceWorkerMessageEvent(destination.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(destinationReady.event);
  await destinationReady.settle();
  assert.equal(destination.calls.messages.length, 1);
  assert.equal(destination.calls.messages[0].id, pendingAfterDrop.id);

  const consumed = createServiceWorkerMessageEvent(destination.client, {
    id: pendingAfterDrop.id,
    type: CONSUMED_MESSAGE,
  });
  messageHandler(harness)(consumed.event);
  await consumed.settle();
  assert.equal(await readPendingNavigation(harness.cacheStorage), null);
});

test("a new root client reclaims a route whose target disappeared", async () => {
  const replacement = createWindowClient("https://friendly.test/");
  const harness = createHarness({ windowClients: [replacement.client] });
  await seedPendingNavigation(harness.cacheStorage, {
    path: "/history/31/",
    targetClientId: "discarded-client",
  });
  const ready = createServiceWorkerMessageEvent(replacement.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(ready.event);
  await ready.settle();

  assert.equal(replacement.calls.messages.length, 1);
  assert.equal(replacement.calls.messages[0].type, OPEN_MESSAGE);
  const rebound = await readPendingNavigation(harness.cacheStorage);
  assert.equal(rebound.targetClientId, replacement.client.id);
});

test("an expired target lease lets a new root reclaim from a silent live client", async () => {
  const silentTarget = createWindowClient("https://friendly.test/", {
    id: "silent-live-client",
    messageOutcome: "drop",
  });
  const replacement = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    windowClients: [silentTarget.client, replacement.client],
  });
  await seedPendingNavigation(harness.cacheStorage, {
    path: "/history/31/",
    targetClientId: silentTarget.client.id,
    targetedAt: Date.now() - 30_001,
  });
  const ready = createServiceWorkerMessageEvent(replacement.client, {
    type: READY_MESSAGE,
  });

  messageHandler(harness)(ready.event);
  await ready.settle();

  assert.equal(replacement.calls.messages.length, 1);
  assert.equal(replacement.calls.messages[0].type, OPEN_MESSAGE);
  const rebound = await readPendingNavigation(harness.cacheStorage);
  assert.equal(rebound.targetClientId, replacement.client.id);
  assert.ok(rebound.targetedAt > Date.now() - 5_000);
});

test("a consumed focus token leaves the route claimable by a later root client", async () => {
  const stale = createWindowClient("https://friendly.test/history/31/", {
    focusOutcome: "reject",
  });
  const harness = createHarness({
    openWindowOutcome: "reject",
    windowClients: [stale.client],
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.equal(stale.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  const pendingNavigation = await readPendingNavigation(harness.cacheStorage);
  assert.equal(pendingNavigation.targetClientId, null);

  const replacement = createWindowClient("https://friendly.test/");
  const ready = createServiceWorkerMessageEvent(replacement.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();
  assert.equal(replacement.calls.messages.length, 1);
  assert.equal(replacement.calls.messages[0].type, OPEN_MESSAGE);
});

test("a stale acknowledgement cannot delete a newer notification", async () => {
  const destination = createWindowClient("https://friendly.test/history/32/");
  const harness = createHarness({ windowClients: [destination.client] });
  await seedPendingNavigation(harness.cacheStorage, {
    id: "new-push-navigation",
    path: "/history/32/",
    targetClientId: destination.client.id,
  });
  const staleAcknowledgement = createServiceWorkerMessageEvent(
    destination.client,
    {
      id: "old-push-navigation",
      type: CONSUMED_MESSAGE,
    },
  );

  messageHandler(harness)(staleAcknowledgement.event);
  await staleAcknowledgement.settle();
  assert.equal(
    (await readPendingNavigation(harness.cacheStorage)).id,
    "new-push-navigation",
  );

  const currentAcknowledgement = createServiceWorkerMessageEvent(
    destination.client,
    {
      id: "new-push-navigation",
      type: CONSUMED_MESSAGE,
    },
  );
  messageHandler(harness)(currentAcknowledgement.event);
  await currentAcknowledgement.settle();
  assert.equal(await readPendingNavigation(harness.cacheStorage), null);
});

test("the client selected by the notification click owns the pending route", async () => {
  const selected = createWindowClient("https://friendly.test/");
  const unrelated = createWindowClient("https://friendly.test/history/");
  const harness = createHarness({
    windowClients: [selected.client, unrelated.client],
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();
  assert.equal(selected.calls.messages.length, 1);
  assert.equal(selected.calls.messages[0].type, AVAILABLE_MESSAGE);

  const unrelatedReady = createServiceWorkerMessageEvent(unrelated.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(unrelatedReady.event);
  await unrelatedReady.settle();
  assert.deepEqual(unrelated.calls.messages, []);

  const selectedReady = createServiceWorkerMessageEvent(selected.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(selectedReady.event);
  await selectedReady.settle();
  assert.equal(selected.calls.messages.length, 2);
  assert.equal(selected.calls.messages[1].type, OPEN_MESSAGE);
});

test("window activation starts before a slow handoff write finishes", async () => {
  let releaseHandoffWrite;
  const handoffWriteGate = new Promise((resolve) => {
    releaseHandoffWrite = resolve;
  });
  const cacheStorage = createCacheStorage({
    beforeHandoffPut: () => handoffWriteGate,
  });
  const opened = createWindowClient("https://friendly.test/");
  const harness = createHarness({
    cacheStorage,
    openWindowClient: opened.client,
  });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  releaseHandoffWrite();
  await click.settle();
});

test("a newer notification replaces an older pending route", async () => {
  const harness = createHarness();

  for (const path of ["/history/31/", "/history/32/"]) {
    const click = createNotificationClickEvent(path);
    clickHandler(harness)(click.event);
    await click.settle();
  }

  const client = createWindowClient("https://friendly.test/");
  const ready = createServiceWorkerMessageEvent(client.client, {
    type: READY_MESSAGE,
  });
  messageHandler(harness)(ready.event);
  await ready.settle();

  assert.equal(client.calls.messages.length, 1);
  assert.equal(client.calls.messages[0].type, OPEN_MESSAGE);
  assert.equal(client.calls.messages[0].path, "/history/32/");
});

test("activation preserves the handoff cache while removing old app caches", async () => {
  const cacheStorage = createCacheStorage();
  for (const cacheName of [
    "woorisai-static-v7",
    "woorisai-static-v8",
    "woorisai-static-v9",
    "woorisai-static-v10",
    "woorisai-static-v11",
    HANDOFF_CACHE,
    "third-party-cache",
  ]) {
    await cacheStorage.open(cacheName);
  }
  const harness = createHarness({ cacheStorage });
  const activate = createExtendableEvent();

  activateHandler(harness)(activate.event);
  await activate.settle();

  assert.deepEqual(cacheStorage.deletedCaches, [
    "woorisai-static-v7",
    "woorisai-static-v8",
    "woorisai-static-v9",
    "woorisai-static-v10",
  ]);
  assert.equal(cacheStorage.stores.has("woorisai-static-v11"), true);
  assert.equal(cacheStorage.stores.has(HANDOFF_CACHE), true);
  assert.equal(cacheStorage.stores.has("third-party-cache"), true);
});

test("background push does not navigate an unrelated same-origin window", async () => {
  const admin = createWindowClient("https://friendly.test/admin/");
  const harness = createHarness({ windowClients: [admin.client] });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(admin.calls.actions, []);
  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
});

test("background push rejects links outside local conversation routes", () => {
  for (const unsafeLink of [
    "https://evil.test/history/31/",
    "//evil.test/history/31/",
    "https://friendly.test/",
    "https://friendly.test/history/0/",
    "https://friendly.test/history/31/extra/",
    "https://friendly.test/diary/0/",
    "https://friendly.test/diary/31/extra/",
    "http://[",
    null,
    31,
  ]) {
    const harness = createHarness();
    const click = createNotificationClickEvent(unsafeLink);

    clickHandler(harness)(click.event);

    assert.equal(click.state.propagationStopped, true);
    assert.equal(click.state.closed, true);
    assert.deepEqual(harness.matchAllCalls, []);
    assert.deepEqual(harness.openedUrls, []);
    assert.equal(harness.cacheStorage.stores.has(HANDOFF_CACHE), false);
  }
});

test("unrelated notification clicks remain available to other handlers", () => {
  const harness = createHarness();
  const click = createNotificationClickEvent("/history/31/");
  delete click.event.notification.data.FCM_MSG;

  clickHandler(harness)(click.event);

  assert.equal(click.state.propagationStopped, false);
  assert.equal(click.state.closed, false);
  assert.deepEqual(harness.matchAllCalls, []);
  assert.deepEqual(harness.openedUrls, []);
});

test("notification action clicks remain available to action handlers", () => {
  const harness = createHarness();
  const click = createNotificationClickEvent("/history/31/");
  click.event.action = "future-action";

  clickHandler(harness)(click.event);

  assert.equal(click.state.propagationStopped, false);
  assert.equal(click.state.closed, false);
  assert.deepEqual(harness.matchAllCalls, []);
  assert.deepEqual(harness.openedUrls, []);
});

test("notification click routing is registered before Firebase Messaging loads", () => {
  const harness = createHarness({ firebaseConfig: { projectId: "test-project" } });

  assert.equal(harness.importStates.length, 2);
  assert.equal(harness.importStates[0].notificationClickHandlers, 1);
  assert.equal(harness.importStates[1].notificationClickHandlers, 1);
});
