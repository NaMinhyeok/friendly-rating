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
const diaryTemplatePath = path.resolve(
  __dirname,
  "../../templates/ratings/diary.html",
);
const diaryTemplateSource = fs.readFileSync(diaryTemplatePath, "utf8");

class FakeElement {
  constructor({ dataset = {}, tagName = "div" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.classList = {
      toggle: (name, enabled) => {
        this.attributes[`class:${name}`] = enabled;
      },
    };
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
    this.files = [];
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
  attachments = [],
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
    attachments,
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

function mediaAttachment({
  byteSize = 512,
  contentType = "image/jpeg",
  contentUrl,
  fileName = "오늘 사진.jpg",
  id = "00000000-0000-4000-8000-000000000001",
  kind = "image",
} = {}) {
  return {
    byteSize,
    contentType,
    contentUrl: contentUrl || `/media/${id}/content/`,
    fileName,
    id,
    kind,
  };
}

function createDiaryHarness({
  fetchImplementation,
  response = jsonResponse(200, diaryPage()),
  search = "?pageNumber=1",
  withMedia = false,
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
  const mediaInput = new FakeElement({ tagName: "input" });
  mediaInput.disabled = true;
  const mediaSelection = new FakeElement({ tagName: "div" });
  mediaSelection.hidden = true;
  const mediaStatus = new FakeElement({ tagName: "p" });
  createForm.selectors = {
    '[data-diary-error-for="content"]': contentError,
    "[data-diary-character-current]": characterCount,
    "[data-diary-create-status]": createStatus,
    "[name=content]": diaryContent,
    "[name=csrfmiddlewaretoken]": csrf,
  };
  if (withMedia) {
    createForm.selectors["[data-diary-media-input]"] = mediaInput;
    createForm.selectors["[data-diary-media-selection]"] = mediaSelection;
    createForm.selectors["[data-diary-media-status]"] = mediaStatus;
  }
  const root = new FakeElement({
    dataset: {
      diaryEntriesUrl: "/api/v1/diary-entries/",
      ...(withMedia ? { mediaUploadsUrl: "/api/v1/media-uploads/" } : {}),
    },
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
    mediaInput,
    mediaSelection,
    mediaStatus,
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

function findByAriaLabel(element, label) {
  return descendants(element).find(
    (child) => child.attributes["aria-label"] === label,
  );
}

function readMediaValidation(files) {
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
    `${diarySource}
      globalThis.mediaValidation = validateDiaryMediaSelection(${JSON.stringify(files)});
    `,
    sandbox,
    { filename: diaryScriptPath },
  );
  return sandbox.mediaValidation;
}

function runXhrUpload(responseURL) {
  class FakeXMLHttpRequest {
    constructor() {
      this.listeners = {};
      this.responseURL = responseURL;
      this.status = 200;
      this.upload = { addEventListener() {} };
    }

    addEventListener(type, listener) {
      this.listeners[type] = listener;
    }

    open(method, url, isAsync) {
      this.method = method;
      this.url = url;
      this.isAsync = isAsync;
    }

    send(body) {
      this.body = body;
      this.listeners.load();
    }

    setRequestHeader() {}
  }

  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
    XMLHttpRequest: FakeXMLHttpRequest,
    URL,
    window: { location: { origin: "https://friendly.test" } },
  };
  vm.runInNewContext(
    `${diarySource}
      globalThis.uploadPromise = putFileWithProgress(
        {
          requiredHeaders: { "Content-Type": "image/jpeg" },
          uploadUrl: "https://r2.example.test/pending/original?signature=one",
        },
        { name: "오늘.jpg", size: 512, type: "image/jpeg" },
        () => undefined,
      );
    `,
    sandbox,
    { filename: diaryScriptPath },
  );
  return sandbox.uploadPromise;
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

test("diary directly uploads selected photos once and renders safe attachments", async () => {
  const uploadIds = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
  ];
  const files = [
    {
      name: '<script>globalThis.compromised=true</script>.jpg',
      size: 512,
      type: "image/jpeg",
    },
    { name: "산책.png", size: 1024, type: "image/png" },
  ];
  let initiatedCount = 0;
  let created = null;
  const harness = createDiaryHarness({
    withMedia: true,
    fetchImplementation(url, options) {
      const target = String(url);
      if (!options.method) {
        return Promise.resolve(
          jsonResponse(200, diaryPage({ results: created ? [created] : [] })),
        );
      }
      if (target === "/api/v1/media-uploads/" && options.method === "POST") {
        const uploadId = uploadIds[initiatedCount];
        initiatedCount += 1;
        return Promise.resolve(
          jsonResponse(201, {
            error: null,
            resultType: "SUCCESS",
            success: {
              expiresAt: "2026-07-19T12:00:00Z",
              requiredHeaders: { "Content-Type": files[initiatedCount - 1].type },
              uploadId,
              uploadUrl: `https://r2.example.test/pending/${uploadId}`,
            },
          }),
        );
      }
      if (target.startsWith("https://r2.example.test/pending/")) {
        return Promise.resolve({ ok: true, redirected: false });
      }
      const completedUploadId = uploadIds.find((uploadId) =>
        target.endsWith(`/media-uploads/${uploadId}/complete/`),
      );
      if (completedUploadId) {
        const index = uploadIds.indexOf(completedUploadId);
        return Promise.resolve(
          jsonResponse(200, {
            error: null,
            resultType: "SUCCESS",
            success: {
              byteSize: files[index].size,
              contentType: files[index].type,
              fileName: files[index].name,
              id: completedUploadId,
              kind: "image",
            },
          }),
        );
      }
      if (target === "/api/v1/diary-entries/" && options.method === "POST") {
        created = diaryEntry({
          attachments: uploadIds.map((id, index) =>
            mediaAttachment({
              byteSize: files[index].size,
              contentType: files[index].type,
              fileName: files[index].name,
              id,
            }),
          ),
          content: "사진과 함께 남긴 기록",
        });
        return Promise.resolve(jsonResponse(201, mutationSuccess(created)));
      }
      throw new Error(`Unexpected request: ${target} ${options.method}`);
    },
  });
  await settleAsyncWork();

  harness.mediaInput.files = files;
  harness.mediaInput.listeners.change();
  harness.diaryContent.value = "사진과 함께 남긴 기록";
  harness.createForm.listeners.submit({ preventDefault() {} });
  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const initiationCalls = harness.fetchCalls.filter(
    (call) =>
      call.url === "/api/v1/media-uploads/" && call.options.method === "POST",
  );
  assert.equal(initiationCalls.length, 2);
  assert.deepEqual(
    initiationCalls.map((call) => JSON.parse(call.options.body)),
    files.map((file) => ({
      byteSize: file.size,
      contentType: file.type,
      fileName: file.name,
      kind: "image",
      purpose: "diaryEntry",
    })),
  );
  const directPuts = harness.fetchCalls.filter((call) =>
    String(call.url).startsWith("https://r2.example.test/pending/"),
  );
  assert.equal(directPuts.length, 2);
  assert.equal(directPuts[0].options.credentials, "omit");
  assert.equal(directPuts[0].options.redirect, "error");
  assert.equal(directPuts[0].options.body, files[0]);
  const createCall = harness.fetchCalls.find(
    (call) =>
      call.url === "/api/v1/diary-entries/" && call.options.method === "POST",
  );
  assert.deepEqual(JSON.parse(createCall.options.body), {
    content: "사진과 함께 남긴 기록",
    mediaUploadIds: uploadIds,
  });
  assert.equal(harness.mediaSelection.hidden, true);
  assert.equal(harness.mediaSelection.children.length, 0);
  assert.equal(harness.list.children.length, 1);
  assert.match(descendantText(harness.list), /<script>/);
  assert.equal(harness.createdTags.includes("img"), true);
  assert.equal(harness.createdTags.includes("script"), false);
  assert.equal(harness.sandbox.compromised, undefined);
});

test("XHR upload accepts success only from the intended origin and path", async () => {
  await assert.doesNotReject(
    runXhrUpload(
      "https://r2.example.test/pending/original?provider-response=ok",
    ),
  );
  await assert.rejects(
    runXhrUpload("https://redirected.example.test/pending/original"),
    /최종 업로드 위치/,
  );
  await assert.rejects(
    runXhrUpload("https://r2.example.test/pending/different"),
    /최종 업로드 위치/,
  );
});

test("diary retries completion with the same uploaded intent after uncertain and finalizing responses", async () => {
  const uploadId = "00000000-0000-4000-8000-000000000021";
  const file = { name: "재시도.jpg", size: 512, type: "image/jpeg" };
  let completeCount = 0;
  let created = null;
  const harness = createDiaryHarness({
    withMedia: true,
    fetchImplementation(url, options) {
      const target = String(url);
      if (!options.method) {
        return Promise.resolve(
          jsonResponse(200, diaryPage({ results: created ? [created] : [] })),
        );
      }
      if (target === "/api/v1/media-uploads/" && options.method === "POST") {
        return Promise.resolve(
          jsonResponse(201, {
            error: null,
            resultType: "SUCCESS",
            success: {
              expiresAt: "2099-07-19T12:00:00Z",
              requiredHeaders: { "Content-Type": file.type },
              uploadId,
              uploadUrl: "https://r2.example.test/pending/retry",
            },
          }),
        );
      }
      if (target === "https://r2.example.test/pending/retry") {
        return Promise.resolve({ ok: true, redirected: false });
      }
      if (target.endsWith(`/media-uploads/${uploadId}/complete/`)) {
        completeCount += 1;
        if (completeCount === 1) {
          return Promise.reject(new TypeError("completion response lost"));
        }
        if (completeCount === 2) {
          return Promise.resolve(
            jsonResponse(409, {
              error: {
                details: [],
                errorCode: "MEDIA_UPLOAD_CONFLICT",
                errorType: "CONFLICT",
                reason: "파일을 확인하고 있어요. 잠시 후 다시 시도해 주세요.",
              },
              resultType: "ERROR",
              success: null,
            }),
          );
        }
        return Promise.resolve(
          jsonResponse(200, {
            error: null,
            resultType: "SUCCESS",
            success: {
              byteSize: file.size,
              contentType: file.type,
              fileName: file.name,
              id: uploadId,
              kind: "image",
            },
          }),
        );
      }
      if (target === "/api/v1/diary-entries/" && options.method === "POST") {
        created = diaryEntry({
          attachments: [
            mediaAttachment({
              byteSize: file.size,
              contentType: file.type,
              fileName: file.name,
              id: uploadId,
            }),
          ],
          content: "완료 확인 재시도",
        });
        return Promise.resolve(jsonResponse(201, mutationSuccess(created)));
      }
      throw new Error(`Unexpected request: ${target} ${options.method}`);
    },
  });
  await settleAsyncWork();

  harness.mediaInput.files = [file];
  harness.mediaInput.listeners.change();
  harness.diaryContent.value = "완료 확인 재시도";
  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();
  assert.equal(harness.createSubmit.disabled, false);
  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();
  assert.equal(harness.createSubmit.disabled, false);
  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  assert.equal(
    harness.fetchCalls.filter(
      (call) =>
        call.url === "/api/v1/media-uploads/" && call.options.method === "POST",
    ).length,
    1,
  );
  assert.equal(
    harness.fetchCalls.filter(
      (call) => call.url === "https://r2.example.test/pending/retry",
    ).length,
    1,
  );
  assert.equal(completeCount, 3);
  const createCall = harness.fetchCalls.find(
    (call) =>
      call.url === "/api/v1/diary-entries/" && call.options.method === "POST",
  );
  assert.deepEqual(JSON.parse(createCall.options.body).mediaUploadIds, [
    uploadId,
  ]);
});

test("structured media unavailability keeps a completed upload retryable", async () => {
  const uploadId = "00000000-0000-4000-8000-000000000022";
  const file = { name: "보관.jpg", size: 512, type: "image/jpeg" };
  let diaryPostCount = 0;
  let created = null;
  const harness = createDiaryHarness({
    withMedia: true,
    fetchImplementation(url, options) {
      const target = String(url);
      if (!options.method) {
        return Promise.resolve(
          jsonResponse(200, diaryPage({ results: created ? [created] : [] })),
        );
      }
      if (target === "/api/v1/media-uploads/" && options.method === "POST") {
        return Promise.resolve(
          jsonResponse(201, {
            error: null,
            resultType: "SUCCESS",
            success: {
              expiresAt: "2099-07-19T12:00:00Z",
              requiredHeaders: { "Content-Type": file.type },
              uploadId,
              uploadUrl: "https://r2.example.test/pending/unavailable",
            },
          }),
        );
      }
      if (target === "https://r2.example.test/pending/unavailable") {
        return Promise.resolve({ ok: true, redirected: false });
      }
      if (target.endsWith(`/media-uploads/${uploadId}/complete/`)) {
        return Promise.resolve(
          jsonResponse(200, {
            error: null,
            resultType: "SUCCESS",
            success: {
              byteSize: file.size,
              contentType: file.type,
              fileName: file.name,
              id: uploadId,
              kind: "image",
            },
          }),
        );
      }
      if (target === "/api/v1/diary-entries/" && options.method === "POST") {
        diaryPostCount += 1;
        if (diaryPostCount === 1) {
          return Promise.resolve(
            jsonResponse(503, {
              error: {
                details: [],
                errorCode: "MEDIA_UPLOADS_UNAVAILABLE",
                errorType: "EXTERNAL_SERVICE",
                reason: "파일 업로드 서비스를 지금 사용할 수 없습니다.",
              },
              resultType: "ERROR",
              success: null,
            }),
          );
        }
        created = diaryEntry({
          attachments: [mediaAttachment({ fileName: file.name, id: uploadId })],
          content: "서비스 복구 뒤 저장",
        });
        return Promise.resolve(jsonResponse(201, mutationSuccess(created)));
      }
      throw new Error(`Unexpected request: ${target} ${options.method}`);
    },
  });
  await settleAsyncWork();

  harness.mediaInput.files = [file];
  harness.mediaInput.listeners.change();
  harness.diaryContent.value = "서비스 복구 뒤 저장";
  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  assert.equal(harness.createSubmit.disabled, false);
  assert.equal(harness.createSubmitLabel.textContent, "일기 남기기");
  assert.match(harness.createStatus.textContent, /사용할 수 없습니다/);

  harness.createForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  assert.equal(diaryPostCount, 2);
  assert.equal(
    harness.fetchCalls.filter(
      (call) =>
        call.url === "/api/v1/media-uploads/" && call.options.method === "POST",
    ).length,
    1,
  );
  assert.equal(
    harness.fetchCalls.filter(
      (call) => call.url === "https://r2.example.test/pending/unavailable",
    ).length,
    1,
  );
  const diaryCalls = harness.fetchCalls.filter(
    (call) =>
      call.url === "/api/v1/diary-entries/" && call.options.method === "POST",
  );
  assert.deepEqual(
    diaryCalls.map((call) => JSON.parse(call.options.body).mediaUploadIds),
    [[uploadId], [uploadId]],
  );
});

test("diary attachment selection enforces the server media policy", () => {
  const image = (index) => ({
    name: `${index}.jpg`,
    size: 512,
    type: "image/jpeg",
  });

  assert.match(
    readMediaValidation([image(1), image(2), image(3), image(4), image(5)]),
    /최대 4장/,
  );
  assert.match(
    readMediaValidation([
      image(1),
      { name: "clip.mp4", size: 1024, type: "video/mp4" },
    ]),
    /함께 올릴 수 없고/,
  );
  assert.match(
    readMediaValidation([
      { name: "large.mov", size: 100 * 1024 * 1024 + 1, type: "video/quicktime" },
    ]),
    /100MB 이하/,
  );
  assert.match(
    readMediaValidation([{ name: "note.pdf", size: 512, type: "application/pdf" }]),
    /JPG, PNG, WebP/,
  );
});

test("diary media inputs describe their help and live upload status", async () => {
  assert.match(
    diaryTemplateSource,
    /id="id_diary_media"[\s\S]*?aria-describedby="diary-media-help diary-media-status"/,
  );
  assert.match(
    diaryTemplateSource,
    /id="diary-media-help" class="field-help"/,
  );
  assert.match(
    diaryTemplateSource,
    /id="diary-media-status" class="media-status"[\s\S]*?role="status"[\s\S]*?aria-live="polite"/,
  );

  const harness = createDiaryHarness({
    withMedia: true,
    response: jsonResponse(200, diaryPage({ results: [diaryEntry()] })),
  });
  await settleAsyncWork();

  findButton(harness.list, "수정").listeners.click();
  const editForm = findTag(harness.list, "form");
  const editMediaInput = descendants(editForm).find(
    (element) => element.tagName === "input" && element.type === "file",
  );
  const describedIds = editMediaInput.attributes["aria-describedby"].split(" ");
  const describedElements = describedIds.map((id) =>
    descendants(editForm).find((element) => element.id === id),
  );

  assert.equal(describedElements.every(Boolean), true);
  assert.equal(describedElements[1].attributes.role, "status");
  assert.equal(describedElements[1].attributes["aria-live"], "polite");
});

test("diary rejects a cross-origin attachment URL before rendering media", async () => {
  const entry = diaryEntry({
    attachments: [
      mediaAttachment({ contentUrl: "https://evil.example.test/private.jpg" }),
    ],
  });
  const harness = createDiaryHarness({
    response: jsonResponse(200, diaryPage({ results: [entry] })),
  });

  await settleAsyncWork();

  assert.match(descendantText(harness.listStatus), /불러오지 못했어요/);
  assert.equal(harness.createdTags.includes("img"), false);
});

test("diary renders one allowed video with controls and a download fallback", async () => {
  const entry = diaryEntry({
    attachments: [
      mediaAttachment({
        byteSize: 2048,
        contentType: "video/mp4",
        fileName: "오늘.mp4",
        kind: "video",
      }),
    ],
  });
  const harness = createDiaryHarness({
    response: jsonResponse(200, diaryPage({ results: [entry] })),
  });

  await settleAsyncWork();

  const video = findTag(harness.list, "video");
  assert.ok(video);
  assert.equal(video.controls, true);
  assert.equal(video.preload, "metadata");
  assert.match(descendantText(harness.list), /오늘\.mp4 다운로드/);
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

test("text-only diary edit omits media IDs and preserves existing attachments", async () => {
  const attachment = mediaAttachment();
  let entry = diaryEntry({ attachments: [attachment] });
  const harness = createDiaryHarness({
    withMedia: true,
    fetchImplementation(_url, options) {
      if (options.method === "PATCH") {
        entry = diaryEntry({
          attachments: [attachment],
          content: "본문만 수정",
          updatedAt: "2026-07-19T02:23:00Z",
        });
        return Promise.resolve(jsonResponse(200, mutationSuccess(entry)));
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: [entry] })));
    },
  });
  await settleAsyncWork();

  findButton(harness.list, "수정").listeners.click();
  const editForm = findTag(harness.list, "form");
  findByName(editForm, "content").value = "본문만 수정";
  editForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const patchCall = harness.fetchCalls.find(
    (call) => call.options.method === "PATCH",
  );
  assert.deepEqual(JSON.parse(patchCall.options.body), {
    content: "본문만 수정",
  });
  assert.equal(
    harness.fetchCalls.some(
      (call) =>
        call.url === "/api/v1/media-uploads/" && call.options.method === "POST",
    ),
    false,
  );
  assert.match(descendantText(harness.list), /오늘 사진\.jpg 다운로드/);
});

test("diary edit replaces attachments with retained and newly uploaded IDs", async () => {
  const removed = mediaAttachment({
    fileName: "지울 사진.jpg",
    id: "00000000-0000-4000-8000-000000000011",
  });
  const retained = mediaAttachment({
    contentType: "image/png",
    fileName: "남길 사진.png",
    id: "00000000-0000-4000-8000-000000000012",
  });
  const newUploadId = "00000000-0000-4000-8000-000000000013";
  const newFile = { name: "새 사진.webp", size: 2048, type: "image/webp" };
  let entry = diaryEntry({ attachments: [removed, retained] });
  const harness = createDiaryHarness({
    withMedia: true,
    fetchImplementation(url, options) {
      const target = String(url);
      if (!options.method) {
        return Promise.resolve(jsonResponse(200, diaryPage({ results: [entry] })));
      }
      if (target === "/api/v1/media-uploads/" && options.method === "POST") {
        return Promise.resolve(
          jsonResponse(201, {
            error: null,
            resultType: "SUCCESS",
            success: {
              expiresAt: "2026-07-19T12:00:00Z",
              requiredHeaders: { "Content-Type": newFile.type },
              uploadId: newUploadId,
              uploadUrl: "https://r2.example.test/pending/edit",
            },
          }),
        );
      }
      if (target === "https://r2.example.test/pending/edit") {
        return Promise.resolve({ ok: true, redirected: false });
      }
      if (target.endsWith(`/media-uploads/${newUploadId}/complete/`)) {
        return Promise.resolve(
          jsonResponse(200, {
            error: null,
            resultType: "SUCCESS",
            success: {
              byteSize: newFile.size,
              contentType: newFile.type,
              fileName: newFile.name,
              id: newUploadId,
              kind: "image",
            },
          }),
        );
      }
      if (options.method === "PATCH") {
        entry = diaryEntry({
          attachments: [
            retained,
            mediaAttachment({
              byteSize: newFile.size,
              contentType: newFile.type,
              fileName: newFile.name,
              id: newUploadId,
            }),
          ],
          content: "첨부를 고친 이야기",
          updatedAt: "2026-07-19T02:23:00Z",
        });
        return Promise.resolve(jsonResponse(200, mutationSuccess(entry)));
      }
      throw new Error(`Unexpected request: ${target} ${options.method}`);
    },
  });
  await settleAsyncWork();

  findButton(harness.list, "수정").listeners.click();
  const editForm = findTag(harness.list, "form");
  const removeExisting = findByAriaLabel(editForm, "지울 사진.jpg 삭제");
  assert.ok(removeExisting);
  removeExisting.listeners.click();
  const mediaInput = descendants(editForm).find(
    (element) => element.tagName === "input" && element.type === "file",
  );
  assert.ok(mediaInput);
  mediaInput.files = [newFile];
  mediaInput.listeners.change();
  const editContent = findByName(editForm, "content");
  editContent.value = "첨부를 고친 이야기";
  editForm.listeners.submit({ preventDefault() {} });
  editForm.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();

  const patchCalls = harness.fetchCalls.filter(
    (call) => call.options.method === "PATCH",
  );
  assert.equal(patchCalls.length, 1);
  assert.deepEqual(JSON.parse(patchCalls[0].options.body), {
    content: "첨부를 고친 이야기",
    mediaUploadIds: [retained.id, newUploadId],
  });
  assert.equal(
    harness.fetchCalls.filter(
      (call) =>
        call.url === "/api/v1/media-uploads/" && call.options.method === "POST",
    ).length,
    1,
  );
  const rendered = descendantText(harness.list);
  assert.doesNotMatch(rendered, /지울 사진/);
  assert.match(rendered, /남길 사진/);
  assert.match(rendered, /새 사진/);
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

test("an uncertain deletion stays inert when its verification refresh fails", async () => {
  const entry = diaryEntry();
  let listRequestCount = 0;
  const harness = createDiaryHarness({
    fetchImplementation(_url, options) {
      if (options.method === "DELETE") {
        return Promise.reject(new TypeError("connection lost after delete"));
      }
      listRequestCount += 1;
      if (listRequestCount > 1) {
        return Promise.reject(new TypeError("verification refresh failed"));
      }
      return Promise.resolve(jsonResponse(200, diaryPage({ results: [entry] })));
    },
  });
  await settleAsyncWork();

  const staleRemoveButton = findButton(harness.list, "삭제");
  staleRemoveButton.listeners.click();
  await settleAsyncWork();

  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "DELETE").length,
    1,
  );
  assert.equal(listRequestCount, 2);
  assert.match(
    descendantText(harness.list),
    /삭제 결과를 확인하지 못했어요\. 목록을 다시 확인하고 있어요/,
  );
  assert.equal(findButton(harness.list, "삭제"), undefined);
  assert.match(descendantText(harness.listStatus), /불러오지 못했어요/);

  staleRemoveButton.listeners.click();
  await settleAsyncWork();

  assert.equal(harness.confirmMessages.length, 1);
  assert.equal(
    harness.fetchCalls.filter((call) => call.options.method === "DELETE").length,
    1,
  );
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
