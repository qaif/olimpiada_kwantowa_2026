/* Wiadomości (zadanie CZ-01): „przeczytane” tylko wtedy, gdy człowiek naprawdę patrzy na wątek.
 *
 * Wątek odpytuje swój fragment co 15 s (``hx-trigger`` w ``_messages.html``) także w karcie
 * schowanej w tle. Gdyby każde takie odpytanie oznaczało rozmowę jako przeczytaną, uczestnik
 * z zapomnianą kartą nie dostałby kropki ani listu, a u koordynatora schowana karta gasiłaby
 * odznakę całemu zespołowi. Dlatego odpytanie niesie nagłówek ``X-Chat-Seen: 1`` wyłącznie wtedy,
 * gdy karta jest widoczna i ma fokus – a widok oznacza odczyt tylko z tym nagłówkiem.
 *
 * Filtr w samym ``hx-trigger`` (``every 15s [document.hasFocus()]``) byłby krótszy, ale htmx
 * kompiluje go przez ``Function`` – czyli wymaga ``unsafe-eval`` w CSP. Zdarzenie jest bezpieczne.
 * Powrót do karty wywołuje odpytanie od razu (zdarzenie ``chat-visible``), żeby kropka zgasła
 * wtedy, gdy człowiek spojrzał, a nie kwadrans minuty później.
 */
(function () {
  "use strict";

  function seen() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  document.addEventListener("htmx:configRequest", function (event) {
    const detail = event.detail || {};
    if (String(detail.path || "").indexOf("fragment=messages") !== -1 && seen()) {
      detail.headers["X-Chat-Seen"] = "1";
    }
  });

  function poke() {
    if (seen()) document.dispatchEvent(new CustomEvent("chat-visible"));
  }

  document.addEventListener("visibilitychange", poke);
  window.addEventListener("focus", poke);
})();
