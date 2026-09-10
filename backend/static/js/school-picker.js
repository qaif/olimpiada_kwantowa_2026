/* Wyszukiwarka szkół w formularzu rejestracji (komponent Alpine „schoolPicker”).
 *
 * Build ``@alpinejs/csp`` nie ewaluuje wyrażeń w atrybutach: w ``x-on``/``x-bind`` wolno użyć
 * wyłącznie **nazwy metody** komponentu. Dlatego listy podpowiedzi nie buduje ``x-for`` (który
 * wymagałby parsowania wyrażenia), tylko kod tego pliku – węzły powstają przez ``createElement``
 * i ``textContent``, więc nazwa szkoły z API nigdy nie jest interpretowana jako HTML.
 *
 * Umowa z serwerem (apps/web/forms.py + templates/web/_school_picker.html):
 * - ``x-ref="query"``   – widoczne pole „Szkoła”,
 * - ``x-ref="schoolId"``– ukryte pole z identyfikatorem wybranego wiersza słownika,
 * - ``x-ref="custom"``  – checkbox „Mojej szkoły nie ma na liście”,
 * - ``x-ref="free"``    – akapit z polem wolnego tekstu (chowany, dopóki checkbox jest pusty),
 * - ``x-ref="list"``    – ``<ul role="listbox">`` na podpowiedzi,
 * - ``x-ref="status"``  – komunikat dla czytnika ekranu (aria-live).
 *
 * Bez tego pliku formularz nadal działa: pole wolnego tekstu jest wtedy widoczne od początku,
 * a uczestnik rejestruje się drogą „mojej szkoły nie ma na liście”. Skrypt jest wygodą,
 * nie warunkiem – regułę „albo wybór ze słownika, albo jawny wyjątek” egzekwuje serwer.
 */

(function () {
  "use strict";

  /* Próg i opóźnienie odpowiadają serwerowi (apps/schools/api.py: MIN_QUERY_LENGTH).
   * Krótsze zapytanie nie zawęża niczego, a 250 ms to przerwa, po której człowiek przestaje
   * pisać – bez niej każde naciśnięcie klawisza byłoby osobnym żądaniem. */
  const MIN_QUERY_LENGTH = 2;
  const DEBOUNCE_MS = 250;
  const MAX_SUGGESTIONS = 20;

  function plural(value, one, few, many) {
    if (value === 1) return one;
    const rest = value % 10;
    const teens = value % 100;
    if (rest >= 2 && rest <= 4 && (teens < 12 || teens > 14)) return few;
    return many;
  }

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("schoolPicker", function () {
      return {
        /* Indeks podświetlonej podpowiedzi; -1 = żadna. */
        active: -1,
        items: [],
        timer: null,
        /* Numer ostatnio wysłanego żądania. Odpowiedzi bywają wyprzedzane przez kolejne
         * (sieć nie gwarantuje kolejności), a lista podpowiedzi do „krak” nie może przykryć
         * listy do „krakow”. */
        sequence: 0,

        init: function () {
          this.searchUrl = this.$el.dataset.searchUrl || "/api/schools/";
          const districtId = this.$el.dataset.districtField || "";
          this.district = districtId ? document.getElementById(districtId) : null;
          /* Wolny tekst jest w HTML-u widoczny (wariant bez JS). Skrypt chowa go, dopóki
           * uczestnik nie zaznaczy wyjątku – ale nie wtedy, gdy serwer właśnie odesłał
           * formularz z błędem przy tym polu albo z zaznaczonym checkboxem. */
          this.syncCustom();
        },

        /* --- wolny tekst --------------------------------------------------------------- */

        onCustomToggle: function () {
          this.syncCustom();
          if (this.$refs.custom.checked) {
            this.close();
            /* Wybór z listy przestaje obowiązywać: liczy się ostatnia decyzja uczestnika
             * (tę samą regułę ma ``SchoolChoiceMixin.clean`` po stronie serwera). */
            this.$refs.schoolId.value = "";
          }
        },

        syncCustom: function () {
          const custom = this.$refs.custom.checked;
          this.$refs.free.hidden = !custom;
          this.$refs.query.disabled = custom;
        },

        /* --- wyszukiwanie -------------------------------------------------------------- */

        onFocus: function () {
          if (this.items.length > 0) this.open();
        },

        onBlur: function () {
          /* Kliknięcie w podpowiedź jest poprzedzone rozmyciem pola – bez tej zwłoki lista
           * znikałaby, zanim zdarzenie kliknięcia zdążyłoby do niej dotrzeć. */
          window.setTimeout(this.close.bind(this), 150);
        },

        onInput: function () {
          /* Ręczna zmiana tekstu unieważnia wcześniejszy wybór: inaczej dopisanie litery do
           * nazwy zostawiłoby w ukrytym polu szkołę, której w polu widocznym już nie ma. */
          this.$refs.schoolId.value = "";
          if (this.timer) window.clearTimeout(this.timer);
          const term = this.$refs.query.value.trim();
          if (term.length < MIN_QUERY_LENGTH) {
            this.items = [];
            this.close();
            return;
          }
          this.timer = window.setTimeout(this.search.bind(this), DEBOUNCE_MS);
        },

        search: function () {
          const term = this.$refs.query.value.trim();
          if (term.length < MIN_QUERY_LENGTH) return;
          const params = new URLSearchParams();
          params.set("q", term);
          params.set("limit", String(MAX_SUGGESTIONS));
          const district = this.district ? this.district.value : "";
          if (district) params.set("voivodeship", district);
          const ticket = ++this.sequence;
          const self = this;
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
              self.items = Array.isArray(data.results) ? data.results : [];
              self.render();
            });
        },

        /* --- lista podpowiedzi ---------------------------------------------------------- */

        render: function () {
          const list = this.$refs.list;
          list.textContent = "";
          /* Nowy wynik zaczyna się od pierwszej pozycji – przeglądarka potrafi zachować
           * przewinięcie po podmianie zawartości i lista otwierałaby się „w środku”. */
          list.scrollTop = 0;
          this.active = -1;
          if (this.items.length === 0) {
            this.close();
            this.say("Brak szkół pasujących do wpisanego tekstu.");
            return;
          }
          this.items.forEach(function (school, index) {
            const item = document.createElement("li");
            item.className = "school-picker__item";
            item.id = "school-suggestion-" + index;
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", "false");
            item.dataset.index = String(index);
            const name = document.createElement("span");
            name.className = "school-picker__name";
            name.textContent = school.name;
            const meta = document.createElement("span");
            meta.className = "school-picker__meta";
            meta.textContent = school.city + ", " + school.kind_label;
            item.appendChild(name);
            item.appendChild(meta);
            list.appendChild(item);
          });
          /* Jeden nasłuch na liście zamiast jednego na pozycję – i tak przebudowujemy ją przy
           * każdym zapytaniu. ``mousedown``, nie ``click``: kliknięcie przychodzi po rozmyciu
           * pola, czyli po tym, jak lista zdążyłaby zniknąć. */
          list.onmousedown = this.onListMouseDown.bind(this);
          this.open();
          this.say(
            this.items.length +
              " " +
              plural(this.items.length, "podpowiedź", "podpowiedzi", "podpowiedzi") +
              ". Wybierz strzałkami i zatwierdź Enterem."
          );
        },

        onListMouseDown: function (event) {
          const item = event.target.closest ? event.target.closest("[data-index]") : null;
          if (!item) return;
          /* Bez tego przeglądarka przeniosłaby fokus na listę i wywołała ``onBlur``. */
          event.preventDefault();
          this.choose(parseInt(item.dataset.index, 10));
        },

        open: function () {
          this.$refs.list.hidden = false;
          this.$refs.query.setAttribute("aria-expanded", "true");
        },

        close: function () {
          this.$refs.list.hidden = true;
          this.$refs.query.setAttribute("aria-expanded", "false");
          this.$refs.query.removeAttribute("aria-activedescendant");
          this.active = -1;
          this.highlight();
        },

        say: function (message) {
          if (this.$refs.status) this.$refs.status.textContent = message;
        },

        /* --- klawiatura ----------------------------------------------------------------- */

        onKeydown: function (event) {
          if (event.key === "Escape") {
            this.close();
            return;
          }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            if (this.items.length === 0) return;
            event.preventDefault();
            this.open();
            const step = event.key === "ArrowDown" ? 1 : -1;
            const count = this.items.length;
            if (this.active < 0) {
              this.active = step > 0 ? 0 : count - 1;
            } else {
              this.active = (this.active + step + count) % count;
            }
            this.highlight();
            return;
          }
          if (event.key === "Enter" && this.active >= 0 && !this.$refs.list.hidden) {
            /* Enter na podświetlonej podpowiedzi wybiera szkołę, a **nie** wysyła formularza –
             * inaczej pierwsze naciśnięcie Entera kończyłoby rejestrację z pustym school_id. */
            event.preventDefault();
            this.choose(this.active);
          }
        },

        highlight: function () {
          const list = this.$refs.list;
          const nodes = list.children;
          for (let index = 0; index < nodes.length; index += 1) {
            const selected = index === this.active;
            nodes[index].classList.toggle("school-picker__item--active", selected);
            nodes[index].setAttribute("aria-selected", selected ? "true" : "false");
          }
          if (this.active >= 0 && nodes[this.active]) {
            this.$refs.query.setAttribute("aria-activedescendant", nodes[this.active].id);
            if (nodes[this.active].scrollIntoView) {
              nodes[this.active].scrollIntoView({ block: "nearest" });
            }
          } else {
            this.$refs.query.removeAttribute("aria-activedescendant");
          }
        },

        choose: function (index) {
          const school = this.items[index];
          if (!school) return;
          this.$refs.schoolId.value = String(school.id);
          this.$refs.query.value = school.name + ", " + school.city;
          this.close();
          this.say("Wybrano: " + school.name + ", " + school.city + ".");
        },
      };
    });
  });
})();
