/* Szablony komentarzy w panelu recenzenta: przycisk „Wstaw” dopisuje treść do pola komentarza.
 *
 * Czym ten skrypt jest, a czym nie: dokładką. Bez niego sekcja „Szablony komentarzy” działa
 * w całości – treść każdego szablonu stoi na ekranie i da się ją zaznaczyć oraz skopiować.
 * Dlatego przyciski „Wstaw” są w HTML-u ukryte (``hidden``) i odsłania je dopiero ten plik:
 * przycisk, który bez JavaScriptu nic nie robi, obiecywałby czynność, której nie ma.
 *
 * Zasady bezpieczeństwa (strict CSP, żadnego skryptu inline):
 * - nasłuch jest **jeden**, na całej liście (delegacja zdarzeń), a nie po jednym na przycisk.
 *   Dzięki temu nie ma tu ani jednego atrybutu ``onclick`` w HTML-u,
 * - treść szablonu przychodzi atrybutem ``data-snippet-text`` – autoescapowanym przez Django –
 *   i trafia do pola formularza jako wartość, nigdy przez ``innerHTML``,
 * - skrypt nic nie wysyła. Wstawiony tekst zapisuje dopiero „Zapisz szkic” albo wysłanie oceny,
 *   czyli te same drogi, co dla tekstu napisanego ręcznie.
 *
 * Wstawianie dopisuje na **końcu** pola i nigdy nie nadpisuje tego, co recenzent już napisał:
 * szablon jest punktem wyjścia do poprawienia, a nie gotowym komentarzem.
 */
(function () {
  "use strict";

  var list = document.querySelector("[data-snippet-list]");
  if (!list) return;

  var target = document.getElementById(list.dataset.snippetTarget || "");
  if (!target) return;

  /* Przyciski istnieją w HTML-u, ale są ukryte do chwili, gdy jest co nimi zrobić. */
  var buttons = list.querySelectorAll("[data-snippet-insert]");
  for (var index = 0; index < buttons.length; index += 1) {
    buttons[index].hidden = false;
  }

  list.addEventListener("click", function (event) {
    var button = event.target.closest("[data-snippet-insert]");
    if (!button || !list.contains(button)) return;
    var text = button.dataset.snippetText || "";
    if (!text) return;
    var current = target.value || "";
    /* Pusty akapit między wstawkami: dwa szablony sklejone w jedno zdanie byłyby nieczytelne,
       a pusty wiersz jest tym, co recenzent i tak by wstawił ręcznie. */
    var separator = current === "" ? "" : current.endsWith("\n\n") ? "" : current.endsWith("\n") ? "\n" : "\n\n";
    target.value = current + separator + text;
    /* Kursor na końcu wstawionego tekstu: następne zdanie recenzent pisze zwykle właśnie tam. */
    target.focus();
    target.setSelectionRange(target.value.length, target.value.length);
  });
})();
