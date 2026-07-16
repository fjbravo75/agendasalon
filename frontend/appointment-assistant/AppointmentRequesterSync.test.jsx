import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";


const renderAssistant = ({ searchPerformed = true } = {}) => {
  document.body.innerHTML = `
    <section class="assistant-shell">
      <form id="appointment-search-form" data-appointment-search>
        <select name="business_client">
          <option value="6" selected>Lucas López</option>
          <option value="7">Carmen Ruiz</option>
        </select>
        <select name="manual_channel">
          <option value="telefono" selected>Teléfono</option>
          <option value="mostrador">Mostrador</option>
        </select>
        <select name="requested_by_contact">
          <option value="self" selected>Lucas López (para sí)</option>
          <option value="contact:3">María López · Madre</option>
        </select>
        <input name="target_date" type="date" value="2026-07-16">
        <input name="services" type="checkbox" value="4" data-appointment-service data-duration="30">
        <input name="adjusted_duration_minutes" type="number" value="">
        <textarea name="duration_adjustment_reason"></textarea>
        <strong data-appointment-duration-total></strong>
        <small data-appointment-duration-detail></small>
        <p data-duration-adjust-help></p>
      </form>
      <div data-appointment-results-current data-results-actionable="${String(searchPerformed)}">
        <form class="slot-confirm-form">
          <input type="hidden" name="requested_by_contact" value="self">
          <button type="submit">Confirmar cita</button>
        </form>
        <form class="suggestion-row--form">
          <input type="hidden" name="requested_by_contact" value="self">
          <button type="submit">Revisar hora</button>
        </form>
        <form class="slot-choice-form">
          <input type="hidden" name="requested_by_contact" value="self">
          <button type="submit">Elegir</button>
        </form>
      </div>
      <section data-appointment-results-stale hidden>
        <button type="submit" form="appointment-search-form">Buscar con estos cambios</button>
      </section>
    </section>
    <script id="appointment-requester-options" type="application/json">
      {
        "6": [
          {"value": "self", "label": "Lucas López (para sí)"},
          {"value": "contact:3", "label": "María López · Madre"}
        ],
        "7": [
          {"value": "self", "label": "Carmen Ruiz (para sí)"},
          {"value": "contact:4", "label": "Daniel Vega · Cuidador"}
        ]
      }
    </script>
  `;
};

const loadAssistantScript = async () => {
  vi.resetModules();
  await import("../../static/js/app.js?appointment-requester-sync-test");
};

const expectCurrentResults = () => {
  expect(document.querySelector("[data-appointment-results-current]")).not.toHaveAttribute(
    "hidden",
  );
  expect(document.querySelector("[data-appointment-results-stale]")).toHaveAttribute(
    "hidden",
  );
};

const expectStaleResults = () => {
  expect(document.querySelector("[data-appointment-results-current]")).toHaveAttribute(
    "hidden",
  );
  expect(document.querySelector("[data-appointment-results-current]")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect(document.querySelector("[data-appointment-results-stale]")).not.toHaveAttribute(
    "hidden",
  );
  expect(
    [...document.querySelectorAll("[data-appointment-results-current] button[type='submit']")].map(
      (button) => button.disabled,
    ),
  ).toEqual([true, true, true]);
};

describe("asistente de cita profesional", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("sincroniza el solicitante sin invalidar y bloquea los huecos al cambiar cliente", async () => {
    renderAssistant();
    await loadAssistantScript();

    const requester = document.querySelector('select[name="requested_by_contact"]');
    const client = document.querySelector('select[name="business_client"]');
    const payloads = [
      ...document.querySelectorAll(
        '.assistant-shell input[type="hidden"][name="requested_by_contact"]',
      ),
    ];

    fireEvent.change(requester, { target: { value: "contact:3" } });
    expect(payloads.map((input) => input.value)).toEqual([
      "contact:3",
      "contact:3",
      "contact:3",
    ]);
    expectCurrentResults();

    fireEvent.change(client, { target: { value: "7" } });
    expect(requester).toHaveValue("self");
    expect([...requester.options].map((option) => option.textContent)).toEqual([
      "Carmen Ruiz (para sí)",
      "Daniel Vega · Cuidador",
    ]);
    expect(payloads.map((input) => input.value)).toEqual(["self", "self", "self"]);
    expectStaleResults();
  });

  it.each([
    ["manual_channel", "mostrador"],
    ["target_date", "2026-07-17"],
    ["services", "4"],
    ["adjusted_duration_minutes", "45"],
    ["duration_adjustment_reason", "Necesita más tiempo"],
  ])("invalida los huecos al cambiar %s", async (fieldName, value) => {
    renderAssistant();
    await loadAssistantScript();

    const field = document.querySelector(`[name="${fieldName}"]`);
    if (field.type === "checkbox") {
      fireEvent.change(field, { target: { checked: true } });
    } else {
      fireEvent.change(field, { target: { value } });
    }

    expectStaleResults();
  });

  it("no muestra un aviso obsoleto antes de la primera búsqueda", async () => {
    renderAssistant({ searchPerformed: false });
    await loadAssistantScript();

    fireEvent.change(document.querySelector('[name="target_date"]'), {
      target: { value: "2026-07-17" },
    });

    expectCurrentResults();
  });
});
