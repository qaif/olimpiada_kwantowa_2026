/* Baner komunikatów organizatora: pamięć o zamkniętych komunikatach.
 *
 * Czego ten skrypt **nie** robi: nie rysuje banera i nie decyduje o tym, czy się pokazać. Robi to
 * serwer (``templates/cms/_announcements.html`` plus procesor kontekstu ``apps.cms.announcements``),
 * dzięki czemu komunikat o awarii dociera także do kogoś z wyłączonym JavaScriptem – a to jest
 * dokładnie ta sytuacja, w której komunikat o awarii bywa potrzebny.
 *
 * Skrypt dokłada jedną rzecz: **przycisk zamknięcia i pamięć o nim**. Przycisk jest w HTML-u
 * z atrybutem ``hidden`` i to skrypt go odsłania – bez skryptu byłby przyciskiem-atrapą, który
 * po kliknięciu nic nie robi.
 *
 * Gdzie mieszka pamięć: ``localStorage`` przeglądarki, pod kluczem z identyfikatorem komunikatu.
 * Nie cookie (nie ma powodu wysyłać tego na serwer przy każdym żądaniu) i nie konto (to jest
 * preferencja widoku na tym urządzeniu, a nie dana o osobie – i ma działać także dla gościa).
 *
 * Komunikat **niezamykalny** (``dismissible=False``: awaria, termin) nie dostaje przycisku
 * w ogóle. To jest decyzja organizatora zapisana w modelu i skrypt jej nie podważa: nie ma tu
 * żadnej drogi, którą dałoby się schować baner, którego schować nie wolno.
 *
 * Tryb prywatny i przeglądarki z zablokowanym magazynem rzucają wyjątkiem już przy odczycie –
 * wtedy baner po prostu wraca po przeładowaniu. To gorsze doświadczenie, a nie błąd strony,
 * i po właściwej stronie: w razie wątpliwości komunikat organizatora jest widoczny.
 */
(function () {
  "use strict";

  /* Prefiks klucza. Z identyfikatorem komunikatu na końcu, więc zamknięcie jednego banera nie
     chowa następnego – a nowy komunikat pojawia się mimo tego, że poprzedni został zamknięty. */
  var KEY_PREFIX = "announcement-dismissed:";

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      /* zignorowane świadomie: patrz komentarz w nagłówku pliku */
    }
  }

  function key(banner) {
    return KEY_PREFIX + banner.getAttribute("data-announcement");
  }

  function dismissed(banner) {
    return read(key(banner)) === "1";
  }

  function hide(banner) {
    banner.hidden = true;
  }

  function wire() {
    var banners = document.querySelectorAll("[data-announcement][data-announcement-dismissible]");
    Array.prototype.forEach.call(banners, function (banner) {
      if (dismissed(banner)) {
        hide(banner);
        return;
      }
      var button = banner.querySelector("[data-announcement-close]");
      if (button === null) {
        return;
      }
      /* Odsłaniamy przycisk dopiero tutaj: jego obecność jest obietnicą, że kliknięcie coś zrobi. */
      button.hidden = false;
      button.addEventListener("click", function () {
        write(key(banner), "1");
        hide(banner);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
