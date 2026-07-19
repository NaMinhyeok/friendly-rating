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
