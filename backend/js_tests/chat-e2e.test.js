/* Testy kryptografii rozmów szyfrowanych (``static/js/chat-e2e.js``) – ``node --test backend/js_tests``.
 *
 * Node ≥ 20 ma WebCrypto w ``globalThis.crypto``, więc test biegnie na **tej samej** implementacji,
 * co przeglądarka. Uruchamia go też ``apps/chat/tests/test_e2e_js.py`` (pomija się, gdy nie ma Node).
 * Iteracje PBKDF2 są tu obniżone – test sprawdza schemat, a nie koszt; jeden test mierzy wartość domyślną.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const E2E = require("../static/js/chat-e2e.js");

const FAST = 1000;

test("para kluczy: SPKI P-256 i odcisk SHA-256 w hex", async () => {
  const identity = await E2E.generateIdentity();
  const spki = E2E.fromB64(identity.publicKey);
  assert.equal(spki.length, 91);
  const fingerprint = await E2E.fingerprint(identity.publicKey);
  assert.match(fingerprint, /^[0-9a-f]{64}$/);
});

test("owinięcie hasłem i odwinięcie tym samym hasłem daje działający klucz", async () => {
  const alice = await E2E.generateIdentity();
  const bob = await E2E.generateIdentity();
  const bundle = await E2E.wrapPrivateKey(alice.pkcs8, "dlugie-haslo-do-wiadomosci", FAST);
  const unwrapped = await E2E.unwrapPrivateKey(bundle, "dlugie-haslo-do-wiadomosci", false);
  assert.equal(unwrapped.extractable, false);
  const key = await E2E.conversationKey(unwrapped, bob.publicKey, 7);
  const payload = await E2E.encryptMessage(key, "cześć", 7, alice.publicKey);
  const bobKey = await E2E.conversationKey(bob.privateKey, alice.publicKey, 7);
  assert.equal(await E2E.decryptMessage(bobKey, payload, 7, alice.publicKey), "cześć");
});

test("złe hasło kończy się błędem", async () => {
  const alice = await E2E.generateIdentity();
  const bundle = await E2E.wrapPrivateKey(alice.pkcs8, "dlugie-haslo-do-wiadomosci", FAST);
  await assert.rejects(() => E2E.unwrapPrivateKey(bundle, "inne-haslo-calkiem", false), /hasło/);
});

test("za krótkie hasło jest odrzucane przed szyfrowaniem", async () => {
  const alice = await E2E.generateIdentity();
  await assert.rejects(() => E2E.wrapPrivateKey(alice.pkcs8, "krotkie", FAST), /co najmniej/);
});

test("domyślna liczba iteracji PBKDF2 to co najmniej 600 000", async () => {
  const alice = await E2E.generateIdentity();
  const bundle = await E2E.wrapPrivateKey(alice.pkcs8, "dlugie-haslo-do-wiadomosci");
  assert.ok(bundle.iterations >= 600000);
  assert.equal(E2E.fromB64(bundle.salt).length, 16);
  assert.equal(E2E.fromB64(bundle.iv).length, 12);
});

test("dwie pary: obie strony wyprowadzają ten sam klucz rozmowy", async () => {
  const alice = await E2E.generateIdentity();
  const bob = await E2E.generateIdentity();
  const aliceKey = await E2E.conversationKey(alice.privateKey, bob.publicKey, 42);
  const bobKey = await E2E.conversationKey(bob.privateKey, alice.publicKey, 42);
  const payload = await E2E.encryptMessage(aliceKey, "Zadanie 3 jest trudne\nale da się.", 42, alice.publicKey);
  assert.equal(
    await E2E.decryptMessage(bobKey, payload, 42, alice.publicKey),
    "Zadanie 3 jest trudne\nale da się.",
  );
  assert.equal(E2E.fromB64(payload.iv).length, 12);
});

test("zła AAD (inny klucz nadawcy albo inna rozmowa) kończy się błędem", async () => {
  const alice = await E2E.generateIdentity();
  const bob = await E2E.generateIdentity();
  const key = await E2E.conversationKey(alice.privateKey, bob.publicKey, 42);
  const payload = await E2E.encryptMessage(key, "tajne", 42, alice.publicKey);
  // Ta sama treść podpisana jako wiadomość Boba – AAD z jego kluczem nie pasuje.
  await assert.rejects(() => E2E.decryptMessage(key, payload, 42, bob.publicKey));
  const otherConversation = await E2E.conversationKey(alice.privateKey, bob.publicKey, 43);
  await assert.rejects(() => E2E.decryptMessage(otherConversation, payload, 43, alice.publicKey));
});

test("odszyfrowanie nie potrzebuje identyfikatora konta nadawcy (konto usunięte)", async () => {
  // Odbiorca ma na wiadomości wyłącznie to, co zapisał serwer: klucze obu stron, szyfrogram i IV.
  const alice = await E2E.generateIdentity();
  const bob = await E2E.generateIdentity();
  const aliceKey = await E2E.conversationKey(alice.privateKey, bob.publicKey, 9);
  const stored = {
    senderKey: alice.publicKey,
    recipientKey: bob.publicKey,
    ...(await E2E.encryptMessage(aliceKey, "zostaję po usunięciu konta", 9, alice.publicKey)),
  };
  const bobKey = await E2E.conversationKey(bob.privateKey, stored.senderKey, 9);
  assert.equal(await E2E.decryptMessage(bobKey, stored, 9, stored.senderKey), "zostaję po usunięciu konta");
});

test("trzecia osoba nie odszyfruje rozmowy dwóch innych", async () => {
  const alice = await E2E.generateIdentity();
  const bob = await E2E.generateIdentity();
  const eve = await E2E.generateIdentity();
  const key = await E2E.conversationKey(alice.privateKey, bob.publicKey, 5);
  const payload = await E2E.encryptMessage(key, "tylko dla Boba", 5, alice.publicKey);
  const eveKey = await E2E.conversationKey(eve.privateKey, alice.publicKey, 5);
  await assert.rejects(() => E2E.decryptMessage(eveKey, payload, 5, alice.publicKey));
});
