/* Szyfrowanie end-to-end rozmów między uczestnikami (zadanie CZ-01, § 11) – czysta kryptografia.
 *
 * Wyłącznie WebCrypto (``crypto.subtle``), bez żadnej biblioteki: ta sama implementacja działa
 * w przeglądarce i w Node ≥ 20, więc da się ją sprawdzić testem (``backend/js_tests``) bez
 * przeglądarki. Plik nie dotyka DOM-u ani IndexedDB – to robi ``chat-ui.js``.
 *
 * Schemat:
 * - klucz tożsamości: ECDH P-256, klucz publiczny eksportowany jako SPKI (base64),
 * - klucz rozmowy: ECDH(mój prywatny, jego publiczny) → HKDF-SHA-256 (sól = identyfikator rozmowy,
 *   info = "olimpiada-chat-v1") → AES-GCM 256. Sól z identyfikatora rozmowy sprawia, że ta sama para
 *   osób ma w każdej rozmowie (i w każdym konkursie) inny klucz,
 * - wiadomość: AES-GCM z losowym IV (12 B) i AAD = "v1|<id rozmowy>|<klucz publiczny nadawcy>" –
 *   szyfrogramu nie da się przenieść do innej rozmowy ani podpisać jako wiadomość drugiej strony.
 *   Nadawcę wiąże jego **klucz publiczny** (SPKI, base64), a nie identyfikator konta: klucz stoi na
 *   każdej wiadomości (serwer wpisuje go z bieżącego ``ChatKey``) i przeżywa usunięcie konta
 *   nadawcy, a ``Message.sender`` jest wtedy ``NULL`` – AAD z identyfikatorem konta robiłaby
 *   z historii drugiej strony nieczytelny szyfrogram,
 * - kopia klucza prywatnego na serwerze: PKCS8 zaszyfrowany AES-GCM kluczem z PBKDF2-SHA-256
 *   (≥ 600 000 iteracji, losowa sól 16 B) z hasła do wiadomości, którego serwer nie zna.
 *
 * Moduł jest UMD bez zależności: w przeglądarce ustawia ``window.ChatE2E``, w Node – ``module.exports``.
 */
(function (root, factory) {
  "use strict";
  const api = factory(root.crypto);
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ChatE2E = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (webcrypto) {
  "use strict";

  const subtle = webcrypto.subtle;
  const INFO = "olimpiada-chat-v1";
  const PBKDF2_ITERATIONS = 600000;
  const MIN_PASSPHRASE = 10;
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function toB64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
    return btoa(binary);
  }

  function fromB64(text) {
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function random(length) {
    return webcrypto.getRandomValues(new Uint8Array(length));
  }

  /* Nowa para kluczy tożsamości. Klucz prywatny jest tu **eksportowalny** wyłącznie po to, żeby
   * dało się go raz owinąć hasłem; do codziennej pracy ``chat-ui.js`` importuje go ponownie jako
   * nieeksportowalny (``unwrapPrivateKey``). */
  async function generateIdentity() {
    const pair = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    const spki = await subtle.exportKey("spki", pair.publicKey);
    const pkcs8 = await subtle.exportKey("pkcs8", pair.privateKey);
    return { publicKey: toB64(spki), pkcs8: new Uint8Array(pkcs8), privateKey: pair.privateKey };
  }

  async function passphraseKey(passphrase, salt, iterations) {
    const material = await subtle.importKey("raw", encoder.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return subtle.deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt: salt, iterations: iterations },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  /* Owija PKCS8 hasłem. Zwraca komplet pól, które serwer zapisuje w ``ChatKey``. */
  async function wrapPrivateKey(pkcs8, passphrase, iterations) {
    if (!passphrase || passphrase.length < MIN_PASSPHRASE) {
      throw new Error("Hasło do wiadomości musi mieć co najmniej " + MIN_PASSPHRASE + " znaków.");
    }
    const rounds = iterations || PBKDF2_ITERATIONS;
    const salt = random(16);
    const iv = random(12);
    const key = await passphraseKey(passphrase, salt, rounds);
    const wrapped = await subtle.encrypt({ name: "AES-GCM", iv: iv }, key, pkcs8);
    return { wrapped: toB64(wrapped), salt: toB64(salt), iv: toB64(iv), iterations: rounds };
  }

  /* Odwija klucz prywatny hasłem. Złe hasło kończy się wyjątkiem (znacznik GCM się nie zgadza). */
  async function unwrapPrivateKey(bundle, passphrase, extractable) {
    const key = await passphraseKey(passphrase, fromB64(bundle.salt), Number(bundle.iterations));
    let pkcs8;
    try {
      pkcs8 = await subtle.decrypt({ name: "AES-GCM", iv: fromB64(bundle.iv) }, key, fromB64(bundle.wrapped));
    } catch (error) {
      throw new Error("Nieprawidłowe hasło do wiadomości.");
    }
    return subtle.importKey("pkcs8", pkcs8, { name: "ECDH", namedCurve: "P-256" }, !!extractable, [
      "deriveBits",
    ]);
  }

  async function importPublicKey(spkiB64) {
    return subtle.importKey("spki", fromB64(spkiB64), { name: "ECDH", namedCurve: "P-256" }, false, []);
  }

  /* Klucz rozmowy: ECDH → HKDF-SHA-256 (sól = id rozmowy, info = wersja schematu) → AES-GCM 256. */
  async function conversationKey(privateKey, otherPublicB64, conversationId) {
    const otherPublic = await importPublicKey(otherPublicB64);
    const shared = await subtle.deriveBits({ name: "ECDH", public: otherPublic }, privateKey, 256);
    const hkdf = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    return subtle.deriveKey(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: encoder.encode(String(conversationId)),
        info: encoder.encode(INFO),
      },
      hkdf,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  function additionalData(conversationId, senderPublicKey) {
    return encoder.encode("v1|" + String(conversationId) + "|" + String(senderPublicKey));
  }

  async function encryptMessage(key, plaintext, conversationId, senderPublicKey) {
    const iv = random(12);
    const ciphertext = await subtle.encrypt(
      { name: "AES-GCM", iv: iv, additionalData: additionalData(conversationId, senderPublicKey) },
      key,
      encoder.encode(plaintext),
    );
    return { ciphertext: toB64(ciphertext), iv: toB64(iv) };
  }

  async function decryptMessage(key, payload, conversationId, senderPublicKey) {
    const plaintext = await subtle.decrypt(
      {
        name: "AES-GCM",
        iv: fromB64(payload.iv),
        additionalData: additionalData(conversationId, senderPublicKey),
      },
      key,
      fromB64(payload.ciphertext),
    );
    return decoder.decode(plaintext);
  }

  /* Odcisk klucza publicznego: SHA-256 z SPKI, szesnastkowo – ten sam, który liczy serwer. */
  async function fingerprint(spkiB64) {
    const digest = new Uint8Array(await subtle.digest("SHA-256", fromB64(spkiB64)));
    return Array.from(digest, function (byte) {
      return byte.toString(16).padStart(2, "0");
    }).join("");
  }

  return {
    INFO: INFO,
    PBKDF2_ITERATIONS: PBKDF2_ITERATIONS,
    MIN_PASSPHRASE: MIN_PASSPHRASE,
    toB64: toB64,
    fromB64: fromB64,
    generateIdentity: generateIdentity,
    wrapPrivateKey: wrapPrivateKey,
    unwrapPrivateKey: unwrapPrivateKey,
    importPublicKey: importPublicKey,
    conversationKey: conversationKey,
    encryptMessage: encryptMessage,
    decryptMessage: decryptMessage,
    fingerprint: fingerprint,
  };
});
