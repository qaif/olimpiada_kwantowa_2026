/* Slider w nagłówku strony głównej (templates/cms/home_page.html).
 *
 * Plansze są kompletne bez tego skryptu: leżą w rzędzie w pudełku z ``scroll-snap`` i przewija się
 * je natywnie (palec, kółko, klawiatura). Skrypt dokłada wyłącznie:
 * - strzałki i kropki (w HTML-u kontrolki mają ``hidden`` – bez skryptu byłyby atrapą),
 * - automatyczne przejście co ``INTERVAL_MS``, zapętlone,
 * - przycisk „Wstrzymaj” (WCAG 2.2.2: ruch dłuższy niż 5 s musi dać się zatrzymać).
 *
 * Pauza ma kilka **niezależnych** powodów: kursor nad sliderem, fokus w środku, ukryta karta,
 * ręczne zatrzymanie. Wznowienie następuje dopiero, gdy żaden nie obowiązuje. Przy
 * ``prefers-reduced-motion: reduce`` slider nie rusza się sam wcale – kontrolki zostają.
 *
 * Bieżąca plansza wynika z pozycji przewinięcia (``scrollLeft``), a nie z licznika – dzięki temu
 * kropka zgadza się także po przewinięciu palcem.
 */
(function () {
  "use strict";

  var INTERVAL_MS = 7000;
  var REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

  function reducedMotion() {
    return window.matchMedia && window.matchMedia(REDUCED_MOTION_QUERY).matches;
  }

  function setup(root) {
    var viewport = root.querySelector("[data-hero-slider-viewport]");
    var controls = root.querySelector("[data-hero-slider-controls]");
    var dotsBox = root.querySelector("[data-hero-slider-dots]");
    var pauseButton = root.querySelector("[data-hero-slider-pause]");
    if (!viewport || !controls || !dotsBox) {
      return;
    }
    var slides = Array.prototype.slice.call(viewport.querySelectorAll("[data-hero-slide]"));
    if (slides.length < 2) {
      return;
    }

    var reasons = { hover: false, focus: false, hidden: document.hidden, user: false };
    var timer = null;
    var dots = slides.map(function (slide, index) {
      var dot = document.createElement("button");
      dot.type = "button";
      dot.className = "hero-slider__dot";
      dot.setAttribute(
        "aria-label",
        "Plansza " + (index + 1) + " z " + slides.length + ": " + (slide.getAttribute("aria-label") || "")
      );
      dot.addEventListener("click", function () {
        go(index);
        restart();
      });
      dotsBox.appendChild(dot);
      return dot;
    });

    function current() {
      var width = viewport.clientWidth || 1;
      return Math.max(0, Math.min(slides.length - 1, Math.round(viewport.scrollLeft / width)));
    }

    function go(index) {
      var target = (index + slides.length) % slides.length;
      viewport.scrollTo({
        left: target * viewport.clientWidth,
        behavior: reducedMotion() ? "auto" : "smooth",
      });
      mark(target);
    }

    function mark(active) {
      dots.forEach(function (dot, index) {
        if (index === active) {
          dot.setAttribute("aria-current", "true");
        } else {
          dot.removeAttribute("aria-current");
        }
      });
      slides.forEach(function (slide, index) {
        // Plansza poza kadrem nie może łapać fokusu klawiatury ani czytnika.
        if (index === active) {
          slide.removeAttribute("inert");
          slide.removeAttribute("aria-hidden");
        } else {
          slide.setAttribute("inert", "");
          slide.setAttribute("aria-hidden", "true");
        }
      });
    }

    function paused() {
      return reasons.hover || reasons.focus || reasons.hidden || reasons.user || reducedMotion();
    }

    function stop() {
      window.clearInterval(timer);
      timer = null;
    }

    function restart() {
      stop();
      if (!paused()) {
        timer = window.setInterval(function () {
          go(current() + 1);
        }, INTERVAL_MS);
      }
    }

    function setReason(name, value) {
      reasons[name] = value;
      restart();
    }

    root.querySelector("[data-hero-slider-prev]").addEventListener("click", function () {
      go(current() - 1);
      restart();
    });
    root.querySelector("[data-hero-slider-next]").addEventListener("click", function () {
      go(current() + 1);
      restart();
    });
    if (pauseButton) {
      pauseButton.addEventListener("click", function () {
        var nowPaused = !reasons.user;
        pauseButton.setAttribute("aria-pressed", nowPaused ? "true" : "false");
        pauseButton.textContent = nowPaused ? "Wznów" : "Wstrzymaj";
        setReason("user", nowPaused);
      });
      if (reducedMotion()) {
        pauseButton.hidden = true;
      }
    }

    var scrollHandle = null;
    viewport.addEventListener("scroll", function () {
      window.clearTimeout(scrollHandle);
      scrollHandle = window.setTimeout(function () {
        mark(current());
      }, 80);
    });

    root.addEventListener("mouseenter", function () {
      setReason("hover", true);
    });
    root.addEventListener("mouseleave", function () {
      setReason("hover", false);
    });
    root.addEventListener("focusin", function () {
      setReason("focus", true);
    });
    root.addEventListener("focusout", function (event) {
      if (!root.contains(event.relatedTarget)) {
        setReason("focus", false);
      }
    });
    document.addEventListener("visibilitychange", function () {
      setReason("hidden", document.hidden);
    });
    root.addEventListener("keydown", function (event) {
      if (event.target.closest && event.target.closest("a, input, textarea, select")) {
        return;
      }
      if (event.key === "ArrowRight") {
        go(current() + 1);
        restart();
      } else if (event.key === "ArrowLeft") {
        go(current() - 1);
        restart();
      }
    });
    window.addEventListener("resize", function () {
      // Po zmianie szerokości pozycja w pikselach przestaje trafiać w granicę planszy.
      viewport.scrollTo({ left: current() * viewport.clientWidth, behavior: "auto" });
    });

    controls.hidden = false;
    root.setAttribute("data-hero-slider-state", "ready");
    mark(current());
    restart();
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-hero-slider]"), setup);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
