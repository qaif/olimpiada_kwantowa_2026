/* Odświeżanie odliczania w nagłówku „Co teraz” panelu uczestnika.
 *
 * Czego ten skrypt **nie** robi: nie liczy terminu i nie decyduje o niczym. Liczbę („za 3 dni,
 * 14 godz.”) wypisuje serwer przy renderowaniu strony i bez JavaScriptu jest ona prawdziwa –
 * po prostu z chwili wczytania. Skrypt dokłada jedno: w karcie zostawionej otwartej na godzinę
 * licznik nie zostaje na wartości sprzed godziny.
 *
 * Punktem odniesienia jest czas SERWERA z ``data-server-now``: przesunięty zegar przeglądarki
 * nie może pokazać uczestnikowi fałszywego zapasu czasu. Sam deadline i tak egzekwuje serwer
 * w transakcji uploadu (``apps.submissions.services.create_submission``).
 *
 * Słowa („dni”, „godz.”, „termin minął”) przychodzą w atrybutach ``data-word-*``, a nie są
 * wpisane tutaj: przeglądarka nie ma katalogu tłumaczeń, więc w angielskim interfejsie skrypt
 * napisałby po minucie „za 3 dni” zamiast „in 3 days”. Skład tekstu (kolejność części, przecinek)
 * jest **ten sam**, co w ``apps.web.participant_now.remaining_text`` – test porównuje oba.
 *
 * Krok to minuta, nie sekunda: odliczanie panelu jest podane z dokładnością do minut, a budzenie
 * karty sześćdziesiąt razy częściej niż zmienia się napis kosztowałoby baterię i nic nie dawało.
 */

(function () {
  "use strict";

  var STEP_MS = 60 * 1000;

  function wordsOf(element) {
    var data = element.dataset;
    return {
      prefix: data.wordPrefix || "",
      day: data.wordDay || "",
      days: data.wordDays || "",
      hours: data.wordHours || "",
      minutes: data.wordMinutes || "",
      passed: data.wordPassed || "",
      soon: data.wordSoon || "",
    };
  }

  function remainingText(milliseconds, words) {
    var total = Math.floor(milliseconds / 1000);
    if (total <= 0) return words.passed;
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var parts;
    if (days > 0) {
      parts = [days + " " + (days === 1 ? words.day : words.days), hours + " " + words.hours];
    } else if (hours > 0) {
      parts = [hours + " " + words.hours, minutes + " " + words.minutes];
    } else if (minutes > 0) {
      parts = [minutes + " " + words.minutes];
    } else {
      return words.soon;
    }
    return words.prefix + " " + parts.join(", ");
  }

  function start(element) {
    var value = element.querySelector("[data-countdown-value]");
    var deadline = Date.parse(element.dataset.deadline || "");
    var serverNow = Date.parse(element.dataset.serverNow || "");
    if (!value || isNaN(deadline)) return;
    var words = wordsOf(element);
    /* Różnica między zegarem przeglądarki a zegarem serwera, zdjęta raz przy starcie. */
    var skew = isNaN(serverNow) ? 0 : Date.now() - serverNow;
    var tick = function () {
      var remaining = deadline - (Date.now() - skew);
      value.textContent = remainingText(remaining, words);
      value.classList.toggle("now__remaining--passed", remaining <= 0);
    };
    tick();
    window.setInterval(tick, STEP_MS);
  }

  function init() {
    var nodes = document.querySelectorAll("[data-countdown]");
    for (var i = 0; i < nodes.length; i += 1) start(nodes[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
