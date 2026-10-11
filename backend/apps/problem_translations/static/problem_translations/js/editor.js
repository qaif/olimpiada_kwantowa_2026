/* Edytor tłumaczenia (TR-01): widoczna porażka autozapisu.
 *
 * Odmowy, które serwer umie opisać (okno zamknięte w trakcie pisania, konflikt z drugim opiekunem,
 * zablokowane tłumaczenie), przychodzą jako 200 z komunikatem w ``#autosave-status``
 * (``AutosaveView``). Ten skrypt łapie resztę: odpowiedź 4xx/5xx spoza widoku (odebrana rola,
 * wygasła sesja, limit żądań bez fragmentu) i brak sieci. htmx domyślnie nic wtedy nie pokazuje,
 * a cichy autozapis, który przestał zapisywać, to utracona praca – więc w miejscu stanu zapisu stawiamy
 * stały komunikat z ``data-error-message`` (tłumaczy go serwer; przeglądarka nie ma katalogu).
 *
 * Plik statyczny z nonce, bez ``hx-on`` i bez skryptów inline – CSP bez wyjątków. Do DOM trafia
 * wyłącznie ``textContent``.
 */
(function () {
  "use strict";

  function statusBox() {
    return document.getElementById("autosave-status");
  }

  function showFailure() {
    const box = statusBox();
    if (!box) {
      return;
    }
    const message = document.createElement("p");
    message.className = "msg msg-error";
    message.setAttribute("role", "alert");
    message.textContent = box.getAttribute("data-error-message") || "";
    box.replaceChildren(message);
  }

  function isAutosave(event) {
    const target = event.detail && event.detail.target;
    return Boolean(target && target.id === "autosave-status");
  }

  document.addEventListener("htmx:responseError", function (event) {
    if (isAutosave(event) && event.detail.xhr && event.detail.xhr.status !== 429) {
      showFailure();
    }
  });
  document.addEventListener("htmx:sendError", function (event) {
    if (isAutosave(event)) {
      showFailure();
    }
  });
})();
