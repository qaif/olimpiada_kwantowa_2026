/* Wyszukiwarka szkół w formularzu rejestracji – czysty JavaScript, bez żadnej biblioteki.
 *
 * Dlaczego nie Alpine: komponent chodził wcześniej na buildzie ``@alpinejs/csp`` z cdn.jsdelivr.net.
 * Uczestnik zgłosił, że „wpisał szkołę z listy”, nie dostał ani jednej podpowiedzi, wypełnił
 * widoczne pole „Nazwa szkoły” i usłyszał od serwera, żeby wybrał szkołę z listy. Wystarczyło, że
 * firmowe proxy albo wtyczka blokująca zewnętrzne CDN-y nie wpuściły jednego skryptu – a razem
 * z nim przestawał działać cały blok. Ten plik jest self-hostowany i nie zależy od niczego poza
 * przeglądarką, więc jedynym sposobem, by go zabrakło, jest wyłączenie JavaScriptu w ogóle.
 *
 * Umowa z serwerem (apps/web/forms.py::SchoolChoiceMixin + templates/web/_school_picker.html) –
 * atrybuty ``data-*``, a nie ``x-ref``/``x-on``, bo nie ma już komponentu, w którym nazwa metody
 * musiałaby się z czymkolwiek zgadzać:
 * - ``[data-school-picker]``        – korzeń bloku (na stronie może stać więcej niż jeden),
 * - ``[data-picker="query"]``       – widoczne pole „Szkoła”,
 * - ``[data-picker="school-id"]``   – ukryte pole z identyfikatorem wiersza słownika,
 * - ``[data-picker="custom"]``      – checkbox „Mojej szkoły nie ma na liście”,
 * - ``[data-picker="free"]``        – akapit z polem wolnego tekstu,
 * - ``[data-picker="free-input"]``  – samo pole wolnego tekstu,
 * - ``[data-picker="list"]``        – ``<ul role="listbox">`` na podpowiedzi,
 * - ``[data-picker="status"]``      – komunikat dla czytnika ekranu (aria-live).
 *
 * Węzły podpowiedzi powstają przez ``createElement`` i ``textContent``, więc nazwa szkoły z API
 * nigdy nie jest interpretowana jako HTML.
 *
 * Bez tego pliku formularz nadal działa: pole wolnego tekstu jest wtedy widoczne od początku,
 * a uczestnik wpisuje nazwę szkoły ręcznie. Serwer przyjmuje taki wpis **nawet bez zaznaczonego
 * checkboksa** – wpisany tekst jest jednoznaczną odpowiedzią na pytanie „jaka szkoła”, a odmowa
 * z powodu niezaznaczonej kratki była dokładnie tym, na co skarżył się uczestnik.
 */

(function () {
  "use strict";

  /* Próg i opóźnienie odpowiadają serwerowi (apps/schools/api.py: MIN_QUERY_LENGTH).
   * Krótsze zapytanie nie zawęża niczego, a 250 ms to przerwa, po której człowiek przestaje
   * pisać – bez niej każde naciśnięcie klawisza byłoby osobnym żądaniem. */
  var MIN_QUERY_LENGTH = 2;
  var DEBOUNCE_MS = 250;
  var MAX_SUGGESTIONS = 20;
  /* Kliknięcie w podpowiedź jest poprzedzone rozmyciem pola – bez tej zwłoki lista znikałaby,
   * zanim zdarzenie kliknięcia zdążyłoby do niej dotrzeć. */
  var BLUR_DELAY_MS = 150;

  function plural(value, one, few, many) {
    if (value === 1) return one;
    var rest = value % 10;
    var teens = value % 100;
    if (rest >= 2 && rest <= 4 && (teens < 12 || teens > 14)) return few;
    return many;
  }

  function ref(root, name) {
    return root.querySelector('[data-picker="' + name + '"]');
  }

  function SchoolPicker(root) {
    this.root = root;
    this.query = ref(root, "query");
    this.schoolId = ref(root, "school-id");
    this.custom = ref(root, "custom");
    this.free = ref(root, "free");
    this.freeInput = ref(root, "free-input");
    this.list = ref(root, "list");
    this.status = ref(root, "status");
    this.searchUrl = root.dataset.searchUrl || "/api/schools/";
    var districtId = root.dataset.districtField || "";
    this.district = districtId ? document.getElementById(districtId) : null;
    /* Indeks podświetlonej podpowiedzi; -1 = żadna. */
    this.active = -1;
    this.items = [];
    this.timer = null;
    /* Numer ostatnio wysłanego żądania. Odpowiedzi bywają wyprzedzane przez kolejne (sieć nie
     * gwarantuje kolejności), a lista podpowiedzi do „krak” nie może przykryć listy do „krakow”. */
    this.sequence = 0;
  }

  /** Czy blok da się w ogóle obsłużyć – brak którejkolwiek kontrolki znaczy „zostaw wariant bez JS”. */
  SchoolPicker.prototype.isComplete = function () {
    return !!(this.query && this.schoolId && this.custom && this.free && this.list);
  };

  SchoolPicker.prototype.init = function () {
    var self = this;
    this.query.addEventListener("input", function () {
      self.onInput();
    });
    this.query.addEventListener("keydown", function (event) {
      self.onKeydown(event);
    });
    this.query.addEventListener("focus", function () {
      if (self.items.length > 0) self.open();
    });
    this.query.addEventListener("blur", function () {
      window.setTimeout(function () {
        self.close();
      }, BLUR_DELAY_MS);
    });
    this.custom.addEventListener("change", function () {
      self.onCustomToggle();
    });
    /* Jeden nasłuch na całej liście zamiast jednego na pozycję – i tak przebudowujemy ją przy
     * każdym zapytaniu. ``mousedown``, nie ``click``: kliknięcie przychodzi po rozmyciu pola,
     * czyli po tym, jak lista zdążyłaby zniknąć. */
    this.list.addEventListener("mousedown", function (event) {
      self.onListMouseDown(event);
    });
    this.syncCustom();
  };

  /* --- wolny tekst ------------------------------------------------------------------------- */

  SchoolPicker.prototype.onCustomToggle = function () {
    this.syncCustom();
    if (this.custom.checked) {
      this.close();
      /* Wybór z listy przestaje obowiązywać: liczy się ostatnia decyzja uczestnika
       * (tę samą regułę ma ``SchoolChoiceMixin.clean`` po stronie serwera). */
      this.schoolId.value = "";
      if (this.freeInput) this.freeInput.focus();
    }
  };

  SchoolPicker.prototype.syncCustom = function () {
    var custom = this.custom.checked;
    /* Wolny tekst jest w HTML-u widoczny (wariant bez JS) i skrypt go chowa – ale **nie** wtedy,
     * gdy coś już w nim stoi albo serwer odesłał przy nim błąd. Ukrycie wypełnionego pola
     * zabrałoby uczestnikowi z oczu odpowiedź, którą właśnie wpisał, zostawiając samo „popraw”. */
    var filled = !!(this.freeInput && this.freeInput.value.trim());
    var hasError = !!this.free.querySelector("ul.errorlist, .errorlist");
    this.free.hidden = !(custom || filled || hasError);
    this.query.disabled = custom;
  };

  /* --- wyszukiwanie ----------------------------------------------------------------------- */

  SchoolPicker.prototype.onInput = function () {
    /* Ręczna zmiana tekstu unieważnia wcześniejszy wybór: inaczej dopisanie litery do nazwy
     * zostawiłoby w ukrytym polu szkołę, której w polu widocznym już nie ma. */
    this.schoolId.value = "";
    if (this.timer) window.clearTimeout(this.timer);
    var term = this.query.value.trim();
    if (term.length < MIN_QUERY_LENGTH) {
      this.items = [];
      this.close();
      return;
    }
    var self = this;
    this.timer = window.setTimeout(function () {
      self.search();
    }, DEBOUNCE_MS);
  };

  SchoolPicker.prototype.search = function () {
    var term = this.query.value.trim();
    if (term.length < MIN_QUERY_LENGTH) return;
    var params = new URLSearchParams();
    params.set("q", term);
    params.set("limit", String(MAX_SUGGESTIONS));
    var district = this.district ? this.district.value : "";
    if (district) params.set("voivodeship", district);
    var ticket = ++this.sequence;
    var self = this;
    window
      .fetch(this.searchUrl + "?" + params.toString(), {
        headers: { Accept: "application/json" },
      })
      .then(function (response) {
        return response.ok ? response.json() : { results: [] };
      })
      .catch(function () {
        return { results: [] };
      })
      .then(function (data) {
        if (ticket !== self.sequence) return;
        self.items = data && Array.isArray(data.results) ? data.results : [];
        self.render();
      });
  };

  /* --- lista podpowiedzi -------------------------------------------------------------------- */

  SchoolPicker.prototype.render = function () {
    var list = this.list;
    list.textContent = "";
    /* Nowy wynik zaczyna się od pierwszej pozycji – przeglądarka potrafi zachować przewinięcie
     * po podmianie zawartości i lista otwierałaby się „w środku”. */
    list.scrollTop = 0;
    this.active = -1;
    if (this.items.length === 0) {
      this.close();
      this.say("Brak szkół pasujących do wpisanego tekstu. Możesz zaznaczyć „Mojej szkoły nie ma na liście”.");
      return;
    }
    this.items.forEach(function (school, index) {
      var item = document.createElement("li");
      item.className = "school-picker__item";
      item.id = "school-suggestion-" + index;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");
      item.dataset.index = String(index);
      var name = document.createElement("span");
      name.className = "school-picker__name";
      name.textContent = school.name;
      var meta = document.createElement("span");
      meta.className = "school-picker__meta";
      meta.textContent = school.city + ", " + school.kind_label;
      item.appendChild(name);
      item.appendChild(meta);
      list.appendChild(item);
    });
    this.open();
    this.say(
      this.items.length +
        " " +
        plural(this.items.length, "podpowiedź", "podpowiedzi", "podpowiedzi") +
        ". Wybierz strzałkami i zatwierdź Enterem."
    );
  };

  SchoolPicker.prototype.onListMouseDown = function (event) {
    var target = event.target;
    var item = target && target.closest ? target.closest("[data-index]") : null;
    if (!item) return;
    /* Bez tego przeglądarka przeniosłaby fokus na listę i wywołała obsługę rozmycia pola. */
    event.preventDefault();
    this.choose(parseInt(item.dataset.index, 10));
  };

  SchoolPicker.prototype.open = function () {
    this.list.hidden = false;
    this.query.setAttribute("aria-expanded", "true");
  };

  SchoolPicker.prototype.close = function () {
    this.list.hidden = true;
    this.query.setAttribute("aria-expanded", "false");
    this.query.removeAttribute("aria-activedescendant");
    this.active = -1;
    this.highlight();
  };

  SchoolPicker.prototype.say = function (message) {
    if (this.status) this.status.textContent = message;
  };

  /* --- klawiatura --------------------------------------------------------------------------- */

  SchoolPicker.prototype.onKeydown = function (event) {
    if (event.key === "Escape") {
      this.close();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (this.items.length === 0) return;
      event.preventDefault();
      this.open();
      var step = event.key === "ArrowDown" ? 1 : -1;
      var count = this.items.length;
      if (this.active < 0) {
        this.active = step > 0 ? 0 : count - 1;
      } else {
        this.active = (this.active + step + count) % count;
      }
      this.highlight();
      return;
    }
    if (event.key === "Enter" && this.active >= 0 && !this.list.hidden) {
      /* Enter na podświetlonej podpowiedzi wybiera szkołę, a **nie** wysyła formularza – inaczej
       * pierwsze naciśnięcie Entera kończyłoby rejestrację z pustym school_id. */
      event.preventDefault();
      this.choose(this.active);
    }
  };

  SchoolPicker.prototype.highlight = function () {
    var nodes = this.list.children;
    for (var index = 0; index < nodes.length; index += 1) {
      var selected = index === this.active;
      nodes[index].classList.toggle("school-picker__item--active", selected);
      nodes[index].setAttribute("aria-selected", selected ? "true" : "false");
    }
    if (this.active >= 0 && nodes[this.active]) {
      this.query.setAttribute("aria-activedescendant", nodes[this.active].id);
      if (nodes[this.active].scrollIntoView) {
        nodes[this.active].scrollIntoView({ block: "nearest" });
      }
    } else {
      this.query.removeAttribute("aria-activedescendant");
    }
  };

  SchoolPicker.prototype.choose = function (index) {
    var school = this.items[index];
    if (!school) return;
    this.schoolId.value = String(school.id);
    this.query.value = school.name + ", " + school.city;
    this.close();
    this.say("Wybrano: " + school.name + ", " + school.city + ".");
  };

  /* --- start ------------------------------------------------------------------------------- */

  function start() {
    /* Bloków na stronie bywa kilka (rejestracja, dokończenie rejestracji przez dostawcę, edycja
     * profilu), a każdy ma własny stan – stąd instancja na element, a nie moduł na stronę. */
    var roots = document.querySelectorAll("[data-school-picker]");
    Array.prototype.forEach.call(roots, function (root) {
      var picker = new SchoolPicker(root);
      if (!picker.isComplete()) return;
      picker.init();
    });
  }

  /* Skrypt jest ``defer``, więc zwykle wykonuje się przed ``DOMContentLoaded`` – ale gdyby ktoś
   * dołączył go inaczej (albo przeglądarka zdążyła już wczytać dokument), warunek ratuje start. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
