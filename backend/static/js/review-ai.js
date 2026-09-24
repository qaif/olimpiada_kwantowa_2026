/* Panele „Ocena AI” w panelu recenzenta: przyciski „Wstaw punkty AI jako punkt wyjścia”.
 *
 * Skrypt robi dokładnie jedno: zaznacza w formularzu oceny radio o wartości podanej przez serwer
 * (najbliższa propozycji wartość skali) albo – w etapie z dowolnymi wartościami ocen – wpisuje tę
 * wartość do pola liczbowego. Niczego nie wysyła i niczego nie zapisuje – ocenę
 * wystawia recenzent zwykłym przyciskiem, tak samo jak bez sugestii. Tak ma być: sugestia AI
 * jest punktem wyjścia, a nie oceną. Paneli (i przycisków) bywa kilka – po jednym na dostawcę
 * albo model, którym oceniono pracę – więc każdy przycisk dostaje własny nasłuch.
 *
 * Zasady bezpieczeństwa (strict CSP, żadnego skryptu inline): jeden nasłuch na przycisk,
 * wartość z atrybutu ``data-ai-prefill`` porównywana z wartościami istniejących pól – skrypt nie
 * tworzy nowej wartości, której formularz by nie znał. Przyciski są w HTML-u ukryte i odsłania je
 * dopiero ten plik: bez JavaScriptu nie obiecują czynności, której nie ma.
 */
(function () {
  "use strict";

  var buttons = document.querySelectorAll("[data-ai-prefill]");
  for (var b = 0; b < buttons.length; b += 1) {
    (function (button) {
      var form = document.getElementById(button.dataset.aiTarget || "");
      if (!form) return;

      var value = button.dataset.aiPrefill || "";
      var radios = form.querySelectorAll('input[type="radio"][name="score"]');
      var match = null;
      for (var index = 0; index < radios.length; index += 1) {
        if (radios[index].value === value && !radios[index].disabled) match = radios[index];
      }
      // Etap z dowolnymi wartościami ocen (wydanie 0.35.0) nie ma listy radio, tylko pole liczbowe.
      // Serwer podaje wtedy propozycję już przyciętą do zakresu zadania i sprowadzoną do 0,01, z
      // kropką (``points_input``) – skrypt wpisuje ją do pola i niczego więcej nie liczy.
      var field = match ? null : form.querySelector('input[type="number"][name="score"]');
      if (!match && (!field || field.disabled)) return;

      button.hidden = false;
      button.addEventListener("click", function () {
        var target = match || field;
        if (match) {
          match.checked = true;
        } else {
          field.value = value;
        }
        target.dispatchEvent(new Event("change", { bubbles: true }));
        target.focus();
      });
    })(buttons[b]);
  }
})();
