import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";


beforeAll(async () => {
  await import("../static/js/app.js");
});

beforeEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

function renderSyncForm() {
  document.body.innerHTML = `
    <form
      data-confirm-message="Confirmar sincronización"
      data-submit-busy
      data-submit-busy-label="Sincronizando…"
    >
      <button type="submit">Sincronizar con BOE</button>
    </form>
  `;
  const form = document.querySelector("form");
  const button = form.querySelector('button[type="submit"]');
  return { form, button };
}

function submit(form, button) {
  const event = new SubmitEvent("submit", {
    bubbles: true,
    cancelable: true,
    submitter: button,
  });
  return form.dispatchEvent(event);
}

describe("sincronización con el BOE", () => {
  it("no entra en estado ocupado cuando se cancela la confirmación", () => {
    const { form, button } = renderSyncForm();
    vi.spyOn(window, "confirm").mockReturnValue(false);

    expect(submit(form, button)).toBe(false);
    expect(form).not.toHaveAttribute("aria-busy");
    expect(button).not.toBeDisabled();
    expect(button).toHaveTextContent("Sincronizar con BOE");
  });

  it("muestra progreso e impide un segundo envío tras confirmar", () => {
    const { form, button } = renderSyncForm();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    expect(submit(form, button)).toBe(true);
    expect(form).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Sincronizando…");

    expect(submit(form, button)).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(1);
  });
});
