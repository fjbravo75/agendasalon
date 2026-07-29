import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const publicBookingScript = readFileSync(
  new URL("../static/js/public_booking.js", import.meta.url),
  "utf8",
);

test("la reserva pública sigue los cambios del tema del dispositivo", () => {
  let darkThemeIsActive = false;
  let handleThemeChange = null;
  const preferredTheme = {
    matches: true,
    addEventListener(eventName, callback) {
      assert.equal(eventName, "change");
      handleThemeChange = callback;
    },
  };
  const body = {
    classList: {
      contains(className) {
        return className === "theme-auto";
      },
      toggle(className, isActive) {
        assert.equal(className, "theme-dark");
        darkThemeIsActive = isActive;
      },
    },
  };

  vm.runInNewContext(publicBookingScript, {
    document: {
      body,
      querySelector() {
        return null;
      },
    },
    window: {
      matchMedia(query) {
        assert.equal(query, "(prefers-color-scheme: dark)");
        return preferredTheme;
      },
    },
  });

  assert.equal(darkThemeIsActive, true);
  assert.equal(typeof handleThemeChange, "function");
  handleThemeChange({ matches: false });
  assert.equal(darkThemeIsActive, false);
});

test("cambiar servicios o fecha lleva al formulario y devuelve el foco", () => {
  let clickHandler = null;
  let didPreventDefault = false;
  let scrollOptions = null;
  let focusOptions = null;
  const service = {
    checked: true,
    dataset: { duration: "30", price: "22" },
    addEventListener() {},
    focus(options) {
      focusOptions = options;
    },
  };
  const countNode = { textContent: "" };
  const totalNode = { textContent: "" };
  const form = {
    scrollIntoView(options) {
      scrollOptions = options;
    },
    querySelectorAll(selector) {
      assert.equal(selector, "[data-booking-service]");
      return [service];
    },
    querySelector(selector) {
      if (selector === "[data-booking-count]") return countNode;
      if (selector === "[data-booking-total]") return totalNode;
      if (selector === "[data-booking-service]:checked") return service;
      return null;
    },
  };
  const changeSearchLink = {
    addEventListener(eventName, callback) {
      assert.equal(eventName, "click");
      clickHandler = callback;
    },
  };

  vm.runInNewContext(publicBookingScript, {
    document: {
      body: { classList: { contains: () => false, toggle() {} } },
      querySelector(selector) {
        if (selector === "[data-booking-search]") return form;
        if (selector === "[data-booking-change-search]") return changeSearchLink;
        return null;
      },
    },
    window: {
      requestAnimationFrame(callback) {
        callback();
      },
    },
  });

  assert.equal(typeof clickHandler, "function");
  clickHandler({
    preventDefault() {
      didPreventDefault = true;
    },
  });

  assert.equal(didPreventDefault, true);
  assert.equal(scrollOptions.block, "start");
  assert.equal(focusOptions.preventScroll, true);
  assert.equal(countNode.textContent, "1 servicio · 30 min");
  assert.equal(totalNode.textContent, "22,00 €");
});
