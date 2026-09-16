/* Licznik czasu pracy nad recenzją: sygnał życia co minutę, koniec po pięciu minutach bezczynności.
 *
 * Co ten skrypt wysyła: puste żądanie POST pod adres recenzji. Nic poza tym – ani treści, ani
 * pozycji kursora, ani zdarzeń klawiatury. Ruch myszy i naciśnięcia klawiszy są tu wyłącznie
 * sygnałem „ktoś jeszcze pracuje”; nie opuszczają przeglądarki i nie są nigdzie zapisywane.
 * Długość doliczonego czasu ustala **serwer** (``apps.grading.worklog.heartbeat``), bo czas
 * przysłany przez mierzonego nie byłby pomiarem.
 *
 * Trzy decyzje, które warto rozumieć czytając ten plik:
 *
 * - **bezczynność kończy pomiar**. Po ``IDLE_MS`` bez ruchu myszy i klawiatury przestajemy wysyłać
 *   sygnały. Karta zostawiona otwarta na noc nie dopisze więc ośmiu godzin – a gdyby jakiś sygnał
 *   i tak przeszedł, serwer i tak przytnie go do własnego sufitu,
 * - **``sendBeacon`` przy chowaniu karty**. Zwykły ``fetch`` wysłany w chwili zamykania zakładki
 *   bywa porzucany razem z dokumentem; ``sendBeacon`` przeglądarka dostarcza już po jego zamknięciu.
 *   Dzięki temu ostatnie minuty pracy nie giną przy zamknięciu karty,
 * - **token CSRF z ciasteczka**, a nie z ukrytego pola formularza: formularz oceny znika z ekranu
 *   przy recenzji, której nie wolno już zmieniać, a licznik ma działać niezależnie od tego, co
 *   akurat stoi w drugiej kolumnie.
 *
 * Brak połączenia jest ignorowany bez śladu: pomiar czasu nie może zasypać recenzenta komunikatami
 * o błędach w trakcie oceniania pracy.
 */
(function () {
  "use strict";

  var label = document.querySelector("[data-worklog-label]");
  if (!label || label.dataset.worklogActive !== "1") return;

  var url = label.dataset.worklogUrl || "";
  if (!url) return;

  /* Te same wartości, co w ``apps.grading.worklog``. Serwer i tak przycina przyrost, więc
     rozjazd byłby najwyżej niedoszacowaniem – ale liczby mają się zgadzać z dokumentacją. */
  var HEARTBEAT_MS = 60 * 1000;
  var IDLE_MS = 5 * 60 * 1000;

  var lastActivity = Date.now();

  function readCookie(name) {
    var parts = (document.cookie || "").split(";");
    for (var index = 0; index < parts.length; index += 1) {
      var pair = parts[index].trim();
      if (pair.indexOf(name + "=") === 0) {
        return decodeURIComponent(pair.slice(name.length + 1));
      }
    }
    return "";
  }

  function markActivity() {
    lastActivity = Date.now();
  }

  ["pointerdown", "pointermove", "keydown", "wheel", "scroll"].forEach(function (name) {
    document.addEventListener(name, markActivity, { passive: true });
  });

  function idle() {
    return Date.now() - lastActivity > IDLE_MS;
  }

  function send() {
    if (idle() || document.visibilityState === "hidden") return;
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": readCookie("csrftoken"), Accept: "application/json" },
      body: "",
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        if (payload && payload.label) {
          label.textContent = "Czas pracy: " + payload.label;
        }
      })
      .catch(function () {
        /* Cisza jest tu świadoma – patrz nagłówek pliku. */
      });
  }

  /* Ostatni sygnał przy chowaniu karty. ``sendBeacon`` nie przyjmuje własnych nagłówków, więc
     token CSRF jedzie w ciele jako pole formularza – Django akceptuje obie drogi. */
  function flush() {
    if (idle()) return;
    var payload = new FormData();
    payload.append("csrfmiddlewaretoken", readCookie("csrftoken"));
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, payload);
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      flush();
    } else {
      markActivity();
    }
  });
  window.addEventListener("pagehide", flush);

  /* Pierwszy sygnał od razu: zakłada licznik, żeby kolejny miał od czego liczyć odstęp. */
  send();
  window.setInterval(send, HEARTBEAT_MS);
})();
