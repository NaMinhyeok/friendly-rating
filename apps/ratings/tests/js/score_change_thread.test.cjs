const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const threadScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/score_change_thread.js",
);
const threadSource = fs.readFileSync(threadScriptPath, "utf8");

class FakeElement {
  constructor({ dataset = {}, disabled = false, hidden = false, value = "" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = dataset;
    this.disabled = disabled;
    this.focused = false;
    this.hidden = hidden;
    this.listeners = {};
    this.selectors = {};
    this.textContent = "";
    this.value = value;
    this.classList = {
      toggle: (name, enabled) => {
        this.attributes[`class:${name}`] = enabled;
      },
    };
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

function comment({
  id = 91,
  authorName = "둘째 <script>",
  content = '<img src=x onerror="globalThis.compromised=true">고마워',
  isMine = false,
  attachments = [],
} = {}) {
  return {
    id,
    author: { slot: isMine ? 1 : 2, displayName: authorName },
    content,
    createdAt: "2026-07-19T01:24:00Z",
    isMine,
    attachments,
  };
}

function threadPayload({ comments = [comment()] } = {}) {
  return {
    resultType: "SUCCESS",
    error: null,
    success: {
      id: 31,
      sourceParticipant: { slot: 1, displayName: "첫째 <script>" },
      targetParticipant: { slot: 2, displayName: "둘째" },
      changedBy: { slot: 1, displayName: "첫째 <script>" },
      delta: 3,
      reason: '<svg onload="globalThis.compromised=true">고마운 마음',
      resultingScore: 53,
      createdAt: "2026-07-19T01:23:00Z",
      commentCount: comments.length,
      threadUrl: "/history/31/",
      attachments: [],
      comments,
    },
  };
}

function createThreadHarness({
  fetchImplementation,
  search = "?from=push",
  withMedia = false,
} = {}) {
  const content = new FakeElement();
  const status = new FakeElement();
  const view = new FakeElement({ hidden: true });
  const changeRoot = new FakeElement();
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
  const mediaInput = new FakeElement();
  const mediaSelection = new FakeElement({ hidden: true });
  const mediaStatus = new FakeElement();

  form.selectors = {
    "[data-comment-character-current]": characterCount,
    "[data-comment-form-status]": formStatus,
    "[data-comment-submit-label]": submitLabel,
    "[data-comment-submit]": submitButton,
    "[name=content]": textarea,
    "[name=csrfmiddlewaretoken]": csrf,
  };
  if (withMedia) {
    form.selectors["[data-comment-media-input]"] = mediaInput;
    form.selectors["[data-comment-media-selection]"] = mediaSelection;
    form.selectors["[data-comment-media-status]"] = mediaStatus;
  }

  const root = new FakeElement({
    dataset: {
      commentsUrl: "/api/v1/score-changes/31/comments/",
      ...(withMedia ? { mediaUploadsUrl: "/api/v1/media-uploads/" } : {}),
      threadUrl: "/api/v1/score-changes/31/",
    },
  });
  root.selectors = {
    "[data-comment-count]": commentCount,
    "[data-comment-empty]": commentEmpty,
    "[data-comment-form]": form,
    "[data-comment-list]": commentList,
    "[data-thread-change]": changeRoot,
    "[data-thread-content]": content,
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
    cookie: "",
    visibilityState: "visible",
    addEventListener(type, listener) {
      documentListeners[type] = listener;
    },
    createElement(tagName) {
      createdTags.push(tagName);
      return new FakeElement();
    },
    querySelector(selector) {
      return selector === "[data-score-thread-root]" ? root : null;
    },
  };
  const location = {
    assign(value) {
      assignedLocations.push(value);
    },
    origin: "https://friendly.test",
    pathname: "/history/31/",
    search,
  };
  const defaultResponse = jsonResponse(200, threadPayload());
  const sandbox = {
    console,
    document,
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      return fetchImplementation
        ? fetchImplementation(url, options, fetchCalls.length)
        : Promise.resolve(defaultResponse);
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
    changeRoot,
    characterCount,
    commentCount,
    commentEmpty,
    commentList,
    content,
    createdTags,
    document,
    documentListeners,
    fetchCalls,
    form,
    formStatus,
    globalListeners,
    mediaInput,
    mediaSelection,
    mediaStatus,
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

function readMediaUploadResetDecision({ status, errorCode }) {
  const fixture = JSON.stringify({ status, errorCode });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${threadSource}
      {
        const fixture = ${fixture};
        globalThis.mediaUploadResetDecision = shouldResetMediaUploads(
          new ApiRequestError(fixture.status, {
            errorCode: fixture.errorCode,
            reason: "test",
            details: [],
          }),
        );
      }`,
    sandbox,
    { filename: threadScriptPath },
  );

  return sandbox.mediaUploadResetDecision;
}

function readAttachmentValidation(fileName) {
  const fixture = JSON.stringify({ fileName });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
    URL,
    window: { location: { origin: "https://friendly.test" } },
  };

  vm.runInNewContext(
    `${threadSource}
      {
        const fixture = ${fixture};
        globalThis.attachmentIsValid = validateAttachment({
          id: 1,
          kind: "image",
          fileName: fixture.fileName,
          contentType: "image/jpeg",
          byteSize: 512,
          contentUrl: "/media/1/content/",
        });
      }`,
    sandbox,
    { filename: threadScriptPath },
  );

  return sandbox.attachmentIsValid;
}

test("threads measure attachment filenames in Unicode code points", () => {
  assert.equal(readAttachmentValidation(`${"😀".repeat(128)}.jpg`), true);
  assert.equal(readAttachmentValidation("가".repeat(256)), false);
});

test("comments reset upload IDs that can no longer be attached", () => {
  for (const [status, errorCode] of [
    [409, "MEDIA_UPLOAD_CONFLICT"],
    [404, "NOT_FOUND"],
    [403, "PERMISSION_DENIED"],
  ]) {
    assert.equal(readMediaUploadResetDecision({ status, errorCode }), true);
  }
  assert.equal(
    readMediaUploadResetDecision({ status: 400, errorCode: "INVALID_INPUT" }),
    false,
  );
});

test("thread fetches and safely renders the score change and comments", async () => {
  const harness = createThreadHarness();

  await settleAsyncWork();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/v1/score-changes/31/");
  assert.equal(harness.fetchCalls[0].options.credentials, "same-origin");
  assert.equal(harness.fetchCalls[0].options.cache, "no-store");
  assert.equal(harness.fetchCalls[0].options.headers.Accept, "application/json");

  assert.equal(harness.view.hidden, false);
  assert.equal(harness.content.attributes["aria-busy"], "false");
  assert.equal(harness.commentCount.textContent, "1");
  assert.equal(harness.commentList.children.length, 1);
  assert.equal(harness.commentEmpty.hidden, true);
  assert.equal(harness.textarea.disabled, false);
  assert.equal(harness.submitButton.disabled, false);

  const renderedText = `${descendantText(harness.changeRoot)}${descendantText(
    harness.commentList,
  )}`;
  assert.match(renderedText, /첫째 <script> → 둘째/);
  assert.match(renderedText, /<svg onload=/);
  assert.match(renderedText, /둘째 <script>/);
  assert.match(renderedText, /<img src=x onerror=/);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.createdTags.includes("img"), false);
  assert.equal(harness.createdTags.includes("svg"), false);
  assert.equal(harness.sandbox.compromised, undefined);
});

test("comment submission sends CSRF once and appends the created comment", async () => {
  let resolvePost;
  let commentCommitted = false;
  const createdComment = comment({
    id: 92,
    authorName: "첫째",
    content: "좋아 <script>",
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
                resultType: "SUCCESS",
                error: null,
                success: createdComment,
              }),
            );
          };
        });
      }
      return Promise.resolve(
        jsonResponse(
          200,
          threadPayload({ comments: commentCommitted ? [createdComment] : [] }),
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
  assert.equal(postCalls[0].url, "/api/v1/score-changes/31/comments/");
  assert.equal(postCalls[0].options.credentials, "same-origin");
  assert.equal(postCalls[0].options.headers.Accept, "application/json");
  assert.equal(postCalls[0].options.headers["Content-Type"], "application/json");
  assert.equal(
    postCalls[0].options.headers["X-CSRFToken"],
    "rendered-csrf-token",
  );
  assert.deepEqual(JSON.parse(postCalls[0].options.body), {
    content: "좋아 <script>",
  });
  assert.equal(preventedSubmissions, 2);
  assert.equal(harness.form.attributes["aria-busy"], "true");
  assert.equal(harness.textarea.disabled, true);
  assert.equal(harness.submitButton.disabled, true);
  assert.equal(harness.submitLabel.textContent, "남기고 있어요…");

  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/history/31/" },
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
  assert.match(descendantText(harness.commentList.children[0]), /나/);
  assert.match(descendantText(harness.commentList.children[0]), /좋아 <script>/);
  assert.equal(harness.commentList.hidden, false);
  assert.equal(harness.commentEmpty.hidden, true);
  assert.equal(harness.textarea.value, "");
  assert.equal(harness.characterCount.textContent, "0");
  assert.equal(harness.formStatus.textContent, "댓글을 남겼어요.");
  assert.equal(harness.form.attributes["aria-busy"], "false");
  assert.equal(harness.textarea.disabled, false);
  assert.equal(harness.submitButton.disabled, false);
  assert.equal(harness.submitLabel.textContent, "댓글 남기기");
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method !== "POST").length,
    2,
  );
});

test("a media-only comment uploads directly and renders the attachment safely", async () => {
  const uploadId = "00000000-0000-4000-8000-000000000001";
  const attachment = {
    id: uploadId,
    kind: "image",
    fileName: '<script>globalThis.compromised=true</script>.jpg',
    contentType: "image/jpeg",
    byteSize: 512,
    contentUrl: `/media/${uploadId}/content/`,
  };
  const createdComment = comment({
    id: 94,
    authorName: "첫째",
    content: "",
    isMine: true,
    attachments: [attachment],
  });
  const harness = createThreadHarness({
    withMedia: true,
    fetchImplementation(url, options) {
      if (url === "/api/v1/score-changes/31/") {
        return Promise.resolve(
          jsonResponse(200, threadPayload({ comments: [] })),
        );
      }
      if (url === "/api/v1/media-uploads/") {
        return Promise.resolve(
          jsonResponse(201, {
            resultType: "SUCCESS",
            error: null,
            success: {
              uploadId,
              uploadUrl: "https://r2.example.test/pending/object",
              requiredHeaders: { "Content-Type": "image/jpeg" },
              expiresAt: "2026-07-19T12:00:00Z",
            },
          }),
        );
      }
      if (url === "https://r2.example.test/pending/object") {
        return Promise.resolve({ ok: true, redirected: false });
      }
      if (url === `/api/v1/media-uploads/${uploadId}/complete/`) {
        return Promise.resolve(
          jsonResponse(200, {
            resultType: "SUCCESS",
            error: null,
            success: {
              id: attachment.id,
              kind: attachment.kind,
              fileName: attachment.fileName,
              contentType: attachment.contentType,
              byteSize: attachment.byteSize,
            },
          }),
        );
      }
      if (url === "/api/v1/score-changes/31/comments/") {
        return Promise.resolve(
          jsonResponse(201, {
            resultType: "SUCCESS",
            error: null,
            success: createdComment,
          }),
        );
      }
      throw new Error(`Unexpected request: ${url} ${options.method || "GET"}`);
    },
  });
  await settleAsyncWork();

  const file = {
    name: '<script>globalThis.compromised=true</script>.jpg',
    size: 512,
    type: "image/jpeg",
  };
  harness.mediaInput.files = [file];
  harness.mediaInput.listeners.change();
  harness.form.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const initiateCall = harness.fetchCalls.find(
    (call) => call.url === "/api/v1/media-uploads/",
  );
  assert.deepEqual(JSON.parse(initiateCall.options.body), {
    purpose: "comment",
    kind: "image",
    fileName: file.name,
    contentType: "image/jpeg",
    byteSize: 512,
    scoreChangeId: 31,
  });
  assert.equal(
    initiateCall.options.headers["X-CSRFToken"],
    "rendered-csrf-token",
  );

  const directPut = harness.fetchCalls.find(
    (call) => call.url === "https://r2.example.test/pending/object",
  );
  assert.equal(directPut.options.method, "PUT");
  assert.equal(directPut.options.body, file);
  assert.deepEqual(directPut.options.headers, { "Content-Type": "image/jpeg" });
  assert.equal(directPut.options.credentials, "omit");

  const completeCall = harness.fetchCalls.find(
    (call) => call.url === `/api/v1/media-uploads/${uploadId}/complete/`,
  );
  assert.equal(completeCall.options.body, "{}");
  const commentCall = harness.fetchCalls.find(
    (call) => call.url === "/api/v1/score-changes/31/comments/",
  );
  assert.deepEqual(JSON.parse(commentCall.options.body), {
    content: "",
    mediaUploadIds: [uploadId],
  });

  assert.equal(harness.commentList.children.length, 1);
  assert.equal(harness.commentCount.textContent, "1");
  assert.match(descendantText(harness.commentList), /<script>/);
  assert.equal(harness.createdTags.includes("img"), true);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.createdTags.includes("svg"), false);
  assert.equal(harness.sandbox.compromised, undefined);
  assert.equal(harness.formStatus.textContent, "댓글을 남겼어요.");
  assert.equal(harness.textarea.value, "");
});

test("a score refresh started before submission cannot erase the created comment", async () => {
  let getCount = 0;
  let resolvePost;
  let resolveStaleGet;
  const createdComment = comment({ id: 93, content: "남아 있어야 해", isMine: true });
  const harness = createThreadHarness({
    fetchImplementation(url, options) {
      if (options.method === "POST") {
        return new Promise((resolve) => {
          resolvePost = () =>
            resolve(
              jsonResponse(201, {
                resultType: "SUCCESS",
                error: null,
                success: createdComment,
              }),
            );
        });
      }
      getCount += 1;
      if (getCount === 2) {
        return new Promise((resolve) => {
          resolveStaleGet = () =>
            resolve(jsonResponse(200, threadPayload({ comments: [] })));
        });
      }
      return Promise.resolve(jsonResponse(200, threadPayload({ comments: [] })));
    },
  });
  await settleAsyncWork();

  harness.refreshButton.listeners.click();
  harness.textarea.value = "남아 있어야 해";
  harness.form.listeners.submit({ preventDefault() {} });
  assert.equal(harness.refreshButton.disabled, true);

  resolvePost();
  await settleAsyncWork();
  assert.equal(harness.commentList.children.length, 1);
  assert.match(descendantText(harness.commentList), /남아 있어야 해/);

  resolveStaleGet();
  await settleAsyncWork();
  assert.equal(harness.commentList.children.length, 1);
  assert.match(descendantText(harness.commentList), /남아 있어야 해/);
  assert.equal(harness.content.attributes["aria-busy"], "false");
  assert.equal(harness.refreshButton.disabled, false);
});

test("a successful retry clears an earlier background refresh error", async () => {
  let getCount = 0;
  const harness = createThreadHarness({
    fetchImplementation() {
      getCount += 1;
      if (getCount === 2) {
        return Promise.resolve(
          jsonResponse(500, {
            resultType: "ERROR",
            error: {
              errorType: "SERVER",
              errorCode: "INTERNAL_SERVER_ERROR",
              reason: "잠시 후 다시 시도해 주세요.",
              details: [],
            },
            success: null,
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, threadPayload()));
    },
  });
  await settleAsyncWork();

  harness.document.visibilityState = "visible";
  harness.documentListeners.visibilitychange();
  await settleAsyncWork();
  assert.match(harness.formStatus.textContent, /최신 댓글을 불러오지 못했어요/);

  harness.documentListeners.visibilitychange();
  await settleAsyncWork();
  assert.equal(harness.formStatus.textContent, "");
});

test("visible and persisted page restores refetch the thread", async () => {
  const harness = createThreadHarness();
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 1);

  harness.document.visibilityState = "hidden";
  harness.documentListeners.visibilitychange();
  harness.globalListeners.pageshow({ persisted: false });
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 1);

  harness.document.visibilityState = "visible";
  harness.documentListeners.visibilitychange();
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 2);

  harness.globalListeners.pageshow({ persisted: true });
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 3);

  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/history/31/" },
  });
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 4);

  harness.documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/history/99/" },
  });
  await settleAsyncWork();
  assert.equal(harness.fetchCalls.length, 4);
  for (const call of harness.fetchCalls) {
    assert.equal(call.url, "/api/v1/score-changes/31/");
    assert.equal(call.options.cache, "no-store");
  }
});

test("an expired session redirects to login with the local thread path", async () => {
  const harness = createThreadHarness({
    fetchImplementation() {
      return Promise.resolve(
        jsonResponse(403, {
          resultType: "ERROR",
          error: {
            errorType: "AUTHENTICATION",
            errorCode: "AUTHENTICATION_REQUIRED",
            reason: "로그인이 필요합니다.",
            details: [],
          },
          success: null,
        }),
      );
    },
  });

  await settleAsyncWork();

  assert.deepEqual(harness.assignedLocations, [
    `/login/?next=${encodeURIComponent("/history/31/?from=push")}`,
  ]);
  assert.equal(harness.content.attributes["aria-busy"], "false");
});

test("thread rendering never assigns HTML strings", () => {
  assert.equal(/\binnerHTML\b/.test(threadSource), false);
  assert.match(threadSource, /\.textContent\s*=/);
});
