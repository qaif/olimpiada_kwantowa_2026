/* Kratka „Zaznacz wszystkie” nad tabelą z zaznaczaniem wierszy – czysty JavaScript, bez biblioteki.
 *
 * Po co osobny plik: strict CSP nie dopuszcza ani jednego skryptu inline, a to jedyna rzecz w tabeli
 * przydziałów, której nie da się zrobić samym HTML-em. Reszta zaznaczania działa bez JavaScriptu –
 * kratki wierszy należą do formularza pobierania przez atrybut ``form=…``, więc wysyłają się nawet
 * wtedy, gdy ten plik nie dojedzie.
 *
 * Umowa z szablonem (templates/web/coordinator/assignments.html):
 * - ``[data-select-all="<id formularza>"]`` – kratka nadrzędna. W HTML-u stoi z atrybutem ``hidden``
 *   i to ten skrypt ją odsłania: bez niego byłaby kratką, która niczego nie robi,
 * - ``[data-select-all-label="<id formularza>"]`` – jej podpis, odsłaniany razem z nią,
 * - kratki wierszy rozpoznajemy po ``form="<id formularza>"``, a nie po klasie – to ten sam atrybut,
 *   który decyduje o tym, co naprawdę zostanie wysłane.
 *
 * Stan pośredni (``indeterminate``) jest ustawiany, gdy zaznaczona jest część wierszy – inaczej
 * kratka nadrzędna kłamałaby o tym, co pójdzie do paczki.
 */
(function () {
  "use strict";

  var masters = document.querySelectorAll("[data-select-all]");

  Array.prototype.forEach.call(masters, function (master) {
    var formId = master.getAttribute("data-select-all");
    var selector = 'input[type="checkbox"][form="' + formId + '"]';
    var targets = document.querySelectorAll(selector);
    if (!targets.length) {
      return;
    }

    master.hidden = false;
    var label = document.querySelector('[data-select-all-label="' + formId + '"]');
    if (label) {
      label.hidden = false;
    }

    function sync() {
      var checked = 0;
      Array.prototype.forEach.call(targets, function (box) {
        if (box.checked) {
          checked += 1;
        }
      });
      master.checked = checked === targets.length;
      master.indeterminate = checked > 0 && checked < targets.length;
    }

    master.addEventListener("change", function () {
      Array.prototype.forEach.call(targets, function (box) {
        box.checked = master.checked;
      });
      master.indeterminate = false;
    });

    Array.prototype.forEach.call(targets, function (box) {
      box.addEventListener("change", sync);
    });

    sync();
  });
})();
