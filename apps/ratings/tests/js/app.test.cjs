const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/app.js",
);
const appSource = fs.readFileSync(appScriptPath, "utf8");

const READY_MESSAGE = "woorisai:push-navigation-ready";
const AVAILABLE_MESSAGE = "woorisai:push-navigation-available";
const OPEN_MESSAGE = "woorisai:push-navigation-open";
const CONSUMED_MESSAGE = "woorisai:push-navigation-consumed";

function createHarness({
  controlled = true,
  historyOutcome = "success",
  loginNextValue = null,
  pathname = "/",
  search = "",
} = {}) {
  const actions = [];
  const documentListeners = new Map();
  const replacedUrls = [];
  const replacedHistoryUrls = [];
  const scheduledTimeouts = [];
  const serviceWorkerListeners = new Map();
  const windowListeners = new Map();
  const workerMessages = [];
  const loginNextInput =
    loginNextValue === null ? null : { value: loginNextValue };
  const worker = {
    postMessage(message) {
      workerMessages.push(message);
    },
  };
  const registration = { active: worker };
  const serviceWorker = {
    controller: controlled ? worker : null,
    ready: Promise.resolve(registration),
    addEventListener(type, listener) {
      actions.push(`listen:${type}`);
      const listeners = serviceWorkerListeners.get(type) || [];
      listeners.push(listener);
      serviceWorkerListeners.set(type, listeners);
    },
    register(url, options) {
      actions.push(`register:${url}`);
      assert.equal(options.scope, "/");
      assert.equal(options.updateViaCache, "none");
      return Promise.resolve(registration);
    },
  };
  const document = {
    visibilityState: "visible",
    addEventListener(type, listener) {
      const listeners = documentListeners.get(type) || [];
      listeners.push(listener);
      documentListeners.set(type, listeners);
    },
    querySelector(selector) {
      return selector === 'input[name="next"]' ? loginNextInput : null;
    },
    querySelectorAll() {
      return [];
    },
  };
  const location = {
    origin: "https://friendly.test",
    pathname,
    search,
    replace(url) {
      replacedUrls.push(url);
    },
  };
  const window = {
    addEventListener(type, listener) {
      const listeners = windowListeners.get(type) || [];
      listeners.push(listener);
      windowListeners.set(type, listeners);
    },
    history: {
      replaceState(_state, _title, url) {
        if (historyOutcome === "reject") {
          throw new TypeError("The login URL cannot be updated.");
        }
        replacedHistoryUrls.push(url);
        const parsedUrl = new URL(url, location.origin);
        location.pathname = parsedUrl.pathname;
        location.search = parsedUrl.search;
      },
    },
    location,
    setTimeout(callback, delay) {
      scheduledTimeouts.push({ callback, delay });
      return scheduledTimeouts.length;
    },
  };
  const sandbox = {
    URL,
    console,
    document,
    navigator: { serviceWorker },
    window,
  };

  vm.runInNewContext(
    `${appSource}\n` +
      "globalThis.appTestApi = { readPushConversationPath, signalPushNavigationReady };",
    sandbox,
    { filename: appScriptPath },
  );

  return {
    actions,
    document,
    documentListeners,
    loginNextInput,
    replacedHistoryUrls,
    replacedUrls,
    scheduledTimeouts,
    sandbox,
    serviceWorkerListeners,
    windowListeners,
    worker,
    workerMessages,
    async settle() {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    },
    dispatchServiceWorkerMessage(data, source = worker) {
      const listeners = serviceWorkerListeners.get("message") || [];
      for (const listener of listeners) {
        listener({ data, source });
      }
    },
    runScheduledTimeout(delay) {
      const timeoutIndex = scheduledTimeouts.findIndex(
        (timeout) => timeout.delay === delay,
      );
      assert.notEqual(timeoutIndex, -1, `missing ${delay}ms retry`);
      const [timeout] = scheduledTimeouts.splice(timeoutIndex, 1);
      timeout.callback();
    },
  };
}

test("push navigation listener is ready before service worker registration", async () => {
  const harness = createHarness();
  await harness.settle();

  assert.ok(harness.actions.indexOf("listen:message") >= 0);
  assert.ok(harness.actions.indexOf("register:/service-worker.js") >= 0);
  assert.ok(
    harness.actions.indexOf("listen:message") <
      harness.actions.indexOf("register:/service-worker.js"),
  );
  assert.ok(
    harness.workerMessages.some((message) => message.type === READY_MESSAGE),
  );
});

test("available pending navigation triggers another ready handshake", async () => {
  const harness = createHarness();
  await harness.settle();
  harness.workerMessages.length = 0;

  harness.dispatchServiceWorkerMessage({ type: AVAILABLE_MESSAGE });

  assert.equal(harness.workerMessages.length, 1);
  assert.equal(harness.workerMessages[0].type, READY_MESSAGE);
});

test("an unavailable message source falls back to the controlling worker", async () => {
  const harness = createHarness();
  await harness.settle();
  harness.workerMessages.length = 0;
  const unavailableWorker = {
    postMessage() {
      throw new TypeError("This worker is no longer active.");
    },
  };

  harness.dispatchServiceWorkerMessage(
    { type: AVAILABLE_MESSAGE },
    unavailableWorker,
  );

  assert.equal(harness.workerMessages.length, 1);
  assert.equal(harness.workerMessages[0].type, READY_MESSAGE);
});

test("an active worker receives ready when the page is initially uncontrolled", async () => {
  const harness = createHarness({ controlled: false });

  await harness.settle();

  assert.ok(
    harness.workerMessages.some((message) => message.type === READY_MESSAGE),
  );
});

test("lost startup handoffs get bounded automatic ready retries", async () => {
  const harness = createHarness();
  await harness.settle();
  harness.workerMessages.length = 0;

  assert.deepEqual(
    harness.scheduledTimeouts.map((timeout) => timeout.delay),
    [1_000, 31_000],
  );

  harness.runScheduledTimeout(1_000);
  harness.runScheduledTimeout(31_000);
  await harness.settle();

  assert.deepEqual(
    harness.workerMessages.map((message) => message.type),
    [READY_MESSAGE, READY_MESSAGE],
  );
});

test("service worker handoff replaces the current page with the score thread", () => {
  const harness = createHarness({ pathname: "/", search: "" });

  harness.dispatchServiceWorkerMessage({
    id: "push-navigation-31",
    type: OPEN_MESSAGE,
    path: "/history/31/?from=push",
  });

  assert.deepEqual(harness.replacedUrls, ["/history/31/?from=push"]);
});

test("service worker handoff replaces the current page with the diary conversation", () => {
  const harness = createHarness({ pathname: "/diary/", search: "" });

  harness.dispatchServiceWorkerMessage({
    id: "push-navigation-diary-31",
    type: OPEN_MESSAGE,
    path: "/diary/31/?from=push",
  });

  assert.deepEqual(harness.replacedUrls, ["/diary/31/?from=push"]);
});

test("duplicate handoff messages replace the current page only once", () => {
  const harness = createHarness();
  const message = {
    id: "push-navigation-31",
    type: OPEN_MESSAGE,
    path: "/history/31/?from=push",
  };

  harness.dispatchServiceWorkerMessage(message);
  harness.dispatchServiceWorkerMessage(message);

  assert.deepEqual(harness.replacedUrls, ["/history/31/?from=push"]);
});

test("handoff already at the target thread acknowledges without reloading", async () => {
  const harness = createHarness({
    pathname: "/history/31/",
    search: "?from=push",
  });
  await harness.settle();
  harness.workerMessages.length = 0;

  harness.dispatchServiceWorkerMessage({
    id: "push-navigation-31",
    type: OPEN_MESSAGE,
    path: "/history/31/?from=push",
  });

  assert.deepEqual(harness.replacedUrls, []);
  assert.equal(harness.workerMessages.length, 1);
  assert.equal(harness.workerMessages[0].type, CONSUMED_MESSAGE);
  assert.equal(harness.workerMessages[0].id, "push-navigation-31");
});

test("login keeps the safe thread as next before acknowledging", async () => {
  const harness = createHarness({
    loginNextValue: "/",
    pathname: "/login/",
    search: "?next=%2F",
  });
  await harness.settle();
  harness.workerMessages.length = 0;

  harness.dispatchServiceWorkerMessage({
    id: "push-navigation-31",
    type: OPEN_MESSAGE,
    path: "/history/31/",
  });

  assert.equal(harness.loginNextInput.value, "/history/31/");
  assert.equal(
    new URLSearchParams(harness.sandbox.window.location.search).get("next"),
    "/history/31/",
  );
  assert.deepEqual(harness.replacedHistoryUrls, [
    "/login/?next=%2Fhistory%2F31%2F",
  ]);
  assert.deepEqual(harness.replacedUrls, []);
  assert.equal(harness.workerMessages.length, 1);
  assert.equal(harness.workerMessages[0].type, CONSUMED_MESSAGE);
  assert.equal(harness.workerMessages[0].id, "push-navigation-31");
});

test("login keeps the pending route when its next URL cannot be persisted", async () => {
  const harness = createHarness({
    historyOutcome: "reject",
    loginNextValue: "/",
    pathname: "/login/",
    search: "?next=%2F",
  });
  await harness.settle();
  harness.workerMessages.length = 0;

  harness.dispatchServiceWorkerMessage({
    id: "push-navigation-31",
    type: OPEN_MESSAGE,
    path: "/history/31/",
  });

  assert.equal(harness.loginNextInput.value, "/");
  assert.deepEqual(harness.replacedHistoryUrls, []);
  assert.deepEqual(harness.replacedUrls, ["/history/31/"]);
  assert.deepEqual(harness.workerMessages, []);
});

test("handoff rejects external and malformed conversation paths", () => {
  const harness = createHarness();

  for (const pathValue of [
    "https://evil.test/history/31/",
    "//evil.test/history/31/",
    "/history/0/",
    "/history/31/extra/",
    "/diary/0/",
    "/diary/31/extra/",
    "http://[",
    null,
    31,
  ]) {
    harness.dispatchServiceWorkerMessage({
      type: OPEN_MESSAGE,
      path: pathValue,
    });
  }

  assert.deepEqual(harness.replacedUrls, []);
});

test("unrelated service worker messages do not change navigation", () => {
  const harness = createHarness();

  harness.dispatchServiceWorkerMessage({
    type: "unrelated-message",
    path: "/history/31/",
  });

  assert.deepEqual(harness.replacedUrls, []);
});
