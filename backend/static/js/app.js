/* Wspólny skrypt interfejsu: komponenty Alpine (build CSP) i drobne ustawienia HTMX.
 *
 * Build ``@alpinejs/csp`` nie ewaluuje wyrażeń – w atrybutach x-* wolno użyć wyłącznie nazwy
 * właściwości albo metody komponentu zarejestrowanego przez ``Alpine.data``. Dzięki temu
 * ``script-src`` nie potrzebuje ani 'unsafe-inline', ani 'unsafe-eval'.
 *
 * Rejestracja idzie przez zdarzenie ``alpine:init``, więc kolejność ładowania skryptów nie ma
 * znaczenia: Alpine wywoła ten listener, zanim zacznie przetwarzać drzewo.
 */

(function () {
  "use strict";

  function plural(value, one, few, many) {
    if (value === 1) return one;
    const rest = value % 10;
    const teens = value % 100;
    if (rest >= 2 && rest <= 4 && (teens < 12 || teens > 14)) return few;
    return many;
  }

  function formatRemaining(milliseconds) {
    if (milliseconds <= 0) return "Termin minął.";
    const total = Math.floor(milliseconds / 1000);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const parts = [];
    if (days > 0) parts.push(days + " " + plural(days, "dzień", "dni", "dni"));
    parts.push(hours + " godz.");
    parts.push(minutes + " min.");
    parts.push(seconds + " s");
    return "Pozostało: " + parts.join(" ");
  }

  document.addEventListener("alpine:init", function () {
    /* Odliczanie do deadline'u. Punktem odniesienia jest czas SERWERA podany w data-server-now:
     * przesunięty zegar przeglądarki nie może pokazać uczestnikowi fałszywego zapasu czasu.
     * Sam deadline i tak egzekwuje serwer w transakcji uploadu. */
    window.Alpine.data("countdown", function () {
      return {
        label: "",
        init: function () {
          const el = this.$el;
          const deadline = Date.parse(el.dataset.deadline || "");
          const serverNow = Date.parse(el.dataset.serverNow || "");
          if (isNaN(deadline)) {
            this.label = "";
            return;
          }
          const skew = isNaN(serverNow) ? 0 : Date.now() - serverNow;
          const tick = () => {
            this.label = formatRemaining(deadline - (Date.now() - skew));
          };
          tick();
          this.timer = setInterval(tick, 1000);
        },
        destroy: function () {
          if (this.timer) clearInterval(this.timer);
        },
      };
    });

    /* Potwierdzenie wysłania nieodwracalnego formularza (wystawienie oceny, decyzja komisji). */
    window.Alpine.data("confirmSubmit", function () {
      return {
        check: function (event) {
          const form = event.target;
          const question = (form && form.dataset && form.dataset.confirm) || "Na pewno?";
          if (!window.confirm(question)) {
            event.preventDefault();
          }
        },
      };
    });
  });

  /* HTMX: błąd sieci nie może zostawić użytkownika bez informacji. */
  document.addEventListener("htmx:responseError", function (event) {
    const status = event.detail && event.detail.xhr ? event.detail.xhr.status : "?";
    const target = document.getElementById("draft-status");
    if (target) {
      target.textContent = "Nie udało się zapisać (HTTP " + status + ").";
    }
  });
})();
