const diaryRoot = document.querySelector("[data-diary-root]");
const DIARY_PAGE_SIZE = 20;
const MAX_DIARY_CONTENT_LENGTH = 1000;

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
  let isCreating = false;
  let creationRequiresRefresh = false;
  let renderedResultCount = 0;
  let shouldFocusAfterMutation = false;

  const loadPage = () =>
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
    updateCharacterCount(contentInput, characterCount);

    contentInput.addEventListener("input", () => {
      clearFieldError(contentInput, createFieldErrors.content);
      updateCharacterCount(contentInput, characterCount);
    });

    createForm.addEventListener("submit", (event) => {
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
        submit: createSubmit,
      });
      createSubmitLabel.textContent = "남기고 있어요…";

      requestJson(root.dataset.diaryEntriesUrl, {
        body: JSON.stringify(command),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(createForm),
        },
        method: "POST",
      })
        .then((payload) => {
          readDiaryEntry(payload?.success);
          contentInput.value = "";
          updateCharacterCount(contentInput, characterCount);
          showFormStatus(createStatus, "일기를 남겼어요.", "success");
          showDiaryToast("우리 일기에 새 이야기를 남겼어요.", "success");
          if (pageNumber > 1) {
            window.location.assign(pageHref(1));
            return;
          }
          return loadPage();
        })
        .catch((error) => {
          if (redirectWhenAuthenticationExpired(error)) {
            return;
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
        })
        .finally(() => {
          isCreating = false;
          setCreateFormDisabled({
            contentInput,
            disabled: false,
            form: createForm,
            submit: createSubmit,
          });
          createSubmit.disabled = !shouldUnlockCreation;
          createSubmitLabel.textContent = shouldUnlockCreation
            ? "일기 남기기"
            : "새로고침 후 확인";
        });
    });
  }

  root.querySelector("[data-diary-focus-compose]")?.addEventListener(
    "click",
    () => {
      createForm?.scrollIntoView?.({ behavior: "smooth", block: "center" });
      contentInput?.focus();
    },
  );

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
    typeof entry.isMine !== "boolean"
  ) {
    throw new Error("우리 일기 항목 형식이 올바르지 않습니다.");
  }
  return entry;
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
  let isMutating = false;
  const item = document.createElement("li");
  item.className = "diary-list__item";
  const card = document.createElement("article");

  const renderCard = ({ focusEdit = false } = {}) => {
    card.className = `surface diary-card${entry.isMine ? " diary-card--mine" : ""}`;
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
            edit.disabled = false;
            remove.disabled = false;
          });
      });
      actions.append(edit, remove);
      header.append(actions);
    }

    const body = document.createElement("p");
    body.className = "diary-card__content";
    body.textContent = entry.content;
    if (entry.updatedAt !== null) {
      const footer = document.createElement("footer");
      const editedAt = document.createElement("time");
      editedAt.dateTime = entry.updatedAt;
      editedAt.textContent = `${formatTimestamp(entry.updatedAt)} 수정`;
      footer.append(editedAt);
      card.replaceChildren(header, body, footer);
    } else {
      card.replaceChildren(header, body);
    }
    if (focusEdit) {
      editButton?.focus();
    }
  };

  const renderDeletedCard = () => {
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

  const renderEditor = () => {
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
    form.append(contentGroup, status, actions);
    card.className = "surface diary-card diary-card--mine diary-card--editing";
    card.replaceChildren(form);
    updateCharacterCount(contentInput, current);

    const fields = {
      content: [contentInput, contentError],
    };
    contentInput.addEventListener("input", () => {
      clearFieldError(contentInput, contentError);
      updateCharacterCount(contentInput, current);
    });
    cancel.addEventListener("click", () => {
      if (!isMutating) {
        renderCard({ focusEdit: true });
      }
    });
    form.addEventListener("submit", (event) => {
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
      cancel.disabled = true;
      save.disabled = true;
      save.textContent = "저장하고 있어요…";
      requestJson(diaryEntryUrl(callbacks.collectionUrl, entry.id), {
        body: JSON.stringify(command),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": callbacks.csrfToken,
        },
        method: "PATCH",
      })
        .then((payload) => {
          entry = readDiaryEntry(payload?.success);
          renderCard({ focusEdit: true });
          showDiaryToast("일기를 수정했어요.", "success");
          return callbacks.onEntryChanged();
        })
        .catch((error) => {
          if (redirectWhenAuthenticationExpired(error)) {
            return;
          }
          showDiaryMutationError(error, {
            fallback: "일기를 수정하지 못했어요. 잠시 후 다시 시도해 주세요.",
            fields,
            status,
          });
        })
        .finally(() => {
          isMutating = false;
          form.setAttribute("aria-busy", "false");
          contentInput.disabled = false;
          cancel.disabled = false;
          save.disabled = false;
          save.textContent = "수정 저장";
        });
    });
    contentInput.focus();
  };

  renderCard();
  item.append(card);
  return item;
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
  return (
    !(error instanceof ApiRequestError) ||
    (error.status >= 200 && error.status < 300) ||
    !apiError ||
    error.status >= 500 ||
    apiError.errorCode === "CSRF_FAILED"
  );
}

function setCreateFormDisabled({
  contentInput,
  disabled,
  form,
  submit,
}) {
  form.setAttribute("aria-busy", String(disabled));
  contentInput.disabled = disabled;
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
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/login/?next=${encodeURIComponent(next)}`);
    return true;
  }
  return false;
}
