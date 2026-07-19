const dashboard = document.querySelector("[data-dashboard-root]");

if (dashboard) {
  initializeDashboard(dashboard);
}

function initializeDashboard(root) {
  const scoreList = root.querySelector("[data-score-list]");
  const form = root.querySelector("[data-score-change-form]");
  const submitButton = root.querySelector("[data-score-submit]");
  let isSubmitting = false;
  let scoreLoadSequence = 0;
  let ownCurrentScore = null;
  let selectedOperation = readScoreOperation(form);

  if (form) {
    updateScoreInputUi(form, selectedOperation, ownCurrentScore);
    form.addEventListener("change", (event) => {
      if (event.target?.name !== "operation") {
        return;
      }

      const nextOperation = event.target.value;
      if (!["increase", "decrease", "target"].includes(nextOperation)) {
        return;
      }
      if ((selectedOperation === "target") !== (nextOperation === "target")) {
        form.querySelector("[name=amount]").value = "";
      }
      selectedOperation = nextOperation;
      clearFormFeedback(form);
      updateScoreInputUi(form, selectedOperation, ownCurrentScore);
    });
    form.querySelector("[name=amount]")?.addEventListener("input", () => {
      updateScorePreview(form, ownCurrentScore, readScoreOperation(form));
    });
  }

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    submitScoreChange().catch(() => undefined);
  });

  const loadScores = async () => {
    if (!scoreList) {
      return;
    }

    const loadSequence = ++scoreLoadSequence;
    scoreList.setAttribute("aria-busy", "true");
    const status = scoreList.querySelector("[data-score-list-status]");
    if (status) {
      status.textContent = "현재 마음을 불러오고 있어요…";
    }

    try {
      const payload = await requestJson(root.dataset.scoresUrl, {
        cache: "no-store",
      });
      const scores = readRelationshipScores(payload);
      if (loadSequence !== scoreLoadSequence) {
        return;
      }
      const ownScore = renderScores(root, scoreList, scores);
      ownCurrentScore = ownScore.currentScore;
      updateScoreInputUi(form, selectedOperation, ownCurrentScore);
      if (submitButton) {
        submitButton.disabled = false;
      }
    } catch (error) {
      if (redirectWhenAuthenticationExpired(error)) {
        return;
      }
      if (loadSequence === scoreLoadSequence) {
        renderScoreLoadError(scoreList, loadScores);
      }
    } finally {
      if (loadSequence === scoreLoadSequence) {
        scoreList.setAttribute("aria-busy", "false");
      }
    }
  };

  const submitScoreChange = async () => {
    if (!form || !submitButton) {
      return;
    }

    clearFormFeedback(form);
    const command = readScoreChangeCommand(form);
    if (!command) {
      return;
    }
    const isTargetCommand = Object.hasOwn(command, "targetScore");

    isSubmitting = true;
    let shouldUnlockSubmission = true;
    form.setAttribute("aria-busy", "true");
    submitButton.disabled = true;
    setFormFieldsDisabled(form, true);
    setSubmitLabel(form, "기록하고 있어요…");

    try {
      const payload = await requestJson(root.dataset.scoreChangesUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(form),
        },
        body: JSON.stringify(command),
      });
      const change = readCreatedScoreChange(payload, command);

      ownCurrentScore = change.resultingScore;
      if (isTargetCommand) {
        showScoreToast(
          form,
          `친밀도를 ${change.resultingScore}점으로 기록했어요.`,
          "success",
        );
      } else {
        const sign = change.delta > 0 ? "+" : "";
        showScoreToast(
          form,
          `친밀도를 ${sign}${change.delta}점 변경했어요.`,
          "success",
        );
      }
      form.querySelector("[name=amount]").value = "";
      form.querySelector("[name=reason]").value = "";
      updateCharacterCount(form);
      updateScorePreview(form, ownCurrentScore, selectedOperation);
      await loadScores();
    } catch (error) {
      if (redirectWhenAuthenticationExpired(error)) {
        return;
      }
      if (isScoreUnchangedError(error)) {
        showScoreToast(form, error.message, "warning");
        form.querySelector("[name=amount]").value = "";
        await loadScores();
      } else {
        shouldUnlockSubmission = !showApiFormError(form, error);
      }
    } finally {
      isSubmitting = false;
      form.setAttribute("aria-busy", "false");
      setFormFieldsDisabled(form, false);
      submitButton.disabled = !shouldUnlockSubmission;
      setSubmitLabel(
        form,
        shouldUnlockSubmission ? "이 마음 기록하기" : "새로고침 후 확인",
      );
    }
  };

  document.addEventListener?.("visibilitychange", () => {
    if (document.visibilityState === "visible" && !isSubmitting) {
      loadScores().catch(() => undefined);
    }
  });
  document.addEventListener?.("woorisai:push-message", () => {
    loadScores().catch(() => undefined);
  });
  globalThis.addEventListener?.("pageshow", (event) => {
    if (event.persisted && !isSubmitting) {
      loadScores().catch(() => undefined);
    }
  });

  loadScores().catch(() => undefined);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("Content-Type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    payload = await response.json();
  }

  if (
    !response.ok ||
    response.redirected ||
    payload?.resultType !== "SUCCESS" ||
    payload?.error !== null
  ) {
    throw new ApiRequestError(response.status, payload?.error);
  }
  return payload;
}

function readCreatedScoreChange(payload, command) {
  const change = payload?.success;
  const isTargetCommand = Object.hasOwn(command, "targetScore");
  if (
    payload?.resultType !== "SUCCESS" ||
    payload?.error !== null ||
    !change ||
    !Number.isInteger(change.delta) ||
    change.delta === 0 ||
    change.delta < -100 ||
    change.delta > 100 ||
    !Number.isInteger(change.resultingScore) ||
    change.resultingScore < 0 ||
    change.resultingScore > 100 ||
    (isTargetCommand && change.resultingScore !== command.targetScore) ||
    (!isTargetCommand && change.delta !== command.delta)
  ) {
    throw new Error("점수 변경 응답 형식이 올바르지 않습니다.");
  }
  return change;
}

class ApiRequestError extends Error {
  constructor(status, apiError) {
    super(apiError?.reason || "요청을 처리하지 못했어요.");
    this.name = "ApiRequestError";
    this.status = status;
    this.apiError = apiError;
  }
}

function readRelationshipScores(payload) {
  const results = payload?.success?.results;
  if (!Array.isArray(results) || results.length !== 2) {
    throw new Error("관계 점수 응답 형식이 올바르지 않습니다.");
  }

  results.forEach((score) => {
    if (
      !score ||
      !Number.isInteger(score.currentScore) ||
      score.currentScore < 0 ||
      score.currentScore > 100 ||
      typeof score.isMine !== "boolean" ||
      typeof score.sourceParticipant?.displayName !== "string" ||
      typeof score.targetParticipant?.displayName !== "string"
    ) {
      throw new Error("관계 점수 항목 형식이 올바르지 않습니다.");
    }
  });

  if (results.filter((score) => score.isMine).length !== 1) {
    throw new Error("변경 가능한 관계 점수를 식별하지 못했습니다.");
  }
  return results;
}

function renderScores(root, scoreList, scores) {
  scoreList.replaceChildren(...scores.map((score) => createScoreCard(score)));
  const ownScore = scores.find((score) => score.isMine);
  root.querySelector("[data-current-participant-space]").textContent =
    `${ownScore.sourceParticipant.displayName}님의 마음 공간`;
  root.querySelector("[data-score-target]").textContent =
    ownScore.targetParticipant.displayName;
  return ownScore;
}

function createScoreCard(score) {
  const sourceName = score.sourceParticipant.displayName;
  const targetName = score.targetParticipant.displayName;
  const card = document.createElement("article");
  card.className = `surface score-card${score.isMine ? " score-card--mine" : ""}`;

  const topline = document.createElement("div");
  topline.className = "score-card__topline";
  const owner = document.createElement("span");
  owner.className = "score-owner";
  owner.textContent = score.isMine ? "내가 느끼는 마음" : `${sourceName}님의 마음`;
  topline.append(owner);
  if (score.isMine) {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = "내 점수";
    topline.append(pill);
  }

  const direction = document.createElement("p");
  direction.className = "score-direction";
  direction.setAttribute("aria-label", `${sourceName} → ${targetName}`);
  direction.append(document.createTextNode(`${sourceName} `));
  const arrow = document.createElement("span");
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";
  direction.append(arrow, document.createTextNode(` ${targetName}`));

  const value = document.createElement("p");
  value.className = "score-value";
  value.setAttribute("aria-label", `${score.currentScore}점`);
  const strong = document.createElement("strong");
  strong.textContent = String(score.currentScore);
  const scale = document.createElement("span");
  scale.textContent = "/ 100";
  value.append(strong, scale);

  const track = document.createElement("div");
  track.className = "score-track";
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", `100점 중 ${score.currentScore}점`);
  const fill = document.createElement("span");
  fill.style.width = `${score.currentScore}%`;
  track.append(fill);

  card.append(topline, direction, value, track);
  return card;
}

function renderScoreLoadError(scoreList, retry) {
  const state = document.createElement("div");
  state.className = "surface loading-state loading-state--error";
  state.setAttribute("role", "status");
  const message = document.createElement("p");
  message.textContent = "현재 마음을 불러오지 못했어요.";
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "button";
  button.textContent = "다시 시도";
  button.addEventListener("click", () => retry());
  state.append(message, button);
  scoreList.replaceChildren(state);
}

function readScoreOperation(form) {
  return form?.querySelector("[name=operation]:checked")?.value || null;
}

function readIntegerInputValue(input) {
  const rawValue = typeof input?.value === "string" ? input.value.trim() : "";
  if (rawValue === "") {
    return null;
  }
  const value = Number(rawValue);
  return Number.isInteger(value) ? value : null;
}

function hasFractionalInputValue(input) {
  const rawValue = typeof input?.value === "string" ? input.value.trim() : "";
  if (rawValue === "") {
    return false;
  }
  const value = Number(rawValue);
  return Number.isFinite(value) && !Number.isInteger(value);
}

function updateScoreInputUi(form, operation, currentScore) {
  if (!form) {
    return;
  }
  const isTargetMode = operation === "target";
  const amountInput = form.querySelector("[name=amount]");
  const amountLabel = form.querySelector("[data-score-amount-label]");
  const amountHint = form.querySelector("[data-score-amount-hint]");

  if (amountLabel) {
    amountLabel.textContent = isTargetMode ? "최종 점수" : "몇 점을 바꿀까요?";
  }
  if (amountHint) {
    amountHint.textContent = isTargetMode
      ? "입력한 점수가 그대로 새 점수가 돼요."
      : "현재 점수에서 입력한 만큼 바뀌어요.";
  }
  if (amountInput) {
    amountInput.min = isTargetMode ? "0" : "1";
    amountInput.placeholder = isTargetMode ? "0~100 입력" : "1~100 입력";
  }
  updateScorePreview(form, currentScore, operation);
}

function updateScorePreview(form, currentScore, operation) {
  const preview = form?.querySelector("[data-score-preview]");
  if (!preview) {
    return;
  }

  const amount = readIntegerInputValue(form.querySelector("[name=amount]"));
  const minimum = operation === "target" ? 0 : 1;
  if (
    !Number.isInteger(currentScore) ||
    !["increase", "decrease", "target"].includes(operation) ||
    amount === null ||
    amount < minimum ||
    amount > 100
  ) {
    preview.textContent = "";
    preview.hidden = true;
    return;
  }

  const resultingScore =
    operation === "target"
      ? amount
      : currentScore + (operation === "decrease" ? -amount : amount);
  const delta = resultingScore - currentScore;
  if (resultingScore < 0 || resultingScore > 100 || delta === 0) {
    preview.textContent = "";
    preview.hidden = true;
    return;
  }

  const sign = delta > 0 ? "+" : "";
  preview.textContent = `현재 ${currentScore}점 → ${resultingScore}점 (${sign}${delta}점)`;
  preview.hidden = false;
}

function readScoreChangeCommand(form) {
  const operation = readScoreOperation(form);
  const amountInput = form.querySelector("[name=amount]");
  const reasonInput = form.querySelector("[name=reason]");
  const amount = readIntegerInputValue(amountInput);
  const reason = reasonInput?.value.trim() || "";
  let isValid = true;
  let hasInlineError = false;

  if (!["increase", "decrease", "target"].includes(operation)) {
    showFieldError(form, "operation", "점수를 바꿀 방법을 선택해 주세요.");
    isValid = false;
    hasInlineError = true;
  }
  const minimum = operation === "target" ? 0 : 1;
  if (hasFractionalInputValue(amountInput)) {
    const message = "점수는 소수점 없이 정수로 입력해 주세요.";
    showFieldError(form, "amount", message, { assistiveOnly: true });
    showScoreToast(form, message, "warning");
    isValid = false;
  } else if (amount === null || amount < minimum || amount > 100) {
    const message =
      operation === "target"
        ? "최종 점수는 0부터 100 사이의 정수여야 합니다."
        : "변경할 점수는 1부터 100 사이의 정수여야 합니다.";
    showFieldError(form, "amount", message);
    isValid = false;
    hasInlineError = true;
  }
  if (reason.length > 200) {
    showFieldError(form, "reason", "변경 이유는 200자 이하여야 합니다.");
    isValid = false;
    hasInlineError = true;
  }
  if (!isValid) {
    if (hasInlineError) {
      showFormStatus(form, "입력값을 확인해 주세요.", "error");
    }
    focusFirstInvalidField(form);
    return null;
  }

  if (operation === "target") {
    return { targetScore: amount, reason };
  }
  return { delta: operation === "decrease" ? -amount : amount, reason };
}

function showApiFormError(form, error) {
  const apiError = error instanceof ApiRequestError ? error.apiError : null;
  const details = Array.isArray(apiError?.details) ? apiError.details : [];
  details.forEach((detail) => {
    const field = ["delta", "targetScore"].includes(detail?.field)
      ? "amount"
      : detail?.field;
    if (["amount", "reason", "operation"].includes(field)) {
      showFieldError(form, field, detail.message);
    }
  });
  let message = apiError?.reason || "요청 결과를 확인하지 못했어요.";
  let requiresRefresh = false;
  if (
    !(error instanceof ApiRequestError) ||
    (error.status >= 200 && error.status < 300) ||
    !apiError ||
    error.status >= 500
  ) {
    message = "요청 결과를 확인하지 못했어요. 새로고침해 현재 점수를 확인해 주세요.";
    requiresRefresh = true;
  } else if (apiError?.errorCode === "CSRF_FAILED") {
    message = "보안 토큰이 만료되었어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.";
    requiresRefresh = true;
  }
  showFormStatus(form, message, "error");
  focusFirstInvalidField(form);
  return requiresRefresh;
}

function isScoreUnchangedError(error) {
  return (
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "SCORE_UNCHANGED"
  );
}

function clearFormFeedback(form) {
  const errorLists = [...form.querySelectorAll("[data-error-for]")];
  const errorIds = new Set(errorLists.map((list) => list.id).filter(Boolean));
  errorLists.forEach((list) => {
    list.replaceChildren();
    list.hidden = true;
    list.classList.toggle("errorlist--assistive", false);
  });
  form.querySelectorAll("[aria-invalid=true]").forEach((field) => {
    field.removeAttribute("aria-invalid");
    const describedBy = (field.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((id) => id && !errorIds.has(id));
    if (describedBy.length > 0) {
      field.setAttribute("aria-describedby", describedBy.join(" "));
    } else {
      field.removeAttribute("aria-describedby");
    }
  });
  showFormStatus(form, "", "");
}

function showFieldError(
  form,
  field,
  message,
  { assistiveOnly = false } = {},
) {
  const list = form.querySelector(`[data-error-for="${field}"]`);
  if (!list || typeof message !== "string") {
    return;
  }
  const item = document.createElement("li");
  item.textContent = message;
  list.append(item);
  list.hidden = false;
  list.classList.toggle("errorlist--assistive", assistiveOnly);
  form.querySelectorAll(`[name="${field}"]`).forEach((input) => {
    input.setAttribute("aria-invalid", "true");
    const describedBy = new Set(
      (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean),
    );
    describedBy.add(list.id);
    input.setAttribute("aria-describedby", [...describedBy].filter(Boolean).join(" "));
  });
}

function focusFirstInvalidField(form) {
  form.querySelector("[aria-invalid=true]")?.focus();
}

function showFormStatus(form, message, state) {
  const status = form.querySelector("[data-score-form-status]");
  if (!status) {
    return;
  }
  status.textContent = message;
  status.classList.toggle("form-status--success", state === "success");
  status.classList.toggle("form-status--error", state === "error");
}

function showScoreToast(form, message, tone) {
  if (typeof globalThis.woorisaiShowToast === "function") {
    globalThis.woorisaiShowToast(message, { tone });
    return;
  }
  showFormStatus(form, message, tone === "success" ? "success" : "error");
}

function setSubmitLabel(form, label) {
  const element = form.querySelector("[data-score-submit-label]");
  if (element) {
    element.textContent = label;
  }
}

function setFormFieldsDisabled(form, disabled) {
  form
    .querySelectorAll('input:not([type="hidden"]), textarea')
    .forEach((field) => {
      field.disabled = disabled;
    });
}

function updateCharacterCount(form) {
  const reason = form.querySelector("[name=reason]");
  const count = form.querySelector("[data-character-current]");
  if (reason && count) {
    count.textContent = String(reason.value.length);
  }
}

function getCsrfToken(form) {
  const formToken = form.querySelector("[name=csrfmiddlewaretoken]")?.value;
  if (formToken) {
    return formToken;
  }
  const cookie = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
}

function redirectWhenAuthenticationExpired(error) {
  if (
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "AUTHENTICATION_REQUIRED"
  ) {
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/login/?next=${encodeURIComponent(next)}`);
    return true;
  }
  return false;
}
