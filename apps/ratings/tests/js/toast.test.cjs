const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const toastScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/toast.js",
);
const toastSource = fs.readFileSync(toastScriptPath, "utf8");

class FakeElement {
  constructor(tagName = "div") {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.href = undefined;
    this.listeners = {};
    this.parent = null;
    this.removed = false;
    this.tagName = tagName;
    this.textContent = "";
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  append(...children) {
    children.forEach((child) => {
      child.parent = this;
      this.children.push(child);
    });
  }

  querySelector(selector) {
    if (selector === "[data-toast]") {
      return this.children.find((child) => !child.removed && "toast" in child.dataset) || null;
    }
    return null;
  }

  remove() {
    this.removed = true;
    if (this.parent) {
      this.parent.children = this.parent.children.filter((child) => child !== this);
      this.parent = null;
    }
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

function createHarness({ includeRegion = true } = {}) {
  const body = new FakeElement("body");
  let region = includeRegion ? new FakeElement("div") : null;
  if (region) {
    region.dataset.toastRegion = "";
    body.append(region);
  }
  const timers = new Map();
  let nextTimerId = 1;
  const sandbox = {
    clearTimeout(timerId) {
      timers.delete(timerId);
    },
    document: {
      body,
      createElement(tagName) {
        return new FakeElement(tagName);
      },
      querySelector(selector) {
        if (selector === "[data-toast-region]") {
          return region;
        }
        return null;
      },
    },
    setTimeout(callback, duration) {
      const timerId = nextTimerId;
      nextTimerId += 1;
      timers.set(timerId, { callback, duration });
      return timerId;
    },
  };
  const originalAppend = body.append.bind(body);
  body.append = (...children) => {
    originalAppend(...children);
    const toastRegion = children.find((child) => "toastRegion" in child.dataset);
    if (toastRegion) {
      region = toastRegion;
    }
  };
  vm.runInNewContext(toastSource, sandbox, { filename: toastScriptPath });

  return {
    get region() {
      return region;
    },
    runTimers() {
      for (const [timerId, timer] of [...timers]) {
        timers.delete(timerId);
        timer.callback();
      }
    },
    sandbox,
    timers,
  };
}

test("toast renders text safely with the requested tone and default duration", () => {
  const harness = createHarness();
  const toast = harness.sandbox.woorisaiShowToast("<img src=x>", {
    tone: "success",
  });

  assert.equal(toast.className, "toast toast--success");
  assert.equal(toast.dataset.toast, "");
  assert.equal(toast.children[0].tagName, "div");
  assert.equal(descendantText(toast), "♥<img src=x>");
  assert.equal(harness.timers.size, 1);
  assert.equal([...harness.timers.values()][0].duration, 6000);
  assert.equal(toastSource.includes("innerHTML"), false);
});

test("toast supports a labelled link and all documented tones", () => {
  const harness = createHarness();

  for (const tone of ["info", "success", "warning", "error"]) {
    const toast = harness.sandbox.woorisaiShowToast("알림", {
      ariaLabel: "대화 열기",
      duration: 10000,
      href: "/history/31/",
      tone,
    });
    const link = toast.children[0];
    assert.equal(toast.className, `toast toast--${tone}`);
    assert.equal(link.tagName, "a");
    assert.equal(link.href, "/history/31/");
    assert.equal(link.attributes["aria-label"], "대화 열기");
    assert.equal([...harness.timers.values()][0].duration, 10000);
  }

  const fallback = harness.sandbox.woorisaiShowToast("알림", {
    tone: "unknown",
  });
  assert.equal(fallback.className, "toast toast--info");
});

test("multiple toasts remain available with independent timers", () => {
  const harness = createHarness();
  const first = harness.sandbox.woorisaiShowToast("첫 알림");
  const second = harness.sandbox.woorisaiShowToast("둘째 알림");

  assert.equal(first.removed, false);
  assert.equal(second.removed, false);
  assert.deepEqual(harness.region.children, [first, second]);
  assert.equal(harness.timers.size, 2);
});

test("an identical toast replaces its earlier copy and refreshes the timer", () => {
  const harness = createHarness();
  const first = harness.sandbox.woorisaiShowToast("같은 안내", {
    tone: "warning",
  });
  const second = harness.sandbox.woorisaiShowToast("같은 안내", {
    tone: "warning",
  });

  assert.equal(first.removed, true);
  assert.equal(second.removed, false);
  assert.deepEqual(harness.region.children, [second]);
  assert.equal(harness.timers.size, 1);
});

test("a non-link toast is keyboard focusable and pauses its timeout", () => {
  const harness = createHarness();
  const toast = harness.sandbox.woorisaiShowToast("새로운 마음 기록", {
    duration: 10000,
  });

  assert.equal(toast.tabIndex, 0);
  assert.equal(harness.timers.size, 1);
  toast.listeners.mouseenter();
  assert.equal(harness.timers.size, 0);
  toast.listeners.focusin();
  toast.listeners.mouseleave();
  assert.equal(harness.timers.size, 0);
  harness.runTimers();
  assert.equal(toast.removed, false);

  toast.listeners.focusout();
  assert.equal(harness.timers.size, 1);
  assert.equal([...harness.timers.values()][0].duration, 10000);
  harness.runTimers();
  assert.equal(toast.removed, true);
});

test("toast creates an accessible live region when the template region is absent", () => {
  const harness = createHarness({ includeRegion: false });
  harness.sandbox.woorisaiShowToast("알림");

  assert.equal(harness.region.dataset.toastRegion, "");
  assert.equal(harness.region.attributes.role, "status");
  assert.equal(harness.region.attributes["aria-live"], "polite");
  assert.equal(harness.region.attributes["aria-atomic"], "false");
});
