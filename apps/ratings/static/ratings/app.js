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
} else {
  window.woorisaiServiceWorkerReady = Promise.resolve(null);
}
