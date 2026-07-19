const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const diaryScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/diary.js",
);
const diarySource = fs.readFileSync(diaryScriptPath, "utf8");

class FakeElement {
  constructor({ dataset = {}, tagName = "div" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = dataset;
    this.disabled = false;
    this.hidden = false;
    this.listeners = {};
    this.max = "";
    this.name = "";
    this.selectors = {};
    this.tagName = tagName;
    this.textContent = "";
    this.value = "";
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  append(...children) {
    this.children.push(...children);
  }

  focus() {
    this.focused = true;
  }

  querySelector(selector) {
    return Object.hasOwn(this.selectors, selector) ? this.selectors[selector] : null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  replaceChildren(...children) {
    this.children = children;
  }

  scrollIntoView(options) {
    this.scrollOptions = options;
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

function diaryEntry({
  content = "함께 걸어서 좋았어",
  id = 31,
  isMine = true,
  updatedAt = null,
} = {}) {
  return {
    author: {
      displayName: isMine ? "첫째 <script>" : "둘째",
      slot: isMine ? 1 : 2,
    },
    content,
    createdAt: "2026-07-19T01:23:00Z",
    id,
    isMine,
    updatedAt,
  };
}

function diaryPage({ pageNumber = 1, results = [], totalCount = results.length } = {}) {
  return {
    error: null,
    resultType: "SUCCESS",
    success: {
      paging: {
        hasNext: pageNumber * 20 < totalCount,
        pageNumber,
        pageSize: 20,
        totalCount,
      },
      results,
    },
  };
}

function mutationSuccess(value) {
  return { error: null, resultType: "SUCCESS", success: value };
}

function createDiaryHarness({
  fetchImplementation,
  response = jsonResponse(200, diaryPage()),
  search = "?pageNumber=1",
} = {}) {
  const content = new FakeElement();
  const listStatus = new FakeElement();
  const list = new FakeElement({ tagName: "ol" });
  const empty = new FakeElement();
  const pagination = new FakeElement({ tagName: "nav" });
  const focusCompose = new FakeElement({ tagName: "button" });
  const createForm = new FakeElement({ tagName: "form" });
  const csrf = new FakeElement({ tagName: "input" });
  csrf.name = "csrfmiddlewaretoken";
  csrf.value = "test-csrf-token";
  const diaryContent = new FakeElement({ tagName: "textarea" });
  diaryContent.name = "content";
  diaryContent.disabled = true;
  const characterCount = new FakeElement({ tagName: "span" });
  const createStatus = new FakeElement({ tagName: "p" });
  const contentError = new FakeElement({ tagName: "ul" });
  const createSubmit = new FakeElement({ tagName: "button" });
  createSubmit.disabled = true;
  const createSubmitLabel = new FakeElement({ tagName: "span" });
  createForm.selectors = {
    '[data-diary-error-for="content"]': contentError,
    "[data-diary-character-current]": characterCount,
    "[data-diary-create-status]": createStatus,
    "[name=content]": diaryContent,
    "[name=csrfmiddlewaretoken]": csrf,
  };
  const root = new FakeElement({
    dataset: { diaryEntriesUrl: "/api/v1/diary-entries/" },
  });
  root.selectors = {
    "[data-diary-content]": content,
    "[data-diary-create-form]": createForm,
    "[data-diary-create-submit-label]": createSubmitLabel,
    "[data-diary-create-submit]": createSubmit,
    "[data-diary-empty]": empty,
    "[data-diary-focus-compose]": focusCompose,
    "[data-diary-list-status]": listStatus,
    "[data-diary-list]": list,
    "[data-diary-pagination]": pagination,
  };

  const assignedLocations = [];
  const confirmMessages = [];
  const createdTags = [];
  const fetchCalls = [];
  const toasts = [];
  const location = {
    assign(value) {
      assignedLocations.push(value);
    },
    href: `https://friendly.test/diary/${search}`,
    origin: "https://friendly.test",
    pathname: "/diary/",
    search,
  };
  const sandbox = {
    URL,
    URLSearchParams,
    console,
    document: {
      createElement(tagName) {
        createdTags.push(tagName);
        return new FakeElement({ tagName });
      },
      createTextNode(text) {
        const node = new FakeElement({ tagName: "#text" });
        node.textContent = text;
        return node;
      },
      querySelector(selector) {
        return selector === "[data-diary-root]" ? root : null;
      },
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      return fetchImplementation
        ? fetchImplementation(url, options, fetchCalls.length)
        : Promise.resolve(response);
    },
    window: {
      confirm(message) {
        confirmMessages.push(message);
        return true;
      },
      location,
    },
    woorisaiShowToast(message, options) {
      toasts.push({ message, options });
    },
  };

  vm.runInNewContext(diarySource, sandbox, { filename: diaryScriptPath });

  return {
    assignedLocations,
    characterCount,
    confirmMessages,
    content,
    contentError,
    createForm,
    createStatus,
    createSubmit,
    createSubmitLabel,
    createdTags,
    diaryContent,
    empty,
    fetchCalls,
    focusCompose,
    list,
    listStatus,
    pagination,
    root,
    sandbox,
    toasts,
  };
}

async function settleAsyncWork() {
  for (let count = 0; count < 8; count += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function descendants(element) {
  return [element, ...element.children.flatMap((child) => descendants(child))];
}

function descendantText(element) {
  return descendants(element)
    .map((child) => child.textContent)
    .join("");
}

function findButton(element, label) {
  return descendants(element).find(
    (child) => child.tagName === "button" && child.textContent === label,
  );
}

function findByName(element, name) {
  return descendants(element).find((child) => child.name === name);
}

function findTag(element, tagName) {
  return descendants(element).find((child) => child.tagName === tagName);
}

test("diary fetches and safely renders the requested shared page", async () => {
  const unsafeContent =
    '<img src=x onerror="globalThis.compromised=true">\n' + "😀".repeat(10);
  const entry = diaryEntry({ content: unsafeContent, isMine: false });
  const harness = createDiaryHarness({
    response: jsonResponse(
      200,
      diaryPage({ pageNumber: 2, results: [entry], totalCount: 41 }),
    ),
    search: "?pageNumber=2",
  });

  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(
    String(harness.fetchCalls[0].url),
    "https://friendly.test/api/v1/diary-entries/?pageNumber=2",
  );
  assert.equal(harness.fetchCalls[0].options.credentials, "same-origin");
  assert.equal(harness.fetchCalls[0].options.headers.Accept, "application/json");
  assert.equal(harness.fetchCalls[0].options.cache, "no-store");
  assert.equal(harness.list.hidden, false);
  assert.equal(harness.list.children.length, 1);
  assert.equal(descendantText(harness.list).includes(unsafeContent), true);
  assert.match(descendantText(harness.list), /둘째님의 글/);
  assert.match(descendantText(harness.list), /2026\.07\.19 10:23 게시/);
  assert.equal(findButton(harness.list, "수정"), undefined);
  assert.equal(findButton(harness.list, "삭제"), undefined);
  assert.equal(harness.createdTags.includes("img"), false);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.sandbox.compromised, undefined);
  assert.equal(harness.pagination.hidden, false);
  assert.equal(harness.pagination.children[0].href, "/diary/?pageNumber=1");
  assert.equal(harness.pagination.children[2].href, "/diary/?pageNumber=3");
  assert.equal(harness.content.attributes["aria-busy"], "false");
  assert.equal(harness.diaryContent.disabled, false);
  assert.equal(harness.createSubmit.disabled, false);
});

test("diary create sends CSRF once, locks the form, and refreshes", async () => {
  let finishCreate;
  let entries = [];
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "POST") {
        return new Promise((resolve) => {
          finishCreate = resolve;
        });
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: entries })));
    },
  });
  await settleAsyncWork();
  const created = diaryEntry();
  harness.diaryContent.value = "  오늘의 기록  ";
  harness.diaryContent.listeners.input();
  let prevented = 0;

  harness.createForm.listeners.submit({
    preventDefault() {
      prevented += 1;
    },
  });
  harness.createForm.listeners.submit({
    preventDefault() {
      prevented += 1;
    },
  });

  assert.equal(prevented, 2);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "POST").length,
    1,
  );
  assert.equal(harness.diaryContent.disabled, true);
  assert.equal(harness.createSubmit.disabled, true);
  assert.equal(harness.createSubmitLabel.textContent, "남기고 있어요…");

  entries = [created];
  finishCreate(jsonResponse(201, mutationSuccess(created)));
  await settleAsyncWork();

  const createCall = harness.fetchCalls.find(
    (call) => call.options.method === "POST",
  );
  assert.equal(String(createCall.url), "/api/v1/diary-entries/");
  assert.equal(createCall.options.credentials, "same-origin");
  assert.equal(createCall.options.headers["Content-Type"], "application/json");
  assert.equal(createCall.options.headers["X-CSRFToken"], "test-csrf-token");
  assert.deepEqual(JSON.parse(createCall.options.body), {
    content: "오늘의 기록",
  });
  assert.equal(harness.diaryContent.value, "");
  assert.equal(harness.characterCount.textContent, "0");
  assert.equal(harness.diaryContent.disabled, false);
  assert.equal(harness.createSubmit.disabled, false);
  assert.equal(harness.createSubmitLabel.textContent, "일기 남기기");
  assert.equal(harness.list.children.length, 1);
  assert.match(harness.createStatus.textContent, /일기를 남겼어요/);
  assert.equal(
    harness.toasts[0].message,
    "우리 일기에 새 이야기를 남겼어요.",
  );
  assert.equal(harness.toasts[0].options.tone, "success");
});

test("diary locks create retry when the saved result cannot be verified", async () => {
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "POST") {
        return Promise.resolve(
          jsonResponse(201, mutationSuccess({ id: "invalid" })),
        );
      }
      return Promise.resolve(jsonResponse(200, diaryPage()));
    },
  });
  await settleAsyncWork();
  harness.diaryContent.value = "응답을 확인하지 못한 기록";

  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();
  harness.createForm.listeners.submit({ preventDefault() {} });

  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "POST").length,
    1,
  );
  assert.equal(harness.diaryContent.value, "응답을 확인하지 못한 기록");
  assert.equal(harness.createSubmit.disabled, true);
  assert.equal(harness.createSubmitLabel.textContent, "새로고침 후 확인");
  assert.match(harness.createStatus.textContent, /저장 결과를 확인하지 못했어요/);
});

test("diary validates an empty post before fetching", async () => {
  const harness = createDiaryHarness();
  await settleAsyncWork();
  const initialFetchCount = harness.fetchCalls.length;
  harness.diaryContent.value = "   ";

  harness.createForm.listeners.submit({ preventDefault() {} });

  assert.equal(harness.fetchCalls.length, initialFetchCount);
  assert.equal(harness.diaryContent.attributes["aria-invalid"], "true");
  assert.match(descendantText(harness.contentError), /일기 내용을 입력/);
  assert.match(harness.createStatus.textContent, /입력한 내용을 확인/);
});

test("diary counts and submits one thousand emoji as one thousand characters", async () => {
  let entries = [];
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "POST") {
        const content = JSON.parse(options.body).content;
        const created = diaryEntry({ content });
        entries = [created];
        return Promise.resolve(jsonResponse(201, mutationSuccess(created)));
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: entries })));
    },
  });
  await settleAsyncWork();
  harness.diaryContent.value = "🙂".repeat(1000);
  harness.diaryContent.listeners.input();

  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const createCall = harness.fetchCalls.find(
    (call) => call.options.method === "POST",
  );
  assert.equal(harness.characterCount.textContent, "0");
  assert.equal(JSON.parse(createCall.options.body).content, "🙂".repeat(1000));
});

test("an author can edit and delete an entry without duplicate mutations", async () => {
  let entry = diaryEntry();
  let finishUpdate;
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "PATCH") {
        return new Promise((resolve) => {
          finishUpdate = resolve;
        });
      }
      if (options.method === "DELETE") {
        entry = null;
        return Promise.resolve(jsonResponse(200, mutationSuccess(null)));
      }
      return Promise.resolve(
        jsonResponse(200, diaryPage({ results: entry ? [entry] : [] })),
      );
    },
  });
  await settleAsyncWork();

  const edit = findButton(harness.list, "수정");
  assert.ok(edit);
  edit.listeners.click();
  const editForm = findTag(harness.list, "form");
  assert.equal(findByName(editForm, "entryDate"), undefined);
  const editContent = findByName(editForm, "content");
  editContent.value = "수정한 우리 이야기";
  editForm.listeners.submit({ preventDefault() {} });
  editForm.listeners.submit({ preventDefault() {} });

  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "PATCH").length,
    1,
  );
  assert.equal(editContent.disabled, true);
  const updated = diaryEntry({
    content: "수정한 우리 이야기",
    updatedAt: "2026-07-19T02:23:00Z",
  });
  entry = updated;
  finishUpdate(jsonResponse(200, mutationSuccess(updated)));
  await settleAsyncWork();

  const patchCall = harness.fetchCalls.find(
    (call) => call.options.method === "PATCH",
  );
  assert.equal(
    String(patchCall.url),
    "https://friendly.test/api/v1/diary-entries/31/",
  );
  assert.equal(patchCall.options.headers["X-CSRFToken"], "test-csrf-token");
  assert.deepEqual(JSON.parse(patchCall.options.body), {
    content: "수정한 우리 이야기",
  });
  assert.match(descendantText(harness.list), /수정한 우리 이야기/);
  assert.match(descendantText(harness.list), /2026\.07\.19 11:23 수정/);
  assert.equal(harness.list.focused, true);

  const remove = findButton(harness.list, "삭제");
  remove.listeners.click();
  remove.listeners.click();
  await settleAsyncWork();

  assert.equal(harness.confirmMessages.length, 1);
  assert.match(harness.confirmMessages[0], /되돌릴 수 없어요/);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "DELETE").length,
    1,
  );
  const deleteCall = harness.fetchCalls.find(
    (call) => call.options.method === "DELETE",
  );
  assert.equal(deleteCall.options.headers["X-CSRFToken"], "test-csrf-token");
  assert.equal(harness.list.hidden, true);
  assert.equal(harness.empty.hidden, false);
  assert.equal(harness.empty.focused, true);
  assert.equal(harness.toasts.at(-1).message, "일기를 삭제했어요.");
});

test("a confirmed update stays rendered when the following list refresh fails", async () => {
  let entry = diaryEntry();
  let listRequestCount = 0;
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "PATCH") {
        entry = diaryEntry({
          content: "서버에 저장된 수정 내용",
          updatedAt: "2026-07-19T02:23:00Z",
        });
        return Promise.resolve(jsonResponse(200, mutationSuccess(entry)));
      }
      listRequestCount += 1;
      if (listRequestCount > 1) {
        return Promise.resolve(
          jsonResponse(500, {
            error: {
              details: [],
              errorCode: "INTERNAL_SERVER_ERROR",
              errorType: "SERVER",
              reason: "잠시 후 다시 시도해 주세요.",
            },
            resultType: "ERROR",
            success: null,
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: [entry] })));
    },
  });
  await settleAsyncWork();

  findButton(harness.list, "수정").listeners.click();
  const editForm = findTag(harness.list, "form");
  findByName(editForm, "content").value = "서버에 저장된 수정 내용";
  editForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  assert.match(descendantText(harness.list), /서버에 저장된 수정 내용/);
  assert.ok(findButton(harness.list, "수정"));
  assert.match(descendantText(harness.listStatus), /불러오지 못했어요/);
  assert.equal(harness.toasts.at(-1).message, "일기를 수정했어요.");
});

test("a confirmed deletion does not leave an active stale card after refresh failure", async () => {
  const entry = diaryEntry();
  let listRequestCount = 0;
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "DELETE") {
        return Promise.resolve(jsonResponse(200, mutationSuccess(null)));
      }
      listRequestCount += 1;
      if (listRequestCount > 1) {
        return Promise.resolve(
          jsonResponse(500, {
            error: {
              details: [],
              errorCode: "INTERNAL_SERVER_ERROR",
              errorType: "SERVER",
              reason: "잠시 후 다시 시도해 주세요.",
            },
            resultType: "ERROR",
            success: null,
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: [entry] })));
    },
  });
  await settleAsyncWork();

  findButton(harness.list, "삭제").listeners.click();
  await settleAsyncWork();

  assert.match(descendantText(harness.list), /삭제한 일기예요/);
  assert.equal(findButton(harness.list, "삭제"), undefined);
  assert.match(descendantText(harness.listStatus), /불러오지 못했어요/);
  assert.equal(harness.toasts.at(-1).message, "일기를 삭제했어요.");
});

test("diary redirects an expired session to login with a local next path", async () => {
  const harness = createDiaryHarness({
    response: jsonResponse(403, {
      error: {
        details: [],
        errorCode: "AUTHENTICATION_REQUIRED",
        errorType: "AUTHENTICATION",
        reason: "로그인이 필요합니다.",
      },
      resultType: "ERROR",
      success: null,
    }),
    search: "?pageNumber=2&from=nav",
  });

  await settleAsyncWork();

  assert.deepEqual(harness.assignedLocations, [
    `/login/?next=${encodeURIComponent("/diary/?pageNumber=2&from=nav")}`,
  ]);
  assert.equal(harness.content.attributes["aria-busy"], "false");
});

test("diary rendering never assigns HTML strings", () => {
  assert.equal(/\binnerHTML\b/.test(diarySource), false);
  assert.equal(/\bentryDate\b/.test(diarySource), false);
  assert.equal(/\bmaxLength\b/.test(diarySource), false);
  assert.match(diarySource, /\.textContent\s*=/);
});
