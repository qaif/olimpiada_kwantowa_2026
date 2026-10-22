// Jedna analiza struktury (latex/structure.js) na wersję dokumentu CodeMirror: korzystają z niej
// szybkie sprawdzanie, podgląd wzorów, pisownia i licznik słów. Dokumenty CodeMirror są niezmienne,
// więc wersja = obiekt Text, a wynik żyje tak długo jak ta wersja.
import { analyzeStructure } from './latex/structure.js';

const cache = new WeakMap();

/** Analiza struktury dla `doc` (Text z CodeMirror), liczona najwyżej raz na wersję. */
export function structureOf(doc) {
  let s = cache.get(doc);
  if (!s) {
    s = analyzeStructure(doc.toString());
    cache.set(doc, s);
  }
  return s;
}
