// PDF preview: continuous vertical page list rendered lazily with pdf.js.
// Coordinates exchanged with the outside world (inverse search, highlights) are PDF points
// with the origin in the top-left corner of the page, which is what SyncTeX uses.
import './pdfviewer.css';
import { GlobalWorkerOptions, RenderingCancelledException, TextLayer, getDocument } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

GlobalWorkerOptions.workerSrc = workerUrl;

const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 3, 4];
const MIN_SCALE = 0.1;
const MAX_SCALE = 8;
const GAP = 12; // px between pages and around the page column
const MAX_CONCURRENT_RENDERS = 2;
const MAX_RENDERED_PAGES = 24;
// pdf.js renders at 96 dpi for scale 1 ("100%" means real size on screen).
const CSS_UNITS = 96 / 72;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export class PdfViewer {
  constructor(container, { onInverseSearch, onPageChange, onScaleChange } = {}) {
    this.container = container;
    this.callbacks = { onInverseSearch, onPageChange, onScaleChange };

    this.doc = null;
    this.pages = []; // { num, div, canvas, textDiv, linkDiv, width, height (pt), renderedScale, task, textLayer, proxy }
    this.scale = 1;
    this._zoomMode = 'fit-width';
    this._page = 0;
    this._loadToken = 0;
    this._renderQueue = new Set();
    this._activeRenders = 0;
    this._visible = new Set();
    this._destroyed = false;

    container.classList.add('pdfv-root');
    container.replaceChildren();
    this.scrollEl = document.createElement('div');
    this.scrollEl.className = 'pdfv-scroll';
    this.pagesEl = document.createElement('div');
    this.pagesEl.className = 'pdfv-pages';
    this.emptyEl = document.createElement('div');
    this.emptyEl.className = 'pdfv-empty';
    this.scrollEl.append(this.pagesEl);
    container.append(this.scrollEl, this.emptyEl);
    this.clear();

    this._observer = new IntersectionObserver((entries) => this._onIntersect(entries), {
      root: this.scrollEl,
      rootMargin: '50% 0px',
    });

    this._onScroll = () => {
      if (this._scrollRaf) return;
      this._scrollRaf = requestAnimationFrame(() => {
        this._scrollRaf = 0;
        this._updateCurrentPage();
      });
    };
    this.scrollEl.addEventListener('scroll', this._onScroll, { passive: true });

    this._onWheel = (e) => {
      if (!(e.ctrlKey || e.metaKey) || !this.doc) return;
      e.preventDefault();
      const factor = Math.exp(-clamp(e.deltaY, -100, 100) * 0.0035);
      this._applyScale(this.scale * factor, 'custom', { clientX: e.clientX, clientY: e.clientY });
    };
    this.scrollEl.addEventListener('wheel', this._onWheel, { passive: false });

    this._onDblClick = (e) => {
      const pageDiv = e.target.closest?.('.pdfv-page');
      if (!pageDiv || e.target.closest('.pdfv-link')) return;
      const info = this.pages[Number(pageDiv.dataset.page) - 1];
      if (!info) return;
      const rect = pageDiv.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * info.width;
      const y = ((e.clientY - rect.top) / rect.height) * info.height;
      window.getSelection()?.removeAllRanges();
      this.callbacks.onInverseSearch?.(info.num, x, y);
    };
    this.scrollEl.addEventListener('dblclick', this._onDblClick);

    this._resizeObserver = new ResizeObserver(() => {
      if (!this.doc || typeof this._zoomMode === 'number') return;
      cancelAnimationFrame(this._resizeRaf);
      this._resizeRaf = requestAnimationFrame(() => this._applyScale(this._fitScale(this._zoomMode), this._zoomMode));
    });
    this._resizeObserver.observe(this.scrollEl);
  }

  get page() {
    return this._page;
  }
  get numPages() {
    return this.doc ? this.doc.numPages : 0;
  }
  get zoomMode() {
    return this._zoomMode;
  }

  // ---------- loading ----------

  async load(url) {
    const token = ++this._loadToken;
    const anchor = this.doc ? this._captureAnchor(0, null) : null;
    const scrollLeft = this.scrollEl.scrollLeft;

    const task = getDocument({ url });
    let doc;
    try {
      doc = await task.promise;
    } catch (err) {
      task.destroy();
      if (token === this._loadToken) this.clear(`Could not load PDF: ${err?.message || err}`);
      throw err;
    }
    if (token !== this._loadToken || this._destroyed) {
      doc.loadingTask.destroy();
      return;
    }

    // Page sizes (in points, at the page's own rotation).
    const proxies = await Promise.all(Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1)));
    if (token !== this._loadToken || this._destroyed) {
      doc.loadingTask.destroy();
      return;
    }
    const pages = proxies.map((proxy, i) => {
      const vp = proxy.getViewport({ scale: 1 });
      const div = document.createElement('div');
      div.className = 'pdfv-page';
      div.dataset.page = String(i + 1);
      return { num: i + 1, proxy, div, width: vp.width, height: vp.height, renderedScale: 0 };
    });

    // Build the new page list off to the side, render what will be visible, then swap it in
    // so that a recompile replaces the pages without a blank flash.
    const oldDoc = this.doc;
    const oldPages = this.pages;
    for (const p of oldPages) this._cancelRender(p);
    this._observer.disconnect();
    this._renderQueue.clear();
    this._visible.clear();

    this.doc = doc;
    this.pages = pages;
    const newPagesEl = document.createElement('div');
    newPagesEl.className = 'pdfv-pages';
    newPagesEl.append(...pages.map((p) => p.div));

    this.scale = typeof this._zoomMode === 'number' ? this._zoomMode : this._fitScale(this._zoomMode);
    this._layoutPages();

    const targetIdx = anchor ? clamp(anchor.pageIdx, 0, pages.length - 1) : 0;
    const eager = [targetIdx, targetIdx + 1].filter((i) => i < pages.length);
    await Promise.race([
      Promise.all(eager.map((i) => this._renderPage(pages[i], { force: true }).catch(() => {}))),
      new Promise((r) => setTimeout(r, 1500)),
    ]);
    if (token !== this._loadToken || this._destroyed) return;

    this.pagesEl.replaceWith(newPagesEl);
    this.pagesEl = newPagesEl;
    this.emptyEl.hidden = true;
    this.container.classList.remove('pdfv-is-empty');

    if (anchor) {
      this._restoreAnchor({ ...anchor, pageIdx: targetIdx });
      this.scrollEl.scrollLeft = scrollLeft;
    } else {
      this.scrollEl.scrollTop = 0;
      this._centerHorizontally();
    }
    for (const p of pages) this._observer.observe(p.div);

    if (oldDoc) {
      for (const p of oldPages) p.textLayer?.cancel();
      oldDoc.loadingTask.destroy();
    }
    this._page = 0;
    this._updateCurrentPage();
    this.callbacks.onScaleChange?.(this._scaleLabel());
  }

  clear(message = '') {
    this._loadToken++;
    for (const p of this.pages) {
      this._cancelRender(p);
      p.textLayer?.cancel();
    }
    this._observer?.disconnect();
    this._renderQueue.clear();
    this._visible.clear();
    this.doc?.loadingTask.destroy();
    this.doc = null;
    this.pages = [];
    this.pagesEl.replaceChildren();
    this.emptyEl.textContent = message;
    this.emptyEl.hidden = false;
    this.container.classList.add('pdfv-is-empty');
    if (this._page !== 0) {
      this._page = 0;
      this.callbacks.onPageChange?.(0, 0);
    }
  }

  destroy() {
    this.clear();
    this._destroyed = true;
    this._observer.disconnect();
    this._resizeObserver.disconnect();
    cancelAnimationFrame(this._scrollRaf);
    cancelAnimationFrame(this._resizeRaf);
    clearTimeout(this._renderTimer);
    this.scrollEl.removeEventListener('scroll', this._onScroll);
    this.scrollEl.removeEventListener('wheel', this._onWheel);
    this.scrollEl.removeEventListener('dblclick', this._onDblClick);
    this.container.replaceChildren();
    this.container.classList.remove('pdfv-root', 'pdfv-is-empty');
  }

  // ---------- zoom & navigation ----------

  setZoom(mode) {
    if (mode !== 'fit-width' && mode !== 'fit-page') {
      mode = clamp(Number(mode) || 1, MIN_SCALE, MAX_SCALE);
    }
    this._zoomMode = mode;
    if (!this.doc) return;
    this._applyScale(typeof mode === 'number' ? mode : this._fitScale(mode), mode);
  }

  zoomIn() {
    const next = ZOOM_STEPS.find((s) => s > this.scale + 1e-3) ?? MAX_SCALE;
    this.setZoom(Math.min(next, MAX_SCALE));
  }

  zoomOut() {
    const prev = [...ZOOM_STEPS].reverse().find((s) => s < this.scale - 1e-3) ?? MIN_SCALE;
    this.setZoom(Math.max(prev, MIN_SCALE));
  }

  goToPage(n) {
    const p = this.pages[clamp(Math.round(n), 1, this.pages.length) - 1];
    if (!p) return;
    this.scrollEl.scrollTop = p.div.offsetTop - GAP;
  }

  highlight(boxes) {
    if (!this.doc || !boxes?.length) return;
    const first = boxes[0];
    const p = this.pages[first.page - 1];
    if (!p) return;
    const top = p.div.offsetTop + (first.top / p.height) * p.div.offsetHeight;
    this.scrollEl.scrollTop = top - this.scrollEl.clientHeight / 3;
    const left = p.div.offsetLeft + (first.left / p.width) * p.div.offsetWidth;
    const right = left + (first.width / p.width) * p.div.offsetWidth;
    const viewLeft = this.scrollEl.scrollLeft;
    if (left < viewLeft || right > viewLeft + this.scrollEl.clientWidth) {
      this.scrollEl.scrollLeft = left - 24;
    }
    for (const box of boxes) {
      const page = this.pages[box.page - 1];
      if (!page) continue;
      const el = document.createElement('div');
      el.className = 'pdfv-highlight';
      // Percentages keep the overlay correct if the zoom changes while it is visible.
      el.style.left = `${(box.left / page.width) * 100}%`;
      el.style.top = `${(box.top / page.height) * 100}%`;
      el.style.width = `${(box.width / page.width) * 100}%`;
      el.style.height = `${(box.height / page.height) * 100}%`;
      page.div.append(el);
      el.addEventListener('animationend', () => el.remove());
      setTimeout(() => el.remove(), 2500);
    }
  }

  _scaleLabel() {
    return `${Math.round(this.scale * 100)}%`;
  }

  _fitScale(mode) {
    if (!this.pages.length) return 1;
    const availW = Math.max(50, this.scrollEl.clientWidth - 2 * GAP);
    const availH = Math.max(50, this.scrollEl.clientHeight - 2 * GAP);
    const maxW = Math.max(...this.pages.map((p) => p.width)) * CSS_UNITS;
    const widthScale = availW / maxW;
    if (mode === 'fit-page') {
      const first = this.pages[0];
      return clamp(Math.min(widthScale, availH / (first.height * CSS_UNITS)), MIN_SCALE, MAX_SCALE);
    }
    return clamp(widthScale, MIN_SCALE, MAX_SCALE);
  }

  _applyScale(scale, mode, pointer = null) {
    scale = clamp(scale, MIN_SCALE, MAX_SCALE);
    this._zoomMode = mode === 'custom' ? scale : mode;
    if (Math.abs(scale - this.scale) < 1e-4) return;
    const rect = this.scrollEl.getBoundingClientRect();
    const py = pointer ? pointer.clientY - rect.top : 0;
    const px = pointer ? pointer.clientX - rect.left : null;
    const anchor = this._captureAnchor(py, px);
    this.scale = scale;
    this._layoutPages();
    this._restoreAnchor(anchor);
    if (!pointer) this._centerHorizontally();
    this.callbacks.onScaleChange?.(this._scaleLabel());
    // Old canvases stretch with CSS until the debounced re-render arrives.
    clearTimeout(this._renderTimer);
    this._renderTimer = setTimeout(() => this._scheduleRenders(), 120);
  }

  _layoutPages() {
    const s = this.scale * CSS_UNITS;
    for (const p of this.pages) {
      p.div.style.width = `${Math.floor(p.width * s)}px`;
      p.div.style.height = `${Math.floor(p.height * s)}px`;
      // Variables consumed by pdf.js' text layer CSS.
      p.div.style.setProperty('--scale-factor', String(s));
      p.div.style.setProperty('--total-scale-factor', String(s));
    }
  }

  _centerHorizontally() {
    const el = this.scrollEl;
    el.scrollLeft = Math.max(0, (el.scrollWidth - el.clientWidth) / 2);
  }

  /** Remembers which page point sits at viewport offset (px, py). */
  _captureAnchor(py, px) {
    const y = this.scrollEl.scrollTop + py;
    const idx = this._pageIndexAt(y);
    const p = this.pages[idx];
    if (!p) return { pageIdx: 0, fy: 0, py: 0 };
    const fy = (y - p.div.offsetTop) / p.div.offsetHeight;
    const fx = px === null ? null : (this.scrollEl.scrollLeft + px - p.div.offsetLeft) / p.div.offsetWidth;
    return { pageIdx: idx, fy, py, fx, px };
  }

  _restoreAnchor({ pageIdx, fy, py, fx, px }) {
    const p = this.pages[pageIdx];
    if (!p) return;
    this.scrollEl.scrollTop = p.div.offsetTop + fy * p.div.offsetHeight - py;
    if (fx !== null && fx !== undefined && px !== null) {
      this.scrollEl.scrollLeft = p.div.offsetLeft + fx * p.div.offsetWidth - px;
    }
  }

  _pageIndexAt(y) {
    let lo = 0;
    let hi = this.pages.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.pages[mid].div.offsetTop - GAP / 2 <= y) lo = mid;
      else hi = mid - 1;
    }
    return Math.max(0, lo);
  }

  _updateCurrentPage() {
    if (!this.pages.length) return;
    const idx = this._pageIndexAt(this.scrollEl.scrollTop + this.scrollEl.clientHeight / 3);
    const page = idx + 1;
    if (page !== this._page) {
      this._page = page;
      this.callbacks.onPageChange?.(page, this.pages.length);
    }
  }

  // ---------- rendering ----------

  _onIntersect(entries) {
    for (const e of entries) {
      const num = Number(e.target.dataset.page);
      if (e.isIntersecting) this._visible.add(num);
      else this._visible.delete(num);
    }
    this._scheduleRenders();
  }

  _scheduleRenders() {
    if (!this.doc) return;
    const wanted = new Set();
    for (const n of this._visible) {
      for (const m of [n - 1, n, n + 1]) if (m >= 1 && m <= this.pages.length) wanted.add(m);
    }
    for (const n of wanted) {
      const p = this.pages[n - 1];
      if (p.renderedScale !== this.scale || p.renderedDpr !== window.devicePixelRatio) this._renderQueue.add(n);
    }
    // Drop queued pages that scrolled far away.
    for (const n of this._renderQueue) if (!wanted.has(n)) this._renderQueue.delete(n);
    this._pump();
  }

  _pump() {
    while (this._activeRenders < MAX_CONCURRENT_RENDERS && this._renderQueue.size) {
      const current = this._page || 1;
      const next = [...this._renderQueue].sort((a, b) => Math.abs(a - current) - Math.abs(b - current))[0];
      this._renderQueue.delete(next);
      const p = this.pages[next - 1];
      if (!p) continue;
      this._activeRenders++;
      this._renderPage(p)
        .catch((err) => {
          if (!(err instanceof RenderingCancelledException)) console.error('[pdfviewer] render failed', err);
        })
        .finally(() => {
          this._activeRenders--;
          this._releaseFarPages();
          this._pump();
        });
    }
  }

  _cancelRender(p) {
    if (p.task) {
      p.task.cancel();
      p.task = null;
    }
  }

  async _renderPage(p, { force = false } = {}) {
    const scale = this.scale;
    const dpr = window.devicePixelRatio || 1;
    if (!force && p.renderedScale === scale && p.renderedDpr === dpr) return;
    this._cancelRender(p);

    const viewport = p.proxy.getViewport({ scale: scale * CSS_UNITS });
    const canvas = document.createElement('canvas');
    canvas.className = 'pdfv-canvas';
    // Cap the backing store (very large zoom on big pages) to keep memory sane.
    const maxPixels = 16_777_216;
    let outputScale = dpr;
    if (viewport.width * viewport.height * dpr * dpr > maxPixels) {
      outputScale = Math.sqrt(maxPixels / (viewport.width * viewport.height));
    }
    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    const ctx = canvas.getContext('2d', { alpha: false });

    const task = p.proxy.render({
      canvasContext: ctx,
      viewport,
      transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
    });
    p.task = task;
    try {
      await task.promise;
    } finally {
      if (p.task === task) p.task = null;
    }
    if (!this.pages.includes(p)) return; // document replaced meanwhile

    if (p.canvas) p.canvas.replaceWith(canvas);
    else p.div.prepend(canvas);
    if (p.canvas) {
      p.canvas.width = 0; // free the old bitmap promptly
      p.canvas.height = 0;
    }
    p.canvas = canvas;
    p.renderedScale = scale;
    p.renderedDpr = dpr;
    p.div.classList.add('pdfv-rendered');

    if (!p.textDiv) this._renderTextLayer(p).catch(() => {});
    if (!p.linkDiv) this._renderLinks(p).catch(() => {});
  }

  async _renderTextLayer(p) {
    const textDiv = document.createElement('div');
    textDiv.className = 'pdfv-text';
    p.textDiv = textDiv;
    p.div.append(textDiv);
    // The text layer sizes itself from --total-scale-factor, so it follows zoom changes without re-rendering.
    const textLayer = new TextLayer({
      textContentSource: p.proxy.streamTextContent({ includeMarkedContent: true, disableNormalization: true }),
      container: textDiv,
      viewport: p.proxy.getViewport({ scale: 1 }),
    });
    p.textLayer = textLayer;
    await textLayer.render();
  }

  async _renderLinks(p) {
    const linkDiv = document.createElement('div');
    linkDiv.className = 'pdfv-links';
    p.linkDiv = linkDiv;
    p.div.append(linkDiv);
    const annotations = await p.proxy.getAnnotations({ intent: 'display' });
    const vp = p.proxy.getViewport({ scale: 1 });
    for (const a of annotations) {
      if (a.subtype !== 'Link' || !a.rect) continue;
      const [x1, y1] = vp.convertToViewportPoint(a.rect[0], a.rect[1]);
      const [x2, y2] = vp.convertToViewportPoint(a.rect[2], a.rect[3]);
      const el = document.createElement('a');
      el.className = 'pdfv-link';
      el.style.left = `${(Math.min(x1, x2) / vp.width) * 100}%`;
      el.style.top = `${(Math.min(y1, y2) / vp.height) * 100}%`;
      el.style.width = `${(Math.abs(x2 - x1) / vp.width) * 100}%`;
      el.style.height = `${(Math.abs(y2 - y1) / vp.height) * 100}%`;
      if (a.url && /^(https?|mailto):/i.test(a.url)) {
        el.href = a.url;
        el.target = '_blank';
        el.rel = 'noopener noreferrer';
        el.title = a.url;
      } else if (a.dest) {
        el.href = '#';
        el.addEventListener('click', (e) => {
          e.preventDefault();
          this._goToDest(a.dest).catch(() => {});
        });
      } else {
        continue;
      }
      linkDiv.append(el);
    }
  }

  async _goToDest(dest) {
    const doc = this.doc;
    const explicit = typeof dest === 'string' ? await doc.getDestination(dest) : dest;
    if (!Array.isArray(explicit) || doc !== this.doc) return;
    const ref = explicit[0];
    const pageIdx = typeof ref === 'object' && ref !== null ? await doc.getPageIndex(ref) : Number(ref);
    const p = this.pages[pageIdx];
    if (!p) return;
    let yPt = 0;
    if (explicit[1]?.name === 'XYZ' && explicit[3] != null) {
      const vp = p.proxy.getViewport({ scale: 1 });
      yPt = vp.convertToViewportPoint(explicit[2] ?? 0, explicit[3])[1];
    } else if ((explicit[1]?.name === 'FitH' || explicit[1]?.name === 'FitBH') && explicit[2] != null) {
      const vp = p.proxy.getViewport({ scale: 1 });
      yPt = vp.convertToViewportPoint(0, explicit[2])[1];
    }
    this.scrollEl.scrollTop = p.div.offsetTop + (clamp(yPt, 0, p.height) / p.height) * p.div.offsetHeight - GAP;
  }

  /** Frees canvases of pages far from the current one when too many are rendered. */
  _releaseFarPages() {
    const rendered = this.pages.filter((p) => p.canvas);
    if (rendered.length <= MAX_RENDERED_PAGES) return;
    const current = this._page || 1;
    rendered.sort((a, b) => Math.abs(b.num - current) - Math.abs(a.num - current));
    for (const p of rendered.slice(0, rendered.length - MAX_RENDERED_PAGES)) {
      if (this._visible.has(p.num)) continue;
      p.canvas.width = 0;
      p.canvas.height = 0;
      p.canvas.remove();
      p.canvas = null;
      p.renderedScale = 0;
      p.div.classList.remove('pdfv-rendered');
    }
  }
}
