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
