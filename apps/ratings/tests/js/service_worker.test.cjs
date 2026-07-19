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

function renderServiceWorker(firebaseConfig = {}) {
  return serviceWorkerTemplate.replace(
    firebaseConfigMarker,
    JSON.stringify(firebaseConfig),
  );
}

function createWindowClient(
  url,
  { focusOutcome = "client", navigateOutcome = "client" } = {},
) {
  const calls = { actions: [], focus: 0, navigate: [] };
  const client = {
    url,
    async focus() {
      calls.actions.push("focus");
      calls.focus += 1;
      if (focusOutcome === "reject") {
        throw new TypeError("This window cannot be focused.");
      }
      return client;
    },
    async navigate(targetUrl) {
      calls.actions.push(`navigate:${targetUrl}`);
      calls.navigate.push(targetUrl);
      if (navigateOutcome === "reject") {
        throw new TypeError("This client is not controlled by the service worker.");
      }
      client.url = new URL(targetUrl, "https://friendly.test").href;
      return navigateOutcome === "null" ? null : client;
    },
  };
  return { calls, client };
}

function createHarness({ firebaseConfig = {}, windowClients = [] } = {}) {
  const importStates = [];
  const listeners = new Map();
  const openedUrls = [];
  const matchAllCalls = [];
  const warnings = [];
  const self = {
    clients: {
      claim: async () => undefined,
      async matchAll(options) {
        matchAllCalls.push(options);
        return windowClients;
      },
      async openWindow(url) {
        openedUrls.push(url);
        return null;
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
    URL,
    caches: {},
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

function clickHandler(harness) {
  const handlers = harness.listeners.get("notificationclick") || [];
  assert.equal(handlers.length, 1);
  return handlers[0];
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

test("background push opens a new window when the target thread cannot focus", async () => {
  const existing = createWindowClient("https://friendly.test/history/31/", {
    focusOutcome: "reject",
  });
  const harness = createHarness({ windowClients: [existing.client] });
  const click = createNotificationClickEvent("/history/31/");

  clickHandler(harness)(click.event);
  await click.settle();

  assert.deepEqual(existing.calls.navigate, []);
  assert.equal(existing.calls.focus, 1);
  assert.deepEqual(harness.openedUrls, ["/history/31/"]);
  assert.equal(harness.warnings.length, 1);
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

test("background push opens a new window when an existing client cannot navigate", async () => {
  for (const navigateOutcome of ["null", "reject"]) {
    const existing = createWindowClient("https://friendly.test/", {
      navigateOutcome,
    });
    const harness = createHarness({ windowClients: [existing.client] });
    const click = createNotificationClickEvent("/history/31/");

    clickHandler(harness)(click.event);
    await click.settle();

    assert.deepEqual(existing.calls.navigate, ["/history/31/"]);
    assert.equal(existing.calls.focus, 0);
    assert.deepEqual(harness.openedUrls, ["/history/31/"]);
    assert.equal(harness.warnings.length, navigateOutcome === "reject" ? 1 : 0);
  }
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

test("background push rejects links outside the local score thread route", () => {
  for (const unsafeLink of [
    "https://evil.test/history/31/",
    "//evil.test/history/31/",
    "https://friendly.test/",
    "https://friendly.test/history/0/",
    "https://friendly.test/history/31/extra/",
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
