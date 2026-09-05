/* Wyspa JS panelu recenzenta: podgląd PDF (pdf.js) i warstwa adnotacji jako prostokąty.
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
  const canvas = root.querySelector("[data-pdf-canvas]");
  const layer = root.querySelector("[data-pdf-layer]");
  const pageInfo = root.querySelector("[data-pdf-pageinfo]");
  const hiddenInput = document.getElementById(root.dataset.annotationsInput);
  const list = document.getElementById(root.dataset.annotationsList);

  let annotations = readAnnotations();
  let pdfDocument = null;
  let pageNumber = 1;
  let viewport = null;

  /* Lista adnotacji budowana wyłącznie z węzłów tekstowych – żadnego HTML-a ze stringa. */
  function renderList() {
    if (!list) return;
    list.textContent = "";
    if (annotations.length === 0) {
      const empty = document.createElement("li");
      empty.className = "hint";
      empty.textContent = "Brak adnotacji.";
      list.appendChild(empty);
      return;
    }
    annotations.forEach(function (item, index) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent =
        "str. " + item.page + (item.public ? " (publiczna)" : "") + ": " + (item.text || "");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "usuń";
      remove.addEventListener("click", function () {
        annotations.splice(index, 1);
        syncField();
        renderList();
        renderRects();
      });
      li.appendChild(label);
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
    if (!viewport) return;
    annotations
      .filter(function (item) {
        return Number(item.page) === pageNumber;
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

  root.querySelector("[data-pdf-prev]").addEventListener("click", function () {
    if (pdfDocument && pageNumber > 1) {
      pageNumber -= 1;
      renderPage();
    }
  });
  root.querySelector("[data-pdf-next]").addEventListener("click", function () {
    if (pdfDocument && pageNumber < pdfDocument.numPages) {
      pageNumber += 1;
      renderPage();
    }
  });
  root.querySelector("[data-pdf-save]").addEventListener("click", save);

  syncField();
  renderList();
  attachSelection();

  (async function load() {
    setStatus("Wczytywanie pliku…");
    try {
      const pdfjs = await import(PDFJS_BASE + "/pdf.min.mjs");
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_BASE + "/pdf.worker.min.mjs";
      pdfDocument = await pdfjs.getDocument({ url: root.dataset.pdfUrl, withCredentials: true }).promise;
      await renderPage();
      setStatus("");
    } catch (error) {
      /* Podgląd jest wygodą, nie warunkiem oceny: przy braku CDN-u albo pliku recenzent nadal
       * ma link „Pobierz plik” i pełny formularz oceny. */
      setStatus("Podgląd PDF jest niedostępny – pobierz plik, aby go otworzyć.");
    }
  })();
}

if (root) {
  start();
}
