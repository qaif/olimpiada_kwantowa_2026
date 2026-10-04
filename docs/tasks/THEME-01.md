# THEME-01: Motywy wizualne wgrywane paczkami (jak motywy WordPressa) + motyw IQO

## 0. Cel (polecenie organizatora, 4.10.2026)

Gruntowne przeprojektowanie warstwy wyglądu tak, żeby **wygląd konkursu dało się zmienić wgraniem
paczki** (ZIP), bez wydania aplikacji – jak motywy WordPressa – oraz **nowy, zupełnie inny motyw
dla IQO** (International Quantum Olympiad, iqo-official.org; logo „hybryda D”: medal z paczką
falową + napis |IQO⟩; kolory granat #14123A, elektryczny niebieski #3C4BFF / #7C88FF na ciemnym,
krój Space Grotesk).

Twarde granice:
- **Konkurs #1 (`kwantowa`) bez motywu wygląda co do bajtu jak dziś** (testy złote/niezmienności);
  dzisiejszy wygląd staje się wbudowanym motywem domyślnym `classic`.
- Bezpieczeństwo jest ważniejsze od elastyczności: paczka **nie wykonuje kodu Pythona**, CSP zostaje
  ścisła (bez `unsafe-inline` dla skryptów; motyw nie dokłada JavaScriptu w v1), pliki z paczki są
  serwowane z naszego storage z poprawnym `Content-Type`, nigdy jako HTML.
- Panele (uczestnika, koordynatora, recenzenta) dziedziczą **tokeny** motywu (kolory, krój, promienie),
  ale nie jego szablonów – funkcjonalność paneli nie może zależeć od motywu.

## 1. Format paczki (`*.zip`, max 20 MB)

```
manifest.json          (wymagany)
theme.css              (wymagany; jedyny arkusz motywu)
tokens.json            (wymagany; zmienne projektu)
assets/…               (obrazy png/jpg/webp/svg, kroje woff2) – tylko te typy
templates/…            (opcjonalne nadpisania szablonów z listy dozwolonej, § 3)
screenshot.png         (podgląd na liście motywów, 1200×900)
```

`manifest.json`:
```json
{
  "schema": 1,
  "slug": "iqo-quantum",
  "name": "IQO Quantum",
  "version": "1.0.0",
  "author": "…",
  "description": "…",
  "supports": ["public", "panels"],
  "layouts": {"header": "centered|split|minimal", "home_hero": "full-bleed|split|compact",
              "footer": "columns|compact", "cards": "flat|elevated|outline"},
  "color_scheme": "light|dark|auto",
  "min_app_version": "0.41.0"
}
```

`tokens.json` (mapowane na zmienne CSS `--t-*` używane przez **wszystkie** arkusze aplikacji):
kolory (`bg`, `surface`, `text`, `muted`, `primary`, `primary-contrast`, `accent`, `border`,
`success`, `warning`, `danger`, osobny zestaw `dark`), typografia (`font-body`, `font-display`,
`font-mono`, skala rozmiarów), `radius-*`, `space-*`, `shadow-*`, `header-height`, `max-width`.
Walidacja: kolory jako hex/rgb(), kontrast tekstu do tła ≥ 4.5:1 (ostrzeżenie, nie błąd).

## 2. Walidacja przy wgraniu (serwis, nie widok)

- ZIP: bez ścieżek z `..`, bezwzględnych, dowiązań; limit plików (500) i rozmiaru po rozpakowaniu
  (60 MB, ochrona przed bombą ZIP); tylko dozwolone rozszerzenia; skan ClamAV (jak przy
  zaświadczeniach); SVG oczyszczane (bez `<script>`, `on*`, `foreignObject`, zewnętrznych `href`).
- CSS: parsowany (np. `tinycss2`, jeśli jest w zależnościach; inaczej dodać), odrzucane: `@import`,
  `url()` spoza `assets/` paczki, `expression`, `behavior`, `-moz-binding`, `javascript:`;
  `url()` przepisywane na adresy plików paczki w storage.
- Szablony: tylko z listy dozwolonej (§ 3), składnia sprawdzana kompilacją silnika Django
  z **ograniczonym** zestawem bibliotek tagów (`static`, `i18n`, `wagtailcore_tags`,
  `wagtailimages_tags`, `cms_extras`, `web_extras` – bez `{% load %}` czegokolwiek innego),
  zakaz `{% include %}`/`{% extends %}` spoza szablonów aplikacji i motywu, zakaz `|safe` /
  `{% autoescape off %}` (lint przy wgraniu).
- Wynik walidacji to raport (błędy + ostrzeżenia), zapisany przy wersji motywu.

## 3. Co motyw może nadpisać (lista dozwolona, v1)

Publiczna rama i strony CMS: `base.html` → **wyłącznie** bloki-sloty (`theme/header.html`,
`theme/footer.html`, `theme/home_hero.html`, `theme/page_wrapper.html`, karty aktualności,
nagłówek strony CMS). `base.html` aplikacji zostaje właścicielem `<head>` (CSP nonce, meta, skrypty,
ciasteczka, skip-link, dostępność) – motyw dostaje sloty wewnątrz. Panele i formularze: bez nadpisań.

## 4. Model i działanie

- `Theme` (globalny katalog motywów platformy): slug, nazwa, autor, `is_builtin`.
- `ThemeVersion`: plik paczki w prywatnym storage, rozpakowane zasoby w publicznym bucket pod
  niezmiennym prefiksem `themes/<slug>/<version>/` (cache bez unieważniania), manifest, tokeny,
  raport walidacji, status `valid|invalid`, kto i kiedy wgrał (audyt).
- `Competition.theme_version` (FK, null = `classic`) + `Competition.theme_options` (JSON: wybrane
  warianty layoutów, nadpisania kolorów marki – np. akcent z „Ustawień konkursu”).
- **Wgrywanie** paczek: wyłącznie **superkoordynator** (operator platformy). **Wybór** motywu dla
  konkursu i jego opcji: koordynator konkursu, z **podglądem** (`?theme_preview=<token>` tylko dla
  zalogowanego koordynatora, nie zmienia nic innym) i przełączeniem „Aktywuj”. Cofnięcie = wybór
  poprzedniej wersji (wersje zostają).
- Render: loader szablonów sprawdza najpierw sloty motywu aktywnego dla `request.competition`
  (pamięć podręczna per wersja), potem szablony aplikacji. Arkusz motywu i tokeny wstrzykiwane
  w `<head>` jako `<link>` (bez inline), po `app.css`.
- CSP: `style-src`/`font-src`/`img-src` – origin publicznego bucketu już jest; nic nowego.
- djcms (`dj.`): poza zakresem v1 (zapisz w znanych lukach).

## 5. Refaktor istniejących arkuszy

Wszystkie kolory, kroje, promienie i odstępy w `backend/static/css/*.css` → zmienne `--t-*`
z wartościami domyślnymi równymi dzisiejszym (motyw `classic`), żeby motyw sterował **także**
panelami. Test: zrzut wartości wyliczonych (`getComputedStyle`) albo porównanie arkuszy pokazuje,
że `classic` daje te same wartości co dziś; testy złote HTML bez zmian.

## 6. Panel

- `/coordinator/platform/themes/` (superkoordynator): lista, wgraj paczkę, raport walidacji,
  wersje, usuń nieużywaną wersję.
- `/coordinator/competition/theme/` (koordynator): galeria motywów ze zrzutem, warianty layoutów,
  podgląd, aktywacja.
- Komenda `manage.py theme_install <zip> [--activate <slug_konkursu>]` (dla wdrożeń, ZIP ze stdin).

## 7. Motyw IQO (osobna paczka, `themes/iqo-quantum/` w repo)

„Zupełnie inny” niż `classic`: tryb ciemny jako domyślny (granat #14123A / #0B0A24, tekst biały,
akcent #7C88FF, drugi akcent malinowy #FF6B98 do wyróżnień), Space Grotesk (woff2 w paczce, licencja
OFL w paczce), nagłówek `minimal` z logo hybrydy, hero `full-bleed` z motywem paczki falowej
(SVG w tle, statyczne – bez JS), karty `outline` z cienkimi liniami, siatka i mikrotypografia
„laboratoryjna” (kod/mono dla terminów, ket |…⟩ jako ornament nagłówków sekcji). Dostępność:
kontrast ≥ 4.5:1, focus widoczny, `prefers-reduced-motion`. RTL działa (logiczne właściwości).
Zrzut `screenshot.png`. Wszystkie 11 języków interfejsu muszą się mieścić (niemiecki/rosyjski
dłuższe napisy, arabski RTL, hindi/bengalski wyższe glify).

## 8. Testy

Walidator (każda reguła § 2 z przypadkiem złośliwym: ZIP-slip, bomba, `@import`, `url(http…)`,
SVG ze skryptem, szablon z `{% load %}` spoza listy, `|safe`), kwantowa bez zmian (złote),
aktywacja/podgląd/uprawnienia (koordynator innego konkursu → 404, recenzent → 403), loader slotów,
cache per wersja, CSP nagłówek bez zmian, `theme_install`. E2E: przełączenie IQO na motyw i zrzut
strony głównej w en i ar.
