const scoreThread = document.querySelector("[data-score-thread-root]");

if (scoreThread) {
  initializeScoreThread(scoreThread);
}

function initializeScoreThread(root) {
  const content = root.querySelector("[data-thread-content]");
  const status = root.querySelector("[data-thread-status]");
  const view = root.querySelector("[data-thread-view]");
  const changeRoot = root.querySelector("[data-thread-change]");
  const commentList = root.querySelector("[data-comment-list]");
  const commentEmpty = root.querySelector("[data-comment-empty]");
  const commentCount = root.querySelector("[data-comment-count]");
  const refreshButton = root.querySelector("[data-thread-refresh]");
  const form = root.querySelector("[data-comment-form]");
  const textarea = form?.querySelector("[name=content]");
  const submitButton = form?.querySelector("[data-comment-submit]");
  if (
    !content ||
    !status ||
    !view ||
    !changeRoot ||
    !commentList ||
    !commentEmpty ||
    !commentCount ||
    !form ||
    !textarea ||
    !submitButton
  ) {
    return;
  }

  let currentThread = null;
  let hasLoadError = false;
  let isSubmitting = false;
  let requiresRefresh = false;
  let refreshAfterSubmit = false;
  let loadSequence = 0;

  const renderCurrentThread = () => {
    if (!currentThread) {
      return;
    }
    renderScoreChange(changeRoot, currentThread);
    renderComments({
      commentCount,
      commentEmpty,
      commentList,
      comments: currentThread.comments,
    });
  };

  const loadThread = async ({ announce = true } = {}) => {
    const sequence = ++loadSequence;
    content.setAttribute("aria-busy", "true");
    if (refreshButton) {
      refreshButton.disabled = true;
    }
    if (announce && !currentThread) {
      status.className = "surface loading-state";
      status.hidden = false;
      status.textContent = "점수 대화를 불러오고 있어요…";
    }

    try {
      const payload = await requestJson(root.dataset.threadUrl, {
        cache: "no-store",
      });
      const thread = readScoreThread(payload);
      if (sequence !== loadSequence) {
        return;
      }
      const shouldClearStatus = hasLoadError || requiresRefresh;
      currentThread = thread;
      hasLoadError = false;
      requiresRefresh = false;
      renderCurrentThread();
      if (shouldClearStatus) {
        showCommentFormStatus(form, "", "");
      }
      status.hidden = true;
      status.replaceChildren();
      view.hidden = false;
      if (!isSubmitting) {
        setCommentFormDisabled(form, false);
        setCommentSubmitLabel(form, "댓글 남기기");
      }
    } catch (error) {
      if (sequence !== loadSequence || redirectWhenAuthenticationExpired(error)) {
        return;
      }
      if (currentThread) {
        hasLoadError = true;
        showCommentFormStatus(
          form,
          "최신 댓글을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
          "error",
        );
      } else {
        view.hidden = true;
        renderThreadLoadError(status, () => loadThread());
      }
    } finally {
      if (sequence === loadSequence) {
        content.setAttribute("aria-busy", "false");
        if (refreshButton && !isSubmitting) {
          refreshButton.disabled = false;
        }
      }
    }
  };

  const refreshThread = () => {
    if (isSubmitting) {
      refreshAfterSubmit = true;
      return;
    }
    loadThread({ announce: false }).catch(() => undefined);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (isSubmitting || requiresRefresh) {
      return;
    }

    const commentContent = textarea.value.trim();
    const contentLength = [...commentContent].length;
    if (contentLength < 1 || contentLength > 500) {
      showCommentFormStatus(
        form,
        contentLength < 1
          ? "댓글 내용을 입력해 주세요."
          : "댓글은 500자 이하로 입력해 주세요.",
        "error",
      );
      textarea.focus();
      return;
    }

    isSubmitting = true;
    loadSequence += 1;
    content.setAttribute("aria-busy", "false");
    form.setAttribute("aria-busy", "true");
    if (refreshButton) {
      refreshButton.disabled = true;
    }
    setCommentFormDisabled(form, true);
    setCommentSubmitLabel(form, "남기고 있어요…");
    showCommentFormStatus(form, "", "");

    requestJson(root.dataset.commentsUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(form),
      },
      body: JSON.stringify({ content: commentContent }),
    })
      .then((payload) => {
        const comment = readCreatedComment(payload);
        if (!currentThread) {
          throw new Error("점수 대화를 먼저 확인해야 합니다.");
        }
        if (!currentThread.comments.some((item) => item.id === comment.id)) {
          currentThread.comments.push(comment);
        }
        currentThread.commentCount = currentThread.comments.length;
        renderCurrentThread();
        textarea.value = "";
        updateCommentCharacterCount(form);
        showCommentFormStatus(form, "댓글을 남겼어요.", "success");
      })
      .catch((error) => {
        if (redirectWhenAuthenticationExpired(error)) {
          return;
        }
        requiresRefresh = showCommentApiError(form, error);
      })
      .finally(() => {
        isSubmitting = false;
        form.setAttribute("aria-busy", "false");
        setCommentFormDisabled(form, requiresRefresh || !currentThread);
        setCommentSubmitLabel(
          form,
          requiresRefresh ? "새로고침 후 확인" : "댓글 남기기",
        );
        if (refreshButton) {
          refreshButton.disabled = false;
        }
        const shouldRefresh = refreshAfterSubmit && !requiresRefresh;
        refreshAfterSubmit = false;
        if (shouldRefresh) {
          refreshThread();
        }
      });
  });

  textarea.addEventListener("input", () => updateCommentCharacterCount(form));
  refreshButton?.addEventListener("click", () => {
    refreshThread();
  });
  document.addEventListener?.("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshThread();
    }
  });
  document.addEventListener?.("woorisai:push-message", (event) => {
    const threadLink = event?.detail?.threadLink;
    if (typeof threadLink !== "string") {
      return;
    }
    try {
      const url = new URL(threadLink, window.location.origin);
      if (
        url.origin === window.location.origin &&
        url.pathname === window.location.pathname
      ) {
        refreshThread();
      }
    } catch {
      return;
    }
  });
  globalThis.addEventListener?.("pageshow", (event) => {
    if (event.persisted) {
      refreshThread();
    }
  });

  updateCommentCharacterCount(form);
  loadThread().catch(() => undefined);
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

class ApiRequestError extends Error {
  constructor(status, apiError) {
    super(apiError?.reason || "요청을 처리하지 못했어요.");
    this.name = "ApiRequestError";
    this.status = status;
    this.apiError = apiError;
  }
}

function readScoreThread(payload) {
  const thread = payload?.success;
  if (!isScoreChange(thread) || !Array.isArray(thread.comments)) {
    throw new Error("점수 대화 응답 형식이 올바르지 않습니다.");
  }
  thread.comments.forEach(validateComment);
  if (thread.commentCount !== thread.comments.length) {
    throw new Error("댓글 개수가 올바르지 않습니다.");
  }
  return thread;
}

function readCreatedComment(payload) {
  const comment = payload?.success;
  validateComment(comment);
  return comment;
}

function isScoreChange(change) {
  return (
    change &&
    Number.isInteger(change.id) &&
    change.id > 0 &&
    isParticipantSummary(change.sourceParticipant) &&
    isParticipantSummary(change.targetParticipant) &&
    isParticipantSummary(change.changedBy) &&
    Number.isInteger(change.delta) &&
    change.delta !== 0 &&
    change.delta >= -100 &&
    change.delta <= 100 &&
    typeof change.reason === "string" &&
    [...change.reason].length <= 200 &&
    Number.isInteger(change.resultingScore) &&
    change.resultingScore >= 0 &&
    change.resultingScore <= 100 &&
    typeof change.createdAt === "string" &&
    !Number.isNaN(Date.parse(change.createdAt)) &&
    Number.isInteger(change.commentCount) &&
    change.commentCount >= 0 &&
    typeof change.threadUrl === "string" &&
    /^\/history\/[1-9]\d*\/$/.test(change.threadUrl)
  );
}

function validateComment(comment) {
  if (
    !comment ||
    !Number.isInteger(comment.id) ||
    comment.id < 1 ||
    !isParticipantSummary(comment.author) ||
    typeof comment.content !== "string" ||
    [...comment.content].length < 1 ||
    [...comment.content].length > 500 ||
    typeof comment.createdAt !== "string" ||
    Number.isNaN(Date.parse(comment.createdAt)) ||
    typeof comment.isMine !== "boolean"
  ) {
    throw new Error("댓글 응답 형식이 올바르지 않습니다.");
  }
}

function isParticipantSummary(participant) {
  return (
    participant &&
    (participant.slot === 1 || participant.slot === 2) &&
    typeof participant.displayName === "string"
  );
}

function renderScoreChange(root, change) {
  const header = document.createElement("div");
  header.className = "history-card__header";
  const heading = document.createElement("div");
  const direction = document.createElement("p");
  direction.className = "history-direction";
  direction.textContent = `${change.sourceParticipant.displayName} → ${change.targetParticipant.displayName}`;
  const time = document.createElement("time");
  time.dateTime = change.createdAt;
  time.textContent = formatCreatedAt(change.createdAt);
  heading.append(direction, time);

  const delta = document.createElement("strong");
  delta.className = `delta ${change.delta > 0 ? "delta--positive" : "delta--negative"}`;
  delta.textContent = `${change.delta > 0 ? "+" : ""}${change.delta}점`;
  header.append(heading, delta);
  const children = [header];

  if (change.reason) {
    const reason = document.createElement("p");
    reason.className = "history-reason";
    reason.textContent = `“${change.reason}”`;
    children.push(reason);
  }

  const footer = document.createElement("footer");
  const changedBy = document.createElement("span");
  changedBy.textContent = `변경자 ${change.changedBy.displayName}`;
  const resultingScore = document.createElement("span");
  resultingScore.textContent = `변경 후 ${change.resultingScore}점`;
  footer.append(changedBy, resultingScore);
  children.push(footer);
  root.replaceChildren(...children);
}

function renderComments({ commentCount, commentEmpty, commentList, comments }) {
  commentCount.textContent = String(comments.length);
  commentList.replaceChildren(...comments.map(createCommentItem));
  commentList.hidden = comments.length === 0;
  commentEmpty.hidden = comments.length !== 0;
}

function createCommentItem(comment) {
  const item = document.createElement("li");
  item.className = `comment-item${comment.isMine ? " comment-item--mine" : ""}`;
  const article = document.createElement("article");
  article.className = "comment-bubble";
  const header = document.createElement("header");
  const author = document.createElement("strong");
  author.textContent = comment.isMine ? "나" : comment.author.displayName;
  const time = document.createElement("time");
  time.dateTime = comment.createdAt;
  time.textContent = formatCreatedAt(comment.createdAt);
  header.append(author, time);
  const body = document.createElement("p");
  body.textContent = comment.content;
  article.append(header, body);
  item.append(article);
  return item;
}

function formatCreatedAt(value) {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).formatToParts(new Date(value));
  const part = (type) => parts.find((item) => item.type === type)?.value || "";
  return `${part("year")}.${part("month")}.${part("day")} ${part("hour")}:${part("minute")}`;
}

function renderThreadLoadError(status, retry) {
  status.className = "surface loading-state loading-state--error";
  status.hidden = false;
  const message = document.createElement("p");
  message.textContent = "점수 대화를 불러오지 못했어요.";
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "button";
  button.textContent = "다시 시도";
  button.addEventListener("click", () => retry());
  status.replaceChildren(message, button);
}

function showCommentApiError(form, error) {
  const apiError = error instanceof ApiRequestError ? error.apiError : null;
  let message = apiError?.reason || "댓글 요청 결과를 확인하지 못했어요.";
  let needsRefresh = false;
  if (
    !(error instanceof ApiRequestError) ||
    (error.status >= 200 && error.status < 300) ||
    !apiError ||
    error.status >= 500
  ) {
    message = "댓글 결과를 확인하지 못했어요. 새로고침해 대화를 확인해 주세요.";
    needsRefresh = true;
  } else if (apiError.errorCode === "CSRF_FAILED") {
    message = "보안 토큰이 만료되었어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.";
    needsRefresh = true;
  }
  showCommentFormStatus(form, message, "error");
  return needsRefresh;
}

function showCommentFormStatus(form, message, state) {
  const status = form.querySelector("[data-comment-form-status]");
  if (!status) {
    return;
  }
  status.textContent = message;
  status.classList.toggle("form-status--success", state === "success");
  status.classList.toggle("form-status--error", state === "error");
}

function setCommentFormDisabled(form, disabled) {
  form.querySelector("[name=content]").disabled = disabled;
  form.querySelector("[data-comment-submit]").disabled = disabled;
}

function setCommentSubmitLabel(form, label) {
  const element = form.querySelector("[data-comment-submit-label]");
  if (element) {
    element.textContent = label;
  }
}

function updateCommentCharacterCount(form) {
  const textarea = form.querySelector("[name=content]");
  const count = form.querySelector("[data-comment-character-current]");
  if (textarea && count) {
    count.textContent = String([...textarea.value].length);
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
