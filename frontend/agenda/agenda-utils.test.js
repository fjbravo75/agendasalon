import assert from "node:assert/strict";
import test from "node:test";

import {
  appointmentDetailUrl,
  buildAppointmentAssistantUrl,
  buildMonthCells,
  capitalizeFirst,
  clockMinutes,
  getTimelineRange,
  shiftMonth,
  timelinePosition,
} from "./agenda-utils.js";


test("el calendario mensual empieza en lunes", () => {
  const cells = buildMonthCells([
    { date: "2026-07-01", status: "available" },
    { date: "2026-07-02", status: "available" },
  ]);

  assert.equal(cells.length, 4);
  assert.equal(cells[0].empty, true);
  assert.equal(cells[1].empty, true);
  assert.equal(cells[2].date, "2026-07-01");
});


test("la capitalización respeta las preposiciones en castellano", () => {
  assert.equal(capitalizeFirst("julio de 2026"), "Julio de 2026");
});


test("el cambio de mes respeta el cambio de año", () => {
  assert.deepEqual(shiftMonth({ year: 2026, month: 12 }, 1), {
    year: 2027,
    month: 1,
  });
  assert.deepEqual(shiftMonth({ year: 2026, month: 1 }, -1), {
    year: 2025,
    month: 12,
  });
});


test("las horas ISO conservan la hora operativa del negocio", () => {
  assert.equal(clockMinutes("2026-07-13T09:45:00+02:00"), 9 * 60 + 45);
});


test("la altura de una cita es proporcional a su duración", () => {
  const position = timelinePosition(
    "2026-07-13T10:00:00+02:00",
    "2026-07-13T11:30:00+02:00",
    { start: 8 * 60, end: 20 * 60 },
  );

  assert.equal(position.top, "176px");
  assert.equal(position.height, "132px");
});


test("el rango horario se amplía si existen citas fuera del horario base", () => {
  const range = getTimelineRange({
    work_lines: [
      {
        appointments: [
          {
            starts_at: "2026-07-13T07:30:00+02:00",
            ends_at: "2026-07-13T21:15:00+02:00",
          },
        ],
        available_slots: [],
      },
    ],
    closures: [],
  });

  assert.deepEqual(range, { start: 7 * 60, end: 22 * 60 });
});


test("el enlace a Nueva cita conserva el hueco elegido", () => {
  const url = buildAppointmentAssistantUrl(
    "/profesional/citas/nueva/",
    {
      work_line_id: 2,
      starts_at: "2026-07-13T10:15:00+02:00",
    },
    "2026-07-13",
  );

  assert.match(url, /target_date=2026-07-13/);
  assert.match(url, /selected_work_line_id=2/);
  assert.match(url, /selected_starts_at=2026-07-13T10%3A15%3A00%2B02%3A00/);
});


test("el enlace de detalle sustituye únicamente el identificador", () => {
  assert.equal(
    appointmentDetailUrl(
      "/profesional/citas/__appointment_id__/",
      42,
    ),
    "/profesional/citas/42/",
  );
});
