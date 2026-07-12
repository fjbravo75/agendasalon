import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ProfessionalAgenda from "./ProfessionalAgenda.jsx";


const config = {
  dayEndpoint: "/api/agenda/dia/",
  monthEndpoint: "/api/agenda/mes/",
  appointmentAssistantUrl: "/profesional/citas/nueva/",
  appointmentUrlTemplate: "/profesional/citas/__appointment_id__/",
  businessName: "Peluquería Mari",
  professionalSummaryUrl: "/profesional/",
  scheduleUrl: "/profesional/horarios/",
  initialDate: "2026-07-13",
  initialDuration: 60,
  durationOptions: [30, 60, 90],
  slotIntervalMinutes: 15,
};

function jsonResponse(payload, { ok = true } = {}) {
  return {
    ok,
    redirected: false,
    headers: { get: () => "application/json" },
    json: async () => payload,
  };
}

function payloadFor(url) {
  if (url.startsWith(config.monthEndpoint)) {
    return {
      days: [{ date: "2026-07-13", status: "available", reason: null, first_slot: null }],
    };
  }
  return {
    business: { name: "Peluquería Mari" },
    calendar: { status: "available", reason: null, calculated_from: null, slot_interval_minutes: 15 },
    holidays: [],
    closures: [],
    work_lines: [{ id: 1, name: "Línea 1", appointments: [], available_slots: [] }],
    recommended_slot: null,
    suggestions: [],
  };
}

describe("ProfessionalAgenda", () => {
  it("recarga día y mes al cambiar duración y mes visible", async () => {
    const fetchMock = vi.fn((url) => Promise.resolve(jsonResponse(payloadFor(url))));
    vi.stubGlobal("fetch", fetchMock);
    render(<ProfessionalAgenda config={config} />);

    expect(await screen.findByRole("heading", { name: "La jornada, a una sola mirada." })).toBeInTheDocument();
    expect(screen.getByLabelText("Desplazar la jornada por líneas de trabajo")).toHaveAttribute("tabindex", "0");
    fireEvent.change(screen.getByRole("combobox", { name: "Duración que necesitas" }), { target: { value: "90" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes("duration=90"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Mes siguiente" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes("month=8"))).toBe(true));
  });

  it("mantiene una salida recuperable cuando falla el día", async () => {
    const fetchMock = vi.fn((url) => {
      if (url.startsWith(config.dayEndpoint)) {
        return Promise.resolve(jsonResponse({ error: { message: "No se puede consultar ahora" } }, { ok: false }));
      }
      return Promise.resolve(jsonResponse(payloadFor(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProfessionalAgenda config={config} />);

    expect(await screen.findByText("No se puede consultar ahora")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Intentar de nuevo" })).toBeInTheDocument();
  });
});
