const diaryRoot = document.querySelector("[data-diary-root]");
const DIARY_PAGE_SIZE = 20;
const MAX_DIARY_CONTENT_LENGTH = 1000;
const MEDIA_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000;
const MAX_DIARY_IMAGE_ATTACHMENTS = 4;
const MAX_DIARY_VIDEO_ATTACHMENTS = 1;
const MAX_IMAGE_SIZE = 10 * 1024 * 1024;
const MAX_VIDEO_SIZE = 100 * 1024 * 1024;
const IMAGE_CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp"];
const VIDEO_CONTENT_TYPES = ["video/mp4", "video/webm", "video/quicktime"];
let diaryAuthenticationRedirectStarted = false;

if (diaryRoot) {
  initializeDiary(diaryRoot);
}

function initializeDiary(root) {
  const pageNumber = readPageNumber(window.location.search);
  const createForm = root.querySelector("[data-diary-create-form]");
  const createSubmit = root.querySelector("[data-diary-create-submit]");
  const createSubmitLabel = root.querySelector(
    "[data-diary-create-submit-label]",
  );
  const contentInput = createForm?.querySelector("[name=content]");
  const characterCount = createForm?.querySelector(
    "[data-diary-character-current]",
  );
  const createStatus = createForm?.querySelector("[data-diary-create-status]");
  const createFieldErrors = {
    content: createForm?.querySelector('[data-diary-error-for="content"]'),
  };
  const createMedia = createDiaryMediaController({
    csrfToken: getCsrfToken(createForm),
    input: createForm?.querySelector("[data-diary-media-input]"),
    purpose: "diaryEntry",
    selection: createForm?.querySelector("[data-diary-media-selection]"),
    status: createForm?.querySelector("[data-diary-media-status]"),
    uploadsUrl: root.dataset.mediaUploadsUrl,
  });
  let isCreating = false;
  let creationRequiresRefresh = false;
  let renderedResultCount = 0;
  let shouldFocusAfterMutation = false;

  const loadPage = ({ protectActiveItems = false } = {}) =>
    loadDiaryPage(root, pageNumber, {
      csrfToken: getCsrfToken(createForm),
      consumeFocusAfterMutation: () => {
        const shouldFocus = shouldFocusAfterMutation;
        shouldFocusAfterMutation = false;
        return shouldFocus;
      },
      onEntryChanged: () => {
        shouldFocusAfterMutation = true;
        return loadPage();
      },
      onEntryDeleted: () => {
        if (pageNumber > 1 && renderedResultCount <= 1) {
          window.location.assign(pageHref(pageNumber - 1));
          return Promise.resolve();
        }
        shouldFocusAfterMutation = true;
        return loadPage();
      },
      onResultCount(count) {
        renderedResultCount = count;
      },
      protectActiveItems,
      uploadsUrl: root.dataset.mediaUploadsUrl || "",
    });

  if (
    createForm &&
    createSubmit &&
    createSubmitLabel &&
    contentInput &&
    characterCount &&
    createStatus &&
    createFieldErrors.content
  ) {
    contentInput.disabled = false;
    createSubmit.disabled = false;
    createMedia?.setDisabled(false);
    updateCharacterCount(contentInput, characterCount);

    contentInput.addEventListener("input", () => {
      clearFieldError(contentInput, createFieldErrors.content);
      updateCharacterCount(contentInput, characterCount);
    });

    createForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (isCreating || creationRequiresRefresh) {
        return;
      }

      clearDiaryFormFeedback({
        fields: {
          content: [contentInput, createFieldErrors.content],
        },
        status: createStatus,
      });
      const command = readDiaryCommand({
        contentInput,
        fields: {
          content: [contentInput, createFieldErrors.content],
        },
        status: createStatus,
      });
      if (!command) {
        return;
      }

      isCreating = true;
      let shouldUnlockCreation = true;
      setCreateFormDisabled({
        contentInput,
        disabled: true,
        form: createForm,
        media: createMedia,
        submit: createSubmit,
      });
      createSubmitLabel.textContent = "남기고 있어요…";

      try {
        if (createMedia?.hasNewFiles()) {
          createSubmitLabel.textContent = "첨부 파일을 올리고 있어요…";
          command.mediaUploadIds = await createMedia.upload({
            csrfToken: getCsrfToken(createForm),
            purpose: "diaryEntry",
          });
          createSubmitLabel.textContent = "남기고 있어요…";
        }
        const payload = await requestJson(root.dataset.diaryEntriesUrl, {
          body: JSON.stringify(command),
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(createForm),
          },
          method: "POST",
        });
        readDiaryEntry(payload?.success);
        contentInput.value = "";
        createMedia?.clear({ discardUploads: false });
        updateCharacterCount(contentInput, characterCount);
        showFormStatus(createStatus, "일기를 남겼어요.", "success");
        showDiaryToast("우리 일기에 새 이야기를 남겼어요.", "success");
        if (pageNumber > 1) {
          window.location.assign(pageHref(1));
        } else {
          await loadPage();
        }
      } catch (error) {
        const underlyingError = unwrapMediaUploadError(error);
        if (redirectWhenAuthenticationExpired(underlyingError)) {
          return;
        }
        if (error instanceof MediaUploadError) {
          showFormStatus(createStatus, error.message, "error");
        } else {
          if (shouldResetMediaUploads(error)) {
            createMedia?.resetUploads();
          }
          shouldUnlockCreation = !diaryMutationRequiresRefresh(error);
          creationRequiresRefresh = !shouldUnlockCreation;
          if (creationRequiresRefresh) {
            showFormStatus(
              createStatus,
              "저장 결과를 확인하지 못했어요. 새로고침해 우리 일기를 확인해 주세요.",
              "error",
            );
          } else {
            showDiaryMutationError(error, {
              fields: {
                content: [contentInput, createFieldErrors.content],
              },
              fallback: "일기를 남기지 못했어요. 잠시 후 다시 시도해 주세요.",
              status: createStatus,
            });
          }
        }
      } finally {
        isCreating = false;
        setCreateFormDisabled({
          contentInput,
          disabled: false,
          form: createForm,
          media: createMedia,
          submit: createSubmit,
        });
        createMedia?.setDisabled(!shouldUnlockCreation);
        createSubmit.disabled = !shouldUnlockCreation;
        createSubmitLabel.textContent = shouldUnlockCreation
          ? "일기 남기기"
          : "새로고침 후 확인";
      }
    });
  }

  root.querySelector("[data-diary-focus-compose]")?.addEventListener(
    "click",
    () => {
      createForm?.scrollIntoView?.({ behavior: "smooth", block: "center" });
      contentInput?.focus();
    },
  );
  const refreshPageSafely = () => {
    if (!hasActiveDiaryItem(root)) {
      loadPage({ protectActiveItems: true }).catch(() => undefined);
    }
  };
  document.addEventListener?.("woorisai:push-message", (event) => {
    if (isLocalDiaryThreadLink(event?.detail?.threadLink)) {
      refreshPageSafely();
    }
  });
  document.addEventListener?.("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshPageSafely();
    }
  });
  globalThis.addEventListener?.("pageshow", (event) => {
    if (event.persisted) {
      refreshPageSafely();
    }
  });

  loadPage().catch(() => undefined);
}

async function loadDiaryPage(root, pageNumber, callbacks) {
  const loadSequence = Number(root.dataset.loadSequence || "0") + 1;
  root.dataset.loadSequence = String(loadSequence);
  const content = root.querySelector("[data-diary-content]");
  const status = root.querySelector("[data-diary-list-status]");
  const list = root.querySelector("[data-diary-list]");
  const empty = root.querySelector("[data-diary-empty]");
  const pagination = root.querySelector("[data-diary-pagination]");
  if (!content || !status || !list || !empty || !pagination) {
    return;
  }

  const hasRenderedContent = !list.hidden || !empty.hidden;
  content.setAttribute("aria-busy", "true");
  status.className = "surface loading-state";
  status.hidden = false;
  status.textContent = "우리 일기를 불러오고 있어요…";
  if (!hasRenderedContent) {
    list.hidden = true;
    empty.hidden = true;
    pagination.hidden = true;
  }

  try {
    const url = new URL(root.dataset.diaryEntriesUrl, window.location.origin);
    url.searchParams.set("pageNumber", String(pageNumber));
    const payload = await requestJson(url, { cache: "no-store" });
    const page = readDiaryPage(payload);
    if (root.dataset.loadSequence !== String(loadSequence)) {
      return;
    }
    if (callbacks.protectActiveItems && hasActiveDiaryItem(root)) {
      status.hidden = true;
      status.replaceChildren();
      return;
    }
    callbacks.onResultCount(page.results.length);
    renderDiaryPage({
      callbacks,
      empty,
      list,
      page,
      pagination,
      root,
      status,
    });
  } catch (error) {
    if (
      root.dataset.loadSequence !== String(loadSequence) ||
      redirectWhenAuthenticationExpired(error)
    ) {
      return;
    }
    renderDiaryLoadError(status, pageNumber, error, () =>
      loadDiaryPage(root, pageNumber, callbacks),
    );
  } finally {
    if (root.dataset.loadSequence === String(loadSequence)) {
      content.setAttribute("aria-busy", "false");
    }
  }
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

class DirectPutResponseError extends Error {
  constructor(status) {
    super("파일 저장소가 업로드를 거부했습니다.");
    this.name = "DirectPutResponseError";
    this.status = status;
  }
}

function createDiaryMediaController({
  csrfToken,
  existingAttachments = [],
  input,
  purpose,
  selection,
  status,
  uploadsUrl,
}) {
  if (!input || !selection || !status || !uploadsUrl) {
    return null;
  }

  let retainedAttachments = [...existingAttachments];
  let retainedRemoveButtons = [];
  let items = [];
  let isDisabled = true;
  let hasChanges = false;
  const backgroundUploadContext = {
    csrfToken,
    purpose,
    uploadsUrl,
  };

  const setStatus = (message, state = "") => {
    status.textContent = message;
    status.classList.toggle("media-status--error", state === "error");
    status.classList.toggle("media-status--success", state === "success");
  };

  const currentSelection = () => [
    ...retainedAttachments,
    ...items.map((item) => item.file),
  ];

  const selectionMessage = () => {
    const selected = currentSelection();
    if (selected.length === 0) {
      return "";
    }
    const hasVideo = selected.some((value) =>
      VIDEO_CONTENT_TYPES.includes(mediaContentType(value)),
    );
    return hasVideo
      ? "영상 1개를 선택했어요."
      : `사진 ${selected.length}장을 선택했어요.`;
  };

  const updateSelectionStatus = () => {
    if (items.some((item) => item.uploadState === "uploading")) {
      setStatus("첨부 파일을 올리고 있어요…");
      return;
    }
    if (items.some((item) => item.uploadState === "failed")) {
      setStatus(
        "첨부 파일을 업로드하지 못했어요. 일기를 저장할 때 다시 시도해 주세요.",
        "error",
      );
      return;
    }
    if (items.length > 0 && items.every((item) => item.uploadId)) {
      setStatus("첨부 파일 업로드를 마쳤어요.", "success");
      return;
    }
    setStatus(selectionMessage());
  };

  const uploadFailureMessage = (error) => {
    const apiError = error instanceof ApiRequestError ? error : null;
    return apiError?.apiError?.errorCode === "CSRF_FAILED"
      ? "보안 토큰이 만료되었어요. 페이지를 새로고침한 뒤 다시 시도해 주세요."
      : apiError?.apiError?.reason ||
          "첨부 파일을 업로드하지 못했어요. 잠시 후 다시 시도해 주세요.";
  };

  const startItemUpload = (item, context) => {
    if (item.isDiscarded) {
      return Promise.reject(new MediaUploadCancelledError());
    }
    if (item.uploadPromise && !item.needsFreshIntent) {
      return item.uploadPromise;
    }
    if (item.uploadId && !item.needsFreshIntent) {
      updateUploadProgress(item, 100, "업로드 완료");
      return Promise.resolve(item.uploadId);
    }

    item.uploadContext = context;
    item.uploadState = "uploading";
    updateSelectionStatus();
    const uploadPromise = ensureMediaUploaded(item, context)
      .then((uploadId) => {
        item.uploadState = "uploaded";
        return uploadId;
      })
      .catch((error) => {
        if (!item.isDiscarded && !(error instanceof MediaUploadCancelledError)) {
          if (uploadIntentCannotBeRetried(error)) {
            item.needsFreshIntent = true;
          }
          markUploadFailed(item);
        }
        if (item.uploadPromise === uploadPromise) {
          item.uploadPromise = null;
        }
        throw error;
      })
      .finally(() => {
        if (items.includes(item)) {
          updateSelectionStatus();
        }
      });
    item.uploadPromise = uploadPromise;
    uploadPromise.catch(() => undefined);
    return uploadPromise;
  };

  const startBackgroundUpload = (item) => {
    startItemUpload(item, backgroundUploadContext).catch((error) => {
      if (!items.includes(item) || error instanceof MediaUploadCancelledError) {
        return;
      }
      if (redirectWhenAuthenticationExpired(error)) {
        return;
      }
      setStatus(uploadFailureMessage(error), "error");
    });
  };

  const abandonItem = (item) => {
    item.isDiscarded = true;
    item.uploadState = "cancelled";
    item.uploadAbortController?.abort();
    const context = item.uploadContext || backgroundUploadContext;
    if (item.intent || item.uploadId) {
      discardDiaryMediaUpload(item, context).catch((error) => {
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
    retainedAttachments = [];
    hasChanges = false;
    input.value = "";
    selection.replaceChildren();
    selection.hidden = true;
    setStatus("");
  };

  const render = () => {
    retainedRemoveButtons = [];
    const retainedPreviews = retainedAttachments.map((attachment) => {
      const preview = createExistingAttachmentPreview(attachment, {
        disabled: isDisabled,
        onRemove: () => {
          if (isDisabled) {
            return;
          }
          retainedAttachments = retainedAttachments.filter(
            (candidate) => candidate.id !== attachment.id,
          );
          hasChanges = true;
          render();
          updateSelectionStatus();
        },
      });
      retainedRemoveButtons.push(preview.removeButton);
      return preview.element;
    });
    const newPreviews = items.map((item) => {
      const preview = createUploadPreview(item, {
        onRemove: () => {
          if (isDisabled) {
            return;
          }
          revokePreviewUrl(item.previewUrl);
          items = items.filter((candidate) => candidate !== item);
          abandonItem(item);
          hasChanges = true;
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
    selection.replaceChildren(...retainedPreviews, ...newPreviews);
    selection.hidden = retainedPreviews.length + newPreviews.length === 0;
  };

  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    input.value = "";
    if (files.length === 0) {
      return;
    }
    const error = validateDiaryMediaSelection([...currentSelection(), ...files]);
    if (error) {
      setStatus(error, "error");
      return;
    }
    const newItems = files.map(createUploadItem);
    items.push(...newItems);
    hasChanges = true;
    render();
    newItems.forEach(startBackgroundUpload);
  });

  render();

  return {
    clear,
    destroy: clear,
    hasChanges() {
      return hasChanges;
    },
    hasNewFiles() {
      return items.length > 0;
    },
    resetUploads() {
      items.forEach((item) => {
        item.uploadPromise = null;
        item.needsFreshIntent = Boolean(item.intent || item.uploadId);
        markUploadFailed(item);
      });
      if (items.length > 0) {
        setStatus("첨부 파일을 다시 업로드해 주세요.", "error");
      }
    },
    setDisabled(disabled) {
      isDisabled = disabled;
      input.disabled = disabled;
      retainedRemoveButtons.forEach((button) => {
        button.disabled = disabled;
      });
      items.forEach((item) => {
        if (item.removeButton) {
          item.removeButton.disabled = disabled;
        }
      });
    },
    async upload({ csrfToken, purpose }) {
      const selectedItems = [...items];
      try {
        const uploadedIds = await Promise.all(
          selectedItems.map((item) =>
            startItemUpload(item, {
              csrfToken,
              purpose,
              uploadsUrl,
            }),
          ),
        );
        updateSelectionStatus();
        return [
          ...retainedAttachments.map((attachment) => attachment.id),
          ...uploadedIds.filter((_, index) =>
            items.includes(selectedItems[index]),
          ),
        ];
      } catch (error) {
        const message = uploadFailureMessage(error);
        setStatus(message, "error");
        throw new MediaUploadError(message, error);
      }
    },
  };
}

function validateDiaryMediaSelection(values) {
  for (const value of values) {
    const contentType = mediaContentType(value);
    const byteSize = mediaByteSize(value);
    if (
      !IMAGE_CONTENT_TYPES.includes(contentType) &&
      !VIDEO_CONTENT_TYPES.includes(contentType)
    ) {
      return "JPG, PNG, WebP 사진이나 MP4, WebM, MOV 영상을 선택해 주세요.";
    }
    const maximumBytes = VIDEO_CONTENT_TYPES.includes(contentType)
      ? MAX_VIDEO_SIZE
      : MAX_IMAGE_SIZE;
    if (!Number.isFinite(byteSize) || byteSize < 1 || byteSize > maximumBytes) {
      return VIDEO_CONTENT_TYPES.includes(contentType)
        ? "영상은 100MB 이하의 파일만 올릴 수 있어요."
        : "사진은 한 장당 10MB 이하의 파일만 올릴 수 있어요.";
    }
  }
  const videoCount = values.filter((value) =>
    VIDEO_CONTENT_TYPES.includes(mediaContentType(value)),
  ).length;
  if (
    videoCount > 0 &&
    (videoCount > MAX_DIARY_VIDEO_ATTACHMENTS || values.length > videoCount)
  ) {
    return "사진과 영상은 함께 올릴 수 없고, 영상은 한 개만 선택할 수 있어요.";
  }
  if (videoCount === 0 && values.length > MAX_DIARY_IMAGE_ATTACHMENTS) {
    return "사진은 한 일기에 최대 4장까지 올릴 수 있어요.";
  }
  return "";
}

function mediaContentType(value) {
  return typeof value?.type === "string" ? value.type : value?.contentType;
}

function mediaByteSize(value) {
  return Number.isFinite(value?.size) ? value.size : value?.byteSize;
}

function createUploadItem(file) {
  return {
    discardPromise: null,
    discardedUploadId: null,
    file,
    intent: null,
    isDiscarded: false,
    isPutComplete: false,
    needsFreshIntent: false,
    previewUrl: createPreviewUrl(file),
    progress: null,
    progressStatus: null,
    removeButton: null,
    uploadAbortController: null,
    uploadContext: null,
    uploadId: null,
    uploadProgressLabel: "업로드 전",
    uploadProgressValue: 0,
    uploadPromise: null,
    uploadState: "pending",
  };
}

function createUploadPreview(item, { onRemove, removeLabel }) {
  const card = document.createElement("article");
  card.className = "media-preview-card";
  let visual;
  if (VIDEO_CONTENT_TYPES.includes(item.file.type)) {
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
  progress.value = item.uploadProgressValue;
  progress.setAttribute("aria-label", `${item.file.name} 업로드 진행률`);
  const progressStatus = document.createElement("span");
  progressStatus.className = "media-upload-progress__label";
  progressStatus.textContent = item.uploadProgressLabel;
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

function createExistingAttachmentPreview(attachment, { disabled, onRemove }) {
  const card = document.createElement("article");
  card.className = "media-preview-card";
  const visual = createAttachmentVisual(attachment, { isPreview: true });
  const details = document.createElement("div");
  details.className = "media-preview-card__details";
  const name = document.createElement("strong");
  name.textContent = attachment.fileName;
  const size = document.createElement("span");
  size.textContent = formatFileSize(attachment.byteSize);
  const state = document.createElement("span");
  state.className = "media-upload-progress__label";
  state.textContent = "현재 첨부됨";
  details.append(name, size, state);
  const removeButton = document.createElement("button");
  removeButton.className = "media-remove-button";
  removeButton.type = "button";
  removeButton.disabled = disabled;
  removeButton.setAttribute("aria-label", `${attachment.fileName} 삭제`);
  removeButton.textContent = "삭제";
  removeButton.addEventListener("click", onRemove);
  card.append(visual, details, removeButton);
  return { element: card, removeButton };
}

async function ensureMediaUploaded(item, { csrfToken, purpose, uploadsUrl }) {
  const discardContext = { csrfToken, uploadsUrl };
  if (item.isDiscarded) {
    throw new MediaUploadCancelledError();
  }
  if (item.uploadId && !item.needsFreshIntent) {
    updateUploadProgress(item, 100, "업로드 완료");
    return item.uploadId;
  }

  if (
    item.intent &&
    !item.isPutComplete &&
    Date.parse(item.intent.expiresAt) <= Date.now()
  ) {
    item.needsFreshIntent = true;
  }

  if (item.needsFreshIntent) {
    if (item.intent || item.uploadId) {
      await discardDiaryMediaUpload(item, discardContext);
    }
    if (item.isDiscarded) {
      throw new MediaUploadCancelledError();
    }
    resetUploadState(item);
  }

  if (!item.intent) {
    updateUploadProgress(item, 2, "업로드를 준비하고 있어요…");
    const intentPayload = await requestJson(uploadsUrl, {
      body: JSON.stringify({
        purpose,
        kind: VIDEO_CONTENT_TYPES.includes(item.file.type) ? "video" : "image",
        fileName: item.file.name,
        contentType: item.file.type,
        byteSize: item.file.size,
      }),
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      method: "POST",
    });
    item.intent = readUploadIntent(intentPayload);
    await stopIfDiaryMediaUploadDiscarded(item, discardContext);
  }

  const intent = item.intent;
  if (!item.isPutComplete) {
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
        { signal: uploadAbortController?.signal },
      );
    } catch (error) {
      if (item.isDiscarded) {
        await stopIfDiaryMediaUploadDiscarded(item, discardContext);
      }
      throw error;
    } finally {
      if (item.uploadAbortController === uploadAbortController) {
        item.uploadAbortController = null;
      }
    }
    item.isPutComplete = true;
  }
  await stopIfDiaryMediaUploadDiscarded(item, discardContext);
  updateUploadProgress(item, 95, "업로드를 확인하고 있어요…");
  let completedPayload;
  try {
    completedPayload = await requestJson(
      mediaCompleteUrl(uploadsUrl, intent.uploadId),
      {
        body: "{}",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        method: "POST",
      },
    );
  } catch (error) {
    if (item.isDiscarded) {
      await stopIfDiaryMediaUploadDiscarded(item, discardContext);
    }
    throw error;
  }
  readCompletedUpload(completedPayload, intent.uploadId);
  item.uploadId = intent.uploadId;
  await stopIfDiaryMediaUploadDiscarded(item, discardContext);
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
    !isAttachmentId(intent.uploadId) ||
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

function isAttachmentId(value) {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    )
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
  const collectionUrl = String(uploadsUrl).replace(/\/?$/, "/");
  return `${collectionUrl}${encodeURIComponent(String(uploadId))}/complete/`;
}

function mediaDiscardUrl(uploadsUrl, uploadId) {
  const collectionUrl = String(uploadsUrl).replace(/\/?$/, "/");
  return `${collectionUrl}${encodeURIComponent(String(uploadId))}/discard/`;
}

function discardDiaryMediaUpload(item, { csrfToken, uploadsUrl }) {
  const uploadId = item.intent?.uploadId || item.uploadId;
  if (!uploadId) {
    return Promise.resolve();
  }
  if (item.discardedUploadId === uploadId) {
    if (item.intent?.uploadId === uploadId) {
      item.intent = null;
      item.isPutComplete = false;
    }
    if (item.uploadId === uploadId) {
      item.uploadId = null;
    }
    return Promise.resolve();
  }
  if (item.discardPromise?.uploadId === uploadId) {
    return item.discardPromise.promise;
  }

  const promise = requestJson(mediaDiscardUrl(uploadsUrl, uploadId), {
    body: "{}",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    method: "POST",
  })
    .then(() => {
      item.discardedUploadId = uploadId;
      if (item.intent?.uploadId === uploadId) {
        item.intent = null;
        item.isPutComplete = false;
      }
      if (item.uploadId === uploadId) {
        item.uploadId = null;
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

async function stopIfDiaryMediaUploadDiscarded(item, context) {
  if (!item.isDiscarded) {
    return;
  }
  try {
    await discardDiaryMediaUpload(item, context);
  } catch (error) {
    redirectWhenAuthenticationExpired(error);
  }
  throw new MediaUploadCancelledError();
}

function putFileWithProgress(intent, file, onProgress, { signal } = {}) {
  if (signal?.aborted) {
    return Promise.reject(new MediaUploadCancelledError());
  }
  onProgress(20);
  const controller =
    typeof AbortController === "function" ? new AbortController() : null;
  let didTimeOut = false;
  const abortUpload = () => controller?.abort();
  signal?.addEventListener?.("abort", abortUpload, { once: true });
  const timeoutId =
    controller && typeof setTimeout === "function"
      ? setTimeout(() => {
          didTimeOut = true;
          controller.abort();
        }, MEDIA_UPLOAD_TIMEOUT_MS)
      : null;
  return fetch(intent.uploadUrl, {
    body: file,
    cache: "no-store",
    credentials: "omit",
    headers: intent.requiredHeaders,
    method: "PUT",
    redirect: "error",
    ...(controller?.signal || signal
      ? { signal: controller?.signal || signal }
      : {}),
  })
    .then((response) => {
      if (response.redirected) {
        throw new Error("파일 저장소가 업로드를 다른 위치로 보냈습니다.");
      }
      if (!response.ok) {
        throw new DirectPutResponseError(response.status);
      }
      onProgress(92);
    })
    .catch((error) => {
      if (signal?.aborted) {
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
      signal?.removeEventListener?.("abort", abortUpload);
      if (timeoutId !== null && typeof clearTimeout === "function") {
        clearTimeout(timeoutId);
      }
    });
}

function updateUploadProgress(item, value, label) {
  item.uploadProgressValue = value;
  item.uploadProgressLabel = label;
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
  updateUploadProgress(
    item,
    item.isPutComplete ? 95 : 0,
    item.isPutComplete
      ? "업로드 확인 실패 · 다시 시도해 주세요"
      : "업로드 실패 · 다시 시도해 주세요",
  );
}

function resetUploadState(item) {
  item.intent = null;
  item.isPutComplete = false;
  item.needsFreshIntent = false;
  item.uploadId = null;
  item.uploadState = "pending";
  updateUploadProgress(item, 0, "업로드 전");
}

function uploadIntentCannotBeRetried(error) {
  if (error instanceof DirectPutResponseError) {
    return error.status >= 400 && error.status < 500;
  }
  if (!(error instanceof ApiRequestError)) {
    return false;
  }
  const errorCode = error.apiError?.errorCode;
  const reason = error.apiError?.reason || "";
  if (
    errorCode === "AUTHENTICATION_REQUIRED" ||
    errorCode === "CSRF_FAILED" ||
    errorCode === "MEDIA_UPLOADS_UNAVAILABLE" ||
    error.status >= 500
  ) {
    return false;
  }
  if (
    errorCode === "MEDIA_UPLOAD_CONFLICT" &&
    reason.includes("파일을 확인하고 있어요")
  ) {
    return false;
  }
  return error.status >= 400 && error.status < 500;
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

function readPageNumber(search) {
  const parameters = new URLSearchParams(search);
  const value = parameters.get("pageNumber") ?? parameters.get("page") ?? "1";
  if (!/^[1-9]\d*$/.test(value)) {
    return 1;
  }
  const pageNumber = Number(value);
  return Number.isSafeInteger(pageNumber) ? pageNumber : 1;
}

function readDiaryPage(payload) {
  const results = payload?.success?.results;
  const paging = payload?.success?.paging;
  if (
    !Array.isArray(results) ||
    results.length > DIARY_PAGE_SIZE ||
    !paging ||
    !Number.isInteger(paging.pageNumber) ||
    paging.pageNumber < 1 ||
    paging.pageSize !== DIARY_PAGE_SIZE ||
    typeof paging.hasNext !== "boolean" ||
    !Number.isInteger(paging.totalCount) ||
    paging.totalCount < 0
  ) {
    throw new Error("우리 일기 응답 형식이 올바르지 않습니다.");
  }
  results.forEach(readDiaryEntry);
  return { paging, results };
}

function readDiaryEntry(entry) {
  if (
    !entry ||
    !Number.isInteger(entry.id) ||
    entry.id < 1 ||
    !isParticipantSummary(entry.author) ||
    typeof entry.content !== "string" ||
    entry.content.trim().length === 0 ||
    [...entry.content].length > MAX_DIARY_CONTENT_LENGTH ||
    !isTimestamp(entry.createdAt) ||
    (entry.updatedAt !== null && !isTimestamp(entry.updatedAt)) ||
    typeof entry.isMine !== "boolean" ||
    !Number.isInteger(entry.commentCount) ||
    entry.commentCount < 0 ||
    !isDiaryThreadUrl(entry.threadUrl, entry.id) ||
    !validateAttachmentList(entry.attachments)
  ) {
    throw new Error("우리 일기 항목 형식이 올바르지 않습니다.");
  }
  return entry;
}

function validateAttachmentList(value) {
  if (!Array.isArray(value)) {
    return false;
  }
  if (validateDiaryMediaSelection(value)) {
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
    (isImage || attachment?.kind === "video") &&
    typeof attachment.fileName === "string" &&
    [...attachment.fileName].length > 0 &&
    [...attachment.fileName].length <= 255 &&
    allowedTypes.includes(attachment.contentType) &&
    Number.isInteger(attachment.byteSize) &&
    attachment.byteSize > 0 &&
    attachment.byteSize <= (isImage ? MAX_IMAGE_SIZE : MAX_VIDEO_SIZE) &&
    isSameOriginContentUrl(attachment.contentUrl)
  );
}

function isSameOriginContentUrl(value) {
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

function isLocalDiaryThreadLink(value) {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const url = new URL(value, window.location.origin);
    return (
      url.origin === window.location.origin &&
      /^\/diary\/[1-9]\d*\/$/.test(url.pathname) &&
      url.search === "" &&
      url.hash === "" &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function hasActiveDiaryItem(root) {
  const list = root.querySelector("[data-diary-list]");
  return Array.from(list?.children || []).some(
    (item) => item.hasActiveDiaryItemState?.() === true,
  );
}

function attachmentItems(entry) {
  return Array.isArray(entry?.attachments) ? entry.attachments : [];
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

function renderDiaryPage({
  callbacks,
  empty,
  list,
  page,
  pagination,
  root,
  status,
}) {
  status.hidden = true;
  status.replaceChildren();
  Array.from(list.children).forEach((item) => item.disposeDiaryItem?.());
  if (page.results.length === 0) {
    list.replaceChildren();
    list.hidden = true;
    empty.hidden = false;
  } else {
    const entries = page.results.map((entry) =>
      createDiaryItem(entry, {
        collectionUrl: root.dataset.diaryEntriesUrl,
        csrfToken: callbacks.csrfToken,
        onEntryChanged: callbacks.onEntryChanged,
        onEntryDeleted: callbacks.onEntryDeleted,
        uploadsUrl: callbacks.uploadsUrl,
      }),
    );
    list.replaceChildren(...entries);
    list.hidden = false;
    empty.hidden = true;
  }
  renderPagination(pagination, page.paging);
  if (callbacks.consumeFocusAfterMutation()) {
    const focusTarget = page.results.length > 0 ? list : empty;
    focusTarget.setAttribute("tabindex", "-1");
    focusTarget.focus();
  }
}

function createDiaryItem(initialEntry, callbacks) {
  let entry = initialEntry;
  let isEditing = false;
  let isMutating = false;
  let deletionRequiresRefresh = false;
  let itemWasDisposed = false;
  let activeEditMedia = null;
  const item = document.createElement("li");
  item.className = "diary-list__item";
  const card = document.createElement("article");
  const releaseEditMedia = ({ discardUploads = true } = {}) => {
    const editMedia = activeEditMedia;
    activeEditMedia = null;
    editMedia?.destroy({ discardUploads });
  };
  item.disposeDiaryItem = () => {
    itemWasDisposed = true;
    if (!isMutating) {
      releaseEditMedia();
    }
  };
  item.hasActiveDiaryItemState = () => isEditing || isMutating;

  const renderCard = ({ focusEdit = false } = {}) => {
    isEditing = false;
    card.className = `surface diary-card${entry.isMine ? " diary-card--mine" : ""}`;
    card.id = `diary-entry-${entry.id}`;
    card.setAttribute("aria-busy", "false");
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

    let editButton = null;
    if (entry.isMine) {
      const actions = document.createElement("div");
      actions.className = "diary-card__actions";
      const edit = document.createElement("button");
      edit.className = "text-button";
      edit.type = "button";
      edit.textContent = "수정";
      edit.setAttribute(
        "aria-label",
        `${formatTimestamp(entry.createdAt)}에 게시한 일기 수정`,
      );
      edit.addEventListener("click", () => {
        if (!isMutating) {
          renderEditor();
        }
      });
      editButton = edit;
      const remove = document.createElement("button");
      remove.className = "text-button text-button--danger";
      remove.type = "button";
      remove.textContent = "삭제";
      remove.setAttribute(
        "aria-label",
        `${formatTimestamp(entry.createdAt)}에 게시한 일기 삭제`,
      );
      remove.addEventListener("click", () => {
        if (
          isMutating ||
          deletionRequiresRefresh ||
          !window.confirm(
            "이 일기를 삭제할까요? 삭제한 일기는 되돌릴 수 없어요.",
          )
        ) {
          return;
        }
        isMutating = true;
        card.setAttribute("aria-busy", "true");
        edit.disabled = true;
        remove.disabled = true;
        requestJson(diaryEntryUrl(callbacks.collectionUrl, entry.id), {
          headers: { "X-CSRFToken": callbacks.csrfToken },
          method: "DELETE",
        })
          .then((payload) => {
            if (payload.success !== null) {
              throw new Error("일기 삭제 응답 형식이 올바르지 않습니다.");
            }
            renderDeletedCard();
            showDiaryToast("일기를 삭제했어요.", "success");
            return callbacks.onEntryDeleted();
          })
          .catch((error) => {
            if (redirectWhenAuthenticationExpired(error)) {
              return;
            }
            if (diaryDeleteRequiresRefresh(error)) {
              deletionRequiresRefresh = true;
              renderDeletionUncertainCard();
              showDiaryToast(
                "삭제 결과를 확인하지 못했어요. 목록을 다시 확인할게요.",
                "error",
              );
              return callbacks.onEntryChanged();
            }
            showDiaryToast(
              error instanceof ApiRequestError
                ? error.message
                : "일기를 삭제하지 못했어요. 잠시 후 다시 시도해 주세요.",
              "error",
            );
          })
          .finally(() => {
            isMutating = false;
            card.setAttribute("aria-busy", "false");
            if (!deletionRequiresRefresh) {
              edit.disabled = false;
              remove.disabled = false;
            }
          });
      });
      actions.append(edit, remove);
      header.append(actions);
    }

    const body = document.createElement("p");
    body.className = "diary-card__content";
    body.textContent = entry.content;
    const children = [header, body];
    const attachments = createAttachmentGallery(attachmentItems(entry), {
      label: "일기에 첨부된 파일",
    });
    if (attachments) {
      children.push(attachments);
    }
    const footer = document.createElement("footer");
    if (entry.updatedAt !== null) {
      const editedAt = document.createElement("time");
      editedAt.dateTime = entry.updatedAt;
      editedAt.textContent = `${formatTimestamp(entry.updatedAt)} 수정`;
      footer.append(editedAt);
    }
    const threadLink = document.createElement("a");
    threadLink.className = "diary-card__thread-link";
    threadLink.href = entry.threadUrl;
    threadLink.textContent = `댓글 ${entry.commentCount}개 · 이야기 나누기`;
    footer.append(threadLink);
    children.push(footer);
    card.replaceChildren(...children);
    if (focusEdit) {
      editButton?.focus();
    }
  };

  const renderDeletedCard = () => {
    isEditing = false;
    card.className = "surface diary-card";
    card.setAttribute("aria-busy", "false");
    card.setAttribute("tabindex", "-1");
    const status = document.createElement("p");
    status.className = "loading-state";
    status.setAttribute("role", "status");
    status.textContent = "삭제한 일기예요.";
    card.replaceChildren(status);
    card.focus();
  };

  const renderDeletionUncertainCard = () => {
    isEditing = false;
    card.className = "surface diary-card";
    card.setAttribute("aria-busy", "true");
    card.setAttribute("tabindex", "-1");
    const status = document.createElement("p");
    status.className = "loading-state";
    status.setAttribute("role", "status");
    status.textContent =
      "삭제 결과를 확인하지 못했어요. 목록을 다시 확인하고 있어요…";
    card.replaceChildren(status);
    card.focus();
  };

  const renderEditor = () => {
    isEditing = true;
    const form = document.createElement("form");
    form.className = "diary-edit-form";
    form.setAttribute("aria-busy", "false");
    form.setAttribute(
      "aria-label",
      `${formatTimestamp(entry.createdAt)}에 게시한 일기 수정`,
    );

    const contentGroup = document.createElement("div");
    contentGroup.className = "field-group";
    const labelRow = document.createElement("div");
    labelRow.className = "field-label-row";
    const contentId = `diary-edit-content-${entry.id}`;
    const contentLabel = document.createElement("label");
    contentLabel.className = "field-label";
    contentLabel.htmlFor = contentId;
    contentLabel.textContent = "일기 내용";
    const count = document.createElement("span");
    count.className = "character-count";
    const current = document.createElement("span");
    count.append(current, document.createTextNode("/1000"));
    labelRow.append(contentLabel, count);
    const contentInput = document.createElement("textarea");
    contentInput.id = contentId;
    contentInput.name = "content";
    contentInput.required = true;
    contentInput.rows = 7;
    contentInput.value = entry.content;
    const contentError = createErrorList(`diary-edit-content-error-${entry.id}`);
    contentInput.setAttribute("aria-describedby", contentError.id);
    contentGroup.append(labelRow, contentInput, contentError);

    const status = document.createElement("p");
    status.className = "form-status";
    status.setAttribute("aria-live", "polite");
    status.setAttribute("role", "status");
    const actions = document.createElement("div");
    actions.className = "diary-edit-form__actions";
    const cancel = document.createElement("button");
    cancel.className = "button button--soft";
    cancel.type = "button";
    cancel.textContent = "취소";
    const save = document.createElement("button");
    save.className = "button button--primary";
    save.type = "submit";
    save.textContent = "수정 저장";
    actions.append(cancel, save);

    let mediaInput = null;
    let mediaSelection = null;
    let mediaStatus = null;
    if (callbacks.uploadsUrl) {
      const mediaGroup = document.createElement("div");
      mediaGroup.className = "field-group media-field diary-edit-media-field";
      const mediaLabelRow = document.createElement("div");
      mediaLabelRow.className = "field-label-row";
      const mediaLabelText = document.createElement("span");
      mediaLabelText.className = "field-label";
      mediaLabelText.textContent = "사진·영상 수정";
      const mediaLimit = document.createElement("span");
      mediaLimit.className = "media-limit";
      mediaLimit.textContent = "사진 4장 또는 영상 1개";
      mediaLabelRow.append(mediaLabelText, mediaLimit);

      const mediaInputId = `diary-edit-media-${entry.id}`;
      mediaInput = document.createElement("input");
      mediaInput.className = "media-file-input";
      mediaInput.id = mediaInputId;
      mediaInput.type = "file";
      mediaInput.accept =
        "image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime";
      mediaInput.multiple = true;
      const mediaHelpId = `diary-edit-media-help-${entry.id}`;
      const mediaStatusId = `diary-edit-media-status-${entry.id}`;
      mediaInput.setAttribute(
        "aria-describedby",
        `${mediaHelpId} ${mediaStatusId}`,
      );
      const mediaPicker = document.createElement("label");
      mediaPicker.className = "media-picker media-picker--compact";
      mediaPicker.htmlFor = mediaInputId;
      const mediaPickerIcon = document.createElement("span");
      mediaPickerIcon.setAttribute("aria-hidden", "true");
      mediaPickerIcon.textContent = "＋";
      mediaPicker.append(mediaPickerIcon, document.createTextNode("사진·영상 추가"));
      const mediaHelp = document.createElement("p");
      mediaHelp.className = "field-help";
      mediaHelp.id = mediaHelpId;
      mediaHelp.textContent =
        "사진은 최대 4장(장당 10MB), 영상은 1개(100MB)까지 올릴 수 있어요.";
      mediaSelection = document.createElement("div");
      mediaSelection.className = "media-selection";
      mediaSelection.hidden = true;
      mediaStatus = document.createElement("p");
      mediaStatus.className = "media-status";
      mediaStatus.id = mediaStatusId;
      mediaStatus.setAttribute("aria-live", "polite");
      mediaStatus.setAttribute("role", "status");
      mediaGroup.append(
        mediaLabelRow,
        mediaInput,
        mediaPicker,
        mediaHelp,
        mediaSelection,
        mediaStatus,
      );
      form.append(contentGroup, mediaGroup, status, actions);
    } else {
      form.append(contentGroup, status, actions);
    }
    card.className = "surface diary-card diary-card--mine diary-card--editing";
    card.replaceChildren(form);
    updateCharacterCount(contentInput, current);

    const editMedia = createDiaryMediaController({
      csrfToken: callbacks.csrfToken,
      existingAttachments: attachmentItems(entry),
      input: mediaInput,
      purpose: "diaryEntry",
      selection: mediaSelection,
      status: mediaStatus,
      uploadsUrl: callbacks.uploadsUrl,
    });
    activeEditMedia = editMedia;
    editMedia?.setDisabled(false);

    const fields = {
      content: [contentInput, contentError],
    };
    contentInput.addEventListener("input", () => {
      clearFieldError(contentInput, contentError);
      updateCharacterCount(contentInput, current);
    });
    cancel.addEventListener("click", () => {
      if (!isMutating) {
        releaseEditMedia();
        renderCard({ focusEdit: true });
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (isMutating) {
        return;
      }
      clearDiaryFormFeedback({ fields, status });
      const command = readDiaryCommand({
        contentInput,
        fields,
        status,
      });
      if (!command) {
        return;
      }

      isMutating = true;
      form.setAttribute("aria-busy", "true");
      contentInput.disabled = true;
      editMedia?.setDisabled(true);
      cancel.disabled = true;
      save.disabled = true;
      save.textContent = "저장하고 있어요…";
      try {
        if (editMedia?.hasChanges()) {
          if (editMedia.hasNewFiles()) {
            save.textContent = "첨부 파일을 올리고 있어요…";
          }
          command.mediaUploadIds = await editMedia.upload({
            csrfToken: callbacks.csrfToken,
            purpose: "diaryEntry",
          });
          save.textContent = "저장하고 있어요…";
        }
        const payload = await requestJson(
          diaryEntryUrl(callbacks.collectionUrl, entry.id),
          {
            body: JSON.stringify(command),
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": callbacks.csrfToken,
            },
            method: "PATCH",
          },
        );
        entry = readDiaryEntry(payload?.success);
        releaseEditMedia({ discardUploads: false });
        renderCard({ focusEdit: true });
        showDiaryToast("일기를 수정했어요.", "success");
        await callbacks.onEntryChanged();
      } catch (error) {
        const underlyingError = unwrapMediaUploadError(error);
        if (redirectWhenAuthenticationExpired(underlyingError)) {
          return;
        }
        if (error instanceof MediaUploadError) {
          showFormStatus(status, error.message, "error");
        } else {
          if (shouldResetMediaUploads(error)) {
            editMedia?.resetUploads();
          }
          showDiaryMutationError(error, {
            fallback: "일기를 수정하지 못했어요. 잠시 후 다시 시도해 주세요.",
            fields,
            status,
          });
        }
      } finally {
        if (itemWasDisposed && activeEditMedia) {
          releaseEditMedia();
        }
        isMutating = false;
        form.setAttribute("aria-busy", "false");
        contentInput.disabled = false;
        editMedia?.setDisabled(false);
        cancel.disabled = false;
        save.disabled = false;
        save.textContent = "수정 저장";
      }
    });
    contentInput.focus();
  };

  renderCard();
  item.append(card);
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
  attachments.forEach((attachment) => {
    gallery.append(createAttachment(attachment));
  });
  return gallery;
}

function createAttachment(attachment) {
  const container = document.createElement("figure");
  container.className = `attachment attachment--${attachment.kind}`;
  const media = createAttachmentVisual(attachment);
  const caption = document.createElement("figcaption");
  const download = document.createElement("a");
  download.className = "attachment-download";
  download.href = sameOriginContentHref(attachment.contentUrl);
  download.download = attachment.fileName;
  download.referrerPolicy = "no-referrer";
  download.textContent = `${attachment.fileName} 다운로드`;
  caption.append(download);
  container.append(media, caption);
  return container;
}

function createAttachmentVisual(attachment, { isPreview = false } = {}) {
  const contentUrl = sameOriginContentHref(attachment.contentUrl);
  let media;
  if (attachment.kind === "image") {
    media = document.createElement("img");
    media.alt = isPreview
      ? `현재 첨부된 사진: ${attachment.fileName}`
      : attachment.fileName;
    media.loading = "lazy";
    media.decoding = "async";
  } else {
    media = document.createElement("video");
    media.autoplay = false;
    media.controls = true;
    media.playsInline = true;
    media.preload = "metadata";
    media.setAttribute(
      "aria-label",
      isPreview
        ? `현재 첨부된 영상: ${attachment.fileName}`
        : attachment.fileName,
    );
    if (!isPreview) {
      const fallback = document.createElement("a");
      fallback.href = contentUrl;
      fallback.referrerPolicy = "no-referrer";
      fallback.textContent = "영상을 재생할 수 없으면 다운로드해 주세요.";
      media.append(fallback);
    }
  }
  media.src = contentUrl;
  media.referrerPolicy = "no-referrer";
  if (isPreview) {
    media.className = "media-preview-card__visual";
  }
  return media;
}

function sameOriginContentHref(value) {
  if (!isSameOriginContentUrl(value)) {
    throw new Error("첨부 파일 주소가 올바르지 않습니다.");
  }
  return new URL(value, window.location.origin).href;
}

function createErrorList(id) {
  const list = document.createElement("ul");
  list.className = "errorlist";
  list.id = id;
  list.hidden = true;
  return list;
}

function readDiaryCommand({ contentInput, fields, status }) {
  const content = contentInput.value.trim();
  let isValid = true;
  if (!content) {
    setFieldError(...fields.content, "일기 내용을 입력해 주세요.");
    isValid = false;
  } else if ([...content].length > MAX_DIARY_CONTENT_LENGTH) {
    setFieldError(
      ...fields.content,
      `일기는 ${MAX_DIARY_CONTENT_LENGTH}자 이하로 입력해 주세요.`,
    );
    isValid = false;
  }
  if (!isValid) {
    showFormStatus(status, "입력한 내용을 확인해 주세요.", "error");
    return null;
  }
  return { content };
}

function clearDiaryFormFeedback({ fields, status }) {
  showFormStatus(status, "");
  Object.values(fields).forEach(([input, errorList]) => {
    clearFieldError(input, errorList);
  });
}

function clearFieldError(input, errorList) {
  input.removeAttribute("aria-invalid");
  errorList.replaceChildren();
  errorList.hidden = true;
}

function setFieldError(input, errorList, message) {
  input.setAttribute("aria-invalid", "true");
  const item = document.createElement("li");
  item.textContent = message;
  errorList.replaceChildren(item);
  errorList.hidden = false;
}

function showDiaryMutationError(error, { fallback, fields, status }) {
  if (error instanceof ApiRequestError) {
    const details = Array.isArray(error.apiError?.details)
      ? error.apiError.details
      : [];
    details.forEach((detail) => {
      if (detail?.field === "content" && typeof detail.message === "string") {
        setFieldError(...fields.content, detail.message);
      }
    });
    showFormStatus(status, error.message, "error");
    return;
  }
  showFormStatus(status, fallback, "error");
}

function diaryMutationRequiresRefresh(error) {
  const apiError = error instanceof ApiRequestError ? error.apiError : null;
  if (
    error instanceof ApiRequestError &&
    error.status === 503 &&
    apiError?.errorCode === "MEDIA_UPLOADS_UNAVAILABLE"
  ) {
    return false;
  }
  return (
    !(error instanceof ApiRequestError) ||
    (error.status >= 200 && error.status < 300) ||
    !apiError ||
    error.status >= 500 ||
    apiError.errorCode === "CSRF_FAILED"
  );
}

function diaryDeleteRequiresRefresh(error) {
  const apiError = error instanceof ApiRequestError ? error.apiError : null;
  return (
    !(error instanceof ApiRequestError) ||
    (error.status >= 200 && error.status < 300) ||
    !apiError ||
    error.status >= 500
  );
}

function setCreateFormDisabled({
  contentInput,
  disabled,
  form,
  media,
  submit,
}) {
  form.setAttribute("aria-busy", String(disabled));
  contentInput.disabled = disabled;
  media?.setDisabled(disabled);
  submit.disabled = disabled;
}

function showFormStatus(element, message, tone = "") {
  element.className = `form-status${tone ? ` form-status--${tone}` : ""}`;
  element.textContent = message;
}

function showDiaryToast(message, tone) {
  globalThis.woorisaiShowToast?.(message, { tone });
}

function updateCharacterCount(input, output) {
  output.textContent = String([...input.value].length);
}

function formatTimestamp(value) {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Seoul",
    year: "numeric",
  }).formatToParts(new Date(value));
  const part = (type) => parts.find((item) => item.type === type)?.value || "";
  return `${part("year")}.${part("month")}.${part("day")} ${part("hour")}:${part("minute")}`;
}

function diaryEntryUrl(collectionUrl, entryId) {
  const url = new URL(collectionUrl, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.endsWith("/")) {
    throw new Error("우리 일기 API 주소가 올바르지 않습니다.");
  }
  url.pathname = `${url.pathname}${entryId}/`;
  url.search = "";
  url.hash = "";
  return url;
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

function renderDiaryLoadError(status, pageNumber, error, retry) {
  status.className = "surface loading-state loading-state--error";
  status.hidden = false;
  const message = document.createElement("p");
  message.textContent = "우리 일기를 불러오지 못했어요.";
  const button = document.createElement("button");
  button.className = "text-button";
  button.type = "button";
  const isMissingPage =
    pageNumber > 1 &&
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "NOT_FOUND";
  button.textContent = isMissingPage ? "첫 페이지로" : "다시 시도";
  button.addEventListener("click", () => {
    if (isMissingPage) {
      window.location.assign(pageHref(1));
    } else {
      retry();
    }
  });
  status.replaceChildren(message, button);
}

function pageHref(pageNumber) {
  const url = new URL(window.location.href);
  url.searchParams.delete("page");
  url.searchParams.set("pageNumber", String(pageNumber));
  return `${url.pathname}${url.search}`;
}

function getCsrfToken(form) {
  return form?.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
}

function redirectWhenAuthenticationExpired(error) {
  if (
    error instanceof ApiRequestError &&
    error.apiError?.errorCode === "AUTHENTICATION_REQUIRED"
  ) {
    if (diaryAuthenticationRedirectStarted) {
      return true;
    }
    diaryAuthenticationRedirectStarted = true;
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/login/?next=${encodeURIComponent(next)}`);
    return true;
  }
  return false;
}
