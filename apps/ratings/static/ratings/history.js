const historyRoot = document.querySelector("[data-history-root]");

if (historyRoot) {
  initializeHistory(historyRoot);
}

function initializeHistory(root) {
  const pageNumber = readPageNumber(window.location.search);
  const refreshHistory = () => {
    loadHistoryPage(root, pageNumber).catch(() => undefined);
  };
  refreshHistory();
  document.addEventListener?.("woorisai:push-message", () => {
    refreshHistory();
  });
  document.addEventListener?.("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshHistory();
    }
  });
  globalThis.addEventListener?.("pageshow", (event) => {
    if (event.persisted) {
      refreshHistory();
    }
  });
}

async function loadHistoryPage(root, pageNumber) {
  const loadSequence = Number(root.dataset.loadSequence || "0") + 1;
  root.dataset.loadSequence = String(loadSequence);
  const content = root.querySelector("[data-history-content]");
  const status = root.querySelector("[data-history-status]");
  const list = root.querySelector("[data-history-list]");
  const empty = root.querySelector("[data-history-empty]");
  const pagination = root.querySelector("[data-history-pagination]");
  if (!content || !status || !list || !empty || !pagination) {
    return;
  }

  const hasRenderedContent = !list.hidden || !empty.hidden;
  content.setAttribute("aria-busy", "true");
  status.hidden = false;
  status.textContent = "마음 기록을 불러오고 있어요…";
  if (!hasRenderedContent) {
    list.hidden = true;
    empty.hidden = true;
    pagination.hidden = true;
  }

  try {
    const url = new URL(root.dataset.historyUrl, window.location.origin);
    url.searchParams.set("pageNumber", String(pageNumber));
    const payload = await requestJson(url);
    const page = readHistoryPage(payload);
    if (root.dataset.loadSequence !== String(loadSequence)) {
      return;
    }
    renderHistoryPage({ empty, list, page, pagination, status });
  } catch (error) {
    if (
      root.dataset.loadSequence !== String(loadSequence) ||
      redirectWhenAuthenticationExpired(error)
    ) {
      return;
    }
    renderHistoryError(
      status,
      pageNumber,
      error,
      () => loadHistoryPage(root, pageNumber),
    );
  } finally {
    if (root.dataset.loadSequence === String(loadSequence)) {
      content.setAttribute("aria-busy", "false");
    }
  }
}

async function requestJson(url) {
  const response = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
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

function readPageNumber(search) {
  const parameters = new URLSearchParams(search);
  const value = parameters.get("pageNumber") ?? parameters.get("page") ?? "1";
  if (!/^[1-9]\d*$/.test(value)) {
    return 1;
  }
  const pageNumber = Number(value);
  return Number.isSafeInteger(pageNumber) ? pageNumber : 1;
}

function readHistoryPage(payload) {
  const results = payload?.success?.results;
  const paging = payload?.success?.paging;
  if (
    !Array.isArray(results) ||
    results.length > 20 ||
    !paging ||
    !Number.isInteger(paging.pageNumber) ||
    paging.pageNumber < 1 ||
    paging.pageSize !== 20 ||
    typeof paging.hasNext !== "boolean" ||
    !Number.isInteger(paging.totalCount) ||
    paging.totalCount < 0
  ) {
    throw new Error("마음 기록 응답 형식이 올바르지 않습니다.");
  }
  results.forEach(validateHistoryItem);
  return { results, paging };
}

function validateHistoryItem(item) {
  if (
    !item ||
    !Number.isInteger(item.id) ||
    item.id < 1 ||
    !isParticipantSummary(item.sourceParticipant) ||
    !isParticipantSummary(item.targetParticipant) ||
    !isParticipantSummary(item.changedBy) ||
    !Number.isInteger(item.delta) ||
    item.delta === 0 ||
    item.delta < -100 ||
    item.delta > 100 ||
    typeof item.reason !== "string" ||
    [...item.reason].length > 200 ||
    !Number.isInteger(item.resultingScore) ||
    item.resultingScore < 0 ||
    item.resultingScore > 100 ||
    typeof item.createdAt !== "string" ||
    Number.isNaN(Date.parse(item.createdAt)) ||
    !Number.isInteger(item.commentCount) ||
    item.commentCount < 0 ||
    typeof item.threadUrl !== "string" ||
    !/^\/history\/[1-9]\d*\/$/.test(item.threadUrl) ||
    !validateAttachments(item.attachments)
  ) {
    throw new Error("마음 기록 항목 형식이 올바르지 않습니다.");
  }
}

function validateAttachments(value) {
  if (value === undefined) {
    return true;
  }
  return (
    Array.isArray(value) &&
    value.length <= 1 &&
    value.every((attachment) => {
      const validId =
        (Number.isInteger(attachment?.id) && attachment.id > 0) ||
        (typeof attachment?.id === "string" && attachment.id.length > 0);
      return (
        validId &&
        attachment.kind === "image" &&
        typeof attachment.fileName === "string" &&
        [...attachment.fileName].length > 0 &&
        [...attachment.fileName].length <= 255 &&
        ["image/jpeg", "image/png", "image/webp"].includes(
          attachment.contentType,
        ) &&
        Number.isInteger(attachment.byteSize) &&
        attachment.byteSize > 0 &&
        attachment.byteSize <= 10 * 1024 * 1024 &&
        typeof attachment.contentUrl === "string" &&
        isHttpUrl(attachment.contentUrl)
      );
    })
  );
}

function isHttpUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function isParticipantSummary(participant) {
  return (
    participant &&
    (participant.slot === 1 || participant.slot === 2) &&
    typeof participant.displayName === "string"
  );
}

function renderHistoryPage({ empty, list, page, pagination, status }) {
  status.hidden = true;
  status.replaceChildren();
  if (page.results.length === 0) {
    list.replaceChildren();
    list.hidden = true;
    empty.hidden = false;
  } else {
    list.replaceChildren(...page.results.map(createHistoryItem));
    list.hidden = false;
    empty.hidden = true;
  }
  renderPagination(pagination, page.paging);
}

function createHistoryItem(change) {
  const item = document.createElement("li");
  item.className = "timeline-item";
  const dot = document.createElement("span");
  dot.className = "timeline-dot";
  dot.setAttribute("aria-hidden", "true");

  const card = document.createElement("article");
  card.className = "surface history-card";
  const link = document.createElement("a");
  link.className = "history-card-link";
  link.href = change.threadUrl;
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
  card.append(header);

  if (change.reason) {
    const reason = document.createElement("p");
    reason.className = "history-reason";
    reason.textContent = `“${change.reason}”`;
    card.append(reason);
  }

  const attachment = Array.isArray(change.attachments)
    ? change.attachments[0]
    : null;
  if (attachment) {
    card.append(createImageAttachment(attachment));
  }

  const footer = document.createElement("footer");
  const changedBy = document.createElement("span");
  changedBy.textContent = `변경자 ${change.changedBy.displayName}`;
  const resultingScore = document.createElement("span");
  resultingScore.setAttribute("aria-label", `변경 후 ${change.resultingScore}점`);
  resultingScore.append(document.createTextNode("변경 후 "));
  const score = document.createElement("strong");
  score.textContent = `${change.resultingScore}점`;
  resultingScore.append(score);
  const commentCount = document.createElement("span");
  commentCount.textContent = `댓글 ${change.commentCount}개`;
  footer.append(changedBy, resultingScore, commentCount);
  card.append(footer);
  link.append(card);
  item.append(dot, link);
  return item;
}

function createImageAttachment(attachment) {
  const container = document.createElement("figure");
  container.className = "attachment attachment--image attachment--history";
  const contentUrl = new URL(
    attachment.contentUrl,
    window.location.origin,
  ).href;
  const image = document.createElement("img");
  image.src = contentUrl;
  image.alt = attachment.fileName;
  image.loading = "lazy";
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  const caption = document.createElement("figcaption");
  caption.textContent = "첨부 사진 · 대화에서 다운로드";
  container.append(image, caption);
  return container;
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

function renderPagination(pagination, paging) {
  const totalPages = Math.max(1, Math.ceil(paging.totalCount / paging.pageSize));
  if (totalPages <= 1) {
    pagination.replaceChildren();
    pagination.hidden = true;
    return;
  }

  const previous =
    paging.pageNumber > 1
      ? createPageLink("← 이전", paging.pageNumber - 1, "prev")
      : document.createElement("span");
  const position = document.createElement("strong");
  position.append(document.createTextNode(String(paging.pageNumber)));
  const total = document.createElement("span");
  total.textContent = ` / ${totalPages}`;
  position.append(total);
  const next = paging.hasNext
    ? createPageLink("다음 →", paging.pageNumber + 1, "next")
    : document.createElement("span");
  pagination.replaceChildren(previous, position, next);
  pagination.hidden = false;
}

function createPageLink(label, pageNumber, relation) {
  const link = document.createElement("a");
  const url = new URL(window.location.href);
  url.searchParams.delete("page");
  url.searchParams.set("pageNumber", String(pageNumber));
  link.href = `${url.pathname}${url.search}`;
  link.rel = relation;
  link.textContent = label;
  return link;
}

function renderHistoryError(status, pageNumber, error, retry) {
  status.className = "surface loading-state loading-state--error";
  status.hidden = false;
  const message = document.createElement("p");
  message.textContent = "마음 기록을 불러오지 못했어요.";
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "button";
  const isMissingPage =
    pageNumber > 1 &&
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "NOT_FOUND";
  button.textContent = isMissingPage ? "첫 페이지로" : "다시 시도";
  if (isMissingPage) {
    button.addEventListener("click", () => {
      window.location.assign(pageHref(1));
    });
  } else {
    button.addEventListener("click", () => retry());
  }
  status.replaceChildren(message, button);
}

function pageHref(pageNumber) {
  const url = new URL(window.location.href);
  url.searchParams.delete("page");
  url.searchParams.set("pageNumber", String(pageNumber));
  return `${url.pathname}${url.search}`;
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
