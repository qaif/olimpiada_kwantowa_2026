/* Zgoda opiekuna odsłaniana rocznikiem – rejestracja uczestnika (`/register/`, dokończenie
 * rejestracji przez Google/Facebooka).
 *
 * Zgłoszenie organizatora: „rok urodzenia nie jest powiązany z obowiązkowością oświadczenia
 * o niepełnoletniości”. Powiązanie istniało od początku, ale **wyłącznie po stronie serwera**
 * (`apps/web/forms.py::ConsentFieldsMixin.clean`): osoba pełnoletnia widziała w bloku zgód
 * oświadczenie, które jej nie dotyczy, a osoba niepełnoletnia dowiadywała się o obowiązku
 * dopiero z odmowy po wysłaniu formularza. Ten plik przenosi tę samą regułę przed wysyłkę.
 *
 * Czego ten skrypt **nie** robi: nie rozstrzyga. Rozstrzyga serwer i tylko serwer – tu chodzi
 * o to, żeby formularz nie milczał, a nie o to, żeby zastąpić walidację. Trzy decyzje wynikają
 * wprost z tego podziału:
 *
 * - **reguła i rok bieżący przychodzą z serwera** (atrybuty `data-minor-max-age`
 *   i `data-current-year` na `<fieldset class="consents">`, patrz
 *   apps/web/context_processors.py). Własna kopia progu rozjechałaby się z
 *   `apps.accounts.consents.MINOR_MAX_AGE` przy pierwszej zmianie, a `new Date().getFullYear()`
 *   czyta zegar użytkownika – przestawiony albo po prostu w innej strefie niż Europe/Warsaw,
 * - **pusty i niepoprawny rocznik znaczy „niepełnoletni”**, dokładnie jak `consents.is_minor`:
 *   przy nieznanym wieku pokazujemy zgodę, a nie chowamy. Zawyżenie kosztuje jeden zbędny
 *   checkbox, zaniżenie – zgodę pobraną od dziecka bez wiedzy opiekuna,
 * - **wiersz z błędem serwera zostaje widoczny** bez względu na rocznik. Schowanie pola,
 *   przy którym stoi „popraw to”, zostawia człowieka z odmową bez wskazanego miejsca.
 *
 * Bez tego pliku (wyłączony JavaScript, zablokowany zasób) formularz działa jak wcześniej:
 * wiersz zgody opiekuna jest widoczny zawsze, a wymagalność rozstrzyga serwer.
 *
 * Umowa z szablonem (templates/web/_participant_form_fields.html):
 * - `[data-age-consents]`            – `<fieldset>` z zgodami; nosi regułę w `data-*`,
 * - `[data-age="minor-consent"]`     – wiersz zgody wymaganej tylko od niepełnoletnich,
 * - `[data-age="birth-year"]`        – pole rocznika (apps/web/forms.py::birth_year_field).
 */

(function () {
  "use strict";

  /** Pole rocznika: najpierw po identyfikatorze z serwera, potem po atrybucie w obrębie formularza.
   *
   * Dwie drogi, bo blok zgód i pole rocznika stoją w **różnych** miejscach formularza: identyfikator
   * jest tym, co szablon zna na pewno (`form.birth_year.auto_id`), a szukanie po atrybucie ratuje
   * sytuację, w której formularz dostanie prefiks i identyfikator się zmieni.
   */
  function findBirthYear(root) {
    var id = root.dataset.birthYearField || "";
    var byId = id ? document.getElementById(id) : null;
    if (byId) return byId;
    var form = root.closest ? root.closest("form") : null;
    return (form || document).querySelector('[data-age="birth-year"]');
  }

  function AgeConsents(root) {
    this.root = root;
    this.birthYear = findBirthYear(root);
    this.items = root.querySelectorAll('[data-age="minor-consent"]');
    this.maxAge = parseInt(root.dataset.minorMaxAge, 10);
    this.currentYear = parseInt(root.dataset.currentYear, 10);
  }

  /** Czy da się cokolwiek zrobić. Brak którejkolwiek części = zostaw wariant bez skryptu. */
  AgeConsents.prototype.isComplete = function () {
    return !!(
      this.birthYear &&
      this.items.length > 0 &&
      !isNaN(this.maxAge) &&
      !isNaN(this.currentYear)
    );
  };

  /** Ta sama arytmetyka, co ``apps.accounts.consents.is_minor`` – łącznie z brakiem rocznika. */
  AgeConsents.prototype.isMinor = function () {
    var year = parseInt(this.birthYear.value, 10);
    if (isNaN(year) || year <= 0) return true;
    return this.currentYear - year <= this.maxAge;
  };

  AgeConsents.prototype.sync = function () {
    var minor = this.isMinor();
    for (var index = 0; index < this.items.length; index += 1) {
      var item = this.items[index];
      var hasError = !!item.querySelector("ul.errorlist, .errorlist");
      var box = item.querySelector('input[type="checkbox"]');
      var show = minor || hasError;
      item.hidden = !show;
      if (box) {
        /* ``required`` tylko wtedy, gdy wiersz jest widoczny: atrybut na schowanym polu blokuje
         * wysyłkę formularza komunikatem przeglądarki przypiętym do niewidocznej kontrolki,
         * czyli odmową bez miejsca, w które można spojrzeć. */
        box.required = minor;
        /* Zaznaczenie zdjęte razem z ukryciem wiersza. Oświadczenie, którego nie widać, nie może
         * pojechać na serwer jako złożone – a właśnie tak wyglądałby przypadek „zaznaczyłem,
         * potem poprawiłem rocznik na pełnoletni”. */
        if (!show) box.checked = false;
      }
    }
  };

  AgeConsents.prototype.init = function () {
    var self = this;
    /* ``input`` łapie też wklejenie i strzałki pola liczbowego, ``change`` – uzupełnienie
     * przez menedżera formularzy przeglądarki, które nie zawsze wywołuje ``input``. */
    this.birthYear.addEventListener("input", function () {
      self.sync();
    });
    this.birthYear.addEventListener("change", function () {
      self.sync();
    });
    this.sync();
  };

  function start() {
    var roots = document.querySelectorAll("[data-age-consents]");
    Array.prototype.forEach.call(roots, function (root) {
      var block = new AgeConsents(root);
      if (!block.isComplete()) return;
      block.init();
    });
  }

  /* Skrypt jest ``defer``, więc zwykle wykonuje się przed ``DOMContentLoaded`` – warunek ratuje
   * start, gdyby ktoś dołączył go inaczej. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
