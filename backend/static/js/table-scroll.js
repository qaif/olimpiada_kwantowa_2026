/* Ramki przewijania tabel (``.scroll`` i ``.table-scroll``) – dostępność z klawiatury i podpowiedź
 * „tabela jedzie dalej”.
 *
 * Tabela jest kompletna bez tego skryptu: przewija się w swojej ramce (CSS), a najważniejsze tabele
 * panelu koordynatora mają ``role="region"``, ``tabindex="0"`` i ``aria-label`` wpisane wprost
 * w szablonie. Skrypt robi trzy rzeczy, których szablon zrobić nie może:
 *
 * 1. pozostałym ramkom (ok. setki tabel w szablonach) dokłada ``role="region"`` i nazwę dostępną
 *    wziętą z ``<caption>``, z ``aria-label`` tabeli albo z najbliższego nagłówka przed nią,
 * 2. ``tabindex="0"`` dokłada **tylko wtedy, gdy ramka rzeczywiście się przewija** – tabela, która
 *    się mieści, nie jest przystankiem klawiatury (Tab przez dwadzieścia pustych przystanków to
 *    regresja, nie dostępność); zdejmuje go po poszerzeniu okna, ale wyłącznie ten, który sam dał,
 * 3. ustawia ``data-scroll="start|middle|end"`` – CSS wygasza wtedy krawędź, za którą są jeszcze
 *    kolumny (``app.css``, sekcja 6.4).
 *
 * Plik zewnętrzny z nonce, jak każdy skrypt serwisu (CSP bez 'unsafe-inline'). Tabele doklejone
 * przez HTMX po wczytaniu strony obsługuje nasłuch ``htmx:afterSettle``.
 */
(function () {
  "use strict";

  var SELECTOR = ".scroll, .table-scroll";
  var OWN_TABINDEX = "data-scroll-tabindex";
  var seen = typeof WeakSet === "function" ? new WeakSet() : null;

  function text(node) {
    return node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  /* Najbliższy nagłówek przed ramką: najpierw rodzeństwo wstecz, potem to samo piętro wyżej.
     Cztery piętra wystarczają na ``section > div.card > form > div.scroll``. */
  function nearestHeading(el) {
    var node = el;
    for (var depth = 0; node && depth < 4; depth += 1) {
      var sibling = node.previousElementSibling;
      while (sibling) {
        if (/^H[2-4]$/.test(sibling.tagName)) {
          return text(sibling);
        }
        var inner = sibling.querySelectorAll ? sibling.querySelectorAll("h2, h3, h4") : [];
        if (inner.length) {
          return text(inner[inner.length - 1]);
        }
        sibling = sibling.previousElementSibling;
      }
      node = node.parentElement;
    }
    return "";
  }

  function label(el) {
    var table = el.querySelector("table");
    if (!table) {
      return "";
    }
    return (
      text(table.querySelector("caption")) ||
      (table.getAttribute("aria-label") || "").trim() ||
      nearestHeading(el) ||
      "Tabela"
    );
  }

  function annotate(el) {
    if (!el.hasAttribute("role")) {
      el.setAttribute("role", "region");
    }
    if (!el.hasAttribute("aria-label") && !el.hasAttribute("aria-labelledby")) {
      var name = label(el);
      if (name) {
        el.setAttribute("aria-label", name.length > 120 ? name.slice(0, 117) + "…" : name);
      }
    }
  }

  /* Tryb szerokości tabeli (``app.css`` 6.4). Tabela bez modyfikatora układu najpierw próbuje
     zmieścić się w ramce; jeżeli się nie mieści, dostaje ``data-fit="wide"`` – kolumny w szerokości
     treści zamiast ściśniętych do najdłuższego słowa, skoro przewijać i tak trzeba. Przy każdej
     zmianie rozmiaru próba zaczyna się od nowa (atrybut zdjęty, jeden pomiar), bo po poszerzeniu
     okna tabela może się już zmieścić. Poniżej 640 px wszystko załatwia CSS. */
  var FIXED_LAYOUT = ".table--wide, .table--sticky-first, .table--sticky-rank, .table--matrix, .table--fluid";
  var wideScreen =
    typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 640px)") : { matches: true };

  function fit(el) {
    var table = el.querySelector(":scope > table");
    if (!table || table.matches(FIXED_LAYOUT)) {
      return;
    }
    el.removeAttribute("data-fit");
    if (wideScreen.matches && el.scrollWidth - el.clientWidth > 1) {
      el.setAttribute("data-fit", "wide");
    }
  }

  function update(el, resized) {
    if (resized) {
      fit(el);
    }
    var overflow = el.scrollWidth - el.clientWidth > 1;
    if (!overflow) {
      el.removeAttribute("data-scroll");
      if (el.hasAttribute(OWN_TABINDEX)) {
        el.removeAttribute("tabindex");
        el.removeAttribute(OWN_TABINDEX);
      }
      return;
    }
    if (!el.hasAttribute("tabindex")) {
      el.setAttribute("tabindex", "0");
      el.setAttribute(OWN_TABINDEX, "");
    }
    /* ``scrollLeft`` bywa ułamkowy (skalowanie ekranu) – stąd margines jednego piksela. */
    var left = Math.abs(el.scrollLeft);
    var max = el.scrollWidth - el.clientWidth;
    var state = left <= 1 ? "start" : left >= max - 1 ? "end" : "middle";
    if (el.getAttribute("data-scroll") !== state) {
      el.setAttribute("data-scroll", state);
    }
  }

  function refit(el) {
    update(el, true);
  }

  var observer =
    typeof ResizeObserver === "function"
      ? new ResizeObserver(function (entries) {
          entries.forEach(function (entry) {
            /* Obserwowana jest ramka i tabela w niej (tabela rośnie po doklejeniu wierszy,
               ramka – po zmianie okna); stan zawsze liczy się na ramce. */
            var box = entry.target.closest(SELECTOR);
            if (box) {
              update(box, true);
            }
          });
        })
      : null;

  function init(root) {
    var boxes = (root || document).querySelectorAll(SELECTOR);
    Array.prototype.forEach.call(boxes, function (el) {
      if (seen && seen.has(el)) {
        return;
      }
      if (seen) {
        seen.add(el);
      }
      annotate(el);
      update(el, true);
      el.addEventListener(
        "scroll",
        function () {
          update(el);
        },
        { passive: true }
      );
      if (observer) {
        observer.observe(el);
        var table = el.querySelector("table");
        if (table) {
          observer.observe(table);
        }
      }
    });
  }

  if (!observer) {
    window.addEventListener("resize", function () {
      Array.prototype.forEach.call(document.querySelectorAll(SELECTOR), refit);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init(document);
    });
  } else {
    init(document);
  }

  /* HTMX podmienia fragment strony: nowe ramki trzeba opisać, a istniejące przeliczyć (podmiana
     samych wierszy zmienia szerokość tabeli bez nowej ramki). */
  document.addEventListener("htmx:afterSettle", function () {
    init(document);
    Array.prototype.forEach.call(document.querySelectorAll(SELECTOR), refit);
  });
})();
