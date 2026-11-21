/* Mapa uczestników (MAP-01) – wyszukiwarka listy i podświetlenie kółka miejscowości.
 *
 * Strona działa bez tego pliku: mapa i lista przychodzą z serwera, a odnośnik z listy prowadzi do
 * kółka (``#mu-N``), które ``:target`` podświetla samym CSS-em. Skrypt dokłada dwie rzeczy:
 *
 * - wyszukiwarkę (pole startuje ukryte, ``hidden`` w szablonie) – dopasowanie po nazwie
 *   miejscowości i województwa **bez polskich znaków**: „lodz” znajduje „Łódź”, „slaskie” –
 *   „śląskie”. Wiersz niesie gotową postać porównawczą z serwera (``data-search``,
 *   ``apps.core.text.fold``), tu składamy tylko zapytanie,
 * - podświetlenie bez skoku strony: kliknięcie w wiersz przygasza pozostałe kółka i pisze pod mapą
 *   „Miejscowość (województwo): liczba”; kliknięcie w kółko zaznacza i przewija do niego wiersz.
 *
 * Żadnego HTML-a sklejanego z napisów – tylko ``textContent``, klasy i atrybuty.
 */

(function () {
  "use strict";

  function fold(text) {
    return text
      .toLowerCase()
      .replace(/ł/g, "l")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function init(root) {
    const filter = root.querySelector("[data-pm-filter]");
    const search = root.querySelector("[data-pm-search]");
    const count = root.querySelector("[data-pm-count]");
    const empty = root.querySelector("[data-pm-empty]");
    const selected = root.querySelector("[data-pm-selected]");
    const map = root.querySelector(".pm-map");
    const rows = Array.prototype.slice.call(root.querySelectorAll(".pm-row"));
    const dots = {};
    root.querySelectorAll(".pm-dot[data-town]").forEach(function (dot) {
      dots[dot.getAttribute("data-town")] = dot;
    });
    let active = null;

    function rowFor(id) {
      return rows.find(function (row) {
        return row.getAttribute("data-town") === id;
      });
    }

    function clear() {
      if (active === null) {
        return;
      }
      const dot = dots[active];
      const row = rowFor(active);
      if (dot) {
        dot.classList.remove("is-on");
      }
      if (row) {
        row.classList.remove("is-on");
      }
      map.classList.remove("is-dimmed");
      selected.textContent = "";
      active = null;
    }

    function select(id) {
      if (active === id) {
        clear();
        return;
      }
      clear();
      active = id;
      const dot = dots[id];
      const row = rowFor(id);
      if (dot) {
        dot.classList.add("is-on");
        // Na wierzch: małe kółko pod dużym byłoby podświetlone, ale niewidoczne.
        dot.parentNode.appendChild(dot);
        map.classList.add("is-dimmed");
      }
      if (row) {
        row.classList.add("is-on");
        selected.textContent = row.getAttribute("data-caption") || "";
      }
    }

    function render() {
      const term = fold(search.value.trim());
      let shown = 0;
      rows.forEach(function (row) {
        const match = !term || (row.getAttribute("data-search") || "").indexOf(term) !== -1;
        row.hidden = !match;
        if (match) {
          shown += 1;
        }
      });
      empty.hidden = shown > 0;
      const template = count.getAttribute("data-template") || "";
      count.textContent = term
        ? template.replace("{shown}", String(shown)).replace("{all}", String(rows.length))
        : "";
    }

    rows.forEach(function (row) {
      const link = row.querySelector(".pm-row__link");
      if (!link) {
        return;
      }
      link.addEventListener("click", function (event) {
        event.preventDefault();
        select(row.getAttribute("data-town"));
      });
    });

    Object.keys(dots).forEach(function (id) {
      const dot = dots[id];
      dot.setAttribute("tabindex", "0");
      dot.setAttribute("role", "button");
      function choose() {
        select(id);
        const row = rowFor(id);
        if (row && !row.hidden) {
          row.scrollIntoView({ block: "nearest" });
        }
      }
      dot.addEventListener("click", choose);
      dot.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      });
    });

    if (filter && search) {
      filter.hidden = false;
      search.addEventListener("input", render);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-participant-map]").forEach(init);
  });
})();
