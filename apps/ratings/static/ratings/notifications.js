import { initializeApp } from "https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js";
import {
  getMessaging,
  isSupported,
  onMessage,
  onRegistered,
  onUnregistered,
  register as registerMessaging,
  unregister as unregisterMessaging,
} from "https://www.gstatic.com/firebasejs/12.16.0/firebase-messaging.js";

const settings = document.querySelector("[data-notification-settings]");

if (settings) {
  initializeNotifications(settings).catch((error) => {
    console.warn("우리 사이 알림을 준비하지 못했어요.", error);
    updateView(settings, {
      status: "지금은 알림을 연결할 수 없어요. 잠시 후 다시 시도해 주세요.",
      buttonLabel: "다시 시도",
      showButton: true,
    });
    settings
      .querySelector("[data-notification-toggle]")
      ?.addEventListener("click", () => window.location.reload(), { once: true });
  });
}

async function initializeNotifications(root) {
  const config = readFirebaseConfig();
  const vapidKey = root.dataset.vapidKey?.trim();
  const isAppleMobile = detectAppleMobile();
  const isStandalone = detectStandaloneMode();

  if (isAppleMobile && !isStandalone) {
    updateView(root, {
      status: "iPhone에서는 홈 화면에 추가한 앱에서 알림을 받을 수 있어요.",
      showInstallGuide: true,
    });
    return;
  }

  if (!window.isSecureContext || !("Notification" in window)) {
    updateView(root, {
      status: "이 브라우저에서는 알림을 사용할 수 없어요.",
    });
    return;
  }

  if (!config || !vapidKey) {
    updateView(root, {
      status: "알림 기능을 준비하고 있어요. 조금만 기다려 주세요.",
    });
    return;
  }

  if (!(await isSupported())) {
    updateView(root, {
      status: "이 브라우저에서는 알림을 지원하지 않아요.",
    });
    return;
  }

  const serviceWorkerRegistration = await getServiceWorkerRegistration();
  if (!serviceWorkerRegistration) {
    throw new Error("Service worker registration is unavailable.");
  }

  const firebaseApp = initializeApp(config);
  const messaging = getMessaging(firebaseApp);
  const preferenceKey = "woorisai:notifications-enabled";
  let currentFid = null;
  let isRegistered = false;
  let isBusy = false;

  const getPreference = () => window.localStorage.getItem(preferenceKey);
  const setPreference = (enabled) => {
    window.localStorage.setItem(preferenceKey, enabled ? "true" : "false");
  };

  const syncFid = async (url, fid) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({ fid }),
    });

    const responseType = response.headers.get("Content-Type") || "";
    if (!response.ok || response.redirected || !responseType.includes("application/json")) {
      throw new Error(`Notification registration failed (${response.status}).`);
    }

    const payload = await response.json();
    if (payload?.ok !== true) {
      throw new Error("Notification registration was not acknowledged.");
    }
  };

  onRegistered(messaging, async (fid) => {
    currentFid = fid;

    if (getPreference() === "false") {
      await syncFid(root.dataset.unregisterUrl, fid).catch(() => undefined);
      return;
    }

    try {
      await syncFid(root.dataset.registerUrl, fid);
      isRegistered = true;
      isBusy = false;
      setPreference(true);
      updateView(root, {
        status: "새 마음이 도착하면 이 기기로 알려드릴게요.",
        buttonLabel: "알림 끄기",
        showButton: true,
        active: true,
      });
    } catch (error) {
      isBusy = false;
      updateView(root, {
        status: "알림 연결을 마무리하지 못했어요. 다시 시도해 주세요.",
        buttonLabel: "다시 시도",
        showButton: true,
      });
      console.warn("알림 기기 정보를 저장하지 못했어요.", error);
    }
  });

  onUnregistered(messaging, async (fid) => {
    currentFid = null;
    isRegistered = false;

    try {
      await syncFid(root.dataset.unregisterUrl, fid);
    } catch (error) {
      console.warn("알림 기기 정보를 정리하지 못했어요.", error);
    } finally {
      isBusy = false;
      updateView(root, {
        status: "알림이 꺼져 있어요. 언제든 다시 켤 수 있어요.",
        buttonLabel: "알림 받기",
        showButton: true,
      });
    }
  });

  onMessage(messaging, () => {
    showForegroundNotification();
  });

  const enableNotifications = async () => {
    isBusy = true;
    updateView(root, {
      status: "알림을 연결하고 있어요…",
      buttonLabel: "연결 중…",
      showButton: true,
      busy: true,
    });

    const permission =
      Notification.permission === "granted"
        ? "granted"
        : await Notification.requestPermission();

    if (permission !== "granted") {
      isBusy = false;
      setPreference(false);
      if (permission === "denied") {
        showPermissionDenied(root);
      } else {
        updateView(root, {
          status: "아직 알림을 허용하지 않았어요. 준비되면 다시 눌러 주세요.",
          buttonLabel: "다시 시도",
          showButton: true,
        });
      }
      return;
    }

    setPreference(true);
    try {
      await registerMessaging(messaging, {
        vapidKey,
        serviceWorkerRegistration,
      });
    } catch (error) {
      isBusy = false;
      updateView(root, {
        status: "알림을 연결하지 못했어요. 잠시 후 다시 시도해 주세요.",
        buttonLabel: "다시 시도",
        showButton: true,
      });
      console.warn("Firebase 알림 등록을 완료하지 못했어요.", error);
    }
  };

  const disableNotifications = async () => {
    isBusy = true;
    setPreference(false);
    updateView(root, {
      status: "알림을 끄고 있어요…",
      buttonLabel: "끄는 중…",
      showButton: true,
      active: true,
      busy: true,
    });

    try {
      await unregisterMessaging(messaging);

      if (!currentFid) {
        isBusy = false;
        isRegistered = false;
        updateView(root, {
          status: "알림이 꺼져 있어요. 언제든 다시 켤 수 있어요.",
          buttonLabel: "알림 받기",
          showButton: true,
        });
      }
    } catch (error) {
      isBusy = false;
      setPreference(true);
      updateView(root, {
        status: "알림을 끄지 못했어요. 다시 시도해 주세요.",
        buttonLabel: "알림 끄기",
        showButton: true,
        active: true,
      });
      throw error;
    }
  };

  root.querySelector("[data-notification-toggle]")?.addEventListener("click", () => {
    if (isBusy) {
      return;
    }

    const action = isRegistered ? disableNotifications : enableNotifications;
    action().catch((error) => {
      console.warn("알림 상태를 바꾸지 못했어요.", error);
    });
  });

  if (Notification.permission === "denied") {
    showPermissionDenied(root);
    return;
  }

  if (Notification.permission === "granted" && getPreference() === "true") {
    await enableNotifications();
    return;
  }

  updateView(root, {
    status: "원할 때 켜 주세요. 알림 내용에는 점수나 이유가 표시되지 않아요.",
    buttonLabel: "알림 받기",
    showButton: true,
  });
}

function readFirebaseConfig() {
  const element = document.querySelector("#firebase-config");
  if (!element) {
    return null;
  }

  try {
    const config = JSON.parse(element.textContent);
    return config && typeof config === "object" ? config : null;
  } catch (error) {
    console.warn("Firebase 공개 설정을 읽지 못했어요.", error);
    return null;
  }
}

async function getServiceWorkerRegistration() {
  if (!("serviceWorker" in navigator)) {
    return null;
  }

  const pendingRegistration =
    window.woorisaiServiceWorkerReady ||
    navigator.serviceWorker.register("/service-worker.js", {
      scope: "/",
      updateViaCache: "none",
    });
  const registration = await pendingRegistration;
  if (!registration) {
    return null;
  }
  return registration.active ? registration : waitForServiceWorkerReady();
}

async function waitForServiceWorkerReady(timeoutMs = 15000) {
  let timeoutId;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(
          () => reject(new Error("Service worker activation timed out.")),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function getCsrfToken() {
  const formToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
  if (formToken) {
    return formToken;
  }

  const cookie = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
}

function detectAppleMobile() {
  return (
    /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

function detectStandaloneMode() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

function showPermissionDenied(root) {
  updateView(root, {
    status: "알림이 차단되어 있어요. 기기 설정에서 ‘우리 사이’ 알림을 허용해 주세요.",
  });
}

function showForegroundNotification() {
  const existingToast = document.querySelector("[data-foreground-notification]");
  existingToast?.remove();

  const toast = document.createElement("div");
  toast.className = "foreground-notification";
  toast.dataset.foregroundNotification = "";
  toast.setAttribute("role", "status");
  toast.innerHTML = "<span aria-hidden=\"true\">♥</span><strong>새로운 마음 기록이 도착했어요</strong>";
  document.body.append(toast);

  window.setTimeout(() => toast.remove(), 5200);
}

function updateView(
  root,
  {
    status,
    buttonLabel = "알림 받기",
    showButton = false,
    showInstallGuide = false,
    active = false,
    busy = false,
  },
) {
  const statusElement = root.querySelector("[data-notification-status]");
  const button = root.querySelector("[data-notification-toggle]");
  const buttonLabelElement = root.querySelector("[data-notification-button-label]");
  const installGuide = root.querySelector("[data-install-guide]");

  if (statusElement && status) {
    statusElement.textContent = status;
  }
  if (buttonLabelElement) {
    buttonLabelElement.textContent = buttonLabel;
  }
  if (button) {
    button.hidden = !showButton;
    button.disabled = busy;
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.classList.toggle("notification-toggle--active", active);
  }
  if (installGuide) {
    installGuide.hidden = !showInstallGuide;
  }
}
