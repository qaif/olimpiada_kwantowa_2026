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
 * modulo, więc po ostatnim sponsorze wraca się do organizatora i tak w kółko, bez zatrzymania
 * i bez cofania animacji.
 *
 * Pauza: najechanie myszą, fokus klawiaturą na którymkolwiek odnośniku i ukryta karta
 * (``visibilitychange``) zatrzymują przewijanie – to samo, co Swiper robi przez
 * ``pauseOnMouseEnter``, tu bez żadnej biblioteki (CSP nie wpuszcza skryptów spoza własnej
 * domeny). Fokus na odnośniku dodatkowo przewija taśmę tak, żeby ta plansza była w całości
 * widoczna – inaczej Tab prowadziłby na odnośnik przycięty poza pudełkiem.
 */
(function () {
  "use strict";

  /** Ile milisekund trwa przesunięcie o jeden slot – musi się zgadzać z arkuszem (``app.css``,
   * ``.sponsor-slider__track``), bo po tylu milisekundach skrypt zakłada, że animacja doszła
   * do końca (patrz ``transitionend`` niżej – to jest tylko zabezpieczenie awaryjne). */
  var TRANSITION_MS = 900;

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function setup(root) {
    var track = root.querySelector("[data-sponsor-slider-track]");
    if (track === null) {
      return;
    }
    var items = Array.prototype.slice.call(track.children);
    var seconds = parseInt(root.getAttribute("data-interval"), 10);
    if (items.length < 2 || !seconds || seconds <= 0 || reducedMotion()) {
      return;
    }

    var slotWidth = items[0].getBoundingClientRect().width;
    if (!slotWidth) {
      return;
    }
    var visible = Math.max(1, Math.floor(root.clientWidth / slotWidth));
    if (items.length <= visible) {
      /* Wszystkie logotypy mieszczą się naraz – nie ma czego przewijać. */
      return;
    }

    /* Podwójna taśma: kopia całej listy, doklejona na końcu. Kopie są niewidoczne dla czytnika
       ekranu i nieosiągalne klawiszem – prawdziwe odnośniki stoją tylko raz, w oryginale. */
    items.forEach(function (item) {
      var clone = item.cloneNode(true);
      clone.setAttribute("aria-hidden", "true");
      var link = clone.querySelector("a");
      if (link !== null) {
        link.setAttribute("tabindex", "-1");
      }
      track.appendChild(clone);
    });

    var total = items.length;
    var index = 0;
    var timer = null;
    var paused = false;

    function moveTo(position, animate) {
      track.style.transition = animate ? "" : "none";
      track.style.transform = "translateX(" + -1 * position * slotWidth + "px)";
    }

    function advance() {
      index += 1;
      moveTo(index, true);
      if (index >= total) {
        /* Taśma dojechała do końca kopii – kopia wygląda identycznie jak oryginał, więc powrót
           na pozycję zerową bez animacji jest niewidoczny dla oka. ``transitionend`` jest
           dokładniejszy niż stały czas, ale zdarza się, że przeglądarka go nie wyśle (np. karta
           w tle) – stąd zapasowy ``setTimeout`` o tej samej długości, co przejście w arkuszu. */
        window.setTimeout(snapBack, TRANSITION_MS + 50);
      }
    }

    var snapped = false;
    function snapBack() {
      if (snapped) {
        return;
      }
      snapped = true;
      index = 0;
      moveTo(0, false);
    }

    track.addEventListener("transitionend", function (event) {
      if (event.propertyName === "transform" && index >= total) {
        snapBack();
      }
    });

    function tick() {
      if (index < total) {
        snapped = false;
      }
      advance();
    }

    function start() {
      if (timer !== null || paused) {
        return;
      }
      timer = window.setInterval(tick, seconds * 1000);
    }

    function stop() {
      if (timer === null) {
        return;
      }
      window.clearInterval(timer);
      timer = null;
    }

    root.addEventListener("mouseenter", function () {
      paused = true;
      stop();
    });
    root.addEventListener("mouseleave", function () {
      paused = false;
      start();
    });

    items.forEach(function (item, itemIndex) {
      var link = item.querySelector("a");
      if (link === null) {
        return;
      }
      link.addEventListener("focus", function () {
        paused = true;
        stop();
        index = itemIndex;
        snapped = false;
        moveTo(index, true);
      });
      link.addEventListener("blur", function () {
        paused = false;
        start();
      });
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stop();
      } else if (!paused) {
        start();
      }
    });

    moveTo(0, false);
    if (!document.hidden) {
      start();
    }
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
