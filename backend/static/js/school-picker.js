/* Wyszukiwarka szkół w formularzu rejestracji – czysty JavaScript, bez żadnej biblioteki.
 *
 * Dlaczego nie Alpine: komponent chodził wcześniej na buildzie ``@alpinejs/csp`` z cdn.jsdelivr.net.
 * Uczestnik zgłosił, że „wpisał szkołę z listy”, nie dostał ani jednej podpowiedzi, wypełnił
 * widoczne pole „Nazwa szkoły” i usłyszał od serwera, żeby wybrał szkołę z listy. Wystarczyło, że
 * firmowe proxy albo wtyczka blokująca zewnętrzne CDN-y nie wpuściły jednego skryptu – a razem
 * z nim przestawał działać cały blok. Ten plik jest self-hostowany i nie zależy od niczego poza
 * przeglądarką, więc jedynym sposobem, by go zabrakło, jest wyłączenie JavaScriptu w ogóle.
 *
 * Krok „Miejscowość” (uwaga organizatora z 16.09: „okno wyboru szkoły po wpisaniu «wrocław» nie
 * pokazuje liceów ogólnokształcących, a wpisanie «liceum» nie pokazuje odpowiedniej listy; może
 * warto dodać pole miasta i wówczas dać pełną listę szkół”). Obie obserwacje mają jedną przyczynę:
 * dwadzieścia trafień z całej Polski, posortowanych alfabetycznie, nie są listą, z której da się
 * wybrać swoją szkołę. Po wskazaniu miejscowości wyszukiwarka pyta **tylko** o nią, pusty tekst
 * znaczy „pokaż wszystkie”, a lista doczytuje się przewijaniem (serwer oddaje ją stronami).
 * Krok jest nieobowiązkowy: kto zna nazwę swojej szkoły, pisze ją tak jak dotąd.
 *
 * Umowa z serwerem (apps/web/forms.py::SchoolChoiceMixin + templates/web/_school_picker.html) –
 * atrybuty ``data-*``, a nie ``x-ref``/``x-on``, bo nie ma już komponentu, w którym nazwa metody
 * musiałaby się z czymkolwiek zgadzać:
 * - ``[data-school-picker]``        – korzeń bloku (na stronie może stać więcej niż jeden),
 * - ``[data-picker="city"]``        – pole „Miejscowość”,
 * - ``[data-picker="city-list"]``   – ``<ul role="listbox">`` na podpowiedzi miejscowości,
 * - ``[data-picker="city-status"]`` – komunikat aria-live kroku „Miejscowość”,
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
    this.city = ref(root, "city");
    this.cityList = ref(root, "city-list");
    this.cityStatus = ref(root, "city-status");
    this.query = ref(root, "query");
    this.schoolId = ref(root, "school-id");
    this.custom = ref(root, "custom");
    this.free = ref(root, "free");
    this.freeInput = ref(root, "free-input");
    this.list = ref(root, "list");
    this.status = ref(root, "status");
    this.searchUrl = root.dataset.searchUrl || "/api/schools/";
    this.citiesUrl = root.dataset.citiesUrl || "/api/schools/cities/";
    var districtId = root.dataset.districtField || "";
    this.district = districtId ? document.getElementById(districtId) : null;
    /* Indeks podświetlonej podpowiedzi; -1 = żadna. */
    this.active = -1;
    this.items = [];
    this.timer = null;
    /* Numer ostatnio wysłanego żądania. Odpowiedzi bywają wyprzedzane przez kolejne (sieć nie
     * gwarantuje kolejności), a lista podpowiedzi do „krak” nie może przykryć listy do „krakow”. */
    this.sequence = 0;
    /* Wybrana miejscowość – pusta znaczy „szukaj w całym kraju”, czyli zachowanie sprzed kroku
     * „Miejscowość”. Wartość początkowa bierze się z pola: formularz wraca z błędem z wpisaną
     * miejscowością, a edycja profilu otwiera się z miastem szkoły z rejestru. */
    this.cityValue = this.city ? this.city.value.trim() : "";
    /* Stan doczytywania pełnej listy szkół miasta (``offset``/``has_more`` z serwera). */
    this.offset = 0;
    this.hasMore = false;
    this.loading = false;
    this.cityItems = [];
    this.cityActive = -1;
    this.cityTimer = null;
    this.citySequence = 0;
  }

  /** Czy blok da się w ogóle obsłużyć – brak którejkolwiek kontrolki znaczy „zostaw wariant bez JS”. */
  SchoolPicker.prototype.isComplete = function () {
    return !!(this.query && this.schoolId && this.custom && this.free && this.list);
  };

  /** Czy krok „Miejscowość” jest na stronie. Bez niego blok działa jak przed jego wprowadzeniem. */
  SchoolPicker.prototype.hasCityStep = function () {
    return !!(this.city && this.cityList);
  };

  SchoolPicker.prototype.init = function () {
    var self = this;
    if (this.hasCityStep()) this.initCity();
    this.query.addEventListener("input", function () {
      self.onInput();
    });
    /* Doczytywanie pełnej listy szkół miasta przy przewijaniu. Warunek „ostatnie 60 px” jest
     * hojny z rozmysłem: na telefonie inercja przewijania potrafi dobić do końca listy szybciej,
     * niż zdąży wrócić odpowiedź, a pusty koniec listy czyta się jak „to już wszystko”. */
    this.list.addEventListener("scroll", function () {
      if (self.list.scrollTop + self.list.clientHeight >= self.list.scrollHeight - 60) {
        self.loadMore();
      }
    });
    this.query.addEventListener("keydown", function (event) {
      self.onKeydown(event);
    });
    this.query.addEventListener("focus", function () {
      if (self.items.length > 0) {
        self.open();
        return;
      }
      /* Z wybraną miejscowością samo wejście w pole pokazuje pełną listę szkół tego miasta –
       * bez tego uczestnik, który nie wie, jak jego szkoła nazywa się w wykazie, dalej musiałby
       * zgadywać pierwsze słowo. Bez miejscowości nic się nie dzieje: lista „wszystkich szkół
       * w Polsce” nie jest listą. */
      if (self.cityValue) self.search();
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
      if (this.hasCityStep()) this.closeCity();
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
    /* Miejscowość jest zakresem wyszukiwarki, a nie daną o szkole: skoro wyszukiwarka jest
     * wyłączona, to pole też – zostawione czynne obiecywałoby, że coś jeszcze zawęża. */
    if (this.city) this.city.disabled = custom;
  };

  /* --- wyszukiwanie ----------------------------------------------------------------------- */

  SchoolPicker.prototype.onInput = function () {
    /* Ręczna zmiana tekstu unieważnia wcześniejszy wybór: inaczej dopisanie litery do nazwy
     * zostawiłoby w ukrytym polu szkołę, której w polu widocznym już nie ma. */
    this.schoolId.value = "";
    if (this.timer) window.clearTimeout(this.timer);
    var term = this.query.value.trim();
    /* Próg długości obowiązuje **tylko** bez wybranej miejscowości. Z miastem zbiór jest z góry
     * ograniczony do kilkudziesięciu wierszy, więc pusty tekst jest sensownym pytaniem („pokaż
     * wszystkie”), a nie prośbą o przejrzenie całego wykazu. Ten sam podział ma serwer
     * (apps/schools/api.py::search_schools). */
    if (!this.cityValue && term.length < MIN_QUERY_LENGTH) {
      this.items = [];
      this.close();
      return;
    }
    var self = this;
    this.timer = window.setTimeout(function () {
      self.search();
    }, DEBOUNCE_MS);
  };

  /** Parametry zapytania o szkoły dla bieżącego stanu bloku. */
  SchoolPicker.prototype.searchParams = function (offset) {
    var params = new URLSearchParams();
    params.set("q", this.query.value.trim());
    params.set("limit", String(MAX_SUGGESTIONS));
    if (offset) params.set("offset", String(offset));
    if (this.cityValue) {
      params.set("city", this.cityValue);
    } else {
      /* Województwo zawęża **zamiast** miejscowości, a nie obok niej: miasto jest węższe, a dwa
       * filtry naraz dałyby pustą listę komuś, kto wybrał miasto z innego województwa niż
       * zaznaczone w formularzu (przeprowadzka, szkoła przy granicy województw). */
      var district = this.district ? this.district.value : "";
      if (district) params.set("voivodeship", district);
    }
    return params;
  };

  /** Pobiera podpowiedzi. ``append`` dokłada kolejną stronę zamiast podmieniać listę. */
  SchoolPicker.prototype.fetchSchools = function (offset, append) {
    var ticket = ++this.sequence;
    var self = this;
    this.loading = true;
    window
      .fetch(this.searchUrl + "?" + this.searchParams(offset).toString(), {
        headers: { Accept: "application/json" },
      })
      .then(function (response) {
        return response.ok ? response.json() : { results: [], has_more: false };
      })
      .catch(function () {
        return { results: [], has_more: false };
      })
      .then(function (data) {
        self.loading = false;
        if (ticket !== self.sequence) return;
        var rows = data && Array.isArray(data.results) ? data.results : [];
        self.items = append ? self.items.concat(rows) : rows;
        self.offset = self.items.length;
        self.hasMore = !!(data && data.has_more);
        self.render(append);
      });
  };

  SchoolPicker.prototype.search = function () {
    if (!this.cityValue && this.query.value.trim().length < MIN_QUERY_LENGTH) return;
    this.fetchSchools(0, false);
  };

  /** Kolejna strona pełnej listy – wołane przewijaniem listy podpowiedzi. */
  SchoolPicker.prototype.loadMore = function () {
    if (!this.hasMore || this.loading || this.list.hidden) return;
    this.fetchSchools(this.offset, true);
  };

  /* --- lista podpowiedzi -------------------------------------------------------------------- */

  /** Buduje jedną pozycję listy. ``textContent``, więc nazwa z API nigdy nie jest HTML-em. */
  SchoolPicker.prototype.itemNode = function (school, index) {
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
    return item;
  };

  SchoolPicker.prototype.render = function (append) {
    var self = this;
    var list = this.list;
    var from = 0;
    if (append) {
      /* Doczytana strona dokłada się na koniec: przebudowa całej listy zgubiłaby pozycję
       * przewinięcia, czyli odesłała czytającego na początek dokładnie w chwili, w której
       * dostał to, o co poprosił przewijaniem. */
      from = list.children.length;
    } else {
      list.textContent = "";
      /* Nowy wynik zaczyna się od pierwszej pozycji – przeglądarka potrafi zachować przewinięcie
       * po podmianie zawartości i lista otwierałaby się „w środku”. */
      list.scrollTop = 0;
      this.active = -1;
    }
    if (this.items.length === 0) {
      this.close();
      this.say(
        this.cityValue
          ? "Brak szkół pasujących do wpisanego tekstu w tej miejscowości. Wyczyść tekst, żeby zobaczyć wszystkie."
          : "Brak szkół pasujących do wpisanego tekstu. Możesz zaznaczyć „Mojej szkoły nie ma na liście”."
      );
      return;
    }
    for (var index = from; index < this.items.length; index += 1) {
      list.appendChild(this.itemNode(this.items[index], index));
    }
    this.open();
    this.say(
      self.items.length +
        " " +
        plural(self.items.length, "podpowiedź", "podpowiedzi", "podpowiedzi") +
        (self.hasMore ? " (lista dłuższa – przewiń, żeby doczytać)" : "") +
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

  /* --- krok „Miejscowość” -------------------------------------------------------------------
   *
   * Osobny, bardzo podobny zestaw metod zamiast jednego uogólnionego „comboboxa” na dwa pola.
   * Podobieństwo jest powierzchowne: obie listy mają inny zasób, inny próg, inną reakcję na wybór
   * (miejscowość **przestawia zakres** drugiego pola, szkoła kończy wybór) i inne komunikaty.
   * Wspólna abstrakcja kosztowałaby tyle parametrów, ile jest różnic – a plik ma być czytelny dla
   * kogoś, kto przyjdzie do niego po roku z jednym zgłoszeniem z produkcji.
   */

  SchoolPicker.prototype.initCity = function () {
    var self = this;
    this.city.addEventListener("input", function () {
      self.onCityInput();
    });
    this.city.addEventListener("keydown", function (event) {
      self.onCityKeydown(event);
    });
    this.city.addEventListener("focus", function () {
      if (self.cityItems.length > 0) self.openCity();
    });
    this.city.addEventListener("blur", function () {
      window.setTimeout(function () {
        self.closeCity();
      }, BLUR_DELAY_MS);
    });
    this.cityList.addEventListener("mousedown", function (event) {
      var target = event.target;
      var item = target && target.closest ? target.closest("[data-index]") : null;
      if (!item) return;
      event.preventDefault();
      self.chooseCity(parseInt(item.dataset.index, 10));
    });
  };

  SchoolPicker.prototype.onCityInput = function () {
    /* Ręczna zmiana tekstu unieważnia wybraną miejscowość – dokładnie tak, jak w polu szkoły
     * unieważnia wybraną szkołę. Inaczej dopisanie litery do „Wrocław” zostawiłoby wyszukiwarkę
     * szkół zawężoną do miasta, którego w polu już nie ma. */
    this.cityValue = "";
    if (this.cityTimer) window.clearTimeout(this.cityTimer);
    var term = this.city.value.trim();
    if (term.length < MIN_QUERY_LENGTH) {
      this.cityItems = [];
      this.closeCity();
      return;
    }
    var self = this;
    this.cityTimer = window.setTimeout(function () {
      self.searchCities();
    }, DEBOUNCE_MS);
  };

  SchoolPicker.prototype.searchCities = function () {
    var term = this.city.value.trim();
    if (term.length < MIN_QUERY_LENGTH) return;
    var params = new URLSearchParams();
    params.set("q", term);
    params.set("limit", String(MAX_SUGGESTIONS));
    var district = this.district ? this.district.value : "";
    if (district) params.set("voivodeship", district);
    var ticket = ++this.citySequence;
    var self = this;
    window
      .fetch(this.citiesUrl + "?" + params.toString(), { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.ok ? response.json() : { results: [] };
      })
      .catch(function () {
        return { results: [] };
      })
      .then(function (data) {
        if (ticket !== self.citySequence) return;
        self.cityItems = data && Array.isArray(data.results) ? data.results : [];
        self.renderCities();
      });
  };

  SchoolPicker.prototype.renderCities = function () {
    var list = this.cityList;
    list.textContent = "";
    list.scrollTop = 0;
    this.cityActive = -1;
    if (this.cityItems.length === 0) {
      this.closeCity();
      /* Komunikat wskazuje wyjście, a nie tylko stwierdza brak: wykaz SIO zapisuje miejscowości
       * nierówno (największe miasta bywają w nim rozbite na dzielnice, a Warszawa figuruje pod
       * samymi nazwami dzielnic), więc „nie ma takiej miejscowości” bywa nieprawdą o szkole.
       * Krok jest nieobowiązkowy i pole „Szkoła” działa bez niego – trzeba to powiedzieć. */
      this.sayCity(
        "Brak miejscowości zaczynających się od wpisanego tekstu. Możesz pominąć ten krok " +
          "i wpisać nazwę szkoły w polu niżej."
      );
      return;
    }
    this.cityItems.forEach(function (row, index) {
      var item = document.createElement("li");
      item.className = "school-picker__item";
      item.id = "city-suggestion-" + index;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");
      item.dataset.index = String(index);
      var name = document.createElement("span");
      name.className = "school-picker__name";
      name.textContent = row.city;
      var meta = document.createElement("span");
      meta.className = "school-picker__meta";
      /* Województwo przy nazwie, bo nazwy miast się powtarzają („Brzeg” jest w dwóch). */
      meta.textContent = row.voivodeship_label || row.voivodeship;
      item.appendChild(name);
      item.appendChild(meta);
      list.appendChild(item);
    });
    this.openCity();
    this.sayCity(
      this.cityItems.length +
        " " +
        plural(this.cityItems.length, "podpowiedź", "podpowiedzi", "podpowiedzi") +
        ". Wybierz strzałkami i zatwierdź Enterem."
    );
  };

  SchoolPicker.prototype.chooseCity = function (index) {
    var row = this.cityItems[index];
    if (!row) return;
    this.cityValue = row.city;
    this.city.value = row.city;
    this.closeCity();
    /* Zmiana miejscowości zaczyna wybór szkoły od nowa: poprzednia szkoła była z innego miasta,
     * a zostawiona w ukrytym polu pojechałaby na serwer mimo zmienionego zakresu. */
    this.schoolId.value = "";
    this.query.value = "";
    this.items = [];
    this.offset = 0;
    this.hasMore = false;
    this.sayCity("Wybrano miejscowość: " + row.city + ". Pole „Szkoła” pokazuje szkoły z tej miejscowości.");
    if (!this.custom.checked) {
      /* Przeniesienie fokusu na pole szkoły samo wywołuje obsługę „wejście w puste pole
       * z wybraną miejscowością”, czyli pobranie pełnej listy. Warunek niżej jest zabezpieczeniem
       * na wypadek, gdyby pole było już aktywne (wtedy zdarzenie ``focus`` nie przychodzi) –
       * bez niego wybór miasta nie pokazałby niczego, a z bezwarunkowym ``search()`` wysyłałby
       * to samo zapytanie dwa razy. */
      this.query.focus();
      if (!this.loading && this.items.length === 0) this.search();
    }
  };

  SchoolPicker.prototype.onCityKeydown = function (event) {
    if (event.key === "Escape") {
      this.closeCity();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (this.cityItems.length === 0) return;
      event.preventDefault();
      this.openCity();
      var step = event.key === "ArrowDown" ? 1 : -1;
      var count = this.cityItems.length;
      if (this.cityActive < 0) {
        this.cityActive = step > 0 ? 0 : count - 1;
      } else {
        this.cityActive = (this.cityActive + step + count) % count;
      }
      this.highlightCity();
      return;
    }
    if (event.key === "Enter" && this.cityActive >= 0 && !this.cityList.hidden) {
      /* Enter na podświetlonej podpowiedzi wybiera miejscowość, a **nie** wysyła formularza. */
      event.preventDefault();
      this.chooseCity(this.cityActive);
    }
  };

  SchoolPicker.prototype.highlightCity = function () {
    var nodes = this.cityList.children;
    for (var index = 0; index < nodes.length; index += 1) {
      var selected = index === this.cityActive;
      nodes[index].classList.toggle("school-picker__item--active", selected);
      nodes[index].setAttribute("aria-selected", selected ? "true" : "false");
    }
    if (this.cityActive >= 0 && nodes[this.cityActive]) {
      this.city.setAttribute("aria-activedescendant", nodes[this.cityActive].id);
      if (nodes[this.cityActive].scrollIntoView) {
        nodes[this.cityActive].scrollIntoView({ block: "nearest" });
      }
    } else {
      this.city.removeAttribute("aria-activedescendant");
    }
  };

  SchoolPicker.prototype.openCity = function () {
    this.cityList.hidden = false;
    this.city.setAttribute("aria-expanded", "true");
  };

  SchoolPicker.prototype.closeCity = function () {
    this.cityList.hidden = true;
    this.city.setAttribute("aria-expanded", "false");
    this.city.removeAttribute("aria-activedescendant");
    this.cityActive = -1;
    this.highlightCity();
  };

  SchoolPicker.prototype.sayCity = function (message) {
    if (this.cityStatus) this.cityStatus.textContent = message;
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
