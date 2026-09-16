/* Pierwsza strona wysłanego PDF-a narysowana w panelu uczestnika.
 *
 * Ten sam sposób ładowania biblioteki, co w ``static/js/review-annotations.js``: pinowana wersja
 * pdf.js z cdnjs, dynamiczny ``import()`` z modułu, który sam ma nonce. ``'strict-dynamic'``
 * w CSP przenosi zaufanie z modułu na jego importy, więc polityka nie potrzebuje ani jednego
 * wyjątku (patrz docstring ``apps/web/middleware.py``).
 *
 * Różnice wobec panelu recenzenta – i powody:
 *
 * - kart zadań jest na stronie kilka, więc szukamy **wszystkich** kontenerów, a nie jednego
 *   elementu po identyfikatorze,
 * - rysujemy wyłącznie stronę 1. Uczestnik ma rozpoznać własny plik, a nie go czytać; komplet
 *   stron jest pod przyciskiem „Pobierz”, gdzie był zawsze,
 * - podgląd jest ozdobą, nie warunkiem: każdy błąd (brak CDN-u, uszkodzony plik, wyłączony
 *   JavaScript) kończy się zdaniem w miejscu obrazka, a praca i tak jest już przyjęta,
 * - karta zadania wraca po uploadzie przez HTMX, czyli podmienia się bez przeładowania strony.
 *   Dlatego poza startem nasłuchujemy ``htmx:afterSwap`` i rysujemy to, co dopiero przyszło.
 *
 * Do DOM trafiają wyłącznie węzły tekstowe (``textContent``) i piksele na ``<canvas>`` – nigdzie
 * nie ma ``innerHTML`` ani budowania HTML-a ze stringów.
 */

const PDFJS_VERSION = "4.10.38";
const PDFJS_BASE = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/" + PDFJS_VERSION;

/* Szerokość rysowania. Karta zadania ma około 46rem, a podgląd zajmuje jej część – 520 px daje
   czytelną stronę tytułową i nie zmusza przeglądarki do rasteryzowania arkusza w pełnej skali. */
const TARGET_WIDTH = 520;

/* Atrybut-znacznik „ten kontener już obsłużyliśmy”. Bez niego ``htmx:afterSwap`` po drugim
   uploadzie rysowałby po raz drugi karty, które się nie zmieniły. */
const DONE_ATTRIBUTE = "data-preview-done";

let pdfjsPromise = null;

function loadPdfjs() {
  if (pdfjsPromise === null) {
    pdfjsPromise = import(PDFJS_BASE + "/pdf.min.mjs").then(function (pdfjs) {
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_BASE + "/pdf.worker.min.mjs";
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

function setStatus(root, text) {
  const status = root.querySelector("[data-preview-status]");
  if (status) status.textContent = text;
}

async function renderFirstPage(root) {
  const canvas = root.querySelector("[data-preview-canvas]");
  const url = root.dataset.pdfUrl;
  if (!canvas || !url) return;
  setStatus(root, "Wczytywanie podglądu…");
  try {
    const pdfjs = await loadPdfjs();
    /* ``withCredentials``: pobranie pliku idzie przez widok aplikacji, który sprawdza sesję –
       bez ciasteczka dostalibyśmy przekierowanie na stronę logowania zamiast dokumentu. */
    const document_ = await pdfjs.getDocument({ url: url, withCredentials: true }).promise;
    const page = await document_.getPage(1);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: TARGET_WIDTH / base.width });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
    setStatus(root, "Strona 1 z " + document_.numPages + ".");
  } catch (error) {
    setStatus(root, "Nie udało się narysować podglądu. Plik jest wysłany – pobierz go, żeby sprawdzić.");
  }
}

function scan(scope) {
  const containers = (scope || document).querySelectorAll("[data-upload-preview]");
  containers.forEach(function (root) {
    if (root.hasAttribute(DONE_ATTRIBUTE)) return;
    root.setAttribute(DONE_ATTRIBUTE, "1");
    renderFirstPage(root);
  });
}

scan(document);
document.body.addEventListener("htmx:afterSwap", function (event) {
  scan(event.target);
});
