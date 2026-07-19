(() => {
  const DEFAULT_DURATION = 6000;
  const TOAST_TONES = new Set(["info", "success", "warning", "error"]);
  const activeToastCleanups = new Map();

  function getToastRegion() {
    let region = document.querySelector("[data-toast-region]");
    if (!region) {
      region = document.createElement("div");
      region.dataset.toastRegion = "";
      document.body.append(region);
    }
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "false");
    return region;
  }

  function normalizeDuration(duration) {
    return Number.isFinite(duration) && duration >= 0
      ? duration
      : DEFAULT_DURATION;
  }

  globalThis.woorisaiShowToast = function showToast(
    message,
    {
      tone = "info",
      href = null,
      ariaLabel = null,
      duration = DEFAULT_DURATION,
    } = {},
  ) {
    const region = getToastRegion();

    const normalizedTone = TOAST_TONES.has(tone) ? tone : "info";
    const normalizedDuration = normalizeDuration(duration);
    const hasLink = typeof href === "string" && href.length > 0;
    const normalizedMessage =
      typeof message === "string" ? message : String(message ?? "");
    const toastKey = JSON.stringify([
      normalizedTone,
      hasLink ? href : null,
      normalizedMessage,
    ]);
    activeToastCleanups.get(toastKey)?.();

    const toast = document.createElement("div");
    toast.className = `toast toast--${normalizedTone}`;
    toast.dataset.toast = "";
    if (!hasLink) {
      toast.tabIndex = 0;
    }

    const content = document.createElement(hasLink ? "a" : "div");
    content.className = "toast__content";
    if (hasLink) {
      content.href = href;
      if (typeof ariaLabel === "string" && ariaLabel.length > 0) {
        content.setAttribute("aria-label", ariaLabel);
      }
    }

    const mark = document.createElement("span");
    mark.className = "toast__mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "♥";
    const messageElement = document.createElement("strong");
    messageElement.textContent = normalizedMessage;
    content.append(mark, messageElement);
    toast.append(content);
    region.append(toast);

    let isActive = true;
    let isFocused = false;
    let isHovered = false;
    let removalTimer = null;
    const pauseRemoval = () => {
      if (removalTimer !== null) {
        globalThis.clearTimeout(removalTimer);
        removalTimer = null;
      }
    };
    const cleanup = () => {
      if (!isActive) {
        return;
      }
      isActive = false;
      pauseRemoval();
      toast.remove();
      if (activeToastCleanups.get(toastKey) === cleanup) {
        activeToastCleanups.delete(toastKey);
      }
    };
    const scheduleRemoval = () => {
      pauseRemoval();
      if (!isActive || isFocused || isHovered || normalizedDuration === 0) {
        return;
      }
      removalTimer = globalThis.setTimeout(cleanup, normalizedDuration);
    };

    toast.addEventListener("mouseenter", () => {
      isHovered = true;
      pauseRemoval();
    });
    toast.addEventListener("mouseleave", () => {
      isHovered = false;
      scheduleRemoval();
    });
    toast.addEventListener("focusin", () => {
      isFocused = true;
      pauseRemoval();
    });
    toast.addEventListener("focusout", () => {
      isFocused = false;
      scheduleRemoval();
    });

    activeToastCleanups.set(toastKey, cleanup);
    scheduleRemoval();
    return toast;
  };
})();
