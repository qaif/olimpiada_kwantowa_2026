/* Loader błędów JavaScriptu (``apps/monitoring/static/monitoring/errors.js``, OPS-02 § 5) –
 * ``node --test backend/js_tests``. Plik jest wykonywany w piaskownicy ``vm`` z atrapami ``window``,
 * ``document`` i ``fetch``: sprawdzamy, co naprawdę wyszłoby do GlitchTipa. Uruchamia go też
 * ``apps/monitoring/tests/test_errors_js.py`` (pomija się, gdy nie ma Node).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "apps", "monitoring", "static", "monitoring", "errors.js"), "utf8");

function load(attrs) {
  const listeners = {};
  const sent = [];
  const script = { getAttribute: (name) => (attrs[name] === undefined ? null : attrs[name]) };
  const window = {
    crypto: webcrypto,
    fetch: (url, options) => { sent.push({ url, options }); return Promise.resolve(); },
    addEventListener: (type, fn) => { listeners[type] = fn; }
  };
  const context = {
    window,
    document: { currentScript: script },
    location: { origin: "https://iqo-official.org", pathname: "/delegation/" },
    Date, JSON, String, Array, Uint8Array, parseInt, Promise, Error
  };
  vm.runInNewContext(SOURCE, context);
  return { listeners, sent, context };
}

const ATTRS = {
  "data-endpoint": "https://errors.example.org/api/3/envelope/?sentry_key=k&sentry_version=7",
  "data-release": "v1.2.3",
  "data-environment": "production",
  "data-competition": "iqo"
};

function event(sent) {
  const lines = sent.options.body.split("\n");
  assert.equal(lines.length, 3);
  assert.deepEqual(JSON.parse(lines[1]), { type: "event" });
  return JSON.parse(lines[2]);
}

test("bez data-endpoint nic się nie podpina", () => {
  const { listeners } = load({});
  assert.deepEqual(Object.keys(listeners), []);
});

test("błąd strony wychodzi bez e-maila, PESEL-u, tokenu i zapytania w adresie", () => {
  const { listeners, sent } = load(ATTRS);
  const err = new Error("Nie zapisano jan.kowalski@example.org PESEL 08241512345 ?token=abc123");
  err.stack = "Error: x\n    at save (https://iqo-official.org/static/js/app.js?v=1:10:5)";
  listeners.error({ error: err, message: err.message });

  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, ATTRS["data-endpoint"]);
  assert.equal(sent[0].options.credentials, "omit");
  assert.equal(sent[0].options.headers["Content-Type"], "text/plain;charset=UTF-8");
  const body = sent[0].options.body;
  for (const secret of ["jan.kowalski", "08241512345", "abc123", "v=1"]) {
    assert.ok(!body.includes(secret), secret);
  }
  const ev = event(sent[0]);
  assert.equal(ev.request.url, "https://iqo-official.org/delegation/");
  assert.equal(ev.tags.competition, "iqo");
  assert.equal(ev.release, "v1.2.3");
  assert.equal(ev.exception.values[0].stacktrace.frames[0].filename, "https://iqo-official.org/static/js/app.js");
  assert.equal(ev.user, undefined);
});

test("duplikaty pomijane, najwyżej pięć zdarzeń na stronę", () => {
  const { listeners, sent } = load(ATTRS);
  for (let i = 0; i < 3; i += 1) listeners.error({ message: "ten sam" });
  for (let i = 0; i < 10; i += 1) listeners.error({ message: "błąd " + i });
  assert.equal(sent.length, 5);
});

test("odrzucona obietnica i błąd ładowania zasobu", () => {
  const { listeners, sent } = load(ATTRS);
  listeners.error({});  // <img> bez pliku – brak komunikatu, brak zdarzenia
  listeners.unhandledrejection({ reason: "nie działa" });
  assert.equal(sent.length, 1);
  assert.equal(event(sent[0]).exception.values[0].type, "UnhandledRejection");
});
