document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm-message]");
  if (!form) {
    return;
  }

  const message = form.dataset.confirmMessage;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) {
    return;
  }

  const input = document.getElementById(button.dataset.copyTarget);
  const status = button.parentElement.querySelector("[data-copy-status]");
  if (!input) {
    return;
  }

  try {
    await navigator.clipboard.writeText(input.value);
    if (status) {
      status.textContent = button.dataset.copySuccess || "Copiado";
    }
  } catch (_error) {
    input.focus();
    input.select();
    if (status) {
      status.textContent = "Selecciona y copia el enlace";
    }
  }
});
