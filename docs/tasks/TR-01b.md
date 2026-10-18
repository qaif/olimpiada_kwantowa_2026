# TR-01b: Konwerter Markdown bez kosztu kwadratowego i jedna bramka kosztu

## 0. Cel i granice

Punkt 6 listy organizatora (18.10.2026). Konwerter Markdown z TR-01
(`apps/problem_translations/markup.py`) składa tłumaczenia zadań pisane przez opiekunów drużyn
i delegacji, a od WM-FMT-01 także podgląd plików `.md` z materiałów warsztatowych. Jego wyrażenia
regularne są **leniwe** (`**(.+?)**`, `\[(.+?)\]` z `DOTALL`, bloki kodu), więc każdy
**niedomknięty** znacznik skanuje tekst do końca wiersza albo całego tekstu – tysiące takich
znaczników to koszt kwadratowy. Wiersz 200 KB z „`**a `” ×50 000 składał się ok. 142 s (pomiar
z 9.10.2026 przy WM-FMT-01). WM-FMT-01 zabezpieczył tylko podgląd materiałów (szacunek
`markdown_cost` przed wywołaniem konwertera); tłumaczenia zadań szły przez konwerter bez limitu –
jeden spreparowany tekst blokował worker web przy każdym wyświetleniu (edytor opiekuna, przegląd
koordynatora, strona ucznia) i przy eksporcie PDF.

Czego zadanie **nie** robi: nie zmienia składni ani wyniku konwertera dla żadnego wejścia
mieszczącego się w budżecie, nie zmienia zachowania podglądu materiałów, nie dodaje zależności.

## 1. Konwerter liniowy (wymóg a)

- Każde wyrażenie leniwe jest próbowane **tylko tam, gdzie na pewno się dopasuje**. Przed
  podstawieniem zbieramy pozycje otwarć i zamknięć znacznika (`str.find` + to samo sprawdzenie
  otoczenia, co w oryginalnym wyrażeniu, np. `(?<=\S)\*\*`), a o otwarciu rozstrzyga
  wyszukiwanie binarne: „czy za nim, w dozwolonym zasięgu (do końca wiersza albo tekstu), jest
  zamknięcie”. Otwarcie bez zamknięcia jest pomijane bez skanowania. Samo dopasowanie robi nadal
  **to samo** wyrażenie (`pattern.match(text, pozycja)`), więc grupy i zastąpienia są identyczne,
  a koszt udanego dopasowania jest pokryty przez tekst, który ono zjada.
- Dotyczy: pogrubienia, kursywy, `\(…\)` w `$…$`, `$$…$$`/`\[…\]`, bloków kodu (HTML) i tego
  samego wyrażenia w `plain_blocks` (PDF).
- Wiersz separatora tabeli (`|---|---|`) miał dwa sąsiednie `\s*` (`\s*\|?\s*`), co dawało koszt
  kwadratowy w długości ciągu spacji (wiersz z `|`, a pod nim 200 KB spacji i litera – ok. 4 min).
  Wyrażenie zapisane jednoznacznie (`\s*(?:\|\s*)?…`) opisuje ten sam język. Ten kształt
  **nie** był objęty szacunkiem WM-FMT-01.
- Zgodność: test różnicowy porównuje każde podstawienie z oryginalnym `pattern.sub`/`finditer`
  na tysiącach losowych krótkich tekstów ze znaków specjalnych (`*`, `$`, `\`, `` ` ``, `|`, …).

## 2. Bramka kosztu w punkcie wejścia (wymóg b)

- `markdown_cost` (liniowy szacunek z WM-FMT-01, bez zmian wzoru) przenosimy do `markup.py`.
  Jest **bezpiecznikiem**: konwerter jest już liniowy, ale gdyby przyszła zmiana dołożyła
  wyrażenie leniwe, teksty o kształcie kwadratowym i tak nie dojdą do składu.
- Budżet w ustawieniu `MARKDOWN_COST_BUDGET` (`config/settings/base.py`, domyślnie 5·10⁷ – jak
  w WM-FMT-01).
- `markup.render_within_budget(tekst)` → HTML albo `None` (ponad budżet). `markup.render(tekst)`
  ponad budżet zwraca **uciekany tekst bez znaczników** w `<pre class="tr-plain">` (zawijany,
  `translations.css`). `plain_blocks` (PDF) ponad budżet zwraca same akapity – bez nagłówków,
  formuł i bloków kodu. Wszyscy wywołujący (widoki opiekuna, koordynatora, ucznia, eksport PDF,
  podgląd materiałów) przechodzą przez te funkcje – nie ma drogi do konwertera z pominięciem bramki.
- Podgląd materiałów (`workshop_materials/preview.py`) traci własną kopię szacunku i budżetu:
  woła `render_within_budget`, a przy `None` pokazuje – jak dotąd – zwykły tekst w `<pre>`
  z adnotacją (`plain_fallback`). Szacunek liczony jest na tym samym tekście, co wcześniej, więc
  zachowanie podglądu się nie zmienia.

## 3. Testy

- Patologiczne wejścia (`**a ` ×50 000, `*a ` ×50 000, `_` ×100 000, `[`, `\[` i `\(` ×50 000,
  `$` ×100 000, „` ```x `” w 20 000 wierszach, spacje w separatorze tabeli) – **sam konwerter**
  (z pominięciem bramki) < 1 s każde i cała `render`/`plain_blocks` < 1 s.
- Test różnicowy z § 1; bramka: tekst ponad budżet → `<pre class="tr-plain">` z uciekanym
  tekstem, budżet z ustawienia (`override_settings`).
- Istniejące testy TR-01 (`test_services`, `test_views`, `test_review_fixes`) i WM-FMT-01
  (`test_preview`) – bez zmian zachowania.
