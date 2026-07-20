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

function createHarness() {
  const dispatchedEvents = [];
  const toastCalls = [];
  const document = {
    dispatchEvent(event) {
      dispatchedEvents.push(event);
    },
    querySelector(selector) {
      if (selector === "[data-notification-settings]") {
        return null;
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
    woorisaiShowToast(message, options) {
      toastCalls.push({ message, options });
    },
    window: {
      location: { origin: "https://friendly.test" },
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
    sandbox,
    toastCalls,
  };
}

test("foreground comment push renders a safe thread link and dispatches refresh", () => {
  const harness = createHarness();

  harness.sandbox.notificationTestApi.handleForegroundMessage({
    fcmOptions: { link: "https://friendly.test/history/31/" },
    notification: { body: "새로운 댓글이 도착했어요" },
  });

  assert.deepEqual(JSON.parse(JSON.stringify(harness.toastCalls)), [
    {
      message: "새로운 댓글이 도착했어요",
      options: {
        ariaLabel: "새로운 댓글이 도착했어요. 대화 열기",
        duration: 10000,
        href: "/history/31/",
        tone: "info",
      },
    },
  ]);
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

  assert.deepEqual(JSON.parse(JSON.stringify(harness.toastCalls)), [
    {
      message: "새로운 알림이 도착했어요",
      options: {
        ariaLabel: null,
        duration: 10000,
        href: null,
        tone: "info",
      },
    },
  ]);
  assert.equal(harness.dispatchedEvents[0].detail.threadLink, null);
  assert.equal(notificationSource.includes("innerHTML"), false);
});

test("foreground conversation link validation accepts only local canonical paths", () => {
  const harness = createHarness();
  const { readThreadLink } = harness.sandbox.notificationTestApi;

  assert.equal(readThreadLink("/history/9/"), "/history/9/");
  assert.equal(readThreadLink("/history/9/?from=push"), "/history/9/?from=push");
  assert.equal(readThreadLink("/diary/9/"), "/diary/9/");
  assert.equal(readThreadLink("/diary/9/?from=push"), "/diary/9/?from=push");
  assert.equal(readThreadLink("/history/0/"), null);
  assert.equal(readThreadLink("/diary/0/"), null);
  assert.equal(readThreadLink("/history/9/extra/"), null);
  assert.equal(readThreadLink("/diary/9/extra/"), null);
  assert.equal(readThreadLink("https://evil.test/history/9/"), null);
});
