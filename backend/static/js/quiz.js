/* Test online: odliczanie, autozapis odpowiedzi i potwierdzenia przy działaniach nieodwracalnych.
 *
 * Czego ten skrypt **nie** robi: nie decyduje o niczym. Nie sprawdza odpowiedzi (klucza nie ma
 * w tej stronie i nie będzie), nie rozstrzyga, czy czas minął (rozstrzyga serwer, przy każdym
 * zapisie), nie blokuje wysłania formularza po terminie. Wszystko, czym się zajmuje, to trzy
 * uprzejmości: pokazanie, ile czasu zostało, zapisanie odpowiedzi, zanim uczestnik kliknie
 * „Zakończ”, i zapytanie „na pewno?” przed działaniem bez odwrotu.
 *
 * Strona jest kompletna bez tego pliku. Arkusz to zwykły formularz: bez JavaScriptu odpowiedzi
 * idą na serwer razem z przyciskiem „Zakończ test”, licznik pokazuje czas z chwili wczytania
 * strony (serwer wpisuje go w HTML), a potwierdzenia po prostu nie ma. To jest wymaganie, a nie
 * ambicja: część zawodów odbywa się w pracowniach szkolnych, w których skrypty bywają blokowane,
 * a uczestnik nie ma jak tego zmienić.
 *
 * Słowa komunikatów przychodzą w atrybutach ``data-word-*``, a nie są wpisane w tym pliku:
 * przeglądarka nie ma katalogu tłumaczeń, więc w angielskim interfejsie skrypt napisałby
 * „Do końca” zamiast „Time left”. Ta sama droga, co ``data-msg-*`` w ``upload-dropzone.js``.
 *
 * Do DOM trafiają wyłącznie węzły tekstowe (``textContent``) i klasy; nigdzie nie ma ``innerHTML``.
 */

(function () {
  "use strict";

  /* Co ile milisekund wysyłamy zmiany. Dwadzieścia sekund to kompromis: rzadziej znaczyłoby, że
     zerwane łącze kosztuje pół minuty pracy, częściej – że test z trzystu osobami generuje
     dziesięć żądań na sekundę przez cały czas trwania zawodów. Zmiana odpowiedzi wysyła się poza
     tym rytmem i od razu (patrz niżej), więc te dwadzieścia sekund dotyczy wyłącznie ponowienia
     po nieudanym zapisie i sytuacji, w której coś się rozjechało. */
  var AUTOSAVE_INTERVAL_MS = 20000;
  /* Zmiana w polu tekstowym nie leci na serwer po każdej literze – czekamy, aż uczestnik przestanie
     pisać. Sekunda i pół: krócej znaczy żądanie na słowo, dłużej – że szybkie „wpisz i kliknij
     Zakończ” wyprzedza zapis (co i tak nie gubi odpowiedzi, bo wysyła je sam formularz). */
  var TYPING_DEBOUNCE_MS = 1500;
  var SECOND = 1000;
  /* Poniżej pięciu minut licznik zmienia wygląd. Próg jest tu, a nie w arkuszu stylów, bo to
     decyzja o zachowaniu („kiedy ostrzegamy”), a nie o wyglądzie. */
  var WARNING_SECONDS = 300;

  function words(node) {
    return {
      left: node.dataset.wordLeft || "",
      over: node.dataset.wordOver || "",
      saved: node.dataset.wordSaved || "",
      saving: node.dataset.wordSaving || "",
      failed: node.dataset.wordFailed || ""
    };
  }

  /* Token CSRF z ciasteczka, nie z ukrytego pola: autozapis wysyła JSON-a fetchem i nie ma
     formularza, z którego miałby ten token przepisać. Nazwa ciasteczka jest domyślna dla Django. */
  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* „m:ss”, a nie same sekundy: przy dwudziestu minutach „1187 s” jest liczbą, którą trzeba
     przeliczyć w głowie, a uczestnik ma w tej chwili inne zajęcie. */
  function formatSeconds(total) {
    var minutes = Math.floor(total / 60);
    var seconds = total % 60;
    return minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
  }

  /* Odpowiedzi z formularza w kształcie, jakiego oczekuje serwer: {"<id pytania>": {...}}.
     Zbieramy **cały** formularz, a nie samo zmienione pole. Jest to droższe o kilka kilobajtów
     na żądanie i warte tego: zapis staje się wtedy bezstanowy (serwer dostaje pełny obraz
     arkusza), więc jedno nieudane żądanie nie zostawia po sobie luki, której nikt już nie nadrobi. */
  function collect(form) {
    var answers = {};
    var fields = form.querySelectorAll("[name^='q']");
    Array.prototype.forEach.call(fields, function (field) {
      var name = field.getAttribute("name");
      if (!/^q\d+$/.test(name)) {
        return;
      }
      var id = name.slice(1);
      if (field.type === "checkbox" || field.type === "radio") {
        if (!answers[id]) {
          answers[id] = { options: [] };
        }
        if (field.checked) {
          answers[id].options.push(field.value);
        }
        return;
      }
      /* Pole tekstowe obsługuje i krótką odpowiedź, i liczbę – wysyłamy oba klucze, a serwer
         zostawia ten, który pasuje do rodzaju pytania. Rozpoznawanie rodzaju w przeglądarce
         znaczyłoby powtórzenie tej wiedzy po stronie, która i tak nie rozstrzyga. */
      answers[id] = { text: field.value, value: field.value };
    });
    return answers;
  }

  function setupAttempt(timer) {
    var form = document.querySelector("[data-quiz-form]");
    var clock = timer.querySelector("[data-quiz-clock]");
    var status = timer.querySelector("[data-quiz-status]");
    var label = timer.querySelector(".quiz-timer__label");
    var text = words(timer);
    var url = timer.dataset.autosaveUrl;
    var deadline = Date.parse(timer.dataset.deadline);
    var secondsLeft = parseInt(timer.dataset.secondsLeft, 10) || 0;
    /* Gdy data z serwera jest nieczytelna (starsza przeglądarka, uszkodzony atrybut), zostajemy
       przy liczbie sekund policzonej przez serwer i odliczamy od niej. Brak licznika byłby tu
       gorszy niż licznik o sekundę niedokładny. */
    var useDeadline = !isNaN(deadline);
    var stopped = false;
    var dirty = false;
    var pending = false;
    var typingTimer = null;

    function remaining() {
      if (useDeadline) {
        return Math.max(0, Math.round((deadline - Date.now()) / SECOND));
      }
      return Math.max(0, secondsLeft);
    }

    function render() {
      var left = remaining();
      clock.textContent = left > 0 ? formatSeconds(left) : text.over;
      timer.classList.toggle("quiz-timer--warning", left > 0 && left <= WARNING_SECONDS);
      timer.classList.toggle("quiz-timer--over", left === 0);
      if (left === 0 && label) {
        /* Po czasie podpis „Do końca” przestaje być prawdą – zostaje samo „Czas minął”. */
        label.textContent = "";
      }
    }

    function say(message, className) {
      if (!status) {
        return;
      }
      status.textContent = message;
      status.className = "quiz-timer__save" + (className ? " " + className : "");
    }

    function send(force) {
      if (stopped || pending || !url || (!dirty && !force)) {
        return;
      }
      pending = true;
      dirty = false;
      say(text.saving);
      fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({ answers: collect(form) })
      })
        .then(function (response) {
          if (response.status === 409) {
            /* Serwer domknął podejście (czas minął). Przestajemy ponawiać i przechodzimy na
               podsumowanie – dalsze próby zapisu byłyby pukaniem do zamkniętych drzwi, a każda
               z nich kosztowałaby kolejne żądanie. */
            stopped = true;
            say(text.over, "quiz-timer__save--error");
            /* Przeładowanie, a nie skok pod zapisany adres: widok podejścia sam kieruje na
               podsumowanie, gdy podejście jest już zamknięte, więc adres wyniku nie musi być
               znany przeglądarce (i nie musi stać w HTML-u). */
            window.location.reload();
            return null;
          }
          if (!response.ok) {
            throw new Error("save failed");
          }
          return response.json();
        })
        .then(function (data) {
          if (!data) {
            return;
          }
          say(text.saved, "quiz-timer__save--ok");
          if (typeof data.seconds_left === "number" && !useDeadline) {
            /* Bez czytelnej daty końca prostujemy licznik odpowiedzią serwera: to jedyny zegar,
               który cokolwiek rozstrzyga. */
            secondsLeft = data.seconds_left;
          }
        })
        .catch(function () {
          /* Nieudany zapis **nie** gubi zmian: wracamy do stanu „jest co zapisać”, więc kolejny
             przebieg spróbuje ponownie. Komunikat mówi o połączeniu, a nie o błędzie strony. */
          dirty = true;
          say(text.failed, "quiz-timer__save--error");
        })
        .then(function () {
          pending = false;
        });
    }

    if (form) {
      form.addEventListener("change", function (event) {
        dirty = true;
        if (event.target && event.target.type === "text") {
          return;
        }
        /* Zaznaczenie wariantu to decyzja skończona – wysyłamy ją od razu. Wpisywanie tekstu
           decyzją skończoną nie jest, więc ma własne opóźnienie niżej. */
        send(false);
      });
      form.addEventListener("input", function (event) {
        if (!event.target || event.target.type !== "text") {
          return;
        }
        dirty = true;
        window.clearTimeout(typingTimer);
        typingTimer = window.setTimeout(function () {
          send(false);
        }, TYPING_DEBOUNCE_MS);
      });
      form.addEventListener("submit", function (event) {
        var message = form.dataset.wordConfirm;
        if (message && !window.confirm(message)) {
          event.preventDefault();
          return;
        }
        /* Od tej chwili autozapis jest zbędny i szkodliwy: formularz niesie komplet odpowiedzi,
           a równoległe żądanie mogłoby dojść po zakończeniu podejścia. */
        stopped = true;
        window.clearTimeout(typingTimer);
      });
    }

    render();
    window.setInterval(function () {
      if (!useDeadline && secondsLeft > 0) {
        secondsLeft -= 1;
      }
      render();
    }, SECOND);
    window.setInterval(function () {
      send(false);
    }, AUTOSAVE_INTERVAL_MS);
    /* Ostatnia szansa na zapis przy zamknięciu karty. ``visibilitychange`` zamiast
       ``beforeunload``: telefon przechodzący w tło nigdy nie wywoła ``beforeunload``, a to jest
       najczęstsza droga, którą uczestnik „znika” w połowie testu. */
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        send(false);
      }
    });
  }

  /* Potwierdzenia przy działaniach bez odwrotu (usunięcie pytania, przeliczenie punktów, start
     podejścia). Atrybut ``data-confirm`` zamiast ``onclick`` w HTML-u: polityka bezpieczeństwa
     nie dopuszcza skryptów inline, a bez skryptu przycisk ma nadal działać – po prostu bez pytania. */
  function setupConfirmations() {
    var buttons = document.querySelectorAll("[data-confirm]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function (event) {
        if (!window.confirm(button.dataset.confirm)) {
          event.preventDefault();
        }
      });
    });
  }

  function init() {
    setupConfirmations();
    var timer = document.querySelector("[data-quiz-timer]");
    if (timer) {
      setupAttempt(timer);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
