/* Google Analytics 4 – wczytywany wyłącznie po zgodzie (templates/base.html).
 *
 * Dlaczego to nie jest zwykły „snippet z GA”: snippet Google'a ładuje ``gtag/js`` już przy
 * wejściu na stronę, a cookie ``_ga`` powstaje przed jakimkolwiek kliknięciem. Dla cookie innych
 * niż niezbędne prawo wymaga zgody **uprzedniej** (art. 173 Prawa telekomunikacyjnego / Prawo
 * komunikacji elektronicznej; podstawa przetwarzania: art. 6 ust. 1 lit. a RODO), więc kolejność
 * jest tu odwrócona: najpierw odpowiedź użytkownika, potem dopiero jakiekolwiek żądanie do Google'a.
 *
 * Dlaczego **nie** używamy „denied pings” trybu zgody Google'a: w tamtym wariancie skrypt wczytuje
 * się od razu i wysyła bezcookie'owe sygnały jeszcze przed decyzją. To dalej jest przekazanie
 * adresu IP i adresu strony do dostawcy spoza EOG bez podstawy – więc dopóki zgody nie ma,
 * nie wychodzi stąd **nic**. Wywołanie ``consent default`` robimy już po zgodzie i wyłącznie po to,
 * żeby zadeklarować Google'owi, że zgoda dotyczy statystyk, a nie reklam.
 *
 * Skąd identyfikator: ``<meta name="ga-measurement-id">`` (szablon bazowy wypisuje go tylko wtedy,
 * gdy organizator wpisał go w ``/cms/``) albo ``data-ga-id`` na ``<html>``. Brak identyfikatora =
 * plik nie robi nic, nawet jeśli ktoś go dołączy ręcznie.
 *
 * CSP: plik jest nonce'owany, a ``script-src`` ma ``'strict-dynamic'``, więc skrypt, który on sam
 * doklei przez ``createElement``, dziedziczy zaufanie – bez ``'unsafe-inline'`` i bez nonce'a na
 * doklejanym znaczniku. Host ``googletagmanager.com`` jest w polityce fallbackiem dla starszych
 * przeglądarek i wchodzi do niej tylko razem z identyfikatorem (apps/web/middleware.py).
 */
(function () {
  "use strict";

  var CONSENT_KEY = "cookie-consent";
  var CONSENT_EVENT = "cookie-consent";
  var GRANTED = "all";

  var loaded = false;

  function measurementId() {
    var fromRoot = document.documentElement.getAttribute("data-ga-id");
    if (fromRoot) {
      return fromRoot;
    }
    var meta = document.querySelector('meta[name="ga-measurement-id"]');
    return (meta && meta.getAttribute("content")) || "";
  }

  /* Brak dostępu do magazynu (tryb prywatny, zablokowane dane witryn) czytamy jako „brak zgody”.
     Strona bez pomiaru działa; pomiar bez zgody jest naruszeniem. */
  function granted() {
    try {
      return window.localStorage.getItem(CONSENT_KEY) === GRANTED;
    } catch (error) {
      return false;
    }
  }

  function gtag() {
    window.dataLayer.push(arguments);
  }

  function load() {
    if (loaded) {
      return;
    }
    var id = measurementId();
    if (id === "") {
      return;
    }
    loaded = true;

    window.dataLayer = window.dataLayer || [];
    /* Biblioteka Google'a szuka globalnego ``gtag`` (tak nazywa go jej własna dokumentacja
       i tak wołają go ewentualne przyszłe zdarzenia własne strony). Kolejka i tak idzie przez
       ``dataLayer``, więc wywołania sprzed wczytania biblioteki nie giną. */
    window.gtag = gtag;

    /* Zgoda dotyczy statystyk i **tylko** statystyk. Trzy „denied” nie są ozdobnikiem: bez nich
       usługa z włączonymi funkcjami reklamowymi zaczęłaby zbierać dane do remarketingu, czego
       ani polityka cookie, ani polityka RODO nie zapowiadają. */
    gtag("consent", "default", {
      analytics_storage: "granted",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
    });

    var script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(id);
    document.head.appendChild(script);

    gtag("js", new Date());
    gtag("config", id, {
      /* Anonimizacja adresu IP (w GA4 domyślna, deklarujemy ją jawnie – polityka RODO ją
         obiecuje, a domyślne ustawienie dostawcy nie jest naszym zobowiązaniem).
         Google Signals i personalizacja reklam wyłączone: bez nich nie powstaje profil
         międzyurządzeniowy, a odbiorcą danych zostaje sama statystyka odwiedzin. */
      anonymize_ip: true,
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
    });
  }

  /* Wejście na kolejną podstronę z już udzieloną zgodą: ładujemy od razu. */
  if (granted()) {
    load();
  }

  /* Kliknięcie „Akceptuję wszystkie” działa natychmiast – bez przeładowania strony, więc pierwsza
     odsłona (ta, na której użytkownik zgodę wyraził) też jest policzona. Zdarzenie wystawia
     static/js/consent.js. Odwrotnej drogi nie ma: raz wczytanej biblioteki nie da się „odwczytać”,
     więc wycofanie zgody kasuje cookie i działa od następnej odsłony (tak samo jak w każdej innej
     implementacji GA). */
  document.addEventListener(CONSENT_EVENT, function (event) {
    if (event.detail && event.detail.value === GRANTED) {
      load();
    }
  });
})();
