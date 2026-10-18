/* Podpowiedź literówek w przeglądarce (``apps/email_delivery/static/email_delivery/email-check.js``,
 * MAIL-02 § 1.4) – ``node --test backend/js_tests``. Te same przypadki co po stronie serwera
 * (``apps/email_delivery/tests/typo_cases.json``): skrypt i ``typos.py`` mają dawać te same odpowiedzi.
 * Plik wykonuje się w piaskownicy ``vm`` z atrapą ``window`` i minimalnym DOM-em. Uruchamia go też
 * ``apps/email_delivery/tests/test_js.py`` (pomija się, gdy nie ma Node).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..", "apps", "email_delivery");
const SOURCE = fs.readFileSync(path.join(ROOT, "static", "email_delivery", "email-check.js"), "utf8");
const CASES = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "typo_cases.json"), "utf8")).cases;

function fakeDocument(inputs) {
  const byId = {};
  const listeners = [];
  const elements = [];
  function element(tag) {
    const node = {
      tagName: tag, children: [], attributes: {}, listeners: {}, textContent: "", id: "",
      parentNode: null,
      setAttribute(name, value) { this.attributes[name] = value; },
      getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; },
      appendChild(child) { child.parentNode = this; this.children.push(child); if (child.id) { byId[child.id] = child; } return child; },
      insertBefore(child) { return this.appendChild(child); },
      removeChild(child) { this.children = this.children.filter((item) => item !== child); delete byId[child.id]; },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      focus() {}
    };
    elements.push(node);
    return node;
  }
  const parent = element("p");
  for (const input of inputs) {
    const node = element("input");
    Object.assign(node, input);
    node.attributes = Object.assign({ "data-email-hint": "Did you mean %s?", "data-email-use": "Use %s" }, input.attributes || {});
    node.nextSibling = null;
    parent.appendChild(node);
    byId[node.id] = node;
  }
  return {
    readyState: "complete",
    getElementById: (id) => byId[id] || null,
    querySelectorAll: () => parent.children.filter((child) => child.tagName === "input"),
    createElement: (tag) => element(tag),
    createTextNode: (text) => ({ textContent: text, nodeType: 3 }),
    addEventListener: (type, fn) => listeners.push([type, fn]),
    byId
  };
}

function load(inputs) {
  const window = {};
  const document = fakeDocument(inputs || []);
  vm.runInNewContext(SOURCE, { window, document, Object, Math, String, Array });
  return { api: window.OlimpiadaEmailCheck, document };
}

test("shared cases give the same answers as the server", () => {
  const { api } = load();
  for (const [address, expected] of CASES) {
    assert.equal(api.suggest(address), expected, address);
  }
});

test("blur shows a hint with a button that fixes the value", () => {
  const { document } = load([{ id: "id_email", value: "jan@gmial.com" }]);
  const input = document.byId.id_email;

  input.listeners.blur();
  const hint = document.getElementById("id_email_hint");
  assert.ok(hint, "brak podpowiedzi");
  assert.equal(hint.getAttribute("role"), "status");
  const button = hint.children.find((child) => child.tagName === "button");
  assert.equal(button.textContent, "Use jan@gmail.com");
  assert.equal(button.type, "button");

  button.listeners.click();
  assert.equal(input.value, "jan@gmail.com");
  assert.equal(document.getElementById("id_email_hint"), null);
});

test("correct address shows nothing and removes an old hint", () => {
  const { document } = load([{ id: "id_email", value: "jan@gmial.com" }]);
  const input = document.byId.id_email;
  input.listeners.blur();
  input.value = "jan@gmail.com";
  input.listeners.blur();
  assert.equal(document.getElementById("id_email_hint"), null);
});

function loadWithServerButtons(withKeep) {
  const { document } = load([{ id: "id_email", value: "jan@gmial.com" }]);
  const block = document.createElement("span");
  block.id = "id_email_suggestion";
  document.byId.id_email.parentNode.appendChild(block);
  const accept = document.createElement("button");
  accept.id = "id_email_accept";
  accept.type = "submit";
  accept.value = "jan@gmail.com";
  block.appendChild(accept);
  let keep = null;
  if (withKeep) {
    keep = document.createElement("button");
    keep.id = "id_email_keep";
    keep.type = "submit";
    block.appendChild(keep);
  }
  // Ponowne wczytanie, bo skrypt wiąże przyciski serwera przy starcie.
  const window = {};
  vm.runInNewContext(SOURCE, { window, document, Object, Math, String, Array });
  return { document, accept, keep };
}

test("server buttons stop submitting the form once the script runs", () => {
  const { accept, keep } = loadWithServerButtons(true);
  assert.equal(accept.type, "button");
  assert.equal(keep.type, "button");
});

test("server fix button swaps the value without submitting", () => {
  const { document, accept } = loadWithServerButtons(true);
  accept.listeners.click();
  assert.equal(document.byId.id_email.value, "jan@gmail.com");
  assert.equal(document.getElementById("id_email_suggestion"), null);
});

test("keeping the address hides the server hint and silences the browser hint", () => {
  const { document, keep } = loadWithServerButtons(true);
  const input = document.byId.id_email;
  keep.listeners.click();
  assert.equal(input.value, "jan@gmial.com");
  assert.equal(document.getElementById("id_email_suggestion"), null);
  input.listeners.blur();
  assert.equal(document.getElementById("id_email_hint"), null);
  // Inny adres to nowe pytanie.
  input.value = "jan@hotmial.com";
  input.listeners.blur();
  assert.ok(document.getElementById("id_email_hint"));
});

test("dead domain has only the fix button", () => {
  const { document, accept, keep } = loadWithServerButtons(false);
  assert.equal(keep, null);
  accept.listeners.click();
  assert.equal(document.byId.id_email.value, "jan@gmail.com");
});
