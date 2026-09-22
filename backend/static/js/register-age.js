/* Zgoda opiekuna odsłaniana datą urodzenia – rejestracja uczestnika (`/register/`, dokończenie
 * rejestracji przez Google/Facebooka).
 *
 * Zgłoszenie organizatora: „rok urodzenia nie jest powiązany z obowiązkowością oświadczenia
 * o niepełnoletniości”. Powiązanie istniało od początku, ale **wyłącznie po stronie serwera**
 * (`apps/web/forms.py::ConsentFieldsMixin.clean`): osoba pełnoletnia widziała w bloku zgód
 * oświadczenie, które jej nie dotyczy, a osoba niepełnoletnia dowiadywała się o obowiązku
 * dopiero z odmowy po wysłaniu formularza. Ten plik przenosi tę samą regułę przed wysyłkę.
 *
 * Od wydania 0.30.0 regułą jest **pełna data**, a nie rocznik: pełnoletni jest ten, kto skończył
 * już 18 lat. Do tej zmiany osoba kończąca 18 lat w tym roku widziała zgodę opiekuna przez cały
 * rok, także dzień po swoich urodzinach.
 *
 * Czego ten skrypt **nie** robi: nie rozstrzyga. Rozstrzyga serwer i tylko serwer – tu chodzi
 * o to, żeby formularz nie milczał, a nie o to, żeby zastąpić walidację. Trzy decyzje wynikają
 * wprost z tego podziału:
 *
 * - **reguła i dzisiejsza data przychodzą z serwera** (atrybuty `data-minor-max-age`
 *   i `data-current-date` na `<fieldset class="consents">`, patrz
 *   apps/web/context_processors.py). Własna kopia progu rozjechałaby się z
 *   `apps.accounts.consents.MINOR_MAX_AGE` przy pierwszej zmianie, a `new Date()` czyta zegar
 *   użytkownika – przestawiony albo po prostu chodzący w innej strefie niż Europe/Warsaw, czyli
 *   pokazujący w części doby inny **dzień**. W sam dzień osiemnastych urodzin to jest różnica
 *   między pokazaniem zgody a jej schowaniem,
 * - **pusta i niepoprawna data znaczy „niepełnoletni”**, dokładnie jak `consents.is_minor`:
 *   przy nieznanym wieku pokazujemy zgodę, a nie chowamy. Zawyżenie kosztuje jeden zbędny
 *   checkbox, zaniżenie – zgodę pobraną od dziecka bez wiedzy opiekuna,
 * - **wiersz z błędem serwera zostaje widoczny** bez względu na datę. Schowanie pola,
 *   przy którym stoi „popraw to”, zostawia człowieka z odmową bez wskazanego miejsca.
 *
 * Bez tego pliku (wyłączony JavaScript, zablokowany zasób) formularz działa jak wcześniej:
 * wiersz zgody opiekuna jest widoczny zawsze, a wymagalność rozstrzyga serwer.
 *
 * Umowa z szablonem (templates/web/_participant_form_fields.html):
 * - `[data-age-consents]`            – `<fieldset>` z zgodami; nosi regułę w `data-*`,
 * - `[data-age="minor-consent"]`     – wiersz zgody wymaganej tylko od niepełnoletnich,
 * - `[data-age="birth-date"]`        – pole daty urodzenia (apps/web/forms.py::birth_date_field).
 */

(function () {
  "use strict";

  /** Data „RRRR-MM-DD” jako trójka liczb, albo `null`, gdy to nie jest data.
   *
   * Świadomie **bez** `new Date(value)`: konstruktor przyjmuje niepełne zapisy, przelicza je na
   * strefę czasową przeglądarki i potrafi oddać dzień sąsiedni. Tutaj chodzi o porównanie dwóch
   * dat kalendarzowych, w którym strefa czasowa nie występuje ani razu.
   */
  function parseDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value || "").trim());
    if (!match) return null;
    var year = parseInt(match[1], 10);
    var month = parseInt(match[2], 10);
    var day = parseInt(match[3], 10);
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    return [year, month, day];
  }

  /** Porównanie kalendarzowe: ujemne, gdy `a` jest wcześniejsze niż `b`. */
  function compare(a, b) {
    for (var index = 0; index < 3; index += 1) {
      if (a[index] !== b[index]) return a[index] - b[index];
    }
    return 0;
  }

  /** Dzień osiemnastych urodzin – ta sama arytmetyka, co `consents.adulthood_date`.
   *
   * 29 lutego w roku nieprzestępnym staje się 1 marca: dnia urodzin po prostu nie ma, a dzień
   * później zamiast dnia wcześniej jest wyborem na korzyść zgody opiekuna.
   */
  function adulthoodDate(birth, maxAge) {
    var year = birth[0] + maxAge;
    if (birth[1] === 2 && birth[2] === 29 && !isLeapYear(year)) return [year, 3, 1];
    return [year, birth[1], birth[2]];
  }

  function isLeapYear(year) {
    return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  }

  /** Pole daty: najpierw po identyfikatorze z serwera, potem po atrybucie w obrębie formularza.
   *
   * Dwie drogi, bo blok zgód i pole daty stoją w **różnych** miejscach formularza: identyfikator
   * jest tym, co szablon zna na pewno (`form.birth_date.auto_id`), a szukanie po atrybucie ratuje
   * sytuację, w której formularz dostanie prefiks i identyfikator się zmieni.
   */
  function findBirthDate(root) {
    var id = root.dataset.birthDateField || "";
    var byId = id ? document.getElementById(id) : null;
    if (byId) return byId;
    var form = root.closest ? root.closest("form") : null;
    return (form || document).querySelector('[data-age="birth-date"]');
  }

  function AgeConsents(root) {
    this.root = root;
    this.birthDate = findBirthDate(root);
    this.items = root.querySelectorAll('[data-age="minor-consent"]');
    this.maxAge = parseInt(root.dataset.minorMaxAge, 10);
    this.today = parseDate(root.dataset.currentDate);
  }

  /** Czy da się cokolwiek zrobić. Brak którejkolwiek części = zostaw wariant bez skryptu. */
  AgeConsents.prototype.isComplete = function () {
    return !!(this.birthDate && this.items.length > 0 && !isNaN(this.maxAge) && this.today);
  };

  /** Ta sama reguła, co ``apps.accounts.consents.is_minor`` – łącznie z brakiem daty. */
  AgeConsents.prototype.isMinor = function () {
    var birth = parseDate(this.birthDate.value);
    if (!birth) return true;
    return compare(this.today, adulthoodDate(birth, this.maxAge)) < 0;
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
         * potem poprawiłem datę na pełnoletnią”. */
        if (!show) box.checked = false;
      }
    }
  };

  AgeConsents.prototype.init = function () {
    var self = this;
    /* ``input`` łapie wpisywanie i wklejenie, ``change`` – wybór z kalendarza przeglądarki
     * i uzupełnienie przez menedżera formularzy, które nie zawsze wywołuje ``input``. */
    this.birthDate.addEventListener("input", function () {
      self.sync();
    });
    this.birthDate.addEventListener("change", function () {
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
