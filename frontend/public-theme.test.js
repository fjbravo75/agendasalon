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
