/* Rozmowy szyfrowane (zadanie CZ-01, § 11) – podpięcie kryptografii (``chat-e2e.js``) do stron.
 *
 * Zasady, których ten plik pilnuje:
 * - **jawna treść nie opuszcza przeglądarki.** Pole wiadomości w rozmowie szyfrowanej nie ma
 *   atrybutu ``name``; skrypt szyfruje tekst i wpisuje szyfrogram do ukrytych pól, a dopiero potem
 *   pozwala htmx wysłać formularz (``htmx:confirm`` → ``issueRequest``). Bez skryptu formularz
 *   wysyła puste pola i serwer odmawia,
 * - **odszyfrowana treść idzie wyłącznie przez ``textContent``** – nigdy ``innerHTML``. Łamanie
 *   wierszy robi CSS (``white-space: pre-wrap``), a nie znaczniki,
 * - **klucz prywatny po odblokowaniu jest nieeksportowalny** (``CryptoKey`` w IndexedDB) – skrypt
 *   strony nie może go odczytać ani wysłać; hasło do wiadomości nie jest nigdzie zapisywane.
 *   Zapis w IndexedDB czyści przycisk „Zablokuj wiadomości” i wylogowanie (``app.js``).
 *
 * Konfiguracja przychodzi z atrybutów ``data-*`` elementu ``#chat-e2e`` (klucz publiczny, odcisk,
 * owinięta kopia klucza prywatnego tej osoby). Zero skryptu w treści strony – CSP.
 */
(function () {
  "use strict";

  const E2E = window.ChatE2E;
  const DB_NAME = "olimpiada-chat";
  const STORE = "keys";
  const SNIPPET = 80;
  const MAX_LENGTH = 4000;
  const config = document.getElementById("chat-e2e");
  if (!E2E || !config || !window.indexedDB || !window.crypto || !window.crypto.subtle) return;

  const data = config.dataset;
  const state = { privateKey: null };
  const derived = new Map();

  // --- IndexedDB ------------------------------------------------------------------------------------

  function openDb() {
    return new Promise(function (resolve, reject) {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = function () {
        request.result.createObjectStore(STORE);
      };
      request.onsuccess = function () {
        resolve(request.result);
      };
      request.onerror = function () {
        reject(request.error);
      };
    });
  }

  function withStore(mode, action) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        const transaction = db.transaction(STORE, mode);
        const request = action(transaction.objectStore(STORE));
        transaction.oncomplete = function () {
          db.close();
          resolve(request.result);
        };
        transaction.onerror = function () {
          db.close();
          reject(transaction.error);
        };
      });
    });
  }

  function recordId() {
    return data.userId + ":" + data.competitionId;
  }

  function loadKey() {
    if (!data.publicKey) return Promise.resolve(null);
    return withStore("readonly", function (store) {
      return store.get(recordId());
    })
      .then(function (record) {
        // Klucz z innego odcisku to klucz sprzed „Utwórz nowy klucz” – nie pasuje do bieżącego
        // klucza publicznego, więc nie odszyfruje niczego nowego. Traktujemy go jak brak klucza.
        return record && record.fingerprint === data.fingerprint ? record.key : null;
      })
      .catch(function () {
        return null;
      });
  }

  function storeKey(key, fingerprint) {
    return withStore("readwrite", function (store) {
      return store.put({ key: key, fingerprint: fingerprint }, recordId());
    });
  }

  function forgetKey() {
    return withStore("readwrite", function (store) {
      return store.delete(recordId());
    }).catch(function () {
      return null;
    });
  }

  // --- stan zablokowany / odblokowany ----------------------------------------------------------------

  function applyState(scope) {
    const unlocked = !!state.privateKey;
    (scope || document).querySelectorAll("[data-e2e-when]").forEach(function (element) {
      const wanted = element.getAttribute("data-e2e-when");
      element.hidden = wanted === "unlocked" ? !unlocked : unlocked;
    });
  }

  function showError(container, text) {
    const target = container && container.querySelector("[data-e2e-error]");
    if (target) {
      target.textContent = text;
      target.hidden = !text;
    }
  }

  // --- odszyfrowanie -----------------------------------------------------------------------------------

  function conversationKey(conversationId, otherKey) {
    const cacheKey = conversationId + "|" + otherKey;
    if (!derived.has(cacheKey)) {
      derived.set(cacheKey, E2E.conversationKey(state.privateKey, otherKey, conversationId));
    }
    return derived.get(cacheKey);
  }

  function decryptOne(element) {
    const item = element.dataset;
    const target = element.querySelector("[data-e2e-plaintext]") || element;
    if (!state.privateKey || item.e2eDone) return Promise.resolve();
    const own = item.senderId === data.userId;
    const myKeyUsed = own ? item.senderKey : item.recipientKey;
    const otherKey = own ? item.recipientKey : item.senderKey;
    element.dataset.e2eDone = "1";
    if (myKeyUsed !== data.publicKey) {
      target.textContent = "Wiadomość zaszyfrowana poprzednim kluczem – nie da się jej już odczytać.";
      return Promise.resolve();
    }
    return conversationKey(item.conversationId, otherKey)
      .then(function (key) {
        return E2E.decryptMessage(key, { ciphertext: item.ciphertext, iv: item.iv }, item.conversationId, item.senderId);
      })
      .then(function (text) {
        const snippet = element.hasAttribute("data-e2e-snippet");
        const clean = snippet ? text.replace(/\s+/g, " ").trim() : text;
        target.textContent = snippet && clean.length > SNIPPET ? clean.slice(0, SNIPPET - 1) + "…" : clean;
        const report = element.querySelector('input[name="reported_plaintext"]');
        if (report) report.value = text;
      })
      .catch(function () {
        target.textContent = "Nie udało się odszyfrować tej wiadomości.";
      });
  }

  function decryptIn(scope) {
    (scope || document).querySelectorAll("[data-e2e-message]").forEach(decryptOne);
  }

  // --- wysyłanie -------------------------------------------------------------------------------------

  function encryptForm(form) {
    const input = form.querySelector("[data-e2e-input]");
    const text = input ? input.value.trim() : "";
    showError(form, "");
    if (!state.privateKey) {
      showError(form, "Odblokuj szyfrowanie (hasło do wiadomości), żeby wysłać wiadomość.");
      return Promise.resolve(false);
    }
    if (!text) {
      showError(form, "Wiadomość nie może być pusta.");
      return Promise.resolve(false);
    }
    if (text.length > MAX_LENGTH) {
      showError(form, "Wiadomość jest za długa (limit " + MAX_LENGTH + " znaków).");
      return Promise.resolve(false);
    }
    const conversation = form.dataset.conversationId;
    return conversationKey(conversation, form.dataset.otherKey)
      .then(function (key) {
        return E2E.encryptMessage(key, text, conversation, data.userId);
      })
      .then(function (payload) {
        form.querySelector('input[name="ciphertext"]').value = payload.ciphertext;
        form.querySelector('input[name="iv"]').value = payload.iv;
        input.value = "";
        return true;
      })
      .catch(function () {
        showError(form, "Nie udało się zaszyfrować wiadomości.");
        return false;
      });
  }

  document.addEventListener("htmx:confirm", function (event) {
    const form = event.detail && event.detail.elt;
    if (!form || !form.matches || !form.matches("form[data-e2e-form]")) return;
    event.preventDefault();
    encryptForm(form).then(function (ok) {
      if (ok) event.detail.issueRequest(true);
    });
  });

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!form.matches) return;
    if (form.matches("form[data-e2e-form]") && !window.htmx) {
      event.preventDefault();
      encryptForm(form).then(function (ok) {
        if (ok) HTMLFormElement.prototype.submit.call(form);
      });
    } else if (form.matches("form[data-e2e-unlock]")) {
      event.preventDefault();
      unlock(form);
    } else if (form.matches("form[data-e2e-setup]")) {
      event.preventDefault();
      setup(form);
    } else if (form.matches("form[data-e2e-report]")) {
      const copy = form.querySelector('input[name="reported_plaintext"]');
      if (copy && !copy.value) {
        event.preventDefault();
        showError(form, "Odblokuj szyfrowanie, żeby zgłosić tę wiadomość – jej treść musi trafić do organizatora.");
      }
    }
  });

  document.addEventListener("click", function (event) {
    const button = event.target.closest && event.target.closest("[data-e2e-lock]");
    if (!button) return;
    event.preventDefault();
    forgetKey().then(function () {
      state.privateKey = null;
      window.location.reload();
    });
  });

  /* Po podmianie fragmentu przez htmx – na całym dokumencie, a nie na ``detail.target``: przy
   * ``hx-swap="outerHTML"`` cel zdarzenia jest **starym**, już odpiętym elementem, więc szukanie
   * w nim nowych wiadomości nic by nie znalazło. Już odszyfrowane mają znacznik ``data-e2e-done``. */
  document.addEventListener("htmx:afterSettle", function () {
    applyState(document);
    decryptIn(document);
  });

  // --- odblokowanie i utworzenie klucza ----------------------------------------------------------------

  function unlock(form) {
    const input = form.querySelector("[data-e2e-passphrase]");
    const bundle = { wrapped: data.wrapped, salt: data.salt, iv: data.iv, iterations: data.iterations };
    showError(form, "");
    E2E.unwrapPrivateKey(bundle, input ? input.value : "", false)
      .then(function (key) {
        state.privateKey = key;
        if (input) input.value = "";
        return storeKey(key, data.fingerprint);
      })
      .then(function () {
        applyState(document);
        decryptIn(document);
      })
      .catch(function (error) {
        showError(form, (error && error.message) || "Nie udało się odblokować klucza.");
      });
  }

  function setup(form) {
    const inputs = form.querySelectorAll("[data-e2e-new-passphrase]");
    const first = inputs[0] ? inputs[0].value : "";
    const second = inputs[1] ? inputs[1].value : "";
    showError(form, "");
    if (first.length < E2E.MIN_PASSPHRASE) {
      showError(form, "Hasło do wiadomości musi mieć co najmniej " + E2E.MIN_PASSPHRASE + " znaków.");
      return;
    }
    if (first !== second) {
      showError(form, "Hasła nie są takie same.");
      return;
    }
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    let identity;
    let bundle;
    E2E.generateIdentity()
      .then(function (created) {
        identity = created;
        return E2E.wrapPrivateKey(identity.pkcs8, first);
      })
      .then(function (wrapped) {
        bundle = wrapped;
        // Do pracy – kopia **nieeksportowalna**, odwinięta tym samym hasłem.
        return E2E.unwrapPrivateKey(bundle, first, false);
      })
      .then(function (key) {
        return E2E.fingerprint(identity.publicKey).then(function (fingerprint) {
          return storeKey(key, fingerprint);
        });
      })
      .then(function () {
        form.querySelector('input[name="public_key"]').value = identity.publicKey;
        form.querySelector('input[name="wrapped_private_key"]').value = bundle.wrapped;
        form.querySelector('input[name="kdf_salt"]').value = bundle.salt;
        form.querySelector('input[name="wrap_iv"]').value = bundle.iv;
        form.querySelector('input[name="kdf_iterations"]').value = String(bundle.iterations);
        inputs.forEach(function (input) {
          input.value = "";
        });
        HTMLFormElement.prototype.submit.call(form);
      })
      .catch(function (error) {
        if (button) button.disabled = false;
        showError(form, (error && error.message) || "Nie udało się utworzyć klucza.");
      });
  }

  // --- start -----------------------------------------------------------------------------------------

  loadKey().then(function (key) {
    state.privateKey = key;
    applyState(document);
    decryptIn(document);
  });
})();
