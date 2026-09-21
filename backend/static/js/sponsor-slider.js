/* Pasek logotypów sponsorów w menu: przesuwanie taśmy o jeden logotyp co ``data-interval`` sekund.
 *
 * Serwer rysuje każdy logotyp **dokładnie raz** (templates/cms/_sponsor_slider.html) i tyle
 * plansz, ile akurat mieści pudełko, widać bez ani jednego wiersza JavaScriptu –
 * ``overflow: hidden`` w arkuszu przycina resztę. To jest poprawny stan spoczynkowy: bez
 * skryptu, z zablokowanym JS-em albo z ``prefers-reduced-motion: reduce`` pasek zostaje
 * nieruchomym rzędem pierwszych logotypów, a nie atrapą.
 *
 * Ten skrypt dokłada wyłącznie **ruch**, na klasycznej sztuczce „podwójnej taśmy”: dokleja na
 * końcu jedną kopię wszystkich plansz (oznaczoną ``aria-hidden`` i bez klawiszowego fokusu),
 * więc przewijanie o jeden slot na raz nigdy nie odsłania pustego miejsca. Po dojechaniu do
 * początku kopii taśma **bez animacji** wraca na pozycję zerową – wizualnie to ta sama plansza,
 * bo kopia jest identyczna z oryginałem – i jedzie dalej. Pętla nie ma końca: indeks liczy się
 * modulo (przez porównanie z ``total`` i powrót do zera), więc po ostatnim sponsorze wraca się
 * do organizatora i tak w kółko, bez zatrzymania i bez cofania animacji.
 *
 * Stan jest jawny w znaczniku: ``data-sponsor-slider-state`` na kontenerze mówi „running” albo
 * „static” – atrybut, nie klasa, więc kontrola z zewnątrz (testy end-to-end, ``check_sponsor_
 * slider.py``) czyta go tak samo, jak czyta ``data-interval``. Skrypt sam decyduje, w którym
 * stanie być, i przelicza to na nowo przy każdej zmianie rozmiaru okna (``ResizeObserver`` na
 * kontenerze – łapie też przejście przez punkt załamania 900 px, bo wtedy szerokość skacze na
 * zero) oraz przy zmianie ``prefers-reduced-motion`` w locie.
 *
 * Pauza: trzy **niezależne** powody – najechanie myszą, fokus klawiaturą w środku i ukryta karta.
 * Wznowienie następuje dopiero, gdy żaden z nich już nie obowiązuje (zjechanie kursorem, kiedy
 * wciąż trzyma fokus, ma nic nie robić – i odwrotnie).
 *
 * Fokus na przyciętej planszy przesuwa ją w widoczne pole natychmiast, **także** w trybie
 * statycznym i przy ograniczonym ruchu – klawiatura nie może utknąć na niewidocznym odnośniku.
 */
(function () {
  "use strict";

  var STATE_ATTR = "data-sponsor-slider-state";
  var TRANSITION_MS = 900;
  var REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

  function reducedMotion() {
    return window.matchMedia && window.matchMedia(REDUCED_MOTION_QUERY).matches;
  }

  function debounce(fn, ms) {
    var handle = null;
    return function () {
      window.clearTimeout(handle);
      handle = window.setTimeout(fn, ms);
    };
  }

  function setup(root) {
    var track = root.querySelector("[data-sponsor-slider-track]");
    if (track === null) {
      return;
    }
    /* Oryginalne plansze, zapamiętane raz: klony (jeśli w ogóle powstaną) dokładają się do tej
       samej taśmy i nie mogą wejść drugi raz do tej listy, gdyby skrypt uruchamiał się ponownie. */
    var items = Array.prototype.slice.call(track.children);
    var total = items.length;
    var seconds = parseInt(root.getAttribute("data-interval"), 10);
    if (!total || !seconds || seconds <= 0) {
      return;
    }

    var cloned = false;
    var index = 0;
    var timer = null;
    var snapped = true;
    var running = false;
    var hovering = false;
    var focused = false;
    var hiddenTab = document.hidden;

    function slotWidth() {
      var rect = items[0].getBoundingClientRect();
      return rect.width;
    }

    function visibleCount(width) {
      return width ? Math.max(1, Math.floor(root.clientWidth / width)) : total;
    }

    function moveTo(position, animate) {
      track.style.transition = animate ? "" : "none";
      track.style.transform = "translateX(" + -1 * position * slotWidth() + "px)";
    }

    function ensureClones() {
      if (cloned) {
        return;
      }
      items.forEach(function (item) {
        var clone = item.cloneNode(true);
        clone.setAttribute("aria-hidden", "true");
        var link = clone.querySelector("a");
        if (link !== null) {
          link.setAttribute("tabindex", "-1");
        }
        track.appendChild(clone);
      });
      cloned = true;
    }

    function snapBack() {
      if (snapped) {
        return;
      }
      snapped = true;
      index = 0;
      moveTo(0, false);
    }

    function advance() {
      index += 1;
      snapped = false;
      moveTo(index, true);
      if (index >= total) {
        /* Taśma dojechała do końca kopii – kopia wygląda identycznie jak oryginał, więc powrót
           na pozycję zerową bez animacji jest niewidoczny dla oka. ``transitionend`` jest
           dokładniejszy niż stały czas, ale zdarza się, że przeglądarka go nie wyśle (karta
           w tle) – stąd zapasowy ``setTimeout`` o tej samej długości, co przejście w arkuszu. */
        window.setTimeout(snapBack, TRANSITION_MS + 50);
      }
    }

    function paused() {
      return hovering || focused || hiddenTab;
    }

    function start() {
      if (timer !== null || !running || paused()) {
        return;
      }
      timer = window.setInterval(advance, seconds * 1000);
    }

    function stop() {
      if (timer === null) {
        return;
      }
      window.clearInterval(timer);
      timer = null;
    }

    /* Rozstrzyga „jechać czy stać” od nowa – wołane na starcie, przy każdej zmianie rozmiaru
       (``ResizeObserver``) i przy zmianie ``prefers-reduced-motion`` w locie. Nic tu się nie
       dzieje po cichu: stan zawsze ląduje w ``data-sponsor-slider-state``. */
    function evaluate() {
      var width = root.clientWidth;
      var shouldRun = width > 0 && !reducedMotion() && total > visibleCount(slotWidth());
      if (shouldRun === running) {
        return;
      }
      running = shouldRun;
      if (running) {
        ensureClones();
        root.setAttribute(STATE_ATTR, "running");
        index = 0;
        snapped = true;
        moveTo(0, false);
        start();
      } else {
        stop();
        root.setAttribute(STATE_ATTR, "static");
        index = 0;
        snapped = true;
        moveTo(0, false);
      }
    }

    track.addEventListener("transitionend", function (event) {
      if (event.propertyName === "transform" && index >= total) {
        snapBack();
      }
    });

    /* Trzy niezależne powody pauzy – żaden nie może „skasować” drugiego. Wznowienie następuje
       dopiero, gdy wszystkie trzy przestają obowiązywać naraz. */
    root.addEventListener("mouseenter", function () {
      hovering = true;
      stop();
    });
    root.addEventListener("mouseleave", function () {
      hovering = false;
      start();
    });

    root.addEventListener("focusin", function () {
      focused = true;
      stop();
    });
    root.addEventListener("focusout", function (event) {
      /* Fokus przeskoczył na inną planszę w tym samym pasku (Tab między kolejnymi odnośnikami) –
         to nie jest wyjście, więc pauza trwa dalej. */
      if (event.relatedTarget !== null && root.contains(event.relatedTarget)) {
        return;
      }
      focused = false;
      start();
    });

    document.addEventListener("visibilitychange", function () {
      hiddenTab = document.hidden;
      if (hiddenTab) {
        stop();
      } else {
        start();
      }
    });

    /* Fokus na przyciętej planszy: przesuwamy ją w widoczne pole natychmiast – niezależnie od
       tego, czy taśma akurat jedzie, stoi statycznie czy działa w trybie ograniczonego ruchu.
       Klawiatura nie może utknąć na odnośniku schowanym poza ``overflow: hidden``. */
    items.forEach(function (item, itemIndex) {
      var link = item.querySelector("a");
      if (link === null) {
        return;
      }
      link.addEventListener("focus", function () {
        index = itemIndex;
        snapped = false;
        moveTo(index, true);
      });
    });

    if (window.matchMedia) {
      var motionQuery = window.matchMedia(REDUCED_MOTION_QUERY);
      var onMotionChange = function () {
        evaluate();
      };
      if (motionQuery.addEventListener) {
        motionQuery.addEventListener("change", onMotionChange);
      } else if (motionQuery.addListener) {
        /* Safari < 14 nie zna addEventListener na MediaQueryList. */
        motionQuery.addListener(onMotionChange);
      }
    }

    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        evaluate();
      }).observe(root);
    } else {
      window.addEventListener("resize", debounce(evaluate, 200));
    }

    evaluate();
  }

  function wire() {
    var sliders = document.querySelectorAll("[data-sponsor-slider]");
    Array.prototype.forEach.call(sliders, setup);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
