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
  constructor({ dataset = {}, disabled = false, value = "" } = {}) {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = dataset;
    this.disabled = disabled;
    this.focused = false;
    this.hidden = false;
    this.listeners = {};
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

function readCommand({ operation, amount, reason }) {
  const fixture = JSON.stringify({ operation, amount, reason });
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
        globalThis.commandResult = readScoreChangeCommand(form);
      }
    `,
    sandbox,
    { filename: dashboardScriptPath },
  );

  return JSON.parse(JSON.stringify(sandbox.commandResult));
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

test("dashboard initializes, submits once, and reconciles the rendered score", async () => {
  const scoreList = new FakeElement();
  const scoreListStatus = new FakeElement();
  scoreList.selectors["[data-score-list-status]"] = scoreListStatus;
  const form = new FakeElement();
  const operation = new FakeElement({ value: "increase" });
  const amount = new FakeElement({ value: "3" });
  const reason = new FakeElement({ value: "고마워" });
  const csrf = new FakeElement({ value: "rendered-csrf-token" });
  const formStatus = new FakeElement();
  const submitButton = new FakeElement({ disabled: true });
  const submitLabel = new FakeElement();
  const characterCount = new FakeElement();
  const currentParticipant = new FakeElement();
  const scoreTarget = new FakeElement();
  const errorLists = [new FakeElement(), new FakeElement(), new FakeElement()];

  form.selectors = {
    "[data-character-current]": characterCount,
    "[data-score-form-status]": formStatus,
    "[data-score-submit-label]": submitLabel,
    "[name=amount]": amount,
    "[name=csrfmiddlewaretoken]": csrf,
    "[name=operation]:checked": operation,
    "[name=reason]": reason,
  };
  form.selectorLists = {
    'input:not([type="hidden"]), textarea': [operation, amount, reason],
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
  let currentScore = 0;
  let resolvePost;
  const sandbox = {
    console,
    document: {
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
    },
    fetch(url, options = {}) {
      fetchCalls.push({ options, url });
      if (options.method === "POST") {
        return new Promise((resolve) => {
          resolvePost = () => {
            currentScore = 3;
            resolve(
              jsonResponse(201, {
                resultType: "SUCCESS",
                error: null,
                success: { delta: 3 },
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

  let preventedSubmissions = 0;
  const event = {
    preventDefault() {
      preventedSubmissions += 1;
    },
  };
  form.listeners.submit(event);
  form.listeners.submit(event);

  assert.equal(
    fetchCalls.filter((call) => call.options.method === "POST").length,
    1,
  );
  assert.equal(amount.disabled, true);
  assert.equal(reason.disabled, true);
  assert.equal(submitButton.disabled, true);

  resolvePost();
  await settleAsyncWork();

  const postCall = fetchCalls.find((call) => call.options.method === "POST");
  assert.deepEqual(JSON.parse(postCall.options.body), {
    delta: 3,
    reason: "고마워",
  });
  assert.equal(postCall.options.credentials, "same-origin");
  assert.equal(postCall.options.headers["X-CSRFToken"], "rendered-csrf-token");
  assert.equal(preventedSubmissions, 2);
  assert.equal(
    fetchCalls.filter((call) => call.url === "/api/v1/relationship-scores/").length,
    2,
  );
  assert.equal(descendantText(scoreList.children[0]).includes("3"), true);
  assert.equal(formStatus.textContent, "친밀도를 +3점 변경했어요.");
  assert.equal(amount.value, "");
  assert.equal(reason.value, "");
  assert.equal(amount.disabled, false);
  assert.equal(reason.disabled, false);
  assert.equal(submitButton.disabled, false);
});
