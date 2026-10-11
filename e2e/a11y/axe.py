"""axe-core w przebiegu Playwrighta: wczytanie z repozytorium, sprawdzenie sumy, uruchomienie.

**Dlaczego wstrzyknięcie nie łamie CSP.** Serwis nie dopuszcza skryptu inline ani obcego
``script-src`` (``config/settings/base.py``) i test ma badać stronę **w tej konfiguracji**, a nie
w rozluźnionej. ``page.evaluate`` wykonuje kod przez protokół DevTools (``Runtime.evaluate``),
którego polityka strony nie obejmuje – tak samo jak konsola deweloperska. Strona nie dostaje więc
ani ``bypass_csp``, ani dodatkowego ``<script>``; axe działa obok niej, a nie w niej.

Plik ``axe.min.js`` pochodzi z ``scripts/vendor_axe_core.sh`` (suma paczki zweryfikowana z rejestrem
npm). Tutaj sprawdzamy jeszcze, że plik w repozytorium jest tym, który wtedy zapisano (``SHA384``) –
podmiana silnika audytu to najprostszy sposób, żeby audyt zawsze „przechodził”.
"""

from __future__ import annotations

import base64
import hashlib
from functools import cache
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "axe-core"

#: Reguły WCAG 2.0/2.1 poziomów A i AA (tagi axe). Bez ``best-practice`` i bez AAA: zadanie mierzy
#: zgodność z WCAG 2.1 AA (deklaracja dostępności), a reguły „dobrej praktyki” są w raporcie jako
#: informacja, nie jako błąd przebiegu.
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]

#: Wpływ, który przewraca przebieg (gdy naruszenia nie ma w ``baseline.json``). ``moderate``
#: i ``minor`` trafiają wyłącznie do raportu.
BLOCKING_IMPACTS = frozenset({"critical", "serious"})


@cache
def axe_source() -> str:
    data = (VENDOR / "axe.min.js").read_bytes()
    expected = (VENDOR / "SHA384").read_text(encoding="utf-8").strip()
    actual = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
    if actual != expected:
        raise RuntimeError(
            f"e2e/vendor/axe-core/axe.min.js nie zgadza się z SHA384 ({actual} ≠ {expected}) – "
            "wgraj go ponownie skryptem scripts/vendor_axe_core.sh."
        )
    return data.decode("utf-8")


RUN = """async (tags) => {
  const result = await axe.run(document, {
    runOnly: { type: 'tag', values: tags },
    resultTypes: ['violations'],
  });
  return {
    version: axe.version,
    url: result.url,
    violations: result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      helpUrl: v.helpUrl,
      tags: v.tags.filter((t) => t.startsWith('wcag')),
      nodes: v.nodes.slice(0, 15).map((n) => ({
        target: n.target.map(String).join(' '),
        html: n.html.slice(0, 300),
        summary: (n.failureSummary || '').slice(0, 400),
      })),
      count: v.nodes.length,
    })),
  };
}"""


#: Własna reguła uzupełniająca: odwołania ``aria-describedby``/``aria-labelledby`` do identyfikatorów,
#: których nie ma na stronie. axe zgłasza je tylko jako „do przejrzenia” (incomplete), a to był
#: najczęstszy błąd audytu: Django dokleja polu ``aria-describedby="<id>_helptext"``, a szablon
#: pisany ręcznie rysował podpowiedź bez ``id`` – czytnik ekranu nie czytał jej wcale (WCAG 1.3.1).
IDREFS = r"""() => {
  const nodes = [];
  for (const el of document.querySelectorAll('[aria-describedby], [aria-labelledby]')) {
    for (const attr of ['aria-describedby', 'aria-labelledby']) {
      const missing = (el.getAttribute(attr) || '').split(/\s+/)
        .filter((id) => id && !document.getElementById(id));
      if (missing.length) {
        const name = el.getAttribute('name') || '?';
        nodes.push({
          target: el.id ? '#' + el.id : `${el.tagName.toLowerCase()}[name=${name}]`,
          html: el.outerHTML.slice(0, 300),
          summary: attr + ' -> brak #' + missing.join(', #'),
        });
      }
    }
  }
  return nodes;
}"""


def run_axe(page) -> dict:
    """Wstrzykuje axe (raz na dokument) i zwraca naruszenia reguł WCAG 2.1 A/AA (+ ``a11y-idref``)."""
    if not page.evaluate("() => typeof window.axe === 'object'"):
        page.evaluate(axe_source())
    result = page.evaluate(RUN, WCAG_TAGS)
    dangling = page.evaluate(IDREFS)
    if dangling:
        result["violations"].append(
            {
                "id": "a11y-idref",
                "impact": "serious",
                "help": "aria-describedby/aria-labelledby wskazuje identyfikator, którego nie ma na stronie",
                "helpUrl": "https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships",
                "tags": ["wcag131"],
                "nodes": dangling[:15],
                "count": len(dangling),
            }
        )
    return result
