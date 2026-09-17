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

  /* Rozwinięcie po najechaniu (organizator: „rozwijanie sekcji po najechaniu myszką”).
     Tylko na urządzeniach z prawdziwym kursorem – na dotykowym „hover” to pierwsze tapnięcie
     i sekcja otwierałaby się przed kliknięciem w pozycję. Krótkie opóźnienie odsiewa przelot
     kursora przez menu w drodze do treści. Zwinięta sekcja otwarta najazdem wraca do stanu
     zwiniętego po zjechaniu, a najazd **nie zapisuje** stanu w pamięci – zapamiętane jest tylko
     to, co użytkownik kliknął. */
  var HOVER_OPEN_DELAY = 150;
  var HOVER_CLOSE_DELAY = 350;

  function hoverCapable() {
    try {
      return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    } catch (error) {
      return false;
    }
  }

  function init() {
    var groups = document.querySelectorAll(".panel-nav__group[data-nav-group]");
    if (!groups.length) {
      return;
    }
    var collapsed = readCollapsed();
    var useHover = hoverCapable();
    Array.prototype.forEach.call(groups, function (group) {
      var slug = group.getAttribute("data-nav-group");
      var pinned = group.hasAttribute("data-nav-pinned");
      var hoverOpened = false;
      var timer = null;
      if (!pinned && collapsed.indexOf(slug) !== -1) {
        group.removeAttribute("open");
      }
      group.addEventListener("toggle", function () {
        if (hoverOpened) {
          return; // zmiana wywołana najazdem – nie jest decyzją użytkownika
        }
        var current = readCollapsed().filter(function (item) {
          return item !== slug;
        });
        if (!group.open) {
          current.push(slug);
        }
        writeCollapsed(current);
      });
      if (!useHover || pinned) {
        return;
      }
      group.addEventListener("mouseenter", function () {
        window.clearTimeout(timer);
        if (group.open) {
          return;
        }
        timer = window.setTimeout(function () {
          hoverOpened = true;
          group.open = true;
        }, HOVER_OPEN_DELAY);
      });
      group.addEventListener("mouseleave", function () {
        window.clearTimeout(timer);
        if (!hoverOpened) {
          return;
        }
        timer = window.setTimeout(function () {
          if (hoverOpened) {
            group.open = false;
            // ``toggle`` jest zdarzeniem asynchronicznym – flaga zdejmowana dopiero po nim.
            window.setTimeout(function () {
              hoverOpened = false;
            }, 0);
          }
        }, HOVER_CLOSE_DELAY);
      });
      // Kliknięcie w nagłówek sekcji otwartej najazdem = „chcę ją mieć otwartą”: od tej chwili
      // to decyzja użytkownika, zapisywana jak każde inne kliknięcie.
      group.addEventListener("click", function (event) {
        if (hoverOpened && event.target.closest(".panel-nav__heading")) {
          hoverOpened = false;
          window.clearTimeout(timer);
          event.preventDefault();
          writeCollapsed(
            readCollapsed().filter(function (item) {
              return item !== slug;
            })
          );
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
