/* Google Analytics 4 w trybie zgody (Consent Mode v2) – odpowiednik „tagu Google” z instrukcji GA.
 *
 * Google każe wkleić w ``<head>`` każdej strony dwa znaczniki: ``<script async src=".../gtag/js">``
 * i skrypt inline z ``dataLayer``/``gtag('config')``. Pierwszy stoi dosłownie w szablonie bazowym
 * (templates/base.html, z nonce, więc bez wyjątku w CSP). Drugiego nie da się wkleić inline –
 * polityka bezpieczeństwa serwisu nie ma ``'unsafe-inline'`` – więc jego treścią jest ten plik.
 * Robi dokładnie to, co snippet Google'a, plus jedno, czego snippet nie ma: **tryb zgody**.
 *
 * Dlaczego tryb zgody, a nie ładowanie dopiero po kliknięciu: organizator chce tagu „tak jak każe
 * Google” (weryfikacja instalacji i raporty w GA widzą tag na każdej odsłonie), a prawo wymaga
 * zgody uprzedniej na cookie inne niż niezbędne (art. 173 Prawa telekomunikacyjnego / Prawo
 * komunikacji elektronicznej; podstawa: art. 6 ust. 1 lit. a RODO). Consent Mode v2 godzi jedno
 * z drugim: dopóki użytkownik nie kliknie „Akceptuję wszystkie”, ``analytics_storage`` jest
 * ``denied`` – biblioteka nie zapisuje żadnego cookie ani identyfikatora i wysyła wyłącznie
 * bezcookie'owe sygnały techniczne (adres strony, typ przeglądarki, przybliżona lokalizacja
 * z adresu IP). Tak opisują to polityka cookies i polityka RODO – ten plik ma się z nimi zgadzać.
 *
 * Skąd identyfikator: ``data-ga-id`` na ``<html>`` (szablon bazowy wypisuje go tylko wtedy, gdy
 * organizator wpisał go w ``/cms/``). Brak identyfikatora = plik nie robi nic.
 *
 * Skrypt jest **synchroniczny w ``<head>``** (bez ``defer``): ``consent default`` musi trafić do
 * ``dataLayer`` zanim biblioteka Google'a (``async``) zacznie go czytać – tak wymaga dokumentacja
 * trybu zgody. Kolejka ``dataLayer`` i tak przetrwa dowolną kolejność ładowania obu skryptów.
 */
(function () {
  "use strict";

  var CONSENT_KEY = "cookie-consent";
  var CONSENT_EVENT = "cookie-consent";
  var GRANTED = "all";

  var id = document.documentElement.getAttribute("data-ga-id") || "";
  if (id === "") {
    return;
  }

  /* Brak dostępu do magazynu (tryb prywatny, zablokowane dane witryn) czytamy jako „brak zgody”:
     strona bez pomiaru działa, pomiar bez zgody jest naruszeniem. */
  function granted() {
    try {
      return window.localStorage.getItem(CONSENT_KEY) === GRANTED;
    } catch (error) {
      return false;
    }
  }

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  /* Biblioteka Google'a i jej dokumentacja szukają globalnego ``gtag``. */
  window.gtag = gtag;

  /* Zgoda dotyczy statystyk i **tylko** statystyk. Trzy „denied” nie są ozdobnikiem: bez nich
     usługa z włączonymi funkcjami reklamowymi zaczęłaby zbierać dane do remarketingu, czego ani
     polityka cookie, ani polityka RODO nie zapowiadają. ``analytics_storage`` zależy od decyzji
     zapisanej przez static/js/consent.js na poprzednich odsłonach. */
  gtag("consent", "default", {
    analytics_storage: granted() ? "granted" : "denied",
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
  });

  gtag("js", new Date());
  gtag("config", id, {
    /* Anonimizacja adresu IP (w GA4 domyślna, deklarujemy ją jawnie – polityka RODO ją obiecuje,
       a domyślne ustawienie dostawcy nie jest naszym zobowiązaniem). Google Signals i personalizacja
       reklam wyłączone: bez nich nie powstaje profil międzyurządzeniowy. */
    anonymize_ip: true,
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
  });

  /* Decyzja na pasku działa natychmiast, bez przeładowania: „Akceptuję wszystkie” włącza cookie
     od tej odsłony, „Tylko niezbędne” (także jako wycofanie zgody) wyłącza je – cookie ``_ga*``
     kasuje wtedy consent.js. Zdarzenie wystawia static/js/consent.js. */
  document.addEventListener(CONSENT_EVENT, function (event) {
    var value = event.detail && event.detail.value;
    gtag("consent", "update", {
      analytics_storage: value === GRANTED ? "granted" : "denied",
    });
  });
})();
