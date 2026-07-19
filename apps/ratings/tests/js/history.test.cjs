const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const historyScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/history.js",
);
const historySource = fs.readFileSync(historyScriptPath, "utf8");

class FakeElement {
  constructor({ dataset = {} } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = dataset;
    this.hidden = false;
    this.listeners = {};
    this.selectors = {};
    this.textContent = "";
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  append(...children) {
    this.children.push(...children);
  }

  querySelector(selector) {
    return Object.hasOwn(this.selectors, selector) ? this.selectors[selector] : null;
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function jsonResponse(status, payload) {
  return {
    headers: {
      get(name) {
        return name === "Content-Type" ? "application/json" : null;
      },
    },
    json: async () => payload,
    ok: status >= 200 && status < 300,
    redirected: false,
    status,
  };
}

function historyPayload({ pageNumber = 1, results = [], totalCount = 0 } = {}) {
  return {
    resultType: "SUCCESS",
    error: null,
    success: {
      results,
      paging: {
        pageNumber,
        pageSize: 20,
        hasNext: pageNumber * 20 < totalCount,
        totalCount,
      },
    },
  };
}

function historyItem() {
  return {
    id: 31,
    sourceParticipant: { slot: 1, displayName: "첫째 <script>" },
    targetParticipant: { slot: 2, displayName: "둘째" },
    changedBy: { slot: 1, displayName: "첫째 <script>" },
    delta: 3,
    reason:
      '<img src=x onerror="globalThis.compromised=true">' + "😀".repeat(101),
    resultingScore: 53,
    createdAt: "2026-07-19T01:23:00Z",
  };
}

function createHistoryHarness({ response, search = "?pageNumber=1" }) {
  const content = new FakeElement();
  const status = new FakeElement();
  const list = new FakeElement();
  const empty = new FakeElement();
  const pagination = new FakeElement();
  const root = new FakeElement({
    dataset: { historyUrl: "/api/v1/score-changes/" },
  });
  root.selectors = {
    "[data-history-content]": content,
    "[data-history-empty]": empty,
    "[data-history-list]": list,
    "[data-history-pagination]": pagination,
    "[data-history-status]": status,
  };

  const assignedLocations = [];
  const createdTags = [];
  const fetchCalls = [];
  const location = {
    assign(value) {
      assignedLocations.push(value);
    },
    href: `https://friendly.test/history/${search}`,
    origin: "https://friendly.test",
    pathname: "/history/",
    search,
  };
  const sandbox = {
    console,
    document: {
      createElement(tagName) {
        createdTags.push(tagName);
        return new FakeElement();
      },
      createTextNode(text) {
        const node = new FakeElement();
        node.textContent = text;
        return node;
      },
      querySelector(selector) {
        return selector === "[data-history-root]" ? root : null;
      },
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      return Promise.resolve(response);
    },
    URL,
    URLSearchParams,
    window: { location },
  };

  vm.runInNewContext(historySource, sandbox, { filename: historyScriptPath });

  return {
    assignedLocations,
    content,
    createdTags,
    empty,
    fetchCalls,
    list,
    pagination,
    sandbox,
    status,
  };
}

async function settleAsyncWork() {
  for (let count = 0; count < 5; count += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function descendantText(element) {
  return [
    element.textContent,
    ...element.children.flatMap((child) => descendantText(child)),
  ].join("");
}

test("history fetches and safely renders the requested page with navigation", async () => {
  const item = historyItem();
  const harness = createHistoryHarness({
    response: jsonResponse(
      200,
      historyPayload({ pageNumber: 2, results: [item], totalCount: 41 }),
    ),
    search: "?pageNumber=2",
  });

  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 1);
  const request = harness.fetchCalls[0];
  assert.equal(
    String(request.url),
    "https://friendly.test/api/v1/score-changes/?pageNumber=2",
  );
  assert.equal(request.url.origin, "https://friendly.test");
  assert.equal(request.options.credentials, "same-origin");
  assert.equal(request.options.headers.Accept, "application/json");

  assert.equal(harness.list.hidden, false);
  assert.equal(harness.list.children.length, 1);
  const renderedText = descendantText(harness.list.children[0]);
  assert.match(renderedText, /첫째 <script> → 둘째/);
  assert.match(renderedText, /\+3점/);
  assert.equal(renderedText.includes(`“${item.reason}”`), true);
  assert.match(renderedText, /변경자 첫째 <script>/);
  assert.match(renderedText, /변경 후 53점/);
  assert.equal(harness.createdTags.includes("img"), false);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.sandbox.compromised, undefined);

  assert.equal(harness.pagination.hidden, false);
  assert.equal(harness.pagination.children[0].href, "/history/?pageNumber=1");
  assert.equal(harness.pagination.children[0].rel, "prev");
  assert.equal(harness.pagination.children[2].href, "/history/?pageNumber=3");
  assert.equal(harness.pagination.children[2].rel, "next");
  assert.equal(harness.content.attributes["aria-busy"], "false");
});

test("history reveals its empty state for the first empty page", async () => {
  const harness = createHistoryHarness({
    response: jsonResponse(200, historyPayload()),
  });

  await settleAsyncWork();

  assert.equal(harness.list.hidden, true);
  assert.equal(harness.list.children.length, 0);
  assert.equal(harness.empty.hidden, false);
  assert.equal(harness.pagination.hidden, true);
  assert.equal(harness.status.hidden, true);
  assert.equal(harness.content.attributes["aria-busy"], "false");
});

test("history redirects an expired session to login with a local next path", async () => {
  const harness = createHistoryHarness({
    response: jsonResponse(403, {
      resultType: "ERROR",
      error: {
        errorType: "AUTHENTICATION",
        errorCode: "AUTHENTICATION_REQUIRED",
        reason: "로그인이 필요합니다.",
        details: [],
      },
      success: null,
    }),
    search: "?pageNumber=2&from=nav",
  });

  await settleAsyncWork();

  assert.deepEqual(harness.assignedLocations, [
    `/login/?next=${encodeURIComponent("/history/?pageNumber=2&from=nav")}`,
  ]);
  assert.equal(harness.content.attributes["aria-busy"], "false");
});

test("history rendering never assigns HTML strings", () => {
  assert.equal(/\binnerHTML\b/.test(historySource), false);
  assert.match(historySource, /\.textContent\s*=/);
});
