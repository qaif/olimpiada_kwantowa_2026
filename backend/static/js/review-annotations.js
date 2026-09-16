/* Wyspa JS panelu recenzenta: podgląd pracy i warstwa adnotacji jako prostokąty.
 *
 * Dwa rodzaje podglądu, jedna warstwa adnotacji. ``data-preview-kind`` mówi, czy plik jest
 * dokumentem (pdf.js rysuje stronę na ``<canvas>``), czy zdjęciem rozwiązania (JPEG – zwykły
 * ``<img>``). Adnotacje nie zależą od tego wyboru: prostokąty zapisujemy we współrzędnych
 * ułamkowych względem widocznego obszaru, a zdjęcie jest po prostu „stroną 1 z 1”. Dzięki temu
 * recenzent zaznacza fragment kartki sfotografowanej telefonem tak samo, jak fragment PDF-u,
 * a serwer (``validate_annotations``) nie musi wiedzieć, co było źródłem.
 *
 * Zasady bezpieczeństwa tego pliku:
 * - treść adnotacji (tekst od recenzenta) trafia do DOM **wyłącznie** przez ``textContent``.
 *   Nigdzie nie ma ``innerHTML``, ``insertAdjacentHTML`` ani budowania HTML-a ze stringów,
 * - dane wejściowe przychodzą atrybutami ``data-*`` (autoescapowanymi przez Django) i są
 *   parsowane przez ``JSON.parse`` – nie ma bloku <script> z danymi,
 * - zapis idzie na ``PATCH /api/grading/reviews/{id}/`` z ciasteczkiem sesji i tokenem CSRF
 *   pobranym z ukrytego pola formularza; serwer i tak waliduje kształt adnotacji
 *   (``apps.grading.services.validate_annotations``), więc klient nie jest tu autorytetem,
 * - biblioteka pochodzi z pinowanej wersji na cdnjs (ten sam host, co w CSP ``script-src``).
 */

const PDFJS_VERSION = "4.10.38";
const PDFJS_BASE = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/" + PDFJS_VERSION;

const root = document.getElementById("pdf-viewer");

function setStatus(text) {
  const status = root.querySelector("[data-pdf-status]");
  if (status) status.textContent = text;
}

function readAnnotations() {
  try {
    const parsed = JSON.parse(root.dataset.annotations || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function csrfToken() {
  const field = document.querySelector("input[name=csrfmiddlewaretoken]");
  return field ? field.value : "";
}

function start() {
  const previewKind = root.dataset.previewKind === "image" ? "image" : "pdf";
  const canvas = root.querySelector("[data-pdf-canvas]");
  const previewImage = root.querySelector("[data-preview-image]");
  const layer = root.querySelector("[data-pdf-layer]");
  const pageInfo = root.querySelector("[data-pdf-pageinfo]");
  const hiddenInput = document.getElementById(root.dataset.annotationsInput);
  const list = document.getElementById(root.dataset.annotationsList);

  let annotations = readAnnotations();
  let pdfDocument = null;
  let pageNumber = 1;
  let viewport = null;

  /* Stan paska nad podglądem. Trzy przełączniki odpowiadają na trzy pytania zadawane przy
   * ocenianiu: czy zaznaczanie myszą ma być teraz aktywne (recenzent chce czasem tylko czytać),
   * czy prostokąty mają zasłaniać stronę i co z tych adnotacji zobaczy uczestnik. Stan jest
   * wyłącznie w przeglądarce – nic z tego nie jedzie na serwer, bo nie jest treścią oceny. */
  let annotatingEnabled = true;
  let annotationsVisible = true;
  let filterMode = "all";

  function matchesFilter(item) {
    if (filterMode === "public") return Boolean(item.public);
    if (filterMode === "internal") return !item.public;
    return true;
  }

  /* Lista adnotacji budowana wyłącznie z węzłów tekstowych – żadnego HTML-a ze stringa. */
  function renderList() {
    if (!list) return;
    list.textContent = "";
    const shown = annotations.filter(matchesFilter);
    if (shown.length === 0) {
      const empty = document.createElement("li");
      empty.className = "hint";
      empty.textContent =
        annotations.length === 0 ? "Brak adnotacji." : "Żadna adnotacja nie pasuje do filtru.";
      list.appendChild(empty);
      return;
    }
    shown.forEach(function (item) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent =
        "str. " + item.page + (item.public ? " (publiczna)" : "") + ": " + (item.text || "");
      /* „Przejdź” przewija podgląd na stronę z adnotacją. Przy ośmiostronicowym rozwiązaniu sama
         informacja „str. 6” nie skraca szukania – trzeba ją jeszcze na tej stronie znaleźć. */
      const goTo = document.createElement("button");
      goTo.type = "button";
      /* Klasa, bo arkusz maluje przyciski w liście adnotacji na kolor „usuwania” – a „przejdź”
         nie kasuje niczego i nie może tak wyglądać (static/css/reviewer.css). */
      goTo.className = "annotations__goto";
      goTo.textContent = "przejdź";
      goTo.addEventListener("click", function () {
        const page = Number(item.page) || 1;
        if (pdfDocument && page !== pageNumber && page >= 1 && page <= pdfDocument.numPages) {
          pageNumber = page;
          renderPage();
        }
        const stage = root.querySelector("[data-pdf-stage]");
        if (stage) stage.scrollIntoView({ block: "center" });
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "usuń";
      remove.addEventListener("click", function () {
        /* Indeks liczony w chwili kliknięcia, a nie przy budowaniu listy: przy włączonym filtrze
           pozycja na ekranie i pozycja w tablicy to dwie różne liczby. */
        const index = annotations.indexOf(item);
        if (index === -1) return;
        annotations.splice(index, 1);
        syncField();
        renderList();
        renderRects();
      });
      li.appendChild(label);
      li.appendChild(document.createTextNode(" "));
      li.appendChild(goTo);
      li.appendChild(document.createTextNode(" "));
      li.appendChild(remove);
      list.appendChild(li);
    });
  }

  function syncField() {
    if (hiddenInput) hiddenInput.value = JSON.stringify(annotations);
  }

  function renderRects() {
    layer.textContent = "";
    if (!viewport || !annotationsVisible) return;
    annotations
      .filter(function (item) {
        return Number(item.page) === pageNumber && matchesFilter(item);
      })
      .forEach(function (item) {
        const rect = item.rect || [0, 0, 0, 0];
        const box = document.createElement("div");
        box.className = "rect";
        box.title = item.text || "";
        box.style.left = rect[0] * viewport.width + "px";
        box.style.top = rect[1] * viewport.height + "px";
        box.style.width = rect[2] * viewport.width + "px";
        box.style.height = rect[3] * viewport.height + "px";
        layer.appendChild(box);
      });
  }

  /* Zdjęcie skalujemy tą samą regułą, co stronę PDF-u (do 1.5×, nie szerzej niż kolumna), a potem
   * przypinamy warstwę adnotacji do jego rzeczywistych wymiarów. Bez jawnych ``width``/``height``
   * obrazek zmieniałby rozmiar razem z oknem i prostokąty rozjeżdżałyby się z tym, co zaznaczono. */
  function renderImage() {
    return new Promise(function (resolve, reject) {
      previewImage.addEventListener(
        "load",
        function () {
          const natural = previewImage.naturalWidth || 1;
          const scale = Math.min(1.5, (root.clientWidth || 800) / natural);
          const factor = scale > 0 ? scale : 1;
          previewImage.width = Math.floor(natural * factor);
          previewImage.height = Math.floor((previewImage.naturalHeight || 1) * factor);
          viewport = { width: previewImage.width, height: previewImage.height };
          layer.style.width = previewImage.width + "px";
          layer.style.height = previewImage.height + "px";
          if (pageInfo) pageInfo.textContent = "Zdjęcie rozwiązania";
          renderRects();
          resolve();
        },
        { once: true }
      );
      previewImage.addEventListener(
        "error",
        function () {
          reject(new Error("Nie udało się wczytać zdjęcia."));
        },
        { once: true }
      );
      /* Adres wskazuje na endpoint pobrania, który przekierowuje na presigned URL. Ciasteczko
       * sesji leci z żądaniem obrazka automatycznie – nie ma tu czego ustawiać. */
      previewImage.src = root.dataset.pdfUrl;
    });
  }

  async function renderPage() {
    const page = await pdfDocument.getPage(pageNumber);
    const scale = Math.min(1.5, (root.clientWidth || 800) / page.getViewport({ scale: 1 }).width);
    viewport = page.getViewport({ scale: scale > 0 ? scale : 1 });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    layer.style.width = canvas.width + "px";
    layer.style.height = canvas.height + "px";
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
    if (pageInfo) pageInfo.textContent = "Strona " + pageNumber + " z " + pdfDocument.numPages;
    renderRects();
  }

  /* Zaznaczenie prostokąta myszą. Współrzędne zapisujemy jako ułamki wymiaru strony,
   * więc adnotacja nie zależy od skali podglądu ani od rozdzielczości ekranu. */
  function attachSelection() {
    let startX = null;
    let startY = null;
    layer.addEventListener("mousedown", function (event) {
      const bounds = layer.getBoundingClientRect();
      startX = event.clientX - bounds.left;
      startY = event.clientY - bounds.top;
    });
    layer.addEventListener("mouseup", function (event) {
      if (startX === null) return;
      if (!annotatingEnabled) {
        startX = null;
        startY = null;
        return;
      }
      const bounds = layer.getBoundingClientRect();
      const endX = event.clientX - bounds.left;
      const endY = event.clientY - bounds.top;
      const x = Math.min(startX, endX);
      const y = Math.min(startY, endY);
      const width = Math.abs(endX - startX);
      const height = Math.abs(endY - startY);
      startX = null;
      startY = null;
      if (width < 6 || height < 6) return;
      const text = window.prompt("Treść adnotacji:", "");
      if (text === null) return;
      annotations.push({
        page: pageNumber,
        rect: [x / bounds.width, y / bounds.height, width / bounds.width, height / bounds.height],
        text: text,
        public: window.confirm("Pokazać tę adnotację uczestnikowi po ogłoszeniu wyników?"),
      });
      syncField();
      renderList();
      renderRects();
    });
  }

  async function save() {
    setStatus("Zapisywanie…");
    try {
      const response = await fetch(root.dataset.reviewUrl, {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          Accept: "application/json",
        },
        body: JSON.stringify({ annotations: annotations }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(function () {
          return {};
        });
        setStatus("Nie zapisano: " + (payload.detail || "HTTP " + response.status));
        return;
      }
      const saved = await response.json();
      annotations = Array.isArray(saved.annotations) ? saved.annotations : annotations;
      syncField();
      renderList();
      renderRects();
      setStatus("Adnotacje zapisane.");
    } catch (error) {
      setStatus("Nie zapisano: brak połączenia.");
    }
  }

  /* Przyciski stron istnieją tylko przy dokumencie – przy zdjęciu szablon ich nie renderuje. */
  const prevButton = root.querySelector("[data-pdf-prev]");
  const nextButton = root.querySelector("[data-pdf-next]");
  if (prevButton) {
    prevButton.addEventListener("click", function () {
      if (pdfDocument && pageNumber > 1) {
        pageNumber -= 1;
        renderPage();
      }
    });
  }
  if (nextButton) {
    nextButton.addEventListener("click", function () {
      if (pdfDocument && pageNumber < pdfDocument.numPages) {
        pageNumber += 1;
        renderPage();
      }
    });
  }
  root.querySelector("[data-pdf-save]").addEventListener("click", save);

  /* Pasek nad podglądem. Szablon rysuje go w tym samym ``{% if file_available %}``, co podgląd,
   * więc jeżeli któregoś z przycisków nie ma, po prostu go tu nie wiążemy – nic nie zakładamy
   * o kształcie HTML-u poza nazwami atrybutów ``data-*``. */
  const modeButton = document.querySelector("[data-annotate-mode]");
  const toggleButton = document.querySelector("[data-annotations-toggle]");
  const filterSelect = document.querySelector("[data-annotations-filter]");

  if (modeButton) {
    modeButton.addEventListener("click", function () {
      annotatingEnabled = !annotatingEnabled;
      modeButton.setAttribute("aria-pressed", annotatingEnabled ? "true" : "false");
      modeButton.textContent = annotatingEnabled ? "Dodaj zaznaczenie" : "Zaznaczanie wyłączone";
      layer.classList.toggle("is-idle", !annotatingEnabled);
    });
  }

  if (toggleButton) {
    toggleButton.addEventListener("click", function () {
      annotationsVisible = !annotationsVisible;
      toggleButton.setAttribute("aria-pressed", annotationsVisible ? "true" : "false");
      toggleButton.textContent = annotationsVisible ? "Ukryj adnotacje" : "Pokaż adnotacje";
      renderRects();
    });
  }

  if (filterSelect) {
    filterSelect.addEventListener("change", function () {
      filterMode = filterSelect.value || "all";
      renderList();
      renderRects();
    });
  }

  syncField();
  renderList();
  attachSelection();

  (async function load() {
    setStatus("Wczytywanie pliku…");
    try {
      if (previewKind === "image") {
        await renderImage();
        setStatus("");
        return;
      }
      const pdfjs = await import(PDFJS_BASE + "/pdf.min.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_BASE + "/pdf.worker.min.mjs";
      pdfDocument = await pdfjs.getDocument({ url: root.dataset.pdfUrl, withCredentials: true }).promise;
      await renderPage();
      setStatus("");
    } catch (error) {
      /* Podgląd jest wygodą, nie warunkiem oceny: przy braku CDN-u albo pliku recenzent nadal
       * ma link „Pobierz plik” i pełny formularz oceny. */
      setStatus("Podgląd pliku jest niedostępny – pobierz go, aby otworzyć.");
    }
  })();
}

if (root) {
  start();
}
