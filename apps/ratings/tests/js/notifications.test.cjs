const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const notificationScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/notifications.js",
);
const notificationSource = fs.readFileSync(notificationScriptPath, "utf8");
const executableSource = notificationSource.slice(
  notificationSource.indexOf("const settings ="),
);

class FakeElement {
  constructor(tagName = "div") {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.href = undefined;
    this.listeners = {};
    this.removed = false;
    this.tagName = tagName;
    this.textContent = "";
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  append(...children) {
    this.children.push(...children);
  }

  querySelector() {
    return null;
  }

  remove() {
    this.removed = true;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function descendantText(element) {
  return [
    element.textContent,
    ...element.children.flatMap((child) => descendantText(child)),
  ].join("");
}

function createHarness() {
  const region = new FakeElement("div");
  const dispatchedEvents = [];
  const timers = new Map();
  let nextTimerId = 1;
  const document = {
    body: new FakeElement("body"),
    createElement(tagName) {
      return new FakeElement(tagName);
    },
    dispatchEvent(event) {
      dispatchedEvents.push(event);
    },
    querySelector(selector) {
      if (selector === "[data-notification-settings]") {
        return null;
      }
      if (selector === "[data-foreground-notification-region]") {
        return region;
      }
      if (selector === "[data-foreground-notification]") {
        return region.children.find((child) => !child.removed) || null;
      }
      return null;
    },
  };
  const sandbox = {
    console,
    CustomEvent: class CustomEvent {
      constructor(type, options) {
        this.detail = options.detail;
        this.type = type;
      }
    },
    document,
    URL,
    window: {
      clearTimeout(timerId) {
        timers.delete(timerId);
      },
      location: { origin: "https://friendly.test" },
      setTimeout(callback) {
        const timerId = nextTimerId;
        nextTimerId += 1;
        timers.set(timerId, callback);
        return timerId;
      },
    },
  };
  vm.runInNewContext(
    `${executableSource}\n` +
      "globalThis.notificationTestApi = { handleForegroundMessage, readThreadLink };",
    sandbox,
    { filename: notificationScriptPath },
  );
  return {
    dispatchedEvents,
    region,
    runTimers() {
      for (const [timerId, callback] of [...timers]) {
        timers.delete(timerId);
        callback();
      }
    },
    sandbox,
    timers,
  };
}

test("foreground comment push renders a safe thread link and dispatches refresh", () => {
  const harness = createHarness();

  harness.sandbox.notificationTestApi.handleForegroundMessage({
    fcmOptions: { link: "https://friendly.test/history/31/" },
    notification: { body: "새로운 댓글이 도착했어요" },
  });

  const toast = harness.region.children[0];
  const link = toast.children[0];
  assert.equal(toast.tagName, "div");
  assert.equal(link.tagName, "a");
  assert.equal(link.href, "/history/31/");
  assert.equal(link.attributes["aria-label"], "새로운 댓글이 도착했어요. 대화 열기");
  assert.match(descendantText(toast), /새로운 댓글이 도착했어요/);
  assert.equal(harness.dispatchedEvents.length, 1);
  assert.equal(harness.dispatchedEvents[0].type, "woorisai:push-message");
  assert.deepEqual(
    JSON.parse(JSON.stringify(harness.dispatchedEvents[0].detail)),
    { threadLink: "/history/31/" },
  );
});

test("foreground push rejects external links and arbitrary private body text", () => {
  const harness = createHarness();

  harness.sandbox.notificationTestApi.handleForegroundMessage({
    fcmOptions: { link: "https://evil.test/history/31/" },
    notification: { body: "점수 3점, 이유는 비밀" },
  });

  const content = harness.region.children[0].children[0];
  assert.equal(content.tagName, "div");
  assert.equal(content.href, undefined);
  assert.equal(descendantText(content), "♥새로운 알림이 도착했어요");
  assert.equal(harness.dispatchedEvents[0].detail.threadLink, null);
  assert.equal(notificationSource.includes("innerHTML"), false);
});

test("foreground thread link validation accepts only the local canonical path", () => {
  const harness = createHarness();
  const { readThreadLink } = harness.sandbox.notificationTestApi;

  assert.equal(readThreadLink("/history/9/"), "/history/9/");
  assert.equal(readThreadLink("/history/9/?from=push"), "/history/9/?from=push");
  assert.equal(readThreadLink("/history/0/"), null);
  assert.equal(readThreadLink("/history/9/extra/"), null);
  assert.equal(readThreadLink("https://evil.test/history/9/"), null);
});

test("foreground toast pauses timeout while it is focused", () => {
  const harness = createHarness();
  harness.sandbox.notificationTestApi.handleForegroundMessage({
    fcmOptions: { link: "/history/31/" },
    notification: { body: "새로운 마음 기록이 도착했어요" },
  });
  const toast = harness.region.children[0];

  assert.equal(harness.timers.size, 1);
  toast.listeners.mouseenter();
  assert.equal(harness.timers.size, 0);
  toast.listeners.focusin();
  assert.equal(harness.timers.size, 0);
  toast.listeners.mouseleave();
  assert.equal(harness.timers.size, 0);
  harness.runTimers();
  assert.equal(toast.removed, false);

  toast.listeners.focusout();
  assert.equal(harness.timers.size, 1);
  harness.runTimers();
  assert.equal(toast.removed, true);
});
