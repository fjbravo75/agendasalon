import test from "node:test";
import assert from "node:assert/strict";

import {
  barPercent,
  businessMatchesFilter,
  filterBusinesses,
  formatShortDate,
  normalizeText,
  pluralize,
  sortBusinesses,
} from "./dashboard-utils.js";


const businesses = [
  {
    name: "Peluquería Mari",
    city: "Madrid",
    health: { code: "operational" },
    counts: { pending_closure: 0 },
  },
  {
    name: "Barbería Norte",
    city: "Alcobendas",
    health: { code: "setup_pending" },
    counts: { pending_closure: 0 },
  },
  {
    name: "Salón Centro",
    city: "Madrid",
    health: { code: "operational" },
    counts: { pending_closure: 2 },
  },
];

test("normaliza tildes y mayúsculas para la búsqueda", () => {
  assert.equal(normalizeText("Peluquería MARÍ"), "peluqueria mari");
});

test("filtra negocios por salud", () => {
  assert.equal(filterBusinesses(businesses, "setup_pending", "").length, 1);
});

test("filtra negocios por ciudad sin depender de tildes", () => {
  assert.equal(filterBusinesses(businesses, "all", "peluqueria")[0].name, "Peluquería Mari");
});

test("el filtro de cierres pendientes usa la tarea real", () => {
  assert.equal(businessMatchesFilter(businesses[2], "pending_closure"), true);
  assert.equal(businessMatchesFilter(businesses[0], "pending_closure"), false);
});

test("ordena primero configuración pendiente y después cierres", () => {
  const sorted = sortBusinesses(businesses);
  assert.deepEqual(sorted.map((item) => item.name), [
    "Barbería Norte",
    "Salón Centro",
    "Peluquería Mari",
  ]);
});

test("las barras conservan presencia para valores pequeños", () => {
  assert.equal(barPercent(1, 100), 8);
  assert.equal(barPercent(50, 100), 50);
  assert.equal(barPercent(0, 100), 0);
});

test("las fechas cortas se presentan en castellano", () => {
  assert.match(formatShortDate("2026-07-10"), /10 jul/i);
});

test("pluraliza unidades sin copy ambiguo", () => {
  assert.equal(pluralize(1, "negocio", "negocios"), "1 negocio");
  assert.equal(pluralize(2, "negocio", "negocios"), "2 negocios");
});
