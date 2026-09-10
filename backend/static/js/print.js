/* Przycisk „Drukuj” przy dokumentach organizatora.
 *
 * Dlaczego osobny plik, a nie ``onclick="window.print()"``: w projekcie nie ma ani jednego
 * skryptu inline, dzięki czemu polityka CSP nie potrzebuje ``'unsafe-inline'`` dla ``script-src``
 * (patrz templates/base.html). Jeden atrybut ``onclick`` kosztowałby rozluźnienie tej polityki
 * na całym serwisie – nieproporcjonalnie dużo za jedno wywołanie ``window.print()``.
 *
 * Przycisk startuje ukryty (``hidden`` w szablonie) i pokazuje go dopiero ten skrypt. Bez
 * JavaScriptu w przeglądarce ``window.print()`` i tak by nie zadziałał, więc martwy przycisk
 * byłby obietnicą bez pokrycia; wydruk zostaje wtedy pod skrótem przeglądarki, a arkusz
 * (``@media print`` w static/css/app.css) i tak zdejmuje z kartki nawigację i stopkę.
 *
 * Obsługa jest delegowana z ``document``: szablon może mieć takich przycisków kilka (nagłówek
 * dokumentu, koniec strony), a skrypt nie musi o żadnym z nich wiedzieć z osobna.
 */

(function () {
  "use strict";

  const SELECTOR = "[data-print]";

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(SELECTOR).forEach(function (button) {
      button.hidden = false;
    });
  });

  document.addEventListener("click", function (event) {
    const trigger = event.target.closest(SELECTOR);
    if (!trigger) {
      return;
    }
    event.preventDefault();
    window.print();
  });
})();
