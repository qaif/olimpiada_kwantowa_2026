/* Przyklejony pasek konta (templates/base.html): klasa ``is-stuck``, gdy menu serwisu wyjechało.
 *
 * Po co: najważniejsze pozycje menu (Zadania, Harmonogram, Warsztaty) mają **przeskakiwać** do
 * paska dopiero wtedy, gdy czytelnik przewinął stronę i dolne menu zniknęło z ekranu. Na górze
 * strony menu stoi w swoim miejscu w pełnym składzie, a pasek zostaje krótki – dwa razy te same
 * odnośniki jeden pod drugim byłyby szumem.
 *
 * Dlaczego ``IntersectionObserver`` na nagłówku z menu, a nie nasłuch ``scroll``: obserwator budzi
 * się tylko przy zmianie stanu, bez liczenia pozycji w każdej klatce przewijania. Margines górny
 * obserwatora równa się wysokości paska, więc „menu zniknęło” znaczy „schowało się pod paskiem”,
 * a nie „dotknęło krawędzi okna”.
 *
 * Bez JavaScriptu (albo w przeglądarce bez obserwatora) klasa nigdy nie pada, pozycje w pasku
 * zostają ukryte arkuszem i dolne menu obsługuje wszystko – nic nie znika, niczego nie brakuje.
 */
(function () {
  "use strict";

  var bar = document.querySelector(".topbar--account");
  var header = document.querySelector("header.topbar");
  if (!bar || !header || !("IntersectionObserver" in window)) {
    return;
  }

  var STUCK_CLASS = "is-stuck";
  var observer = null;

  function setStuck(stuck) {
    bar.classList.toggle(STUCK_CLASS, stuck);
  }

  function observe() {
    if (observer) {
      observer.disconnect();
    }
    /* Na wąskim ekranie pasek nie jest przyklejony (arkusz: position static), więc nie ma czego
       „doklejać” – stan zostaje wyłączony, a obserwator niepotrzebny. */
    if (window.getComputedStyle(bar).position !== "sticky") {
      setStuck(false);
      return;
    }
    var barHeight = Math.ceil(bar.getBoundingClientRect().height);
    observer = new IntersectionObserver(
      function (entries) {
        var entry = entries[entries.length - 1];
        /* Menu „zniknęło” tylko wtedy, gdy przesunęło się **w górę** pod pasek; gdy jest poniżej
           ekranu (nie zdarza się w tym układzie), pasek zostaje krótki. */
        var above = entry.boundingClientRect.bottom <= barHeight;
        setStuck(!entry.isIntersecting && above);
      },
      { rootMargin: "-" + barHeight + "px 0px 0px 0px", threshold: 0 }
    );
    observer.observe(header);
  }

  observe();

  /* Zmiana szerokości okna zmienia wysokość paska (zawijanie przycisków) i próg „static/sticky”. */
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(observe, 150);
  });
})();
