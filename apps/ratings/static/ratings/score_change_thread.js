const scoreThread = document.querySelector("[data-score-thread-root]");
const MEDIA_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000;
let scoreThreadAuthenticationRedirectStarted = false;

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
  const commentMedia = initializeCommentMedia(root, form, {
    getScoreChangeId: () => currentThread?.id,
  });

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
        commentMedia?.setDisabled(false);
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
    if ((contentLength < 1 && !commentMedia?.hasSelection()) || contentLength > 500) {
      showCommentFormStatus(
        form,
        contentLength < 1
          ? "댓글 내용이나 사진·영상을 추가해 주세요."
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
    commentMedia?.setDisabled(true);
    setCommentSubmitLabel(form, "남기고 있어요…");
    showCommentFormStatus(form, "", "");

    const createComment = async () => {
      const command = { content: commentContent };
      if (commentMedia?.hasSelection()) {
        setCommentSubmitLabel(form, "파일을 올리고 있어요…");
        command.mediaUploadIds = await commentMedia.upload({
          csrfToken: getCsrfToken(form),
          purpose: "comment",
          scoreChangeId: currentThread?.id,
        });
        setCommentSubmitLabel(form, "남기고 있어요…");
      }
      return requestJson(root.dataset.commentsUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(form),
        },
        body: JSON.stringify(command),
      });
    };

    createComment()
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
        commentMedia?.clear({ discardUploads: false });
        updateCommentCharacterCount(form);
        showCommentFormStatus(form, "댓글을 남겼어요.", "success");
      })
      .catch((error) => {
        if (redirectWhenAuthenticationExpired(unwrapMediaUploadError(error))) {
          return;
        }
        if (error instanceof MediaUploadError) {
          requiresRefresh = false;
          showCommentFormStatus(form, error.message, "error");
        } else {
          if (shouldResetMediaUploads(error)) {
            commentMedia?.resetUploads();
          }
          requiresRefresh = showCommentApiError(form, error);
        }
      })
      .finally(() => {
        isSubmitting = false;
        form.setAttribute("aria-busy", "false");
        setCommentFormDisabled(form, requiresRefresh || !currentThread);
        commentMedia?.setDisabled(requiresRefresh || !currentThread);
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

function initializeCommentMedia(root, form, { getScoreChangeId }) {
  const input = form?.querySelector("[data-comment-media-input]");
  const selection = form?.querySelector("[data-comment-media-selection]");
  const status = form?.querySelector("[data-comment-media-status]");
  const uploadsUrl = root.dataset.mediaUploadsUrl;
  if (!input || !selection || !status || !uploadsUrl) {
    return null;
  }

  let items = [];
  let isDisabled = true;

  const setStatus = (message, state = "") => {
    status.textContent = message;
    status.classList.toggle("media-status--error", state === "error");
    status.classList.toggle("media-status--success", state === "success");
  };

  const updateSelectionStatus = () => {
    if (items.length === 0) {
      setStatus("");
      return;
    }
    if (items.some((item) => item.uploadState === "uploading")) {
      setStatus("첨부 파일을 올리고 있어요…");
      return;
    }
    if (items.some((item) => item.uploadState === "failed")) {
      setStatus(
        "첨부 파일을 업로드하지 못했어요. 댓글을 남길 때 다시 시도해 주세요.",
        "error",
      );
      return;
    }
    if (items.every((item) => item.uploadId)) {
      setStatus("첨부 파일 업로드를 마쳤어요.", "success");
      return;
    }
    setStatus(`${items.length}개 파일을 선택했어요.`);
  };

  const uploadFailureMessage = (error) => {
    const apiError = error instanceof ApiRequestError ? error : null;
    return apiError?.apiError?.errorCode === "CSRF_FAILED"
      ? "보안 토큰이 만료되었어요. 페이지를 새로고침한 뒤 다시 시도해 주세요."
      : apiError?.apiError?.reason ||
          "첨부 파일을 업로드하지 못했어요. 잠시 후 다시 시도해 주세요.";
  };

  const uploadContext = (scoreChangeId = getScoreChangeId()) => {
    if (!Number.isInteger(scoreChangeId) || scoreChangeId < 1) {
      return null;
    }
    return {
      csrfToken: getCsrfToken(form),
      purpose: "comment",
      scoreChangeId,
      uploadsUrl,
    };
  };

  const startItemUpload = (item, context) => {
    if (item.isDiscarded) {
      return Promise.reject(new MediaUploadCancelledError());
    }
    if (item.uploadId) {
      updateUploadProgress(item, 100, "업로드 완료");
      return Promise.resolve(item.uploadId);
    }
    if (item.uploadPromise) {
      return item.uploadPromise;
    }

    item.uploadState = "uploading";
    item.uploadContext = context;
    updateSelectionStatus();
    const uploadPromise = ensureMediaUploaded(item, context)
      .then((uploadId) => {
        item.uploadState = "uploaded";
        return uploadId;
      })
      .catch((error) => {
        if (!item.isDiscarded && !(error instanceof MediaUploadCancelledError)) {
          markUploadFailed(item);
        }
        throw error;
      })
      .finally(() => {
        if (item.uploadPromise === uploadPromise) {
          item.uploadPromise = null;
        }
        if (items.includes(item)) {
          updateSelectionStatus();
        }
      });
    item.uploadPromise = uploadPromise;
    return uploadPromise;
  };

  const startBackgroundUpload = (item, context) => {
    startItemUpload(item, context).catch((error) => {
      if (!items.includes(item) || error instanceof MediaUploadCancelledError) {
        return;
      }
      if (redirectWhenAuthenticationExpired(error)) {
        return;
      }
      setStatus(uploadFailureMessage(error), "error");
    });
  };

  const abandonItem = (item, context = item.uploadContext || uploadContext()) => {
    item.isDiscarded = true;
    item.uploadAbortController?.abort();
    if (context && (item.pendingUploadId || item.uploadId)) {
      discardInitiatedMediaUpload(item, context).catch((error) => {
        redirectWhenAuthenticationExpired(error);
      });
    }
  };

  const clear = ({ discardUploads = true } = {}) => {
    const clearedItems = items;
    items = [];
    clearedItems.forEach((item) => {
      revokePreviewUrl(item.previewUrl);
      if (discardUploads) {
        abandonItem(item);
      }
    });
    input.value = "";
    selection.replaceChildren();
    selection.hidden = true;
    setStatus("");
  };

  const render = () => {
    const previews = items.map((item) => {
      const preview = createUploadPreview(item, {
        onRemove: () => {
          if (isDisabled) {
            return;
          }
          revokePreviewUrl(item.previewUrl);
          items = items.filter((candidate) => candidate !== item);
          abandonItem(item);
          render();
          updateSelectionStatus();
        },
        removeLabel: `${item.file.name} 삭제`,
      });
      item.removeButton = preview.removeButton;
      item.progress = preview.progress;
      item.progressStatus = preview.progressStatus;
      item.removeButton.disabled = isDisabled;
      return preview.element;
    });
    selection.replaceChildren(...previews);
    selection.hidden = previews.length === 0;
  };

  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    input.value = "";
    if (files.length === 0) {
      return;
    }
    const error = validateCommentFiles([...items.map((item) => item.file), ...files]);
    if (error) {
      setStatus(error, "error");
      return;
    }
    const newItems = files.map(createUploadItem);
    items.push(...newItems);
    render();
    const context = uploadContext();
    if (!context) {
      updateSelectionStatus();
      return;
    }
    newItems.forEach((item) => startBackgroundUpload(item, context));
  });

  return {
    clear,
    hasSelection() {
      return items.length > 0;
    },
    resetUploads() {
      items.forEach((item) => {
        item.uploadPromise = null;
        markUploadFailed(item);
      });
      if (items.length > 0) {
        setStatus("첨부 파일을 다시 업로드해 주세요.", "error");
      }
    },
    setDisabled(disabled) {
      isDisabled = disabled;
      input.disabled = disabled;
      items.forEach((item) => {
        if (item.removeButton) {
          item.removeButton.disabled = disabled;
        }
      });
    },
    async upload({ csrfToken, purpose, scoreChangeId }) {
      const selectedItems = [...items];
      try {
        const uploadIds = await Promise.all(
          selectedItems.map((item) =>
            startItemUpload(item, {
              csrfToken,
              purpose,
              scoreChangeId,
              uploadsUrl,
            }),
          ),
        );
        updateSelectionStatus();
        return uploadIds.filter((_, index) =>
          items.includes(selectedItems[index]),
        );
      } catch (error) {
        const message = uploadFailureMessage(error);
        setStatus(message, "error");
        throw new MediaUploadError(message, error);
      }
    },
  };
}

function validateCommentFiles(files) {
  const imageTypes = ["image/jpeg", "image/png", "image/webp"];
  const videoTypes = ["video/mp4", "video/webm", "video/quicktime"];
  for (const file of files) {
    if (!imageTypes.includes(file.type) && !videoTypes.includes(file.type)) {
      return "JPG, PNG, WebP 사진이나 MP4, WebM, MOV 영상을 선택해 주세요.";
    }
    const maximumBytes = videoTypes.includes(file.type)
      ? 100 * 1024 * 1024
      : 10 * 1024 * 1024;
    if (!Number.isFinite(file.size) || file.size < 1 || file.size > maximumBytes) {
      return videoTypes.includes(file.type)
        ? "영상은 100MB 이하의 파일만 올릴 수 있어요."
        : "사진은 한 장당 10MB 이하의 파일만 올릴 수 있어요.";
    }
  }
  const videos = files.filter((file) => videoTypes.includes(file.type));
  if (videos.length > 0 && (videos.length > 1 || files.length > 1)) {
    return "사진과 영상은 함께 올릴 수 없고, 영상은 한 개만 선택할 수 있어요.";
  }
  if (videos.length === 0 && files.length > 4) {
    return "사진은 한 댓글에 최대 4장까지 올릴 수 있어요.";
  }
  return "";
}

function createUploadItem(file) {
  return {
    file,
    previewUrl: createPreviewUrl(file),
    progress: null,
    progressStatus: null,
    removeButton: null,
    discardPromise: null,
    discardedUploadId: null,
    isDiscarded: false,
    pendingUploadId: null,
    uploadAbortController: null,
    uploadContext: null,
    uploadId: null,
    uploadPromise: null,
    uploadState: "pending",
  };
}

function createUploadPreview(item, { onRemove, removeLabel }) {
  const card = document.createElement("article");
  const isVideo = item.file.type.startsWith("video/");
  card.className = `media-preview-card${
    isVideo ? " media-preview-card--video" : ""
  }`;
  let visual;
  if (isVideo) {
    visual = document.createElement("video");
    visual.autoplay = false;
    visual.controls = true;
    visual.playsInline = true;
    visual.preload = "metadata";
    visual.setAttribute("aria-label", `선택한 영상: ${item.file.name}`);
  } else {
    visual = document.createElement("img");
    visual.alt = `선택한 사진: ${item.file.name}`;
    visual.decoding = "async";
  }
  visual.className = "media-preview-card__visual";
  if (item.previewUrl) {
    visual.src = item.previewUrl;
  }

  const details = document.createElement("div");
  details.className = "media-preview-card__details";
  const name = document.createElement("strong");
  name.textContent = item.file.name;
  const size = document.createElement("span");
  size.textContent = formatFileSize(item.file.size);
  const progress = document.createElement("progress");
  progress.className = "media-upload-progress";
  progress.max = 100;
  progress.value = item.uploadId ? 100 : 0;
  progress.setAttribute("aria-label", `${item.file.name} 업로드 진행률`);
  const progressStatus = document.createElement("span");
  progressStatus.className = "media-upload-progress__label";
  progressStatus.textContent = item.uploadId ? "업로드 완료" : "업로드 전";
  details.append(name, size, progress, progressStatus);

  const removeButton = document.createElement("button");
  removeButton.className = "media-remove-button";
  removeButton.type = "button";
  removeButton.setAttribute("aria-label", removeLabel);
  removeButton.textContent = "삭제";
  removeButton.addEventListener("click", onRemove);

  card.append(visual, details, removeButton);
  return { element: card, progress, progressStatus, removeButton };
}

async function ensureMediaUploaded(
  item,
  { csrfToken, purpose, uploadsUrl, scoreChangeId },
) {
  const uploadContext = { csrfToken, uploadsUrl };
  if (item.isDiscarded) {
    throw new MediaUploadCancelledError();
  }
  if (item.uploadId) {
    updateUploadProgress(item, 100, "업로드 완료");
    return item.uploadId;
  }
  if (item.pendingUploadId) {
    await discardInitiatedMediaUpload(item, uploadContext);
  }

  updateUploadProgress(item, 2, "업로드를 준비하고 있어요…");
  const intentBody = {
    purpose,
    kind: item.file.type.startsWith("video/") ? "video" : "image",
    fileName: item.file.name,
    contentType: item.file.type,
    byteSize: item.file.size,
  };
  if (Number.isInteger(scoreChangeId) && scoreChangeId > 0) {
    intentBody.scoreChangeId = scoreChangeId;
  }
  const intentPayload = await requestJson(uploadsUrl, {
    cache: "no-store",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify(intentBody),
  });
  const intent = readUploadIntent(intentPayload);
  item.pendingUploadId = intent.uploadId;
  await stopIfMediaUploadDiscarded(item, uploadContext);

  updateUploadProgress(item, 5, "파일을 올리고 있어요…");
  const uploadAbortController =
    typeof AbortController === "function" ? new AbortController() : null;
  item.uploadAbortController = uploadAbortController;
  try {
    await putFileWithProgress(
      intent,
      item.file,
      (percentage) => {
        updateUploadProgress(
          item,
          Math.max(5, Math.min(92, percentage)),
          "파일을 올리고 있어요…",
        );
      },
      uploadAbortController?.signal,
    );
  } catch (error) {
    if (item.isDiscarded) {
      await stopIfMediaUploadDiscarded(item, uploadContext);
    }
    throw error;
  } finally {
    if (item.uploadAbortController === uploadAbortController) {
      item.uploadAbortController = null;
    }
  }
  await stopIfMediaUploadDiscarded(item, uploadContext);
  updateUploadProgress(item, 95, "업로드를 확인하고 있어요…");
  let completedPayload;
  try {
    completedPayload = await requestJson(
      mediaCompleteUrl(uploadsUrl, intent.uploadId),
      {
        cache: "no-store",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: "{}",
      },
    );
  } catch (error) {
    if (item.isDiscarded) {
      await stopIfMediaUploadDiscarded(item, uploadContext);
    }
    throw error;
  }
  readCompletedUpload(completedPayload, intent.uploadId);
  await stopIfMediaUploadDiscarded(item, uploadContext);
  item.uploadId = intent.uploadId;
  updateUploadProgress(item, 100, "업로드 완료");
  return item.uploadId;
}

function readUploadIntent(payload) {
  const intent = payload?.success;
  const headerEntries =
    intent?.requiredHeaders && typeof intent.requiredHeaders === "object"
      ? Object.entries(intent.requiredHeaders)
      : null;
  if (
    !intent ||
    !isUploadId(intent.uploadId) ||
    typeof intent.uploadUrl !== "string" ||
    !isHttpUrl(intent.uploadUrl) ||
    !headerEntries ||
    headerEntries.some(
      ([name, value]) =>
        typeof name !== "string" || !name || typeof value !== "string",
    ) ||
    typeof intent.expiresAt !== "string" ||
    Number.isNaN(Date.parse(intent.expiresAt))
  ) {
    throw new Error("업로드 준비 응답 형식이 올바르지 않습니다.");
  }
  return intent;
}

function readCompletedUpload(payload, uploadId) {
  const completed = payload?.success;
  if (
    !completed ||
    String(completed.id) !== String(uploadId) ||
    !["image", "video"].includes(completed.kind) ||
    typeof completed.fileName !== "string" ||
    typeof completed.contentType !== "string" ||
    !Number.isInteger(completed.byteSize) ||
    completed.byteSize <= 0
  ) {
    throw new Error("업로드 완료 응답 형식이 올바르지 않습니다.");
  }
  return completed;
}

function isUploadId(value) {
  return (
    (typeof value === "string" && value.length > 0 && value.length <= 200) ||
    (Number.isInteger(value) && value > 0)
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

function mediaCompleteUrl(uploadsUrl, uploadId) {
  return `${String(uploadsUrl).replace(/\/?$/, "/")}${encodeURIComponent(String(uploadId))}/complete/`;
}

function mediaDiscardUrl(uploadsUrl, uploadId) {
  return `${String(uploadsUrl).replace(/\/?$/, "/")}${encodeURIComponent(String(uploadId))}/discard/`;
}

function discardInitiatedMediaUpload(item, { csrfToken, uploadsUrl }) {
  const uploadId = item.pendingUploadId || item.uploadId;
  if (!uploadId) {
    return Promise.resolve();
  }
  if (item.discardedUploadId === uploadId) {
    if (item.uploadId === uploadId) {
      item.uploadId = null;
    }
    if (item.pendingUploadId === uploadId) {
      item.pendingUploadId = null;
    }
    return Promise.resolve();
  }
  if (item.discardPromise?.uploadId === uploadId) {
    return item.discardPromise.promise;
  }

  const promise = requestJson(mediaDiscardUrl(uploadsUrl, uploadId), {
    cache: "no-store",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    body: "{}",
  })
    .then(() => {
      item.discardedUploadId = uploadId;
      if (item.uploadId === uploadId) {
        item.uploadId = null;
      }
      if (item.pendingUploadId === uploadId) {
        item.pendingUploadId = null;
      }
    })
    .finally(() => {
      if (item.discardPromise?.promise === promise) {
        item.discardPromise = null;
      }
    });
  item.discardPromise = { promise, uploadId };
  return promise;
}

async function stopIfMediaUploadDiscarded(item, context) {
  if (!item.isDiscarded) {
    return;
  }
  try {
    await discardInitiatedMediaUpload(item, context);
  } catch (error) {
    redirectWhenAuthenticationExpired(error);
  }
  throw new MediaUploadCancelledError();
}

function putFileWithProgress(intent, file, onProgress, cancellationSignal = null) {
  onProgress(20);
  const controller =
    typeof AbortController === "function" ? new AbortController() : null;
  let didTimeOut = false;
  const cancelUpload = () => controller?.abort();
  if (cancellationSignal?.aborted) {
    cancelUpload();
  } else {
    cancellationSignal?.addEventListener?.("abort", cancelUpload, {
      once: true,
    });
  }
  const timeoutId =
    controller && typeof setTimeout === "function"
      ? setTimeout(() => {
          didTimeOut = true;
          controller.abort();
        }, MEDIA_UPLOAD_TIMEOUT_MS)
      : null;
  return fetch(intent.uploadUrl, {
    method: "PUT",
    headers: intent.requiredHeaders,
    body: file,
    redirect: "error",
    credentials: "omit",
    cache: "no-store",
    ...(controller
      ? { signal: controller.signal }
      : cancellationSignal
        ? { signal: cancellationSignal }
        : {}),
  })
    .then((response) => {
      if (!response.ok || response.redirected) {
        throw new Error("파일 저장소가 업로드를 거부했습니다.");
      }
      onProgress(92);
    })
    .catch((error) => {
      if (cancellationSignal?.aborted) {
        throw new MediaUploadCancelledError();
      }
      if (didTimeOut) {
        throw new Error(
          "파일 업로드 시간이 초과되었습니다. 다시 시도해 주세요.",
        );
      }
      throw error;
    })
    .finally(() => {
      cancellationSignal?.removeEventListener?.("abort", cancelUpload);
      if (timeoutId !== null && typeof clearTimeout === "function") {
        clearTimeout(timeoutId);
      }
    });
}

function updateUploadProgress(item, value, label) {
  if (item.progress) {
    item.progress.value = value;
  }
  if (item.progressStatus) {
    item.progressStatus.textContent = label;
  }
}

function markUploadFailed(item) {
  item.uploadId = null;
  item.uploadState = "failed";
  updateUploadProgress(item, 0, "업로드 실패 · 다시 시도해 주세요");
}

function createPreviewUrl(file) {
  try {
    return typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : "";
  } catch {
    return "";
  }
}

function revokePreviewUrl(url) {
  if (!url) {
    return;
  }
  try {
    URL.revokeObjectURL?.(url);
  } catch {
    return;
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))}KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
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

class MediaUploadError extends Error {
  constructor(message, cause) {
    super(message);
    this.name = "MediaUploadError";
    this.cause = cause;
  }
}

class MediaUploadCancelledError extends Error {
  constructor() {
    super("파일 업로드가 취소되었습니다.");
    this.name = "MediaUploadCancelledError";
  }
}

function unwrapMediaUploadError(error) {
  return error instanceof MediaUploadError ? error.cause : error;
}

function shouldResetMediaUploads(error) {
  return (
    error instanceof ApiRequestError &&
    ["MEDIA_UPLOAD_CONFLICT", "NOT_FOUND", "PERMISSION_DENIED"].includes(
      error.apiError?.errorCode,
    )
  );
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
    /^\/history\/[1-9]\d*\/$/.test(change.threadUrl) &&
    validateAttachmentList(change.attachments, "scoreChange")
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
    contentLength < 0 ||
    contentLength > 500 ||
    typeof comment.createdAt !== "string" ||
    Number.isNaN(Date.parse(comment.createdAt)) ||
    typeof comment.isMine !== "boolean" ||
    !validateAttachmentList(comment.attachments, "comment") ||
    (contentLength === 0 && attachmentItems(comment).length === 0)
  ) {
    throw new Error("댓글 응답 형식이 올바르지 않습니다.");
  }
}

function validateAttachmentList(value, owner) {
  if (value === undefined) {
    return true;
  }
  if (!Array.isArray(value) || !value.every(validateAttachment)) {
    return false;
  }
  const imageCount = value.filter((attachment) => attachment.kind === "image").length;
  const videoCount = value.filter((attachment) => attachment.kind === "video").length;
  if (owner === "scoreChange") {
    return value.length <= 1 && videoCount === 0;
  }
  return (
    (videoCount === 0 && imageCount <= 4) ||
    (videoCount === 1 && imageCount === 0 && value.length === 1)
  );
}

function validateAttachment(attachment) {
  const validId =
    (Number.isInteger(attachment?.id) && attachment.id > 0) ||
    (typeof attachment?.id === "string" && attachment.id.length > 0);
  const maximumBytes = attachment?.kind === "video" ? 100 * 1024 * 1024 : 10 * 1024 * 1024;
  return (
    validId &&
    ["image", "video"].includes(attachment.kind) &&
    typeof attachment.fileName === "string" &&
    [...attachment.fileName].length > 0 &&
    [...attachment.fileName].length <= 255 &&
    typeof attachment.contentType === "string" &&
    (attachment.kind === "image"
      ? ["image/jpeg", "image/png", "image/webp"].includes(attachment.contentType)
      : ["video/mp4", "video/webm", "video/quicktime"].includes(
          attachment.contentType,
        )) &&
    Number.isInteger(attachment.byteSize) &&
    attachment.byteSize > 0 &&
    attachment.byteSize <= maximumBytes &&
    typeof attachment.contentUrl === "string" &&
    isHttpUrl(attachment.contentUrl)
  );
}

function attachmentItems(owner) {
  return Array.isArray(owner?.attachments) ? owner.attachments : [];
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

  const attachments = createAttachmentGallery(attachmentItems(change), {
    label: "점수 변경에 첨부된 파일",
  });
  if (attachments) {
    children.push(attachments);
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
  article.append(header);
  if (comment.content) {
    const body = document.createElement("p");
    body.textContent = comment.content;
    article.append(body);
  }
  const attachments = createAttachmentGallery(attachmentItems(comment), {
    label: "댓글에 첨부된 파일",
  });
  if (attachments) {
    article.append(attachments);
  }
  item.append(article);
  return item;
}

function createAttachmentGallery(attachments, { label }) {
  if (attachments.length === 0) {
    return null;
  }
  const gallery = document.createElement("div");
  gallery.className = `attachment-gallery${attachments.length === 1 ? " attachment-gallery--single" : ""}`;
  gallery.setAttribute("aria-label", label);
  attachments.forEach((attachment) => {
    gallery.append(createAttachment(attachment));
  });
  return gallery;
}

function createAttachment(attachment) {
  const container = document.createElement("figure");
  container.className = `attachment attachment--${attachment.kind}`;
  const contentUrl = new URL(
    attachment.contentUrl,
    window.location.origin,
  ).href;
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
    if (scoreThreadAuthenticationRedirectStarted) {
      return true;
    }
    scoreThreadAuthenticationRedirectStarted = true;
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/login/?next=${encodeURIComponent(next)}`);
    return true;
  }
  return false;
}
