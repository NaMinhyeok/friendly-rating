const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const dashboardScriptPath = path.resolve(
  __dirname,
  "../../static/ratings/dashboard.js",
);
const dashboardSource = fs.readFileSync(dashboardScriptPath, "utf8");

class FakeElement {
  constructor({ dataset = {}, disabled = false, name = "", value = "" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = dataset;
    this.disabled = disabled;
    this.focused = false;
    this.hidden = false;
    this.listeners = {};
    this.name = name;
    this.selectorLists = {};
    this.selectors = {};
    this.style = {};
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

  getAttribute(name) {
    return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null;
  }

  querySelector(selector) {
    return Object.hasOwn(this.selectors, selector) ? this.selectors[selector] : null;
  }

  querySelectorAll(selector) {
    return this.selectorLists[selector] || [];
  }

  removeAttribute(name) {
    delete this.attributes[name];
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

function scorePayload(currentScore) {
  return {
    resultType: "SUCCESS",
    error: null,
    success: {
      results: [
        {
          currentScore,
          isMine: true,
          sourceParticipant: { displayName: "첫째" },
          targetParticipant: { displayName: "둘째" },
        },
        {
          currentScore: 7,
          isMine: false,
          sourceParticipant: { displayName: "둘째" },
          targetParticipant: { displayName: "첫째" },
        },
      ],
    },
  };
}

async function settleAsyncWork() {
  for (let count = 0; count < 5; count += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, reject, resolve };
}

function descendantText(element) {
  return [
    element.textContent,
    ...element.children.flatMap((child) => descendantText(child)),
  ].join("");
}

function evaluateCommand({ operation, amount, reason, currentScore = null }) {
  const fixture = JSON.stringify({ operation, amount, reason, currentScore });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const fixture = ${fixture};
        const form = {
          querySelector(selector) {
            if (selector === "[name=operation]:checked") {
              return { value: fixture.operation };
            }
            if (selector === "[name=amount]") {
              return { value: fixture.amount };
            }
            if (selector === "[name=reason]") {
              return { value: fixture.reason };
            }
            throw new Error(\`Unexpected selector: \${selector}\`);
          },
        };
        const errors = [];
        const statuses = [];
        const toasts = [];
        showFieldError = (_form, field, message, options = {}) => {
          const error = { field, message };
          if (options.assistiveOnly) {
            error.assistiveOnly = true;
          }
          errors.push(error);
        };
        showFormStatus = (_form, message, state) => statuses.push({ message, state });
        focusFirstInvalidField = () => undefined;
        globalThis.woorisaiShowToast = (message, options) => {
          toasts.push({ message, tone: options.tone });
        };
        globalThis.commandResult = readScoreChangeCommand(form, fixture.currentScore);
        globalThis.commandErrors = errors;
        globalThis.commandStatuses = statuses;
        globalThis.commandToasts = toasts;
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(
    JSON.stringify({
      command: sandbox.commandResult,
      errors: sandbox.commandErrors,
      statuses: sandbox.commandStatuses,
      toasts: sandbox.commandToasts,
    }),
  );
}

function readCommand(input) {
  return evaluateCommand(input).command;
}

function readErrorHandling({ status, apiError }) {
  const fixture = JSON.stringify({ status, apiError });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const fixture = ${fixture};
        const statusElement = {
          classList: { toggle() {} },
          textContent: "",
        };
        const form = {
          querySelector(selector) {
            if (selector === "[data-score-form-status]") {
              return statusElement;
            }
            if (selector === "[aria-invalid=true]") {
              return null;
            }
            throw new Error(\`Unexpected selector: \${selector}\`);
          },
        };
        const error = new ApiRequestError(fixture.status, fixture.apiError);
        globalThis.errorHandlingResult = {
          message: statusElement.textContent,
          requiresRefresh: showApiFormError(form, error),
        };
        globalThis.errorHandlingResult.message = statusElement.textContent;
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(JSON.stringify(sandbox.errorHandlingResult));
}

function readMappedApiFields(details) {
  const fixture = JSON.stringify({ details });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const fixture = ${fixture};
        const mappedFields = [];
        showFieldError = (_form, field, message) => {
          mappedFields.push({ field, message });
        };
        showFormStatus = () => undefined;
        focusFirstInvalidField = () => undefined;
        const error = new ApiRequestError(400, {
          errorCode: "INVALID_REQUEST",
          reason: "입력값을 확인해 주세요.",
          details: fixture.details,
        });
        showApiFormError({}, error);
        globalThis.mappedFields = mappedFields;
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(JSON.stringify(sandbox.mappedFields));
}

function readCreatedChangeValidation(payload, command) {
  const fixture = JSON.stringify({ payload, command });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const fixture = ${fixture};
        try {
          const change = readCreatedScoreChange(fixture.payload, fixture.command);
          globalThis.createdChangeValidation = { ok: true, change };
        } catch (error) {
          globalThis.createdChangeValidation = { ok: false, message: error.message };
        }
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(JSON.stringify(sandbox.createdChangeValidation));
}

function readCompletedUploadValidation(payload, uploadId) {
  const fixture = JSON.stringify({ payload, uploadId });
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const fixture = ${fixture};
        try {
          const completed = readCompletedUpload(
            fixture.payload,
            fixture.uploadId,
          );
          globalThis.completedUploadValidation = { ok: true, completed };
        } catch (error) {
          globalThis.completedUploadValidation = {
            ok: false,
            message: error.message,
          };
        }
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(JSON.stringify(sandbox.completedUploadValidation));
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
    `${dashboardSource}
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
    { filename: dashboardScriptPath },
  );

  return sandbox.mediaUploadResetDecision;
}

async function runDirectUploadLifecycle() {
  const events = [];
  const file = {
    name: "마음 <script>.jpg",
    size: 512,
    type: "image/jpeg",
  };
  const uploadId = "00000000-0000-4000-8000-000000000001";
  const sandbox = {
    console,
    document: {
      querySelector() {
        return null;
      },
    },
    fetch(url, options = {}) {
      events.push({
        bodyIsFile: options.body === file,
        cache: options.cache,
        credentials: options.credentials,
        headers: options.headers,
        method: options.method,
        stage: "put",
        url,
      });
      return Promise.resolve({ ok: true, redirected: false });
    },
    URL,
    window: { location: { origin: "https://friendly.test" } },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const item = {
          file: globalThis.testFile,
          progress: { value: 0 },
          progressStatus: { textContent: "" },
          uploadId: null,
        };
        requestJson = async (url, options) => {
          globalThis.events.push({
            body: options.body,
            cache: options.cache,
            headers: options.headers,
            method: options.method,
            stage: url === "/api/v1/media-uploads/" ? "intent" : "complete",
            url,
          });
          if (url === "/api/v1/media-uploads/") {
            return {
              resultType: "SUCCESS",
              error: null,
              success: {
                uploadId: "${uploadId}",
                uploadUrl: "https://r2.example.test/pending/object",
                requiredHeaders: { "Content-Type": "image/jpeg" },
                expiresAt: "2026-07-19T12:00:00Z",
              },
            };
          }
          return {
            resultType: "SUCCESS",
            error: null,
            success: {
              id: "${uploadId}",
              kind: "image",
              fileName: "마음 <script>.jpg",
              contentType: "image/jpeg",
              byteSize: 512,
            },
          };
        };
        globalThis.uploadPromise = ensureMediaUploaded(item, {
          csrfToken: "rendered-csrf-token",
          purpose: "scoreChange",
          uploadsUrl: "/api/v1/media-uploads/",
        }).then((result) => ({
          progress: item.progress.value,
          progressLabel: item.progressStatus.textContent,
          result,
        }));
      }
    `,
    Object.assign(sandbox, { events, testFile: file }),
    { filename: dashboardScriptPath },
  );

  const result = await sandbox.uploadPromise;
  return {
    events: JSON.parse(JSON.stringify(events)),
    result: JSON.parse(JSON.stringify(result)),
    uploadId,
  };
}

function createLiveScoreMediaDiscardHarness() {
  const uploadId = "00000000-0000-4000-8000-000000000007";
  const file = { name: "cancelled.jpg", size: 512, type: "image/jpeg" };
  const input = new FakeElement();
  const selection = new FakeElement();
  const status = new FakeElement();
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  const form = new FakeElement();
  form.selectors = {
    "[data-score-media-input]": input,
    "[data-score-media-selection]": selection,
    "[data-score-media-status]": status,
    "[name=csrfmiddlewaretoken]": csrf,
  };
  const root = new FakeElement({
    dataset: { mediaUploadsUrl: "/api/v1/media-uploads/" },
  });
  const fetchCalls = [];
  const putStarted = deferred();
  let putSignal = null;
  class TestUrl extends URL {}
  TestUrl.createObjectURL = (selectedFile) => `blob:${selectedFile.name}`;
  TestUrl.revokeObjectURL = () => undefined;

  const sandbox = {
    AbortController,
    clearTimeout,
    console,
    document: {
      createElement() {
        return new FakeElement();
      },
      querySelector() {
        return null;
      },
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      if (url === "/api/v1/media-uploads/") {
        return Promise.resolve(
          jsonResponse(201, {
            resultType: "SUCCESS",
            error: null,
            success: {
              uploadId,
              uploadUrl: "https://r2.example.test/pending/cancelled",
              requiredHeaders: { "Content-Type": "image/jpeg" },
              expiresAt: "2026-07-19T12:00:00Z",
            },
          }),
        );
      }
      if (url === "https://r2.example.test/pending/cancelled") {
        putSignal = options.signal;
        putStarted.resolve();
        return new Promise((_resolve, reject) => {
          const rejectCancelledUpload = () =>
            reject(new Error("direct upload aborted"));
          if (options.signal?.aborted) {
            rejectCancelledUpload();
          } else {
            options.signal?.addEventListener("abort", rejectCancelledUpload, {
              once: true,
            });
          }
        });
      }
      if (url === `/api/v1/media-uploads/${uploadId}/discard/`) {
        return Promise.resolve(
          jsonResponse(200, {
            resultType: "SUCCESS",
            error: null,
            success: null,
          }),
        );
      }
      throw new Error(`Unexpected request: ${url} ${options.method || "GET"}`);
    },
    setTimeout,
    URL: TestUrl,
    window: {
      location: {
        assign() {},
        origin: "https://friendly.test",
        pathname: "/",
        search: "",
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      globalThis.scoreMediaManager = initializeScoreMedia(
        globalThis.root,
        globalThis.form,
      );
    `,
    Object.assign(sandbox, { form, root }),
    { filename: dashboardScriptPath },
  );

  return {
    fetchCalls,
    file,
    input,
    manager: sandbox.scoreMediaManager,
    putStarted: putStarted.promise,
    readPutSignal: () => putSignal,
    selection,
    status,
    uploadId,
  };
}

function createScoreMediaManagerHarness(uploadPromises) {
  const input = new FakeElement();
  const selection = new FakeElement();
  const status = new FakeElement();
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  const form = new FakeElement();
  form.selectors = {
    "[data-score-media-input]": input,
    "[data-score-media-selection]": selection,
    "[data-score-media-status]": status,
    "[name=csrfmiddlewaretoken]": csrf,
  };
  const root = new FakeElement({
    dataset: { mediaUploadsUrl: "/api/v1/media-uploads/" },
  });
  const uploadCalls = [];
  const discardCalls = [];
  const revokedPreviewUrls = [];
  const assignedLocations = [];
  const sandbox = {
    console,
    document: {
      createElement() {
        return new FakeElement();
      },
      querySelector() {
        return null;
      },
    },
    form,
    root,
    discardCalls,
    uploadCalls,
    uploadPromises,
    URL: {
      createObjectURL(file) {
        return `blob:${file.name}`;
      },
      revokeObjectURL(url) {
        revokedPreviewUrls.push(url);
      },
    },
    window: {
      location: {
        assign(url) {
          assignedLocations.push(url);
        },
        origin: "https://friendly.test",
        pathname: "/",
        search: "?from=upload",
      },
    },
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        let uploadIndex = 0;
        ensureMediaUploaded = (item, context) => {
          globalThis.uploadCalls.push({
            csrfToken: context.csrfToken,
            fileName: item.file.name,
            purpose: context.purpose,
            uploadsUrl: context.uploadsUrl,
          });
          const promise = globalThis.uploadPromises[uploadIndex];
          uploadIndex += 1;
          return promise.then(
            (uploadId) => {
              item.intentUploadId = uploadId;
              item.uploadId = uploadId;
              return uploadId;
            },
            (error) => {
              if (error?.asApiRequestError) {
                throw new ApiRequestError(error.status, error.apiError);
              }
              throw error;
            },
          );
        };
        requestJson = async (url, options) => {
          if (!url.endsWith("/discard/")) {
            throw new Error("Unexpected request: " + url);
          }
          globalThis.discardCalls.push({
            body: options.body,
            csrfToken: options.headers["X-CSRFToken"],
            method: options.method,
            url,
          });
          return { resultType: "SUCCESS", error: null, success: null };
        };
        globalThis.scoreMediaManager = initializeScoreMedia(
          globalThis.root,
          globalThis.form,
        );
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return {
    assignedLocations,
    discardCalls,
    input,
    manager: sandbox.scoreMediaManager,
    revokedPreviewUrls,
    select(file) {
      input.files = [file];
      input.listeners.change();
    },
    selection,
    status,
    uploadCalls,
  };
}

test("increase maps the entered amount to a positive delta", () => {
  assert.deepEqual(
    readCommand({ operation: "increase", amount: "3", reason: "  고마워  " }),
    { delta: 3, reason: "고마워" },
  );
});

test("decrease maps the entered amount to a negative delta", () => {
  assert.deepEqual(
    readCommand({ operation: "decrease", amount: "4", reason: "서운했어" }),
    { delta: -4, reason: "서운했어" },
  );
});

test("target mode sends the final score without deriving a delta", () => {
  assert.deepEqual(
    readCommand({
      operation: "target",
      amount: "100",
      reason: "다시 힘내자",
      currentScore: 35,
    }),
    { targetScore: 100, reason: "다시 힘내자" },
  );
});

test("target mode accepts zero as a final score", () => {
  assert.deepEqual(
    readCommand({
      operation: "target",
      amount: "0",
      reason: "",
      currentScore: 35,
    }),
    { targetScore: 0, reason: "" },
  );
});

test("target mode rejects a raw empty value instead of treating it as zero", () => {
  const result = evaluateCommand({
    operation: "target",
    amount: "",
    reason: "",
    currentScore: 35,
  });

  assert.equal(result.command, null);
  assert.deepEqual(result.errors, [
    {
      field: "amount",
      message: "최종 점수는 0부터 100 사이의 정수여야 합니다.",
    },
  ]);
});

test("a fractional score uses a warning toast and marks the input invalid", () => {
  const result = evaluateCommand({
    operation: "target",
    amount: "35.5",
    reason: "",
    currentScore: 35,
  });

  assert.equal(result.command, null);
  assert.deepEqual(result.errors, [
    {
      assistiveOnly: true,
      field: "amount",
      message: "점수는 소수점 없이 정수로 입력해 주세요.",
    },
  ]);
  assert.deepEqual(result.statuses, []);
  assert.deepEqual(result.toasts, [
    {
      message: "점수는 소수점 없이 정수로 입력해 주세요.",
      tone: "warning",
    },
  ]);
});

test("target mode leaves same-score detection to the locked server state", () => {
  assert.deepEqual(
    readCommand({
      operation: "target",
      amount: "35",
      reason: "",
      currentScore: 35,
    }),
    { targetScore: 35, reason: "" },
  );
});

test("a successful mutation response must include a valid resulting score", () => {
  assert.deepEqual(
    readCreatedChangeValidation(
      {
        resultType: "SUCCESS",
        error: null,
        success: { delta: 65 },
      },
      { targetScore: 100, reason: "" },
    ),
    { ok: false, message: "점수 변경 응답 형식이 올바르지 않습니다." },
  );
});

test("a delta mutation response must match the requested delta", () => {
  assert.deepEqual(
    readCreatedChangeValidation(
      {
        resultType: "SUCCESS",
        error: null,
        success: { delta: -3, resultingScore: 20 },
      },
      { delta: 3, reason: "" },
    ),
    { ok: false, message: "점수 변경 응답 형식이 올바르지 않습니다." },
  );
});

test("an unverified successful mutation response locks retry until refresh", () => {
  assert.deepEqual(readErrorHandling({ status: 201 }), {
    message: "요청 결과를 확인하지 못했어요. 새로고침해 현재 점수를 확인해 주세요.",
    requiresRefresh: true,
  });
});

test("a 2xx error envelope also locks retry until refresh", () => {
  assert.deepEqual(
    readErrorHandling({
      status: 201,
      apiError: {
        errorCode: "REQUEST_FAILED",
        reason: "응답 계약이 올바르지 않습니다.",
        details: [],
      },
    }),
    {
      message: "요청 결과를 확인하지 못했어요. 새로고침해 현재 점수를 확인해 주세요.",
      requiresRefresh: true,
    },
  );
});

test("a confirmed score conflict remains retryable", () => {
  assert.deepEqual(
    readErrorHandling({
      status: 409,
      apiError: {
        errorCode: "SCORE_OUT_OF_RANGE",
        reason: "점수 범위를 확인해 주세요.",
        details: [],
      },
    }),
    {
      message: "점수 범위를 확인해 주세요.",
      requiresRefresh: false,
    },
  );
});

test("targetScore API errors are attached to the shared amount input", () => {
  assert.deepEqual(
    readMappedApiFields([
      { field: "targetScore", message: "최종 점수를 확인해 주세요." },
    ]),
    [{ field: "amount", message: "최종 점수를 확인해 주세요." }],
  );
});

test("media upload runs intent, direct PUT, and completion in order", async () => {
  const { events, result, uploadId } = await runDirectUploadLifecycle();

  assert.deepEqual(events.map((event) => event.stage), [
    "intent",
    "put",
    "complete",
  ]);
  assert.deepEqual(JSON.parse(events[0].body), {
    purpose: "scoreChange",
    kind: "image",
    fileName: "마음 <script>.jpg",
    contentType: "image/jpeg",
    byteSize: 512,
  });
  assert.equal(events[0].method, "POST");
  assert.equal(events[0].headers["X-CSRFToken"], "rendered-csrf-token");

  assert.equal(events[1].url, "https://r2.example.test/pending/object");
  assert.equal(events[1].method, "PUT");
  assert.deepEqual(events[1].headers, { "Content-Type": "image/jpeg" });
  assert.equal(events[1].bodyIsFile, true);
  assert.equal(events[1].credentials, "omit");
  assert.equal(events[1].cache, "no-store");

  assert.equal(
    events[2].url,
    `/api/v1/media-uploads/${uploadId}/complete/`,
  );
  assert.equal(events[2].method, "POST");
  assert.equal(events[2].body, "{}");
  assert.equal(events[2].headers["X-CSRFToken"], "rendered-csrf-token");
  assert.deepEqual(result, {
    progress: 100,
    progressLabel: "업로드 완료",
    result: uploadId,
  });
});

test("removing a score image aborts its direct PUT and discards the retained intent", async () => {
  const harness = createLiveScoreMediaDiscardHarness();
  harness.input.files = [harness.file];
  harness.input.listeners.change();
  await harness.putStarted;

  assert.equal(harness.readPutSignal().aborted, false);
  const removeButton = harness.selection.children[0].children[2];
  removeButton.listeners.click();
  await settleAsyncWork();

  assert.equal(harness.readPutSignal().aborted, true);
  assert.equal(harness.manager.hasSelection(), false);
  assert.equal(harness.status.textContent, "");
  const discardCalls = harness.fetchCalls.filter(
    (call) =>
      call.url ===
      `/api/v1/media-uploads/${harness.uploadId}/discard/`,
  );
  assert.equal(discardCalls.length, 1);
  assert.equal(discardCalls[0].options.method, "POST");
  assert.equal(discardCalls[0].options.body, "{}");
  assert.equal(
    discardCalls[0].options.headers["X-CSRFToken"],
    "rendered-csrf-token",
  );
  assert.equal(
    harness.fetchCalls.some((call) => call.url.endsWith("/complete/")),
    false,
  );
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        await harness.manager.upload({
          csrfToken: "rendered-csrf-token",
          purpose: "scoreChange",
        }),
      ),
    ),
    [],
  );
});

test("an upload abort signal cancels the browser XMLHttpRequest PUT", async () => {
  const file = { name: "xhr-cancelled.jpg", size: 512, type: "image/jpeg" };
  let request = null;
  class FakeXmlHttpRequest {
    constructor() {
      request = this;
      this.listeners = {};
      this.requestHeaders = {};
      this.upload = { addEventListener() {} };
    }

    abort() {
      this.aborted = true;
      this.listeners.abort();
    }

    addEventListener(type, listener) {
      this.listeners[type] = listener;
    }

    open(method, url) {
      this.method = method;
      this.url = url;
    }

    send(body) {
      this.body = body;
    }

    setRequestHeader(name, value) {
      this.requestHeaders[name] = value;
    }
  }
  const sandbox = {
    AbortController,
    console,
    document: {
      querySelector() {
        return null;
      },
    },
    file,
    URL,
    window: { location: { origin: "https://friendly.test" } },
    XMLHttpRequest: FakeXmlHttpRequest,
  };

  vm.runInNewContext(
    `${dashboardSource}
      {
        const controller = new AbortController();
        globalThis.uploadAbortController = controller;
        globalThis.xhrUploadPromise = putFileWithProgress(
          {
            uploadUrl: "https://r2.example.test/pending/xhr-cancelled",
            requiredHeaders: { "Content-Type": "image/jpeg" },
          },
          globalThis.file,
          () => undefined,
          { signal: controller.signal },
        );
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  assert.equal(request.method, "PUT");
  assert.equal(request.body, file);
  sandbox.uploadAbortController.abort();
  await assert.rejects(sandbox.xhrUploadPromise, (error) => {
    assert.equal(error.name, "MediaUploadCancelledError");
    return true;
  });
  assert.equal(request.aborted, true);
});

test("score image selection starts upload and submit reuses the in-flight work", async () => {
  const pendingUpload = deferred();
  const harness = createScoreMediaManagerHarness([pendingUpload.promise]);
  const file = { name: "photo.jpg", size: 512, type: "image/jpeg" };

  harness.select(file);

  assert.deepEqual(JSON.parse(JSON.stringify(harness.uploadCalls)), [
    {
      csrfToken: "rendered-csrf-token",
      fileName: "photo.jpg",
      purpose: "scoreChange",
      uploadsUrl: "/api/v1/media-uploads/",
    },
  ]);
  assert.equal(harness.status.textContent, "사진을 올리고 있어요…");

  const submitUpload = harness.manager.upload({
    csrfToken: "rendered-csrf-token",
    purpose: "scoreChange",
  });
  assert.equal(harness.uploadCalls.length, 1);

  pendingUpload.resolve("00000000-0000-4000-8000-000000000001");
  assert.deepEqual(
    JSON.parse(JSON.stringify(await submitUpload)),
    ["00000000-0000-4000-8000-000000000001"],
  );
  assert.equal(harness.status.textContent, "사진 업로드를 마쳤어요.");

  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        await harness.manager.upload({
          csrfToken: "rendered-csrf-token",
          purpose: "scoreChange",
        }),
      ),
    ),
    ["00000000-0000-4000-8000-000000000001"],
  );
  assert.equal(harness.uploadCalls.length, 1);
});

test("dashboard submits the current background upload without duplicating its lifecycle", async () => {
  const directPut = deferred();
  const uploadId = "00000000-0000-4000-8000-000000000006";
  const file = { name: "attached.jpg", size: 512, type: "image/jpeg" };
  const scoreList = new FakeElement();
  scoreList.selectors["[data-score-list-status]"] = new FakeElement();
  const form = new FakeElement();
  const operation = new FakeElement({ name: "operation", value: "increase" });
  const amount = new FakeElement({ value: "3" });
  const reason = new FakeElement({ value: "사진과 함께" });
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  const mediaInput = new FakeElement();
  const mediaSelection = new FakeElement();
  const mediaStatus = new FakeElement();
  form.selectors = {
    "[data-character-current]": new FakeElement(),
    "[data-score-form-status]": new FakeElement(),
    "[data-score-media-input]": mediaInput,
    "[data-score-media-selection]": mediaSelection,
    "[data-score-media-status]": mediaStatus,
    "[data-score-submit-label]": new FakeElement(),
    "[name=amount]": amount,
    "[name=csrfmiddlewaretoken]": csrf,
    "[name=operation]:checked": operation,
    "[name=reason]": reason,
  };
  form.selectorLists = {
    'input:not([type="hidden"]), textarea': [
      operation,
      amount,
      reason,
      mediaInput,
    ],
    "[aria-invalid=true]": [],
    "[data-error-for]": [],
  };
  const submitButton = new FakeElement({ disabled: true });
  const root = new FakeElement({
    dataset: {
      mediaUploadsUrl: "/api/v1/media-uploads/",
      scoreChangesUrl: "/api/v1/score-changes/",
      scoresUrl: "/api/v1/relationship-scores/",
    },
  });
  root.selectors = {
    "[data-current-participant-space]": new FakeElement(),
    "[data-score-change-form]": form,
    "[data-score-list]": scoreList,
    "[data-score-submit]": submitButton,
    "[data-score-target]": new FakeElement(),
  };

  const fetchCalls = [];
  let currentScore = 0;
  const sandbox = {
    console,
    document: {
      addEventListener() {},
      cookie: "",
      createElement() {
        return new FakeElement();
      },
      createTextNode(text) {
        const node = new FakeElement();
        node.textContent = text;
        return node;
      },
      querySelector(selector) {
        return selector === "[data-dashboard-root]" ? root : null;
      },
      visibilityState: "hidden",
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      if (url === "/api/v1/relationship-scores/") {
        return Promise.resolve(jsonResponse(200, scorePayload(currentScore)));
      }
      if (url === "/api/v1/media-uploads/") {
        return Promise.resolve(
          jsonResponse(201, {
            resultType: "SUCCESS",
            error: null,
            success: {
              uploadId,
              uploadUrl: "https://r2.example.test/pending/attached",
              requiredHeaders: { "Content-Type": "image/jpeg" },
              expiresAt: "2026-07-19T12:00:00Z",
            },
          }),
        );
      }
      if (url === "https://r2.example.test/pending/attached") {
        return directPut.promise;
      }
      if (url === `/api/v1/media-uploads/${uploadId}/complete/`) {
        return Promise.resolve(
          jsonResponse(200, {
            resultType: "SUCCESS",
            error: null,
            success: {
              id: uploadId,
              kind: "image",
              fileName: file.name,
              contentType: file.type,
              byteSize: file.size,
            },
          }),
        );
      }
      if (url === "/api/v1/score-changes/") {
        currentScore = 3;
        return Promise.resolve(
          jsonResponse(201, {
            resultType: "SUCCESS",
            error: null,
            success: { delta: 3, resultingScore: 3 },
          }),
        );
      }
      throw new Error(`Unexpected request: ${url} ${options.method || "GET"}`);
    },
    URL,
    window: {
      location: {
        assign() {},
        origin: "https://friendly.test",
        pathname: "/",
        search: "",
      },
    },
    woorisaiShowToast() {},
  };

  vm.runInNewContext(dashboardSource, sandbox, { filename: dashboardScriptPath });
  await settleAsyncWork();

  mediaInput.files = [file];
  mediaInput.listeners.change();
  await settleAsyncWork();
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/media-uploads/").length,
    1,
  );
  assert.equal(
    fetchCalls.filter(
      (call) => call.url === "https://r2.example.test/pending/attached",
    ).length,
    1,
  );

  form.listeners.submit({ preventDefault() {} });
  await settleAsyncWork();
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/score-changes/").length,
    0,
  );
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/media-uploads/").length,
    1,
  );

  directPut.resolve({ ok: true, redirected: false });
  await settleAsyncWork();

  const scorePosts = fetchCalls.filter(
    (call) => call.url === "/api/v1/score-changes/",
  );
  assert.equal(scorePosts.length, 1);
  assert.deepEqual(JSON.parse(scorePosts[0].options.body), {
    delta: 3,
    reason: "사진과 함께",
    mediaUploadIds: [uploadId],
  });
  assert.equal(
    fetchCalls.filter(
      (call) =>
        call.url === `/api/v1/media-uploads/${uploadId}/complete/`,
    ).length,
    1,
  );
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/media-uploads/").length,
    1,
  );
  assert.equal(
    fetchCalls.filter(
      (call) => call.url === "https://r2.example.test/pending/attached",
    ).length,
    1,
  );
});

test("a failed background score image upload keeps the selection and retries on submit", async () => {
  const failedUpload = deferred();
  const retryUpload = deferred();
  const harness = createScoreMediaManagerHarness([
    failedUpload.promise,
    retryUpload.promise,
  ]);
  harness.select({ name: "retry.jpg", size: 512, type: "image/jpeg" });

  failedUpload.reject(new Error("network unavailable"));
  await settleAsyncWork();

  assert.equal(harness.manager.hasSelection(), true);
  assert.equal(harness.selection.hidden, false);
  assert.equal(
    harness.status.textContent,
    "사진을 업로드하지 못했어요. 잠시 후 다시 시도해 주세요.",
  );

  const retriedSubmission = harness.manager.upload({
    csrfToken: "rendered-csrf-token",
    purpose: "scoreChange",
  });
  assert.equal(harness.uploadCalls.length, 2);
  retryUpload.resolve("00000000-0000-4000-8000-000000000002");

  assert.deepEqual(
    JSON.parse(JSON.stringify(await retriedSubmission)),
    ["00000000-0000-4000-8000-000000000002"],
  );
  assert.equal(harness.status.textContent, "사진 업로드를 마쳤어요.");
});

test("an expired session during background score image upload redirects immediately", async () => {
  const failedUpload = deferred();
  const harness = createScoreMediaManagerHarness([failedUpload.promise]);
  harness.select({ name: "private.jpg", size: 512, type: "image/jpeg" });

  failedUpload.reject({
    asApiRequestError: true,
    status: 401,
    apiError: {
      errorCode: "AUTHENTICATION_REQUIRED",
      reason: "로그인이 필요합니다.",
      details: [],
    },
  });
  await settleAsyncWork();

  assert.deepEqual(harness.assignedLocations, [
    "/login/?next=%2F%3Ffrom%3Dupload",
  ]);
  assert.equal(harness.status.textContent, "사진을 올리고 있어요…");
  assert.equal(harness.status.attributes["class:media-status--error"], false);
});

test("a replaced score image discards the stale upload completion", async () => {
  const firstUpload = deferred();
  const secondUpload = deferred();
  const harness = createScoreMediaManagerHarness([
    firstUpload.promise,
    secondUpload.promise,
  ]);

  harness.select({ name: "old.jpg", size: 512, type: "image/jpeg" });
  harness.select({ name: "current.jpg", size: 1024, type: "image/jpeg" });
  const submitUpload = harness.manager.upload({
    csrfToken: "rendered-csrf-token",
    purpose: "scoreChange",
  });

  assert.equal(harness.uploadCalls.length, 2);
  assert.deepEqual(
    harness.uploadCalls.map((call) => call.fileName),
    ["old.jpg", "current.jpg"],
  );
  firstUpload.resolve("00000000-0000-4000-8000-000000000003");
  await settleAsyncWork();
  assert.equal(harness.status.textContent, "사진을 올리고 있어요…");
  assert.deepEqual(JSON.parse(JSON.stringify(harness.discardCalls)), [
    {
      body: "{}",
      csrfToken: "rendered-csrf-token",
      method: "POST",
      url: "/api/v1/media-uploads/00000000-0000-4000-8000-000000000003/discard/",
    },
  ]);

  secondUpload.resolve("00000000-0000-4000-8000-000000000004");
  assert.deepEqual(
    JSON.parse(JSON.stringify(await submitUpload)),
    ["00000000-0000-4000-8000-000000000004"],
  );
  assert.equal(harness.status.textContent, "사진 업로드를 마쳤어요.");
  assert.deepEqual(harness.revokedPreviewUrls, ["blob:old.jpg"]);
});

test("a removed score image discards its later completion and never attaches it", async () => {
  const pendingUpload = deferred();
  const harness = createScoreMediaManagerHarness([pendingUpload.promise]);
  harness.select({ name: "removed.jpg", size: 512, type: "image/jpeg" });

  const removeButton = harness.selection.children[0].children[2];
  removeButton.listeners.click();
  assert.equal(harness.manager.hasSelection(), false);
  assert.equal(harness.status.textContent, "");
  assert.equal(harness.selection.hidden, true);

  pendingUpload.resolve("00000000-0000-4000-8000-000000000005");
  await settleAsyncWork();

  assert.equal(harness.manager.hasSelection(), false);
  assert.equal(harness.status.textContent, "");
  assert.deepEqual(JSON.parse(JSON.stringify(harness.discardCalls)), [
    {
      body: "{}",
      csrfToken: "rendered-csrf-token",
      method: "POST",
      url: "/api/v1/media-uploads/00000000-0000-4000-8000-000000000005/discard/",
    },
  ]);
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        await harness.manager.upload({
          csrfToken: "rendered-csrf-token",
          purpose: "scoreChange",
        }),
      ),
    ),
    [],
  );
  assert.deepEqual(harness.revokedPreviewUrls, ["blob:removed.jpg"]);
});

test("media completion rejects a malformed or mismatched success payload", () => {
  const uploadId = "00000000-0000-4000-8000-000000000001";
  const completed = {
    id: uploadId,
    kind: "image",
    fileName: "photo.jpg",
    contentType: "image/jpeg",
    byteSize: 512,
  };

  assert.deepEqual(
    readCompletedUploadValidation({ success: completed }, uploadId),
    { ok: true, completed },
  );

  assert.deepEqual(readCompletedUploadValidation({ success: {} }, uploadId), {
    ok: false,
    message: "업로드 완료 응답 형식이 올바르지 않습니다.",
  });
  assert.deepEqual(
    readCompletedUploadValidation(
      {
        success: {
          id: "00000000-0000-4000-8000-000000000002",
          kind: "image",
          fileName: "photo.jpg",
          contentType: "image/jpeg",
          byteSize: 512,
          contentUrl: `/media/${uploadId}/content/`,
        },
      },
      uploadId,
    ),
    {
      ok: false,
      message: "업로드 완료 응답 형식이 올바르지 않습니다.",
    },
  );
});

test("dashboard resets upload IDs that can no longer be attached", () => {
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

test("dashboard submits delta and target changes once and reconciles the score", async () => {
  const scoreList = new FakeElement();
  const scoreListStatus = new FakeElement();
  scoreList.selectors["[data-score-list-status]"] = scoreListStatus;
  const form = new FakeElement();
  const operation = new FakeElement({ name: "operation", value: "increase" });
  const amount = new FakeElement({ value: "3" });
  const reason = new FakeElement({ value: "고마워" });
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  const formStatus = new FakeElement();
  const submitButton = new FakeElement({ disabled: true });
  const submitLabel = new FakeElement();
  const characterCount = new FakeElement();
  const currentParticipant = new FakeElement();
  const scoreTarget = new FakeElement();
  const amountLabel = new FakeElement();
  const amountHint = new FakeElement();
  const scorePreview = new FakeElement();
  const operationErrors = new FakeElement();
  operationErrors.id = "error-operation";
  const amountErrors = new FakeElement();
  amountErrors.id = "error-amount";
  const reasonErrors = new FakeElement();
  reasonErrors.id = "error-reason";
  const errorLists = [operationErrors, amountErrors, reasonErrors];

  form.selectors = {
    "[data-character-current]": characterCount,
    "[data-score-amount-hint]": amountHint,
    "[data-score-amount-label]": amountLabel,
    "[data-score-form-status]": formStatus,
    "[data-score-preview]": scorePreview,
    "[data-score-submit-label]": submitLabel,
    '[data-error-for="amount"]': amountErrors,
    "[name=amount]": amount,
    "[name=csrfmiddlewaretoken]": csrf,
    "[name=operation]:checked": operation,
    "[name=reason]": reason,
    "[aria-invalid=true]": amount,
  };
  form.selectorLists = {
    'input:not([type="hidden"]), textarea': [operation, amount, reason],
    '[name="amount"]': [amount],
    "[aria-invalid=true]": [],
    "[data-error-for]": errorLists,
  };

  const root = new FakeElement({
    dataset: {
      scoreChangesUrl: "/api/v1/score-changes/",
      scoresUrl: "/api/v1/relationship-scores/",
    },
  });
  root.selectors = {
    "[data-current-participant-space]": currentParticipant,
    "[data-score-change-form]": form,
    "[data-score-list]": scoreList,
    "[data-score-submit]": submitButton,
    "[data-score-target]": scoreTarget,
  };

  const fetchCalls = [];
  const toastCalls = [];
  const documentListeners = {};
  const globalListeners = {};
  let currentScore = 0;
  let postRequestCount = 0;
  let resolvePost;
  const sandbox = {
    addEventListener(type, listener) {
      globalListeners[type] = listener;
    },
    console,
    woorisaiShowToast(message, options) {
      toastCalls.push({ message, tone: options.tone });
    },
    document: {
      addEventListener(type, listener) {
        documentListeners[type] = listener;
      },
      createElement() {
        return new FakeElement();
      },
      createTextNode(text) {
        const node = new FakeElement();
        node.textContent = text;
        return node;
      },
      querySelector(selector) {
        return selector === "[data-dashboard-root]" ? root : null;
      },
      visibilityState: "hidden",
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      if (options.method === "POST") {
        postRequestCount += 1;
        if (postRequestCount === 1) {
          currentScore = 3;
          return Promise.resolve(
            jsonResponse(201, {
              resultType: "SUCCESS",
              error: null,
              success: { delta: 3, resultingScore: 3 },
            }),
          );
        }
        if (postRequestCount === 3) {
          return Promise.resolve(
            jsonResponse(409, {
              resultType: "ERROR",
              error: {
                errorType: "CONFLICT",
                errorCode: "SCORE_UNCHANGED",
                reason: "이미 100점이에요.",
                details: [],
              },
              success: null,
            }),
          );
        }
        return new Promise((resolve) => {
          resolvePost = () => {
            currentScore = 100;
            resolve(
              jsonResponse(201, {
                resultType: "SUCCESS",
                error: null,
                success: { delta: 97, resultingScore: 100 },
              }),
            );
          };
        });
      }
      return Promise.resolve(jsonResponse(200, scorePayload(currentScore)));
    },
  };

  vm.runInNewContext(dashboardSource, sandbox, { filename: dashboardScriptPath });
  await settleAsyncWork();

  assert.equal(submitButton.disabled, false);
  assert.equal(currentParticipant.textContent, "첫째님의 마음 공간");
  assert.equal(scoreTarget.textContent, "둘째");
  assert.equal(scoreList.children.length, 2);
  assert.deepEqual(toastCalls, []);
  assert.equal(amountLabel.textContent, "몇 점을 바꿀까요?");
  assert.equal(amountHint.textContent, "현재 점수에서 입력한 만큼 바뀌어요.");
  assert.equal(amount.min, "1");
  assert.equal(amount.placeholder, "1~100 입력");

  let preventedSubmissions = 0;
  const event = {
    preventDefault() {
      preventedSubmissions += 1;
    },
  };
  form.listeners.submit(event);
  await settleAsyncWork();

  const deltaPostCall = fetchCalls.find((call) => call.options.method === "POST");
  assert.deepEqual(JSON.parse(deltaPostCall.options.body), {
    delta: 3,
    reason: "고마워",
  });
  assert.equal(currentScore, 3);
  assert.equal(descendantText(scoreList.children[0]).includes("3"), true);
  assert.deepEqual(toastCalls, [
    { message: "친밀도를 +3점 변경했어요.", tone: "success" },
  ]);

  amount.value = "4";
  operation.value = "decrease";
  form.listeners.change({ target: operation });
  assert.equal(amount.value, "4");

  operation.value = "target";
  form.listeners.change({ target: operation });
  assert.equal(amount.value, "");
  assert.equal(amountLabel.textContent, "최종 점수");
  assert.equal(amountHint.textContent, "입력한 점수가 그대로 새 점수가 돼요.");
  assert.equal(amount.min, "0");
  assert.equal(amount.placeholder, "0~100 입력");

  amount.value = "90";
  operation.value = "increase";
  form.listeners.change({ target: operation });
  assert.equal(amount.value, "");
  assert.equal(amount.min, "1");

  operation.value = "target";
  form.listeners.change({ target: operation });
  assert.equal(amount.value, "");

  amount.value = "100";
  reason.value = "다시 힘내자";
  amount.listeners.input();
  assert.equal(scorePreview.hidden, false);
  assert.equal(scorePreview.textContent, "현재 3점 → 100점 (+97점)");

  form.listeners.submit(event);
  form.listeners.submit(event);

  assert.equal(
    fetchCalls.filter((call) => call.options.method === "POST").length,
    2,
  );
  assert.equal(amount.disabled, true);
  assert.equal(reason.disabled, true);
  assert.equal(submitButton.disabled, true);

  resolvePost();
  await settleAsyncWork();

  const postCall = fetchCalls.filter((call) => call.options.method === "POST")[1];
  assert.deepEqual(JSON.parse(postCall.options.body), {
    targetScore: 100,
    reason: "다시 힘내자",
  });
  assert.equal(postCall.options.credentials, "same-origin");
  assert.equal(postCall.options.headers["X-CSRFToken"], "rendered-csrf-token");
  assert.equal(preventedSubmissions, 3);
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    3,
  );
  assert.equal(descendantText(scoreList.children[0]).includes("100"), true);
  assert.equal(formStatus.textContent, "");
  assert.deepEqual(toastCalls, [
    { message: "친밀도를 +3점 변경했어요.", tone: "success" },
    { message: "친밀도를 100점으로 기록했어요.", tone: "success" },
  ]);
  assert.equal(amount.value, "");
  assert.equal(reason.value, "");
  assert.equal(amount.disabled, false);
  assert.equal(reason.disabled, false);
  assert.equal(submitButton.disabled, false);

  amount.value = "100";
  form.listeners.submit(event);
  await settleAsyncWork();

  assert.equal(
    fetchCalls.filter((call) => call.options.method === "POST").length,
    3,
  );
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    4,
  );
  assert.equal(amount.value, "");
  assert.equal(formStatus.textContent, "");
  assert.deepEqual(toastCalls, [
    { message: "친밀도를 +3점 변경했어요.", tone: "success" },
    { message: "친밀도를 100점으로 기록했어요.", tone: "success" },
    { message: "이미 100점이에요.", tone: "warning" },
  ]);
  assert.equal(submitButton.disabled, false);

  amount.value = "99.5";
  form.listeners.submit(event);
  await settleAsyncWork();

  assert.equal(
    fetchCalls.filter((call) => call.options.method === "POST").length,
    3,
  );
  assert.equal(amount.attributes["aria-invalid"], "true");
  assert.equal(amount.focused, true);
  assert.equal(amount.attributes["aria-describedby"], "error-amount");
  assert.equal(amountErrors.hidden, false);
  assert.equal(amountErrors.attributes["class:errorlist--assistive"], true);
  assert.equal(
    descendantText(amountErrors),
    "점수는 소수점 없이 정수로 입력해 주세요.",
  );
  assert.equal(formStatus.textContent, "");
  assert.deepEqual(toastCalls.at(-1), {
    message: "점수는 소수점 없이 정수로 입력해 주세요.",
    tone: "warning",
  });

  sandbox.document.visibilityState = "visible";
  documentListeners.visibilitychange();
  await settleAsyncWork();
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    5,
  );

  globalListeners.pageshow({ persisted: true });
  await settleAsyncWork();
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    6,
  );

  documentListeners["woorisai:push-message"]({
    detail: { threadLink: "/history/31/" },
  });
  await settleAsyncWork();
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    7,
  );
});
