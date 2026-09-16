/* Układ panelu recenzenta: zakładki kolejki, skróty klawiaturowe i podgląd sumy rubryki.
 *
 * Trzy dokładki w jednym pliku, bo dotyczą jednej rzeczy – tego, jak recenzent porusza się po
 * panelu. Żadna z nich nie jest warunkiem działania:
 *
 * - **zakładki kolejki**. Wszystkie cztery panele („Do zrobienia”, „W toku”, „Wystawione”,
 *   „Anulowane”) renderuje serwer. Ten plik chowa nieaktywne i dokłada role ARIA. Bez niego
 *   strona jest listą czterech sekcji z odnośnikami do nich – nic nie znika,
 * - **skróty klawiaturowe** (n/p/s). Każdy z nich klika element, który i tak stoi na ekranie:
 *   „następna praca”, „poprzednia praca”, „Zapisz szkic”. Skrót milczy, kiedy recenzent pisze
 *   w polu tekstowym – „s” w komentarzu ma zostać literą,
 * - **suma rubryki**. Liczy ją **serwer** (``apps.grading.rubric``) i tylko on rozstrzyga, czy
 *   ocena jest dopuszczalna. Licznik pod kryteriami mówi to samo wcześniej, żeby recenzent nie
 *   dowiadywał się o wyjściu poza skalę dopiero z komunikatu po wysłaniu formularza.
 *
 * Strict CSP: zero atrybutów zdarzeń w HTML-u, zero ``innerHTML``, wszystkie dane wejściowe
 * przychodzą atrybutami ``data-*`` wypisanymi (i autoescapowanymi) przez Django.
 */
(function () {
  "use strict";

  /* --- zakładki kolejki ------------------------------------------------------------------- */

  function setupTabs() {
    var nav = document.querySelector("[data-queue-tabs]");
    if (!nav) return;
    var tabs = Array.prototype.slice.call(nav.querySelectorAll("[data-queue-tab]"));
    var panels = {};
    tabs.forEach(function (tab) {
      var key = tab.dataset.queueTab;
      var panel = document.querySelector('[data-queue-panel="' + key + '"]');
      if (panel) panels[key] = panel;
    });
    if (tabs.length === 0) return;

    nav.setAttribute("role", "tablist");
    document.documentElement.classList.add("is-tabbed");

    function select(key, focus) {
      tabs.forEach(function (tab) {
        var active = tab.dataset.queueTab === key;
        tab.setAttribute("aria-selected", active ? "true" : "false");
        /* Tylko aktywna zakładka jest w kolejce tabulacji – po pasku zakładek chodzi się
           strzałkami, tak jak każe wzorzec „tabs” z WAI-ARIA. */
        tab.tabIndex = active ? 0 : -1;
        tab.classList.toggle("is-active", active);
        var panel = panels[tab.dataset.queueTab];
        if (panel) panel.hidden = !active;
        if (active && focus) tab.focus();
      });
    }

    tabs.forEach(function (tab, index) {
      var key = tab.dataset.queueTab;
      var panel = panels[key];
      tab.setAttribute("role", "tab");
      if (panel) {
        tab.setAttribute("aria-controls", panel.id);
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("tabindex", "0");
      }
      tab.addEventListener("click", function (event) {
        /* Odnośnik zostaje odnośnikiem (bez skryptu skacze do sekcji), ale ze skryptem nie
           przewijamy strony – zakładka ma zmienić treść, a nie przenieść widok. */
        event.preventDefault();
        select(key, false);
      });
      tab.addEventListener("keydown", function (event) {
        var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (step === 0) return;
        event.preventDefault();
        var next = tabs[(index + step + tabs.length) % tabs.length];
        select(next.dataset.queueTab, true);
      });
    });

    select(nav.dataset.queueActive || tabs[0].dataset.queueTab, false);
  }

  /* --- skróty klawiaturowe ---------------------------------------------------------------- */

  //: Skrót → element, który ma zostać kliknięty. Wartości są nazwami z atrybutu ``data-shortcut``
  //: w szablonie, więc dopisanie skrótu nie wymaga zmiany selektorów w dwóch miejscach.
  var SHORTCUTS = { n: "next", p: "prev", s: "draft" };

  function typing(element) {
    if (!element) return false;
    if (element.isContentEditable) return true;
    var tag = (element.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  function setupShortcuts() {
    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (typing(event.target)) return;
      var name = SHORTCUTS[(event.key || "").toLowerCase()];
      if (!name) return;
      var target = document.querySelector('[data-shortcut="' + name + '"]');
      if (!target) return;
      event.preventDefault();
      target.click();
    });
  }

  /* --- podgląd sumy rubryki --------------------------------------------------------------- */

  function setupRubric() {
    var rubric = document.querySelector("[data-rubric]");
    if (!rubric) return;
    var totalLabel = rubric.querySelector("[data-rubric-total]");
    var verdict = rubric.querySelector("[data-rubric-verdict]");
    if (!totalLabel) return;

    var inputs = Array.prototype.slice.call(rubric.querySelectorAll("[data-rubric-points]"));
    var scale = (rubric.dataset.rubricScale || "")
      .split(",")
      .map(function (item) {
        return Number(item.trim());
      })
      .filter(function (item) {
        return !Number.isNaN(item);
      });

    function update() {
      var filled = false;
      var total = 0;
      inputs.forEach(function (input) {
        var value = Number(input.value);
        if (input.value !== "" && !Number.isNaN(value)) {
          filled = true;
          total += value;
        }
      });
      if (!filled) {
        totalLabel.textContent = "—";
        if (verdict) verdict.textContent = "";
        return;
      }
      totalLabel.textContent = String(total) + " pkt";
      if (!verdict || scale.length === 0) return;
      /* Ostatnie słowo ma serwer – ten napis jest zapowiedzią jego odpowiedzi, nie bramką. */
      verdict.textContent =
        scale.indexOf(total) === -1
          ? "poza skalą zadania (dopuszczalne: " + scale.join(", ") + ")"
          : "wartość ze skali zadania";
      verdict.classList.toggle("is-invalid", scale.indexOf(total) === -1);
    }

    inputs.forEach(function (input) {
      input.addEventListener("input", update);
    });
    update();
  }

  setupTabs();
  setupShortcuts();
  setupRubric();
})();
