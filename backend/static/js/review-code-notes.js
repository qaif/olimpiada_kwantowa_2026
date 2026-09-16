/* Podgląd kodu w panelu recenzenta: kliknięcie numeru linii wpisuje ją do formularza uwagi.
 *
 * Dokładka, nie warunek działania. Listing, numery linii, podświetlenie wierszy z uwagami i sam
 * formularz renderuje serwer (``apps.grading.code_view``); bez tego pliku numer linii wpisuje się
 * ręcznie i wszystko działa tak samo. Skrypt oszczędza przewijania przy pięciusetlinijkowym
 * rozwiązaniu, a nie udostępnia niczego nowego.
 *
 * Strict CSP: jeden nasłuch na całym listingu (delegacja zdarzeń), zero atrybutów ``onclick``,
 * zero ``innerHTML``. Numer linii przychodzi atrybutem ``data-code-line`` – czyli z tego samego
 * miejsca, w którym wypisał go szablon.
 */
(function () {
  "use strict";

  var listing = document.querySelector("[data-code-listing]");
  var input = document.querySelector("[data-code-line-input]");
  if (!listing || !input) return;

  listing.addEventListener("click", function (event) {
    var button = event.target.closest("[data-code-line]");
    if (!button || !listing.contains(button)) return;
    input.value = button.dataset.codeLine || "";
    /* Kursor ląduje w polu treści, a nie w polu numeru: numer jest już wpisany, a recenzent
       kliknął linię właśnie po to, żeby coś o niej napisać. */
    var text = document.getElementById("id_note_text");
    if (text) text.focus();
  });
})();
