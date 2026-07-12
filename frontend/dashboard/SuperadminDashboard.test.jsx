import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SuperadminDashboard from "./SuperadminDashboard.jsx";


const config = {
  dataEndpoint: "/api/superadmin/",
  businessListUrl: "/superadmin/negocios/",
  businessCreateUrl: "/superadmin/negocios/nuevo/",
};

function jsonResponse(payload, { ok = true } = {}) {
  return {
    ok,
    redirected: false,
    headers: { get: () => "application/json" },
    json: async () => payload,
  };
}

function business(id, name, city) {
  return {
    id,
    name,
    city,
    is_active: true,
    public_booking_enabled: true,
    last_activity_at: "2026-07-12T10:00:00+02:00",
    health: { code: "operational", label: "Operativo", tone: "ready", detail: "Configuración básica completa." },
    counts: { services: 5, work_lines: 2, schedule_rules: 10, professionals: 1, clients: 6, appointments: 12, upcoming_confirmed: 3, pending_closure: 0 },
    urls: { detail: `/superadmin/negocios/${id}/` },
  };
}

function dashboardPayload() {
  const businesses = [business(1, "Peluquería Mari", "Madrid"), business(2, "Barbería Norte", "Bilbao")];
  const recentActivity = Array.from({ length: 7 }, (_, index) => ({
    id: index + 1,
    business: { name: index % 2 ? "Barbería Norte" : "Peluquería Mari" },
    category: "appointments",
    event_label: "Cita creada",
    origin_label: "Panel profesional",
    created_at: `2026-07-${String(12 - index).padStart(2, "0")}T10:00:00+02:00`,
  }));
  return {
    generated_at: "2026-07-12T12:00:00+02:00",
    summary: {
      businesses_active: 2,
      businesses_inactive: 0,
      businesses_operational: 2,
      businesses_setup_pending: 0,
      businesses_public_booking: 2,
      businesses_with_pending_closure: 0,
      pending_closure_appointments: 0,
      professionals_active: 2,
      clients_total: 8,
      appointments_total: 24,
    },
    businesses,
    recent_activity: recentActivity,
    activity_series: [{ date: "2026-07-12", value: 4 }],
    appointment_statuses: [{ code: "completed", label: "Atendidas", value: 6 }],
    appointment_channels: [{ code: "phone", label: "Teléfono", value: 8 }],
  };
}

describe("SuperadminDashboard", () => {
  it("filtra negocios y limita visualmente una actividad extensa", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(dashboardPayload())));
    render(<SuperadminDashboard config={config} />);

    expect(await screen.findByRole("heading", { name: "Negocios" })).toBeInTheDocument();
    const activity = screen.getByRole("list", { name: "Actividad reciente, 7 movimientos" });
    expect(activity).toHaveClass("superadmin-activity-list--scrollable");
    expect(activity).toHaveAttribute("tabindex", "0");

    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar negocio" }), { target: { value: "Mari" } });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Barbería Norte" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Peluquería Mari" })).toBeInTheDocument();
  });

  it("explica el error y permite reintentar la carga", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: { message: "Servicio temporalmente no disponible" } }, { ok: false }))
      .mockResolvedValueOnce(jsonResponse(dashboardPayload()));
    vi.stubGlobal("fetch", fetchMock);
    render(<SuperadminDashboard config={config} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Servicio temporalmente no disponible");
    fireEvent.click(screen.getByRole("button", { name: "Volver a intentar" }));
    expect(await screen.findByRole("heading", { name: "Negocios" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
