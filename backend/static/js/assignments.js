/* Skróty ekranu „Przydziały i oceny” – wyłącznie skróty.
 *
 * Wszystko, co ten plik przyspiesza, da się na tej stronie zrobić bez niego: przydział ma własny
 * przycisk „Przydziel”, czynności zbiorcze są zwykłym formularzem POST, a liczbę zaznaczonych prac
 * widać po kratkach. Skrypt jest osobnym plikiem, bo strict CSP nie dopuszcza ani jednego skryptu
 * inline, i jest ładowany z ``defer`` – nie blokuje składania tabeli.
 *
 * Umowa z szablonem (templates/web/coordinator/assignments.html):
 * - ``[data-submit-on-change]`` – lista wyboru recenzenta w wierszu. Po zmianie wysyła swój
 *   formularz, ale dopiero po potwierdzeniu: przydział jest zmianą stanu, a listę wyboru zmienia
 *   się także przypadkiem (kółko myszy, strzałka w dół przy przewijaniu klawiaturą),
 * - ``[data-confirm-submit="…"]`` – treść tego potwierdzenia, na formularzu listy,
 * - ``[data-selection-count="<id formularza>"]`` – licznik zaznaczonych wierszy tego formularza.
 *
 * Kratkę „zaznacz wszystkie” obsługuje osobny, wspólny ``select-all.js`` – ta sama umowa działa
 * na kilku ekranach panelu i nie ma powodu jej tu powtarzać.
 */
(function () {
  "use strict";

  /* Licznik zaznaczenia. Pasek czynności zbiorczych mówi „Przydziel zaznaczone”, więc ile ich jest,
     musi być widoczne bez liczenia kratek wzrokiem – zwłaszcza przy stu wierszach na stronie. */
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-selection-count]"),
    function (readout) {
      var formId = readout.getAttribute("data-selection-count");
      var boxes = document.querySelectorAll(
        'input[type="checkbox"][form="' + formId + '"][name="submission_ids"]'
      );
      if (!boxes.length) {
        return;
      }

      function render() {
        var checked = 0;
        Array.prototype.forEach.call(boxes, function (box) {
          if (box.checked) {
            checked += 1;
          }
        });
        readout.textContent = checked === 0 ? "— nic nie zaznaczono" : "— " + checked + " z " + boxes.length;
      }

      Array.prototype.forEach.call(boxes, function (box) {
        box.addEventListener("change", render);
      });
      /* Kratka „zaznacz wszystkie” przestawia wiersze w swoim własnym skrypcie i nie wywołuje na
         nich zdarzenia ``change``, więc licznik nasłuchuje dodatkowo jej samej. */
      var master = document.querySelector('[data-select-all="' + formId + '"]');
      if (master) {
        master.addEventListener("change", function () {
          window.setTimeout(render, 0);
        });
      }
      render();
    }
  );

  /* Wysyłka przydziału zaraz po wyborze recenzenta. Oszczędza jedno kliknięcie na każdą pracę,
     czyli tyle, ile trwa przydzielenie całego zadania ręcznie. */
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-submit-on-change]"),
    function (select) {
      var form = select.form;
      if (!form) {
        return;
      }
      var question = form.getAttribute("data-confirm-submit") || "Zapisać zmianę?";
      var initial = select.value;
      select.addEventListener("change", function () {
        if (!window.confirm(question)) {
          /* Odmowa cofa listę do stanu sprzed zmiany: inaczej ekran pokazywałby recenzenta,
             którego nikt nie przydzielił, a następne „Przydziel” trafiłoby w niego. */
          select.value = initial;
          return;
        }
        initial = select.value;
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    }
  );
})();
