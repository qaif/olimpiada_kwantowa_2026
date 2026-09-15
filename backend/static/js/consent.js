/* Pasek cookie: informacja albo zgoda – zależnie od tego, czy serwis ma włączoną analitykę.
 *
 * Plik nazywał się wcześniej ``cookie-notice.js`` i robił jedną rzecz: pamiętał, że informację już
 * pokazaliśmy. Odkąd organizator może włączyć Google Analytics 4 (``cms.SiteSettings``
 * → „Analityka”), ten sam pasek bywa **bramką zgody**, bo cookie analityczne wolno zapisać dopiero
 * po jej udzieleniu (art. 173 Prawa telekomunikacyjnego / Prawo komunikacji elektronicznej oraz
 * art. 6 ust. 1 lit. a RODO). Stąd nowa nazwa: skrypt rozstrzyga o zgodzie, a nie o zamknięciu
 * komunikatu.
 *
 * Dwa tryby, jeden pasek:
 *
 * - **bez identyfikatora GA** (brak ``<meta name="ga-measurement-id">``) serwis stawia wyłącznie
 *   cookie niezbędne. Nie ma czego wstrzymywać do kliknięcia, więc pasek pozostaje informacją
 *   z jednym przyciskiem „Rozumiem”, a jedynym zapisem jest ``cookie-notice-ack``,
 * - **z identyfikatorem GA** pasek pyta o zgodę. Dopóki odpowiedzi nie ma, nie wczytuje się nic:
 *   ``static/js/analytics.js`` czeka na wynik. Decyzja („all” albo „necessary”) i chwila jej
 *   podjęcia idą do ``cookie-consent`` i ``cookie-consent-at``.
 *
 * Dlaczego wcześniejsze „Rozumiem” nie liczy się jako zgoda: klucz ``cookie-notice-ack`` powstał
 * pod paskiem, który o nic nie pytał. Po włączeniu analityki pasek pojawia się więc ponownie –
 * inaczej zgodę „udzieliłby” ktoś, komu nigdy nie zadaliśmy pytania.
 *
 * Dlaczego ``localStorage``, a nie cookie: zapis o zgodzie nie może sam być kolejnym plikiem
 * cookie wysyłanym na serwer. Wpis zostaje w przeglądarce i nikt go nie odczytuje poza tą stroną.
 *
 * Dlaczego klasa na ``<html>``, a nie ukrycie samego paska: skrypt stoi w ``<head>`` bez ``defer``,
 * więc klasa ląduje **przed** narysowaniem ``<body>``. Pasek jest w HTML widoczny (informacja
 * dociera też przy wyłączonym JavaScripcie), a komu raz zniknął, temu nie mignie na każdej
 * kolejnej podstronie.
 */
(function () {
  "use strict";

  var ACK_KEY = "cookie-notice-ack";
  var CONSENT_KEY = "cookie-consent";
  var CONSENT_AT_KEY = "cookie-consent-at";
  var ACK_CLASS = "cookie-notice-ack";

  /* Zdarzenie, którym pasek mówi reszcie strony, co użytkownik wybrał. Nasłuchuje go
     ``static/js/analytics.js`` – dzięki temu zgoda działa **od razu**, bez przeładowania strony,
     a żaden z dwóch plików nie musi znać wnętrza drugiego. */
  var CONSENT_EVENT = "cookie-consent";

  var ALL = "all";
  var NECESSARY = "necessary";

  /* Tryb prywatny i przeglądarki z zablokowanym magazynem rzucają wyjątkiem już przy odczycie –
     wtedy pasek po prostu pokaże się ponownie. To gorsze doświadczenie, nie błąd strony.
     W trybie zgody skutek jest po właściwej stronie: brak zapisu znaczy „nie pytano”, czyli
     analityka się nie wczytuje. */
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
      /* zignorowane świadomie: patrz komentarz wyżej */
    }
  }

  /* Identyfikator GA jest w dokumencie tylko wtedy, gdy organizator go wpisał – jego obecność
     jest więc przełącznikiem trybu. Czytamy go tak samo, jak analytics.js: atrybut na ``<html>``
     ma pierwszeństwo, ``<meta>`` jest wariantem używanym przez szablon bazowy. */
  function measurementId() {
    var fromRoot = document.documentElement.getAttribute("data-ga-id");
    if (fromRoot) {
      return fromRoot;
    }
    var meta = document.querySelector('meta[name="ga-measurement-id"]');
    return (meta && meta.getAttribute("content")) || "";
  }

  function consentMode() {
    return measurementId() !== "";
  }

  function decided() {
    var value = read(CONSENT_KEY);
    return value === ALL || value === NECESSARY;
  }

  function acknowledged() {
    return read(ACK_KEY) === "1";
  }

  function hideBar() {
    document.documentElement.classList.add(ACK_CLASS);
  }

  function showBar() {
    document.documentElement.classList.remove(ACK_CLASS);
  }

  /* --- wycofanie zgody -------------------------------------------------------------------- */

  /* Domeny, na których mogło zostać zapisane cookie GA. Google zapisuje ``_ga`` na najwyższej
     domenie, jaką potrafi ustalić (dla ``www.olimpiadakwantowa.pl`` będzie to
     ``.olimpiadakwantowa.pl``), a skrypt nie ma jak odczytać atrybutu ``Domain`` istniejącego
     cookie – więc kasujemy je na każdym wariancie: bez atrybutu (cookie samego hosta), na hoście
     i na każdej domenie nadrzędnej aż do dwóch członów. Adres produkcyjny nie jest tu wpisany
     literałem: zaszyta domena rozjechałaby się przy pierwszej zmianie adresu serwisu i po cichu
     przestałaby cokolwiek kasować. */
  function cookieDomains() {
    var host = window.location.hostname;
    var domains = ["", host];
    var parts = host.split(".");
    while (parts.length >= 2) {
      domains.push("." + parts.join("."));
      parts.shift();
    }
    return domains;
  }

  /* Nazwy wszystkich widocznych cookie zaczynających się od ``_ga``: samo ``_ga`` oraz
     ``_ga_<identyfikator strumienia>``. Lista jest czytana z dokumentu, a nie budowana
     z identyfikatora, bo po zmianie strumienia w przeglądarce zostają też stare wpisy. */
  function analyticsCookieNames() {
    return document.cookie
      .split(";")
      .map(function (entry) {
        return entry.split("=")[0].trim();
      })
      .filter(function (name) {
        return name.indexOf("_ga") === 0;
      });
  }

  function clearAnalyticsCookies() {
    var names = analyticsCookieNames();
    var domains = cookieDomains();
    names.forEach(function (name) {
      domains.forEach(function (domain) {
        document.cookie =
          name + "=; Max-Age=0; path=/" + (domain ? "; domain=" + domain : "") + "; SameSite=Lax";
      });
    });
  }

  /* --- decyzja --------------------------------------------------------------------------- */

  function decide(value) {
    write(CONSENT_KEY, value);
    /* Znacznik czasu w osobnym kluczu, żeby wartość samej zgody dawała się przeczytać wprost
       (także w kontroli e2e) – i żeby dało się kiedyś odpytać o wiek decyzji, gdyby organizator
       chciał ją odnawiać co jakiś czas. */
    write(CONSENT_AT_KEY, new Date().toISOString());
    hideBar();
    if (value !== ALL) {
      /* Wycofanie zgody nie może zostawić po sobie identyfikatora w przeglądarce – dlatego
         „Tylko niezbędne” kasuje ``_ga*`` także wtedy, gdy wcześniej ktoś kliknął „Akceptuję”. */
      clearAnalyticsCookies();
    }
    document.dispatchEvent(new CustomEvent(CONSENT_EVENT, { detail: { value: value } }));
  }

  /* --- start ------------------------------------------------------------------------------ */

  if (consentMode() ? decided() : acknowledged()) {
    hideBar();
  }

  function wire() {
    var notice = document.querySelector("[data-cookie-notice]");
    if (notice === null) {
      return;
    }

    var dismiss = notice.querySelector("[data-cookie-notice-dismiss]");
    if (dismiss !== null) {
      dismiss.addEventListener("click", function () {
        hideBar();
        write(ACK_KEY, "1");
      });
    }

    Array.prototype.forEach.call(notice.querySelectorAll("[data-cookie-consent]"), function (button) {
      button.addEventListener("click", function () {
        decide(button.getAttribute("data-cookie-consent"));
      });
    });

    /* „Ustawienia cookies” w stopce: jedyna droga wycofania zgody bez czyszczenia przeglądarki.
       Szablon pokazuje ten przycisk wyłącznie w trybie zgody – bez analityki nie ma czego cofać. */
    Array.prototype.forEach.call(document.querySelectorAll("[data-cookie-settings]"), function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        showBar();
      });
    });
  }

  /* Skrypt wykonuje się przed zbudowaniem ``<body>``, więc przycisku jeszcze nie ma; drugi warunek
     jest na wypadek wczytania pliku później (np. z pamięci podręcznej przy nawigacji HTMX). */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
