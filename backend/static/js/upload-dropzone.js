/* Przeciąganie pliku rozwiązania na kartę zadania (panel uczestnika).
 *
 * Czego ten skrypt **nie** robi: nie wysyła formularza i nie decyduje o przyjęciu pliku. Wysyłkę
 * uruchamia dopiero przycisk, po zaznaczeniu potwierdzenia – upuszczenie pliku nie może zastąpić
 * odpowiedzi na pytanie „czy to na pewno to zadanie”. O przyjęciu pliku rozstrzyga serwer
 * (``apps.submissions.validators.validate_upload`` patrzy na treść, nie na rozszerzenie);
 * sprawdzenie robione tutaj jest **uprzejmością**: mówi o pomyłce od razu, zamiast po przesłaniu
 * dwudziestu megabajtów pod górę.
 *
 * Strona jest kompletna bez tego skryptu. Pole ``input[type=file]`` zostaje widoczne i dostępne
 * z klawiatury, a przeglądarka sama przyjmuje plik upuszczony **wprost na pole** – to zachowanie
 * natywne, starsze od tej funkcji. Skrypt rozszerza cel upuszczenia na całą ramkę karty i pokazuje,
 * co jest wybrane.
 *
 * Słowa komunikatów przychodzą w atrybutach ``data-msg-*``, a nie są wpisane w tym pliku:
 * przeglądarka nie ma katalogu tłumaczeń, więc w angielskim interfejsie skrypt napisałby
 * „Wybrano:” zamiast „Selected:”. Ta sama droga, co ``data-word-*`` w ``participant.js``.
 *
 * Karta zadania wraca po uploadzie przez HTMX, czyli podmienia się bez przeładowania strony –
 * dlatego poza startem nasłuchujemy ``htmx:afterSwap`` i uzbrajamy to, co dopiero przyszło.
 *
 * Do DOM trafiają wyłącznie węzły tekstowe (``textContent``) i klasy; nigdzie nie ma ``innerHTML``
 * ani budowania HTML-a ze stringów.
 */

(function () {
  "use strict";

  /* Znacznik „tę strefę już obsłużyliśmy”. Bez niego ``htmx:afterSwap`` po drugim uploadzie
     podpinałby drugi komplet nasłuchiwaczy do kart, które się nie zmieniły. */
  var READY_ATTRIBUTE = "data-dropzone-ready";
  var DRAG_CLASS = "is-dragover";
  var ERROR_CLASS = "dropzone__status--error";
  var MEGABYTE = 1024 * 1024;

  /* Czy da się podłożyć plik do pola. ``input.files`` jest tylko do odczytu – jedyną drogą jest
     lista z ``DataTransfer``, a tej konstruktor nie ma w starszych przeglądarkach (i w części
     z nich rzuca wyjątkiem zamiast być nieobecnym, stąd ``try``). Bez niej nie uzbrajamy niczego:
     strefa zostaje samą ramką z podpowiedzią, a upuszczenie pliku wprost na pole dalej działa,
     bo nie odbieramy przeglądarce jej własnej obsługi. */
  function canAssignFiles() {
    try {
      var probe = new DataTransfer();
      return Boolean(probe && probe.items && typeof probe.items.add === "function");
    } catch (error) {
      return false;
    }
  }

  function extensionsOf(zone) {
    return (zone.dataset.accept || "")
      .split(",")
      .map(function (item) {
        return item.trim().toLowerCase();
      })
      .filter(Boolean);
  }

  /* Rozmiar w megabajtach, bo w megabajtach podany jest limit zadania – uczestnik ma porównać
     dwie liczby, a nie przeliczać bajty. Separator dziesiętny bierze przeglądarka z języka
     dokumentu: po polsku przecinek („1,2 MB”), po angielsku kropka. Poniżej megabajta jedna cyfra
     po przecinku robiłaby z każdego zdjęcia „0,0 MB”, więc tam pokazujemy dwie. */
  function formatSize(bytes) {
    var megabytes = bytes / MEGABYTE;
    var digits = megabytes < 1 ? 2 : 1;
    var language = document.documentElement.lang || undefined;
    return megabytes.toLocaleString(language, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function setStatus(zone, text, isError) {
    var status = zone.querySelector("[data-dropzone-status]");
    if (!status) return;
    status.textContent = text;
    status.classList.toggle(ERROR_CLASS, Boolean(isError));
  }

  function describe(zone, file) {
    return (zone.dataset.msgSelected || "")
      .replace("{name}", file.name)
      .replace("{size}", formatSize(file.size));
  }

  /* Powód odmowy albo pusty tekst. Zwracamy **komunikat**, a nie kod, bo wszystkie trzy zdania
     są już złożone przez serwer w aktywnym języku i nie ma tu czego rozstrzygać drugi raz.
     Pusta lista rozszerzeń (zadanie bez formatów) nie blokuje nic: to stan konfiguracji,
     a nie decyzja o pliku. */
  function reasonToRefuse(zone, file) {
    var allowed = extensionsOf(zone);
    var name = file.name.toLowerCase();
    var fits =
      allowed.length === 0 ||
      allowed.some(function (extension) {
        return name.slice(-extension.length) === extension;
      });
    if (!fits) return zone.dataset.msgFormat || "";
    var limit = parseInt(zone.dataset.maxMb, 10);
    if (limit > 0 && file.size > limit * MEGABYTE) return zone.dataset.msgSize || "";
    return "";
  }

  /* Stan po wyborze pliku – jedno miejsce dla obu dróg (okno wyboru i upuszczenie), żeby
     uczestnik widział to samo zdanie niezależnie od tego, jak plik trafił do pola.
     Plik wybrany z dysku zostaje w polu także wtedy, gdy nam się nie podoba: to jego decyzja,
     a my tylko uprzedzamy, że serwer go odrzuci. Upuszczony plik, który nie przeszedł, do pola
     w ogóle nie trafia – tam żadnego wcześniejszego wyboru nie ma po co kasować. */
  function refreshFromInput(zone, input) {
    var file = input.files && input.files[0];
    if (!file) {
      setStatus(zone, "", false);
      return;
    }
    var refusal = reasonToRefuse(zone, file);
    setStatus(zone, refusal || describe(zone, file), Boolean(refusal));
  }

  function assign(input, file) {
    var transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    /* Podłożenie pliku z kodu nie wywołuje ``change`` samo z siebie. Zdarzenie zamyka różnicę
       między upuszczeniem a wybraniem z dysku: nasz własny nasłuchiwacz wypisuje po nim stan,
       a każdy inny kod podpięty pod to pole (dziś podgląd wysyłki go nie rusza, jutro może)
       zobaczy zmianę tak samo jak po kliknięciu w oknie wyboru. */
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function wire(zone) {
    var input = zone.querySelector('input[type="file"]');
    if (!input) return;
    /* Licznik zagnieżdżeń, a nie zwykła flaga: ``dragleave`` leci także przy przejściu kursora
       między dziećmi strefy (etykieta → pole → przycisk), więc pojedyncze zdarzenie gasiłoby
       podświetlenie w środku przeciągania. */
    var depth = 0;

    input.addEventListener("change", function () {
      refreshFromInput(zone, input);
    });

    zone.addEventListener("dragenter", function (event) {
      event.preventDefault();
      depth += 1;
      zone.classList.add(DRAG_CLASS);
    });

    zone.addEventListener("dragover", function (event) {
      /* Bez ``preventDefault`` na ``dragover`` przeglądarka w ogóle nie wyśle ``drop``. */
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    });

    zone.addEventListener("dragleave", function () {
      depth -= 1;
      if (depth <= 0) {
        depth = 0;
        zone.classList.remove(DRAG_CLASS);
      }
    });

    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      depth = 0;
      zone.classList.remove(DRAG_CLASS);
      var files = event.dataTransfer && event.dataTransfer.files;
      var file = files && files[0];
      /* Bierzemy pierwszy plik i nic nie mówimy o reszcie: jedno zadanie to jedna praca,
         a pole jest bez ``multiple``. */
      if (!file) return;
      var refusal = reasonToRefuse(zone, file);
      if (refusal) {
        setStatus(zone, refusal, true);
        return;
      }
      assign(input, file);
    });
  }

  /* Upuszczenie pliku **obok** strefy otwiera go w karcie zamiast strony – czyli wyrzuca
     uczestnika z wypełnionego formularza. Blokujemy to, ale tylko dopóki na stronie stoi choć
     jedna strefa: poza panelem uczestnika nie mamy powodu odbierać przeglądarce jej zachowania. */
  function blockDropsOutsideZones() {
    ["dragover", "drop"].forEach(function (name) {
      document.addEventListener(name, function (event) {
        if (!document.querySelector("[data-dropzone]")) return;
        event.preventDefault();
      });
    });
  }

  function scan(scope) {
    var zones = (scope || document).querySelectorAll("[data-dropzone]");
    Array.prototype.forEach.call(zones, function (zone) {
      if (zone.hasAttribute(READY_ATTRIBUTE)) return;
      zone.setAttribute(READY_ATTRIBUTE, "1");
      wire(zone);
    });
  }

  if (!canAssignFiles()) return;
  blockDropsOutsideZones();
  scan(document);
  document.body.addEventListener("htmx:afterSwap", function (event) {
    scan(event.target);
  });
})();
