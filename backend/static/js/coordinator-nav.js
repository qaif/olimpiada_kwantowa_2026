/* Menu boczne panelu koordynatora: pamięć zwiniętych sekcji.

   Serwer renderuje każdą sekcję (``<details class="panel-nav__group">``) jako otwartą, więc bez
   skryptu menu jest kompletne. Skrypt robi dwie rzeczy: przy wczytaniu zwija sekcje, które
   użytkownik zwinął wcześniej (localStorage, klucz per sekcja), i zapisuje każdą zmianę.
   Sekcje przypięte (``data-nav-pinned`` – pulpit i sekcja z pozycją aktywną) nigdy nie są zwijane
   automatycznie: zwinięte menu nie może ukrywać miejsca, w którym użytkownik właśnie jest.
   Wszystko w try/catch – localStorage bywa niedostępny (tryb prywatny, polityka firmowa),
   a menu ma wtedy po prostu nie pamiętać. */
(function () {
  "use strict";

  var KEY = "coordinator-nav-collapsed";

  function readCollapsed() {
    try {
      var raw = window.localStorage.getItem(KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function writeCollapsed(list) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(list));
    } catch (error) {
      /* brak pamięci – trudno, menu nie zapamięta */
    }
  }

  function init() {
    var groups = document.querySelectorAll(".panel-nav__group[data-nav-group]");
    if (!groups.length) {
      return;
    }
    var collapsed = readCollapsed();
    Array.prototype.forEach.call(groups, function (group) {
      var slug = group.getAttribute("data-nav-group");
      var pinned = group.hasAttribute("data-nav-pinned");
      if (!pinned && collapsed.indexOf(slug) !== -1) {
        group.removeAttribute("open");
      }
      group.addEventListener("toggle", function () {
        var current = readCollapsed().filter(function (item) {
          return item !== slug;
        });
        if (!group.open) {
          current.push(slug);
        }
        writeCollapsed(current);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
