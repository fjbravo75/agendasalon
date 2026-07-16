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

  it("conserva la línea y la hora exactas de una sugerencia tras cargar el nuevo día", async () => {
    const suggestedSlot = {
      work_line_id: 2,
      work_line_name: "Línea 2",
      starts_at: "2026-07-23T09:00:00+02:00",
      ends_at: "2026-07-23T10:00:00+02:00",
      duration_minutes: 60,
      reason: "primer_hueco",
    };
    const competingRecommendedSlot = {
      ...suggestedSlot,
      work_line_id: 1,
      work_line_name: "Línea 1",
      starts_at: "2026-07-23T10:00:00+02:00",
      ends_at: "2026-07-23T11:00:00+02:00",
    };
    const fetchMock = vi.fn((url) => {
      if (url.startsWith(config.monthEndpoint)) {
        return Promise.resolve(jsonResponse(payloadFor(url)));
      }
      const requestedDate = new URL(url, "https://agenda.test").searchParams.get("date");
      if (requestedDate === "2026-07-23") {
        return Promise.resolve(jsonResponse({
          ...payloadFor(url),
          work_lines: [
            {
              id: 1,
              name: "Línea 1",
              appointments: [],
              available_slots: [competingRecommendedSlot],
            },
            { id: 2, name: "Línea 2", appointments: [], available_slots: [suggestedSlot] },
          ],
          recommended_slot: competingRecommendedSlot,
          suggestions: [],
        }));
      }
      return Promise.resolve(jsonResponse({
        ...payloadFor(url),
        calendar: { status: "unavailable", reason: "sin_hueco", calculated_from: null, slot_interval_minutes: 15 },
        suggestions: [suggestedSlot],
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProfessionalAgenda config={config} />);

    const suggestion = await screen.findByRole("button", { name: /jue.*23.*09:00.*Línea 2/i });
    fireEvent.click(suggestion);

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes("date=2026-07-23"))).toBe(true));
    expect(await screen.findByRole("button", { name: "Línea 2" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Hora elegida" })).toBeInTheDocument();
    expect(screen.getByText("Línea 2 · 60 min")).toBeInTheDocument();
    const continueLink = screen.getByRole("link", { name: "Continuar en Nueva cita" });
    expect(continueLink).toHaveAttribute(
      "href",
      expect.stringContaining("target_date=2026-07-23"),
    );
    expect(continueLink).toHaveAttribute(
      "href",
      expect.stringContaining("selected_work_line_id=2"),
    );
    expect(continueLink.getAttribute("href")).toContain(
      "selected_starts_at=2026-07-23T09%3A00%3A00%2B02%3A00",
    );
  });
});
