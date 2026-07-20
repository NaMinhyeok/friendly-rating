const PUSH_NAVIGATION_READY = "woorisai:push-navigation-ready";
const PUSH_NAVIGATION_AVAILABLE = "woorisai:push-navigation-available";
const PUSH_NAVIGATION_OPEN = "woorisai:push-navigation-open";
const PUSH_NAVIGATION_CONSUMED = "woorisai:push-navigation-consumed";
// The second retry runs just after the service worker's 30-second target lease.
const PUSH_NAVIGATION_READY_RETRY_DELAYS_MS = [1_000, 31_000];
let navigatingPushNavigationId = null;

function readPushConversationPath(value) {
  if (typeof value !== "string") {
    return null;
  }

  try {
    const url = new URL(value, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      !/^\/(?:history|diary)\/[1-9]\d*\/$/.test(url.pathname)
    ) {
      return null;
    }
    return `${url.pathname}${url.search}`;
  } catch {
    return null;
  }
}

function signalPushNavigationReady(worker = navigator.serviceWorker?.controller) {
  if (!worker || typeof worker.postMessage !== "function") {
    return false;
  }
  try {
    worker.postMessage({ type: PUSH_NAVIGATION_READY });
    return true;
  } catch {
    return false;
  }
}

function signalWhenServiceWorkerReady() {
  if (signalPushNavigationReady()) {
    return;
  }
  navigator.serviceWorker.ready
    .then((registration) => signalPushNavigationReady(registration.active))
    .catch(() => undefined);
}

function schedulePushNavigationReadyRetries() {
  for (const delay of PUSH_NAVIGATION_READY_RETRY_DELAYS_MS) {
    window.setTimeout(signalWhenServiceWorkerReady, delay);
  }
}

function acknowledgePushNavigation(worker, id) {
  if (
    !worker ||
    typeof worker.postMessage !== "function" ||
    typeof id !== "string" ||
    id.length === 0
  ) {
    return false;
  }
  try {
    worker.postMessage({ id, type: PUSH_NAVIGATION_CONSUMED });
    return true;
  } catch {
    return false;
  }
}

function preservePushNavigationAfterLogin(path) {
  if (window.location.pathname !== "/login/") {
    return false;
  }
  const nextInput = document.querySelector('input[name="next"]');
  if (!nextInput) {
    return false;
  }
  const loginUrl = new URL(
    `${window.location.pathname}${window.location.search}`,
    window.location.origin,
  );
  loginUrl.searchParams.set("next", path);
  try {
    window.history.replaceState(
      null,
      "",
      `${loginUrl.pathname}${loginUrl.search}`,
    );
  } catch {
    return false;
  }
  nextInput.value = path;
  return true;
}

function handlePushNavigationMessage(event) {
  if (event.data?.type === PUSH_NAVIGATION_AVAILABLE) {
    if (!signalPushNavigationReady(event.source)) {
      signalWhenServiceWorkerReady();
    }
    return;
  }
  if (event.data?.type !== PUSH_NAVIGATION_OPEN) {
    return;
  }

  const path = readPushConversationPath(event.data.path);
  if (!path) {
    return;
  }

  const currentPath = `${window.location.pathname}${window.location.search}`;
  if (currentPath === path || preservePushNavigationAfterLogin(path)) {
    acknowledgePushNavigation(event.source, event.data.id);
    return;
  }
  if (
    typeof event.data.id === "string" &&
    event.data.id === navigatingPushNavigationId
  ) {
    return;
  }
  navigatingPushNavigationId = event.data.id || null;
  window.location.replace(path);
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", handlePushNavigationMessage);
  navigator.serviceWorker.addEventListener(
    "controllerchange",
    signalWhenServiceWorkerReady,
  );
  window.addEventListener("pageshow", signalWhenServiceWorkerReady);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      signalWhenServiceWorkerReady();
    }
  });
  schedulePushNavigationReadyRetries();
}

document.querySelectorAll("[data-pin-input]").forEach((input) => {
  input.addEventListener("input", () => {
    input.value = input.value.replace(/\D/g, "").slice(0, 4);
  });
});

const reasonInput = document.querySelector("#id_reason");
const characterCurrent = document.querySelector("[data-character-current]");

if (reasonInput && characterCurrent) {
  const updateCharacterCount = () => {
    characterCurrent.textContent = reasonInput.value.length;
  };

  reasonInput.addEventListener("input", updateCharacterCount);
  updateCharacterCount();
}

if ("serviceWorker" in navigator) {
  window.woorisaiServiceWorkerReady = navigator.serviceWorker
    .register("/service-worker.js", {
      scope: "/",
      updateViaCache: "none",
    })
    .catch((error) => {
      console.warn("우리 사이 서비스 워커를 등록하지 못했어요.", error);
      return null;
    });
  window.woorisaiServiceWorkerReady.then((registration) => {
    if (registration) {
      signalPushNavigationReady(registration.active);
    }
  });
  navigator.serviceWorker.ready
    .then((registration) => signalPushNavigationReady(registration.active))
    .catch(() => undefined);
} else {
  window.woorisaiServiceWorkerReady = Promise.resolve(null);
}
