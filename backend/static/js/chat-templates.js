/* Szablony odpowiedzi koordynatora (zadanie CZ-01, § 12.1): „Wstaw” wkleja treść szablonu do pola
 * odpowiedzi w miejscu kursora.
 *
 * Wyłącznie ``textarea.value`` – nigdy HTML: treść szablonu i imię uczestnika są tekstem i tekstem
 * zostają. Znacznik ``{imie}`` zamienia się na imię z ``data-first-name`` formularza (serwer wstawia
 * tam imię uczestnika tej rozmowy). Delegacja zdarzeń na dokumencie, bo wątek bywa podmieniany przez
 * htmx po każdej odpowiedzi.
 */
(function () {
  "use strict";

  function fill(template, firstName) {
    return template.split("{imie}").join(firstName || "");
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest && event.target.closest("[data-chat-template]");
    if (!button) return;
    event.preventDefault();
    const thread = button.closest("#chat-thread") || document;
    const form = thread.querySelector("form.chat-form[data-first-name]");
    const field = form && form.querySelector("textarea");
    if (!field) return;
    const text = fill(button.getAttribute("data-chat-template") || "", form.getAttribute("data-first-name"));
    const start = typeof field.selectionStart === "number" ? field.selectionStart : field.value.length;
    const end = typeof field.selectionEnd === "number" ? field.selectionEnd : field.value.length;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    const caret = start + text.length;
    field.focus();
    field.setSelectionRange(caret, caret);
    const details = button.closest("details");
    if (details) details.open = false;
  });
})();
