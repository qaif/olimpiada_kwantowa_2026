/* „Zaznacz kolumnę” w tabeli obecności na warsztatach – czysty JavaScript, bez biblioteki.
 *
 * Po co osobny plik: strict CSP nie dopuszcza ani jednego skryptu inline. Po co w ogóle: kolumna
 * ma tyle kratek, ilu uczestników na stronie (do stu), a obecność odhacza się zwykle całymi
 * zajęciami („na listopadowych byli wszyscy poza trójką”).
 *
 * Umowa z szablonem (templates/web/coordinator/workshop_attendance.html):
 * - ``button[data-check-column="<klucz>"]`` – przycisk w nagłówku kolumny. W HTML-u stoi
 *   z atrybutem ``hidden`` i to ten skrypt go odsłania: bez niego byłby przyciskiem, który
 *   niczego nie robi,
 * - ``input[data-workshop-key="<klucz>"]`` – kratki tej kolumny.
 *
 * Przycisk **przełącza**: gdy cała kolumna jest zaznaczona, kliknięcie ją odznacza. Osobny
 * przycisk „odznacz” zabierałby miejsce w nagłówku, w którym i tak stoi temat i data zajęć.
 * Zapisu nie wywołuje – zapisuje dopiero „Zapisz obecności tej strony”, więc pomyłka kosztuje
 * jedno kliknięcie, a nie przeładowanie strony.
 */
(function () {
  "use strict";

  var buttons = document.querySelectorAll("[data-check-column]");

  Array.prototype.forEach.call(buttons, function (button) {
    var key = button.getAttribute("data-check-column");
    var boxes = document.querySelectorAll('input[data-workshop-key="' + key + '"]');
    if (!boxes.length) {
      return;
    }

    button.hidden = false;

    button.addEventListener("click", function () {
      var allChecked = Array.prototype.every.call(boxes, function (box) {
        return box.checked;
      });
      Array.prototype.forEach.call(boxes, function (box) {
        box.checked = !allChecked;
      });
    });
  });
})();
