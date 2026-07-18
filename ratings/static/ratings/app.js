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
