const diaryThread = document.querySelector("[data-diary-thread-root]");
const MAX_DIARY_CONTENT_LENGTH = 1000;
const MAX_COMMENT_CONTENT_LENGTH = 500;
const MAX_IMAGE_SIZE = 10 * 1024 * 1024;
const MAX_VIDEO_SIZE = 100 * 1024 * 1024;
const IMAGE_CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp"];
const VIDEO_CONTENT_TYPES = ["video/mp4", "video/webm", "video/quicktime"];
let diaryThreadAuthenticationRedirectStarted = false;

if (diaryThread) {
  initializeDiaryThread(diaryThread);
}

function initializeDiaryThread(root) {
  const content = root.querySelector("[data-thread-content]");
  const status = root.querySelector("[data-thread-status]");
  const view = root.querySelector("[data-thread-view]");
  const entryRoot = root.querySelector("[data-thread-entry]");
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
    !entryRoot ||
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
  let isMissing = false;
  let isSubmitting = false;
  let requiresRefresh = false;
  let refreshAfterSubmit = false;
  let loadSequence = 0;

  const renderCurrentThread = () => {
    if (!currentThread) {
      return;
    }
    renderDiaryEntry(entryRoot, currentThread);
    renderComments({
      commentCount,
      commentEmpty,
      commentList,
      comments: currentThread.comments,
    });
  };

  const markThreadMissing = () => {
    currentThread = null;
    hasLoadError = false;
    isMissing = true;
    requiresRefresh = true;
    entryRoot.replaceChildren();
    commentList.replaceChildren();
    commentCount.textContent = "0";
    commentEmpty.hidden = true;
    textarea.value = "";
    updateCommentCharacterCount(form);
    view.hidden = true;
    setCommentFormDisabled(form, true);
    setCommentSubmitLabel(form, "댓글을 남길 수 없어요");
    if (refreshButton) {
      refreshButton.disabled = true;
    }
    renderThreadMissing(status);
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
      status.textContent = "일기 대화를 불러오고 있어요…";
    }

    try {
      const payload = await requestJson(root.dataset.threadUrl, {
        cache: "no-store",
      });
      const thread = readDiaryThread(payload);
      if (sequence !== loadSequence) {
        return;
      }
      const shouldClearFormStatus = hasLoadError || requiresRefresh;
      currentThread = thread;
      hasLoadError = false;
      isMissing = false;
      requiresRefresh = false;
      renderCurrentThread();
      if (shouldClearFormStatus) {
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
      if (
        sequence !== loadSequence ||
        redirectWhenAuthenticationExpired(error)
      ) {
        return;
      }
      if (isDiaryEntryNotFound(error)) {
        markThreadMissing();
      } else if (currentThread) {
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
        if (refreshButton && !isSubmitting && !isMissing) {
          refreshButton.disabled = false;
        }
      }
    }
  };

  const refreshThread = () => {
    if (isMissing) {
      return;
    }
    if (isSubmitting) {
      refreshAfterSubmit = true;
      return;
    }
    loadThread({ announce: false }).catch(() => undefined);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (isSubmitting || requiresRefresh || isMissing) {
      return;
    }

    const commentContent = textarea.value.trim();
    const contentLength = [...commentContent].length;
    if (contentLength < 1 || contentLength > MAX_COMMENT_CONTENT_LENGTH) {
      showCommentFormStatus(
        form,
        contentLength < 1
          ? "댓글 내용을 입력해 주세요."
          : `댓글은 ${MAX_COMMENT_CONTENT_LENGTH}자 이하로 입력해 주세요.`,
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
      body: JSON.stringify({ content: commentContent }),
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(form),
      },
      method: "POST",
    })
      .then((payload) => {
        const comment = readCreatedComment(payload);
        if (!currentThread) {
          throw new Error("일기 대화를 먼저 확인해야 합니다.");
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
        if (isDiaryEntryNotFound(error)) {
          markThreadMissing();
        } else {
          requiresRefresh = showCommentApiError(form, error);
        }
      })
      .finally(() => {
        isSubmitting = false;
        form.setAttribute("aria-busy", "false");
        setCommentFormDisabled(form, requiresRefresh || !currentThread);
        setCommentSubmitLabel(
          form,
          isMissing
            ? "댓글을 남길 수 없어요"
            : requiresRefresh
              ? "새로고침 후 확인"
              : "댓글 남기기",
        );
        if (refreshButton && !isMissing) {
          refreshButton.disabled = false;
        }
        const shouldRefresh = refreshAfterSubmit && !requiresRefresh && !isMissing;
        refreshAfterSubmit = false;
        if (shouldRefresh) {
          refreshThread();
        }
      });
  });

  textarea.addEventListener("input", () => updateCommentCharacterCount(form));
  refreshButton?.addEventListener("click", refreshThread);
  document.addEventListener?.("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshThread();
    }
  });
  document.addEventListener?.("woorisai:push-message", (event) => {
    if (isCurrentDiaryThreadLink(event?.detail?.threadLink)) {
      refreshThread();
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

function readDiaryThread(payload) {
  const thread = payload?.success;
  if (!isDiaryEntry(thread) || !Array.isArray(thread.comments)) {
    throw new Error("일기 대화 응답 형식이 올바르지 않습니다.");
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

function isDiaryEntry(entry) {
  return (
    entry &&
    Number.isInteger(entry.id) &&
    entry.id > 0 &&
    isParticipantSummary(entry.author) &&
    typeof entry.content === "string" &&
    entry.content.trim().length > 0 &&
    [...entry.content].length <= MAX_DIARY_CONTENT_LENGTH &&
    isTimestamp(entry.createdAt) &&
    (entry.updatedAt === null || isTimestamp(entry.updatedAt)) &&
    typeof entry.isMine === "boolean" &&
    Number.isInteger(entry.commentCount) &&
    entry.commentCount >= 0 &&
    isDiaryThreadUrl(entry.threadUrl, entry.id) &&
    validateAttachmentList(entry.attachments)
  );
}

function validateComment(comment) {
  const contentLength =
    typeof comment?.content === "string" ? [...comment.content].length : -1;
  if (
    !comment ||
    !Number.isInteger(comment.id) ||
    comment.id < 1 ||
    !isParticipantSummary(comment.author) ||
    contentLength < 1 ||
    contentLength > MAX_COMMENT_CONTENT_LENGTH ||
    comment.content.trim().length < 1 ||
    !isTimestamp(comment.createdAt) ||
    typeof comment.isMine !== "boolean"
  ) {
    throw new Error("댓글 응답 형식이 올바르지 않습니다.");
  }
}

function validateAttachmentList(value) {
  if (!Array.isArray(value)) {
    return false;
  }
  const imageCount = value.filter((item) => item?.kind === "image").length;
  const videoCount = value.filter((item) => item?.kind === "video").length;
  if (
    !(
      (videoCount === 0 && imageCount <= 4) ||
      (videoCount === 1 && imageCount === 0 && value.length === 1)
    )
  ) {
    return false;
  }
  const ids = new Set();
  for (const attachment of value) {
    if (!validateAttachment(attachment) || ids.has(attachment.id)) {
      return false;
    }
    ids.add(attachment.id);
  }
  return true;
}

function validateAttachment(attachment) {
  const isImage = attachment?.kind === "image";
  const allowedTypes = isImage ? IMAGE_CONTENT_TYPES : VIDEO_CONTENT_TYPES;
  return (
    isAttachmentId(attachment?.id) &&
    (isImage || attachment.kind === "video") &&
    typeof attachment.fileName === "string" &&
    [...attachment.fileName].length > 0 &&
    [...attachment.fileName].length <= 255 &&
    allowedTypes.includes(attachment.contentType) &&
    Number.isInteger(attachment.byteSize) &&
    attachment.byteSize > 0 &&
    attachment.byteSize <= (isImage ? MAX_IMAGE_SIZE : MAX_VIDEO_SIZE) &&
    isSameOriginUrl(attachment.contentUrl)
  );
}

function isAttachmentId(value) {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    )
  );
}

function isParticipantSummary(participant) {
  return (
    participant &&
    (participant.slot === 1 || participant.slot === 2) &&
    typeof participant.displayName === "string" &&
    participant.displayName.length > 0
  );
}

function isTimestamp(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isDiaryThreadUrl(value, entryId) {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const url = new URL(value, window.location.origin);
    return (
      url.origin === window.location.origin &&
      url.pathname === `/diary/${entryId}/` &&
      url.search === "" &&
      url.hash === "" &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function isSameOriginUrl(value) {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const url = new URL(value, window.location.origin);
    return (
      url.origin === window.location.origin &&
      (url.protocol === "https:" || url.protocol === "http:") &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function isCurrentDiaryThreadLink(value) {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const url = new URL(value, window.location.origin);
    return (
      url.origin === window.location.origin &&
      url.pathname === window.location.pathname &&
      url.search === "" &&
      url.hash === "" &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function renderDiaryEntry(root, entry) {
  root.className = `surface diary-card diary-thread-origin${
    entry.isMine ? " diary-card--mine" : ""
  }`;
  const header = document.createElement("header");
  header.className = "diary-card__header";
  const identity = document.createElement("div");
  identity.className = "diary-card__identity";
  const avatar = document.createElement("span");
  avatar.className = "diary-card__avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = entry.author.displayName.slice(0, 1);
  const heading = document.createElement("div");
  const author = document.createElement("strong");
  author.textContent = entry.isMine
    ? `${entry.author.displayName} · 나의 글`
    : `${entry.author.displayName}님의 글`;
  const publishedAt = document.createElement("time");
  publishedAt.dateTime = entry.createdAt;
  publishedAt.textContent = `${formatTimestamp(entry.createdAt)} 게시`;
  heading.append(author, publishedAt);
  identity.append(avatar, heading);
  header.append(identity);

  const body = document.createElement("p");
  body.className = "diary-card__content";
  body.textContent = entry.content;
  const children = [header, body];
  const attachments = createAttachmentGallery(entry.attachments, {
    label: "일기에 첨부된 파일",
  });
  if (attachments) {
    children.push(attachments);
  }
  if (entry.updatedAt !== null) {
    const footer = document.createElement("footer");
    const editedAt = document.createElement("time");
    editedAt.dateTime = entry.updatedAt;
    editedAt.textContent = `${formatTimestamp(entry.updatedAt)} 수정`;
    footer.append(editedAt);
    children.push(footer);
  }
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
  time.textContent = formatTimestamp(comment.createdAt);
  header.append(author, time);
  const body = document.createElement("p");
  body.textContent = comment.content;
  article.append(header, body);
  item.append(article);
  return item;
}

function createAttachmentGallery(attachments, { label }) {
  if (attachments.length === 0) {
    return null;
  }
  const gallery = document.createElement("div");
  gallery.className = `attachment-gallery${
    attachments.length === 1 ? " attachment-gallery--single" : ""
  }`;
  gallery.setAttribute("aria-label", label);
  attachments.forEach((attachment) => gallery.append(createAttachment(attachment)));
  return gallery;
}

function createAttachment(attachment) {
  const container = document.createElement("figure");
  container.className = `attachment attachment--${attachment.kind}`;
  const contentUrl = new URL(attachment.contentUrl, window.location.origin).href;
  let media;
  if (attachment.kind === "image") {
    media = document.createElement("img");
    media.alt = attachment.fileName;
    media.loading = "lazy";
    media.decoding = "async";
  } else {
    media = document.createElement("video");
    media.autoplay = false;
    media.controls = true;
    media.playsInline = true;
    media.preload = "metadata";
    media.setAttribute("aria-label", attachment.fileName);
    const fallback = document.createElement("a");
    fallback.href = contentUrl;
    fallback.textContent = "영상을 재생할 수 없으면 다운로드해 주세요.";
    media.append(fallback);
  }
  media.src = contentUrl;
  media.referrerPolicy = "no-referrer";

  const caption = document.createElement("figcaption");
  const download = document.createElement("a");
  download.className = "attachment-download";
  download.href = contentUrl;
  download.download = attachment.fileName;
  download.referrerPolicy = "no-referrer";
  download.textContent = `${attachment.fileName} 다운로드`;
  caption.append(download);
  container.append(media, caption);
  return container;
}

function formatTimestamp(value) {
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
  message.textContent = "일기 대화를 불러오지 못했어요.";
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "button";
  button.textContent = "다시 시도";
  button.addEventListener("click", retry);
  status.replaceChildren(message, button);
}

function renderThreadMissing(status) {
  status.className = "surface loading-state loading-state--error";
  status.hidden = false;
  const message = document.createElement("p");
  message.textContent = "삭제되었거나 찾을 수 없는 일기예요.";
  const link = document.createElement("a");
  link.className = "text-button";
  link.href = "/diary/";
  link.textContent = "우리 일기로 돌아가기";
  status.replaceChildren(message, link);
}

function isDiaryEntryNotFound(error) {
  return (
    error instanceof ApiRequestError &&
    error.status === 404 &&
    error.apiError?.errorCode === "NOT_FOUND"
  );
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
  return form.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
}

function redirectWhenAuthenticationExpired(error) {
  if (
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "AUTHENTICATION_REQUIRED"
  ) {
    if (diaryThreadAuthenticationRedirectStarted) {
      return true;
    }
    diaryThreadAuthenticationRedirectStarted = true;
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/login/?next=${encodeURIComponent(next)}`);
    return true;
  }
  return false;
}
