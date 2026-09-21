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
 * Pauza: cztery **niezależne** powody – najechanie myszą, fokus klawiaturą w środku, ukryta karta
 * i przeciąganie taśmy palcem/myszą. Wznowienie następuje dopiero, gdy żaden z nich już nie
 * obowiązuje (zjechanie kursorem, kiedy wciąż trzyma fokus, ma nic nie robić – i odwrotnie).
 *
 * Fokus na przyciętej planszy przesuwa ją w widoczne pole natychmiast, **także** w trybie
 * statycznym i przy ograniczonym ruchu – klawiatura nie może utknąć na niewidocznym odnośniku.
 *
 * Przeciąganie (tylko w stanie „running” – w „static” nie ma czego przewijać): naciśnięcie
 * zapamiętuje pozycję X, przekroczenie 5 px ustawia flagę ``dragged`` i od tej chwili taśma
 * jedzie za kursorem/palcem bez animacji (przejście wyłączone). Puszczenie zaokrągla do
 * najbliższego pełnego pola. Ujemny indeks (przeciąganie w prawo od samego początku taśmy) nie ma
 * czego pokazać przed pierwszym oryginałem, więc punkt odniesienia doskakuje o ``total`` w tej
 * samej klatce – kopia na końcu jest identyczna z oryginałem, więc doskok jest niewidoczny (ta
 * sama sztuczka, co ``snapBack()`` na drugim końcu taśmy). Kliknięcie, które kończy realne
 * przeciągnięcie, jest tłumione (capture-phase ``click``), żeby nie otwierało strony partnera.
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

    /* Przeciąganie: ``dragging`` to czwarty powód pauzy (patrz ``paused()``), ``dragged`` mówi,
       czy ruch przekroczył próg 5 px – dopiero wtedy to „prawdziwe” przeciągnięcie, a nie zwykłe
       kliknięcie, i dopiero wtedy następujący po nim ``click`` trzeba stłumić. */
    var pointerId = null;
    var dragging = false;
    var dragged = false;
    var dragStartX = 0;
    var dragBaseIndex = 0;
    var DRAG_THRESHOLD_PX = 5;

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
      return hovering || focused || hiddenTab || dragging;
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

    /* Cztery niezależne powody pauzy (czwarty – przeciąganie – jest niżej) – żaden nie może
       „skasować” drugiego. Wznowienie następuje dopiero, gdy wszystkie cztery przestają
       obowiązywać naraz. */
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

    /* Przeglądarka domyślnie zaczyna natywne przeciąganie obrazka/odnośnika po naciśnięciu
       i przesunięciu myszy nad nimi (widmowa miniatura, można upuścić w pasek adresu) –
       ``draggable="false"`` w szablonie załatwia to w większości przeglądarek, to tylko
       dodatkowe zabezpieczenie (obejmuje też klony, bo ``cloneNode`` kopiuje atrybut). */
    root.addEventListener("dragstart", function (event) {
      event.preventDefault();
    });

    /* Przeciąganie taśmy myszą/palcem – tylko gdy jedzie (klony istnieją, jest czym wypełnić
       pole także „za końcem” listy). W trybie statycznym nie ma czego przewijać.

       Pozycja jest zawsze sprowadzana do zakresu ``[0, total)`` (``wrap``): druga kopia plansz
       stoi pod indeksami ``total..2*total-1``, więc przy pozycji z tego zakresu całe widoczne
       pole jest zawsze zapełnione – niezależnie od tego, jak daleko i jak szybko ktoś pociągnął
       (kilka pełnych obrotów taśmy też). Pojedyncza korekta „± total” tego nie gwarantowała. */
    function wrap(value) {
      var rest = value % total;
      return rest < 0 ? rest + total : rest;
    }

    function dragPosition(clientX) {
      var width = slotWidth();
      if (!width) {
        /* Taśma schowana w trakcie gestu (okno zwężone poniżej progu) – nie ma czego liczyć. */
        return null;
      }
      return wrap(dragBaseIndex - (clientX - dragStartX) / width);
    }

    root.addEventListener("pointerdown", function (event) {
      if (!running || pointerId !== null || (event.pointerType === "mouse" && event.button !== 0)) {
        /* ``pointerId !== null``: drugi palec nie przejmuje gestu rozpoczętego pierwszym. */
        return;
      }
      pointerId = event.pointerId;
      dragStartX = event.clientX;
      dragBaseIndex = index >= total ? index - total : index;
      dragged = false;
      dragging = true;
      stop();
    });

    root.addEventListener("pointermove", function (event) {
      if (pointerId === null || event.pointerId !== pointerId) {
        return;
      }
      if (!dragged) {
        if (Math.abs(event.clientX - dragStartX) <= DRAG_THRESHOLD_PX) {
          return;
        }
        dragged = true;
        /* Przechwycenie wskaźnika dopiero **po** progu, nie przy naciśnięciu: przechwycony
           wskaźnik kieruje także ``click`` na pasek zamiast na odnośnik, więc zwykłe kliknięcie
           w logo przestałoby otwierać stronę partnera. Sprzątanie po geście, który skończył się
           poza paskiem bez przechwycenia, robią nasłuchy na ``window`` niżej. */
        if (root.setPointerCapture) {
          try {
            root.setPointerCapture(pointerId);
          } catch (error) {
            /* Wskaźnik zdążył zniknąć (przycisk puszczony przed tą linią) – nie ma czego łapać. */
          }
        }
      }
      var position = dragPosition(event.clientX);
      if (position !== null) {
        moveTo(position, false);
      }
    });

    function endDrag(event) {
      if (pointerId === null || event.pointerId !== pointerId) {
        return;
      }
      if (root.releasePointerCapture && root.hasPointerCapture && root.hasPointerCapture(pointerId)) {
        root.releasePointerCapture(pointerId);
      }
      pointerId = null;
      dragging = false;
      if (dragged) {
        var position = running ? dragPosition(event.clientX) : null;
        if (position !== null) {
          /* Dosunięcie do najbliższego pełnego pola, z animacją. ``round`` może dać ``total``
             (np. 8,6 → 9 przy dziewięciu planszach) – to poprawna pozycja w kopii, a nasłuch
             ``transitionend`` wyżej zrobi z niej zero dokładnie tak, jak po ``advance()``. */
          index = Math.round(position);
          snapped = index === 0;
          moveTo(index, true);
          if (index >= total) {
            window.setTimeout(snapBack, TRANSITION_MS + 50);
          }
        }
        /* Znacznik tłumi **jedno** kliknięcie – to, które przeglądarka wysyła zaraz po puszczeniu
           przycisku. Gaśnie sam po chwili, bo kliknięcie nie zawsze przychodzi (gest skończony
           poza paskiem): zostawiony na stałe zjadłby następne, prawdziwe kliknięcie – także
           Enter z klawiatury, który ``pointerdown`` nie poprzedza i który nie miałby jak go
           wyzerować. */
        window.setTimeout(function () {
          dragged = false;
        }, 80);
      }
      start();
    }

    /* Na ``window``, nie na pasku: gest zakończony poza paskiem przed przekroczeniem progu
       (czyli bez przechwycenia wskaźnika) nigdy nie dostarczyłby tu ``pointerup`` i pauza
       „dragging” zostałaby na zawsze – taśma stanęłaby do przeładowania strony. */
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    root.addEventListener("lostpointercapture", endDrag);

    /* Kliknięcie kończące realne przeciągnięcie nie ma otwierać strony partnera – zwykłe
       kliknięcie (bez ruchu ponad próg) przechodzi normalnie do odnośnika. Faza przechwytywania,
       żeby zdążyć przed obsługą kliknięcia na ``<a>``. */
    root.addEventListener(
      "click",
      function (event) {
        if (dragged) {
          event.preventDefault();
          event.stopPropagation();
          dragged = false;
        }
      },
      true
    );

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
