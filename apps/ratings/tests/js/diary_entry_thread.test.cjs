const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const threadScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/diary_entry_thread.js",
);
const threadSource = fs.readFileSync(threadScriptPath, "utf8");

class FakeElement {
  constructor({ dataset = {}, disabled = false, hidden = false, value = "" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.classList = {
      toggle: (name, enabled) => {
        this.attributes[`class:${name}`] = enabled;
      },
    };
    this.dataset = dataset;
    this.disabled = disabled;
    this.focused = false;
    this.hidden = hidden;
    this.listeners = {};
    this.selectors = {};
    this.textContent = "";
    this.value = value;
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

function diaryComment({
  authorName = "둘째 <script>",
  content = '<img src=x onerror="globalThis.compromised=true">답장',
  createdAt = "2026-07-20T01:24:00Z",
  id = 91,
  isMine = false,
} = {}) {
  return {
    author: { displayName: authorName, slot: isMine ? 1 : 2 },
    content,
    createdAt,
    id,
    isMine,
  };
}

function diaryThreadPayload({
  attachments = [],
  authorName = "첫째 <script>",
  comments = [diaryComment()],
  content = '<svg onload="globalThis.compromised=true">함께 걸었어',
} = {}) {
  return {
    error: null,
    resultType: "SUCCESS",
    success: {
      attachments,
      author: { displayName: authorName, slot: 1 },
      commentCount: comments.length,
      comments,
      content,
      createdAt: "2026-07-20T01:23:00Z",
      id: 31,
      isMine: true,
      threadUrl: "/diary/31/",
      updatedAt: null,
    },
  };
}

function diaryAttachment({
  fileName = '<script>globalThis.compromised=true</script>.jpg',
  id = "00000000-0000-4000-8000-000000000001",
} = {}) {
  return {
    byteSize: 512,
    contentType: "image/jpeg",
    contentUrl: `/media/${id}/content/`,
    fileName,
    id,
    kind: "image",
  };
}

function diaryNotFoundResponse() {
  return jsonResponse(404, {
    error: {
      details: [],
      errorCode: "NOT_FOUND",
      errorType: "NOT_FOUND",
      reason: "찾을 수 없습니다.",
    },
    resultType: "ERROR",
    success: null,
  });
}

function createThreadHarness({
  fetchImplementation,
  response = jsonResponse(200, diaryThreadPayload()),
  search = "?from=push",
} = {}) {
  const content = new FakeElement();
  const status = new FakeElement();
  const view = new FakeElement({ hidden: true });
  const entryRoot = new FakeElement();
  const commentList = new FakeElement();
  const commentEmpty = new FakeElement();
  const commentCount = new FakeElement();
  const refreshButton = new FakeElement();
  const form = new FakeElement();
  const textarea = new FakeElement({ disabled: true });
  const submitButton = new FakeElement({ disabled: true });
  const submitLabel = new FakeElement();
  const formStatus = new FakeElement();
  const characterCount = new FakeElement();
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  form.selectors = {
    "[data-comment-character-current]": characterCount,
    "[data-comment-form-status]": formStatus,
    "[data-comment-submit-label]": submitLabel,
    "[data-comment-submit]": submitButton,
    "[name=content]": textarea,
    "[name=csrfmiddlewaretoken]": csrf,
  };

  const root = new FakeElement({
    dataset: {
      commentsUrl: "/api/v1/diary-entries/31/comments/",
      threadUrl: "/api/v1/diary-entries/31/",
    },
  });
  root.selectors = {
    "[data-comment-count]": commentCount,
    "[data-comment-empty]": commentEmpty,
    "[data-comment-form]": form,
    "[data-comment-list]": commentList,
    "[data-thread-content]": content,
    "[data-thread-entry]": entryRoot,
    "[data-thread-refresh]": refreshButton,
    "[data-thread-status]": status,
    "[data-thread-view]": view,
  };

  const assignedLocations = [];
  const createdTags = [];
  const documentListeners = {};
  const globalListeners = {};
  const fetchCalls = [];
  const document = {
    visibilityState: "visible",
    addEventListener(type, listener) {
      documentListeners[type] = listener;
    },
    createElement(tagName) {
      createdTags.push(tagName);
      return new FakeElement();
    },
    querySelector(selector) {
      return selector === "[data-diary-thread-root]" ? root : null;
    },
  };
  const location = {
    assign(value) {
      assignedLocations.push(value);
    },
    origin: "https://friendly.test",
    pathname: "/diary/31/",
    search,
  };
  const sandbox = {
    console,
    document,
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      return fetchImplementation
        ? fetchImplementation(url, options, fetchCalls.length)
        : Promise.resolve(response);
    },
    URL,
    window: { location },
    addEventListener(type, listener) {
      globalListeners[type] = listener;
    },
  };

  vm.runInNewContext(threadSource, sandbox, { filename: threadScriptPath });

  return {
    assignedLocations,
    characterCount,
    commentCount,
    commentEmpty,
    commentList,
    content,
    createdTags,
    document,
    documentListeners,
    entryRoot,
    fetchCalls,
    form,
    formStatus,
    globalListeners,
    refreshButton,
    sandbox,
    status,
    submitButton,
    submitLabel,
    textarea,
    view,
  };
}

async function settleAsyncWork() {
  for (let count = 0; count < 6; count += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function descendantText(element) {
  return [
    element.textContent,
    ...element.children.flatMap((child) => descendantText(child)),
  ].join("");
}

function descendants(element) {
  return [element, ...element.children.flatMap((child) => descendants(child))];
}

test("diary thread fetches and safely renders its entry, attachments, and comments", async () => {
  const comments = [
    diaryComment({
      content: "먼저 남긴 답장",
      createdAt: "2026-07-20T01:24:00Z",
      id: 90,
    }),
    diaryComment({
      content: '<img src=x onerror="globalThis.compromised=true">나중 답장',
      createdAt: "2026-07-20T01:25:00Z",
      id: 91,
    }),
  ];
  const harness = createThreadHarness({
    response: jsonResponse(
      200,
      diaryThreadPayload({ attachments: [diaryAttachment()], comments }),
    ),
  });

  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/v1/diary-entries/31/");
  assert.equal(harness.fetchCalls[0].options.credentials, "same-origin");
  assert.equal(harness.fetchCalls[0].options.cache, "no-store");
  assert.equal(harness.fetchCalls[0].options.headers.Accept, "application/json");
  assert.equal(harness.view.hidden, false);
  assert.equal(harness.content.attributes["aria-busy"], "false");
  assert.equal(harness.commentCount.textContent, "2");
  assert.equal(harness.commentList.children.length, 2);
  assert.equal(harness.commentEmpty.hidden, true);
  assert.equal(harness.textarea.disabled, false);
  assert.equal(harness.submitButton.disabled, false);
  assert.match(descendantText(harness.entryRoot), /<svg onload=/);
  assert.match(descendantText(harness.entryRoot), /첫째 <script>/);
  assert.match(
    descendantText(harness.commentList.children[0]),
    /먼저 남긴 답장/,
  );
  assert.match(
    descendantText(harness.commentList.children[1]),
    /<img src=x onerror=.*나중 답장/,
  );
  assert.equal(harness.createdTags.includes("img"), true);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.createdTags.includes("svg"), false);
  assert.equal(harness.sandbox.compromised, undefined);
});

test("diary thread avatars keep a leading emoji intact", async () => {
  const harness = createThreadHarness({
    response: jsonResponse(
      200,
      diaryThreadPayload({ authorName: "🙂민지", comments: [] }),
    ),
  });

  await settleAsyncWork();

  const avatar = descendants(harness.entryRoot).find(
    (child) => child.className === "diary-card__avatar",
  );
  assert.equal(avatar.textContent, "🙂");
});

test("diary thread renders multiple images as an accessible carousel", async () => {
  const attachments = [
    diaryAttachment(),
    diaryAttachment({
      fileName: "두 번째 사진.jpg",
      id: "00000000-0000-4000-8000-000000000002",
    }),
  ];
  const harness = createThreadHarness({
    response: jsonResponse(
      200,
      diaryThreadPayload({ attachments, comments: [] }),
    ),
  });

  await settleAsyncWork();

  const gallery = descendants(harness.entryRoot).find((child) =>
    child.className.includes("attachment-gallery--carousel"),
  );
  assert.equal(
    gallery.className,
    "attachment-gallery attachment-gallery--carousel",
  );
  assert.equal(gallery.attributes.role, "region");
  assert.equal(gallery.attributes["aria-roledescription"], "이미지 슬라이더");
  assert.equal(gallery.attributes["aria-label"], "일기에 첨부된 파일 · 이미지 2장");
  assert.equal(gallery.tabIndex, 0);
  assert.deepEqual(
    gallery.children.map((child) => child.attributes["aria-label"]),
    ["이미지 1 / 2", "이미지 2 / 2"],
  );
});

test("diary comment submission sends CSRF once and refreshes after a matching push", async () => {
  let resolvePost;
  let commentCommitted = false;
  const createdComment = diaryComment({
    authorName: "첫째",
    content: "좋아 <script>",
    id: 92,
    isMine: true,
  });
  const harness = createThreadHarness({
    fetchImplementation(url, options) {
      if (options.method === "POST") {
        return new Promise((resolve) => {
          resolvePost = () => {
            commentCommitted = true;
            resolve(
              jsonResponse(201, {
                error: null,
                resultType: "SUCCESS",
                success: createdComment,
              }),
            );
          };
        });
      }
      return Promise.resolve(
        jsonResponse(
          200,
          diaryThreadPayload({
            comments: commentCommitted ? [createdComment] : [],
          }),
        ),
      );
    },
  });
  await settleAsyncWork();
  harness.textarea.value = "  좋아 <script>  ";
  harness.textarea.listeners.input();
  let preventedSubmissions = 0;
  const event = {
    preventDefault() {
      preventedSubmissions += 1;
    },
  };

  harness.form.listeners.submit(event);
  harness.form.listeners.submit(event);

  const postCalls = harness.fetchCalls.filter(
    (call) => call.options.method === "POST",
  );
  assert.equal(postCalls.length, 1);
  assert.equal(postCalls[0].url, "/api/v1/diary-entries/31/comments/");
  assert.equal(postCalls[0].options.credentials, "same-origin");
  assert.equal(postCalls[0].options.headers["Content-Type"], "application/json");
  assert.equal(postCalls[0].options.headers["X-CSRFToken"], "rendered-csrf-token");
  assert.deepEqual(JSON.parse(postCalls[0].options.body), {
    content: "좋아 <script>",
  });
  assert.equal(preventedSubmissions, 2);
  assert.equal(harness.form.attributes["aria-busy"], "true");
  assert.equal(harness.textarea.disabled, true);
  assert.equal(harness.submitButton.disabled, true);
  assert.equal(harness.submitLabel.textContent, "남기고 있어요…");

  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/diary/31/" },
  });
  await settleAsyncWork();
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method !== "POST").length,
    1,
  );

  resolvePost();
  await settleAsyncWork();

  assert.equal(harness.commentCount.textContent, "1");
  assert.equal(harness.commentList.children.length, 1);
  assert.match(descendantText(harness.commentList), /좋아 <script>/);
  assert.equal(harness.textarea.value, "");
  assert.equal(harness.characterCount.textContent, "0");
  assert.equal(harness.formStatus.textContent, "댓글을 남겼어요.");
  assert.equal(harness.form.attributes["aria-busy"], "false");
  assert.equal(harness.textarea.disabled, false);
  assert.equal(harness.submitButton.disabled, false);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method !== "POST").length,
    2,
  );
});

test("diary comments enforce one through five hundred Unicode characters", async () => {
  const acceptedContent = "🙂".repeat(500);
  const createdComment = diaryComment({
    authorName: "첫째",
    content: acceptedContent,
    isMine: true,
  });
  const harness = createThreadHarness({
    fetchImplementation(_url, options) {
      if (options.method === "POST") {
        return Promise.resolve(
          jsonResponse(201, {
            error: null,
            resultType: "SUCCESS",
            success: createdComment,
          }),
        );
      }
      return Promise.resolve(
        jsonResponse(200, diaryThreadPayload({ comments: [] })),
      );
    },
  });
  await settleAsyncWork();

  harness.textarea.value = "   ";
  harness.form.listeners.submit({ preventDefault() {} });
  assert.equal(harness.textarea.focused, true);
  assert.match(harness.formStatus.textContent, /입력해 주세요/);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "POST").length,
    0,
  );

  harness.textarea.value = "가".repeat(501);
  harness.textarea.listeners.input();
  harness.form.listeners.submit({ preventDefault() {} });
  assert.equal(harness.characterCount.textContent, "501");
  assert.match(harness.formStatus.textContent, /500자 이하/);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "POST").length,
    0,
  );

  harness.textarea.value = acceptedContent;
  harness.textarea.listeners.input();
  harness.form.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const postCall = harness.fetchCalls.find(
    (call) => call.options.method === "POST",
  );
  assert.equal(harness.characterCount.textContent, "0");
  assert.equal(JSON.parse(postCall.options.body).content, acceptedContent);
});

test("diary thread refreshes on manual, visible, matching push, and persisted restores", async () => {
  const harness = createThreadHarness();
  await settleAsyncWork();

  harness.refreshButton.listeners.click();
  await settleAsyncWork();
  harness.documentListeners.visibilitychange();
  await settleAsyncWork();
  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/history/31/" },
  });
  await settleAsyncWork();
  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "https://evil.test/diary/31/" },
  });
  await settleAsyncWork();
  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "https://friendly.test/diary/31/" },
  });
  await settleAsyncWork();
  harness.globalListeners.pageshow({ persisted: false });
  await settleAsyncWork();
  harness.globalListeners.pageshow({ persisted: true });
  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 5);
  assert.equal(
    harness.fetchCalls.every(
      (call) => call.url === "/api/v1/diary-entries/31/",
    ),
    true,
  );
});

test("diary thread redirects an expired session to its local login destination", async () => {
  const harness = createThreadHarness({
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
  });

  await settleAsyncWork();

  assert.deepEqual(harness.assignedLocations, [
    `/login/?next=${encodeURIComponent("/diary/31/?from=push")}`,
  ]);
  assert.equal(harness.view.hidden, true);
});

test("diary thread clears stale content and permanently disables comments after refresh finds deletion", async () => {
  const harness = createThreadHarness({
    fetchImplementation(_url, _options, callNumber) {
      return Promise.resolve(
        callNumber === 1
          ? jsonResponse(200, diaryThreadPayload())
          : diaryNotFoundResponse(),
      );
    },
  });
  await settleAsyncWork();
  assert.equal(harness.view.hidden, false);
  assert.notEqual(harness.entryRoot.children.length, 0);

  harness.refreshButton.listeners.click();
  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 2);
  assert.equal(harness.view.hidden, true);
  assert.equal(harness.entryRoot.children.length, 0);
  assert.equal(harness.commentList.children.length, 0);
  assert.equal(harness.textarea.disabled, true);
  assert.equal(harness.submitButton.disabled, true);
  assert.equal(harness.refreshButton.disabled, true);
  assert.match(descendantText(harness.status), /찾을 수 없는 일기/);
  assert.match(descendantText(harness.status), /우리 일기로 돌아가기/);

  harness.documentListeners.visibilitychange();
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 2);
});

test("diary comment 404 clears the deleted thread instead of enabling retries", async () => {
  const harness = createThreadHarness({
    fetchImplementation(_url, options) {
      return Promise.resolve(
        options.method === "POST"
          ? diaryNotFoundResponse()
          : jsonResponse(200, diaryThreadPayload()),
      );
    },
  });
  await settleAsyncWork();

  harness.textarea.value = "삭제와 겹친 댓글";
  harness.form.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 2);
  assert.equal(harness.view.hidden, true);
  assert.equal(harness.entryRoot.children.length, 0);
  assert.equal(harness.commentList.children.length, 0);
  assert.equal(harness.textarea.value, "");
  assert.equal(harness.textarea.disabled, true);
  assert.equal(harness.submitButton.disabled, true);
  assert.equal(harness.submitLabel.textContent, "댓글을 남길 수 없어요");
  assert.match(descendantText(harness.status), /찾을 수 없는 일기/);
});

test("diary thread rendering never assigns HTML strings", () => {
  assert.equal(/\binnerHTML\b/.test(threadSource), false);
  assert.match(threadSource, /\.textContent\s*=/);
});
