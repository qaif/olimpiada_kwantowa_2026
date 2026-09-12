/* Pasek informacji o plikach cookie (templates/base.html).
 *
 * Dlaczego to nie jest „banner zgody”: serwis stawia wyłącznie cookie niezbędne – sesję logowania
 * i token CSRF. Nie ma tu żadnego skryptu reklamowego ani analitycznego, którego uruchomienie
 * dałoby się wstrzymać do kliknięcia, więc pasek niczego nie blokuje i nie zapisuje „zgody”.
 * Zapamiętuje jedną rzecz: że informację już pokazaliśmy.
 *
 * Dlaczego ``localStorage``, a nie cookie: informacja o cookie nie może dokładać kolejnego cookie –
 * wpis w ``localStorage`` nie jest wysyłany do serwera i nie dotyczy go żadna zgoda.
 *
 * Dlaczego klasa na ``<html>``, a nie ukrycie samego paska: skrypt stoi w ``<head>`` bez ``defer``,
 * więc klasa ląduje **przed** narysowaniem ``<body>``. Pasek jest w HTML widoczny (informacja
 * dociera też przy wyłączonym JavaScripcie), a komu raz zniknął, temu nie mignie na każdej
 * kolejnej podstronie.
 */
(function () {
  "use strict";

  var KEY = "cookie-notice-ack";
  var ACK_CLASS = "cookie-notice-ack";

  /* Tryb prywatny i przeglądarki z zablokowanym magazynem rzucają wyjątkiem już przy odczycie –
     wtedy pasek po prostu pokaże się ponownie. To gorsze doświadczenie, nie błąd strony. */
  function acknowledged() {
    try {
      return window.localStorage.getItem(KEY) === "1";
    } catch (error) {
      return false;
    }
  }

  function remember() {
    try {
      window.localStorage.setItem(KEY, "1");
    } catch (error) {
      /* zignorowane świadomie: patrz komentarz wyżej */
    }
  }

  if (acknowledged()) {
    document.documentElement.classList.add(ACK_CLASS);
  }

  function wire() {
    var notice = document.querySelector("[data-cookie-notice]");
    if (notice === null) {
      return;
    }
    var dismiss = notice.querySelector("[data-cookie-notice-dismiss]");
    if (dismiss === null) {
      return;
    }
    dismiss.addEventListener("click", function () {
      document.documentElement.classList.add(ACK_CLASS);
      remember();
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
