/* Formuły LaTeX w tłumaczeniach zadań (TR-01) – KaTeX z plików statycznych aplikacji.
 *
 * Serwer (``apps/problem_translations/markup.py``) wstawia każdą formułę jako **tekst** w
 * ``<span class="tr-math" data-display="0|1">``; ten skrypt zamienia ją na skład KaTeX-a.
 * Biblioteka leży w ``vendor/katex`` (bez CDN-u): treść zadania i formuły są składane w przeglądarce,
 * a pliki KaTeX-a przychodzą z tego samego serwera – CSP nie potrzebuje wyjątku. Strona jako całość
 * ładuje jednak htmx i Alpine z CDN-ów przypiętych skrótem SRI (``templates/base.html``); te żądania
 * nie niosą treści zadania (Referer nie wychodzi poza serwis – ``SECURE_REFERRER_POLICY``).
 *
 * ``katex.render`` buduje węzły DOM (nie ``innerHTML``), z ``trust: false`` (bez ``\href``,
 * ``\includegraphics`` i podobnych) i ``throwOnError: false`` – błędna formuła zostaje czerwonym
 * źródłem, a nie pustym miejscem. Bez JavaScriptu widać źródło LaTeX-a, czyli treść i tak czytelną.
 *
 * Podgląd w edytorze wraca przez HTMX (autozapis), więc po ``htmx:afterSwap`` rysujemy to,
 * co właśnie przyszło.
 */
(function () {
  "use strict";

  const DONE = "data-math-done";

  function renderAll(root) {
    if (typeof window.katex === "undefined") {
      return;
    }
    root.querySelectorAll(".tr-math:not([" + DONE + "])").forEach(function (node) {
      const source = node.textContent;
      try {
        window.katex.render(source, node, {
          displayMode: node.getAttribute("data-display") === "1",
          throwOnError: false,
          trust: false,
          strict: "ignore",
          maxSize: 50,
          maxExpand: 500,
        });
        node.setAttribute(DONE, "1");
      } catch (error) {
        node.textContent = source;
      }
    });
  }

  function start() {
    renderAll(document);
    document.body.addEventListener("htmx:afterSwap", function (event) {
      renderAll(event.target);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
