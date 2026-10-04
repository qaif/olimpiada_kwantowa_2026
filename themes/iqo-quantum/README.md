# IQO Quantum – motyw International Quantum Olympiad

Paczka motywu w formacie THEME-01 (§ 1–3): wygląd „laboratorium nocą” dla
[iqo-official.org](https://iqo-official.org). Ciemny domyślnie, z kompletem tokenów jasnych.

![zrzut](screenshot.png)

## Zawartość

| Plik | Rola |
|---|---|
| `manifest.json` | schema 1, `iqo-quantum` 1.0.0, układy `header=minimal`, `home_hero=full-bleed`, `footer=compact`, `cards=outline`, `color_scheme=dark` |
| `tokens.json` | `colors` (wariant jasny), `dark` (wariant ciemny), `tokens` (kroje, skala, promienie, odstępy, cienie, `header-height`, `max-width`). Każdy klucz → zmienna `--t-<klucz>` |
| `theme.css` | jedyny arkusz motywu: kroje, most `--t-*` → role `app.css` (`--bg`, `--ink`, `--brand`…, z których korzystają panele), nagłówek, hero, sekcje, karty, stopka. Bez `@import`, `url()` tylko do `assets/`, właściwości logiczne, bez animacji |
| `templates/theme/header.html` | slot nagłówka: jeden pasek – logo, menu serwisu, konto |
| `templates/theme/home_hero.html` | slot hero strony głównej: ta sama mechanika slidera (`data-hero-slider*`) co `cms/home_page.html`, rama full-bleed |
| `templates/theme/footer.html` | slot stopki: dwa poziome pasy, metadane w kroju mono |
| `assets/logo/` | logo „hybryda D” (medal + paczka falowa, napis \|IQO⟩): pełne (`logo*.svg`), znak (`mark*.svg`), poziomy lockup do nagłówka (`lockup*.svg`), favikona |
| `assets/hero/wave-field.svg` | tło hero: medal przecięty paczką falową na prążkach interferencji dwóch szczelin (statyczny SVG) |
| `assets/fonts/` | Space Grotesk (oś `wght` 300–700) i Space Mono 400 jako podzbiory woff2 (łacina, łacina rozszerzona, wietnamski, ⟨⟩) + licencje OFL |
| `screenshot.png` | 1200×900, strona główna en, wariant ciemny |

Poza ZIP-em (narzędzia w repo): `_preview/` (makieta i zrzuty), `tools/` (generatory grafik
i podzbiorów krojów), `build_zip.py`, ten plik.

## Decyzje projektowe

- **Ciemny domyślnie**: tło `#0B0A24`, powierzchnie `#14123A`, tekst `#F4F4FF`, akcent `#7C88FF`
  (na ciemnym), malina `#FF6B98` do wyróżnień (ornament ket, otwarty etap, fokus). Wszystkie pary
  tekst/tło ≥ 4.5:1 w obu wariantach (najsłabsze: `muted` na `surface-2` 7.4:1 ciemny, 6.3:1 jasny).
- **Hero, stopka i dok osi czasu są ciemne także w wariancie jasnym** – stałe kolory z księgi znaku
  (`--iqo-deep`, `--iqo-on-deep…`), bo tło SVG jest rysowane pod granat.
- **Karty „outline”**: półprzezroczyste, linia 1 px, znaczniki narożników jak kadr przyrządu.
- **Ornament ket**: `|Nagłówek⟩` przy `.section__head h2` i `.page-head h1` (kreska = pudełko CSS,
  nawias = glif U+27E9 ze Space Grotesk); kroki jako `|01⟩` (zawsze LTR – zapis matematyczny).
- **Mono** (Space Mono) tylko dla dat, etykiet, odznak, stanu etapu i metadanych stopki; dla ar, hi,
  bn, zh, ru mono przechodzi na krój treści (Space Mono nie ma tych pism).
- **11 języków**: Space Grotesk niesie łacinę, reszta – kroje systemowe per `:lang()`; wyższa
  interlinia dla hi/bn/ar, bez wersalików i rozstrzelenia poza łaciną; RTL przez właściwości
  logiczne + lustrzane odbicie tła hero. Sprawdzone zrzutami en/ar/ru/hi (brak przewijania w bok).
- **Dostępność**: fokus 2 px malinowy z odstępem 3 px; tryb wysokiego kontrastu aplikacji
  (`data-contrast="high"`) wygrywa z paletą motywu; `forced-colors` pokazuje nazwę serwisu zamiast
  logo-tła; motyw nie dodaje żadnej animacji.

## Wymagania wobec systemu motywów

1. Tokeny: każdy klucz z `colors`/`dark`/`tokens` → `--t-<klucz>`; `colors` na `:root`, `dark`
   zamiast nich przy schemacie ciemnym. `theme.css` nigdy nie ustawia `--t-*` – tylko je czyta
   (z zapasem = wartości ciemne).
2. Na `<html>` atrybut `data-color-scheme="dark|light|auto"` (z manifestu albo `theme_options`) –
   arkusz przełącza nim logo nagłówka i `color-scheme`.
3. Sloty: `theme/header.html` zastępuje **oba** paski (`.topbar--account` + `header.topbar`);
   `theme/footer.html` – `<footer class="footer">`; `theme/home_hero.html` – sekcję
   `.hero--slider` (może stać w `<main class="page">` – hero sam wychodzi na 100vw). Sloty dostają
   kontekst strony (`page`, `hero_slides_visible`, `registration`, `edition`, `current_stage`,
   `latest_news`, `news_index`, `settings`, `site_root`, `cms_menu`…).
4. Szablony nie znają adresu paczki – logo i tła są w CSS (`url(assets/…)` przepisywane przez
   walidator).
5. `assets/` zawiera licencje `.txt` (OFL wymaga dołączenia) – walidator musi przepuścić `.txt`
   (serwowane jako `text/plain`).
6. Kroje z publicznego bucketu innego originu wymagają nagłówka CORS
   (`Access-Control-Allow-Origin`) – inaczej przeglądarka ich nie użyje (zostają kroje zapasowe).

## Budowanie i podgląd

```bash
python themes/iqo-quantum/build_zip.py        # → themes/iqo-quantum/dist/iqo-quantum-1.0.0.zip
```

Skrypt sprawdza reguły § 2 (rozszerzenia, `url()`, `@import`, SVG, biblioteki tagów, `|safe`)
i pakuje tylko pliki formatu paczki (bez `_preview/`, `tools/`, README).

```bash
uv run --no-project --with playwright python -m playwright install chromium
uv run --no-project --with playwright --with pillow python themes/iqo-quantum/_preview/render.py [katalog]
```

Odświeża `_preview/tokens.css`, `_preview/index.html`, `screenshot.png` i zapisuje zrzuty
wariantów (ar/RTL, ru, hi, jasny, wysoki kontrast, telefon). Grafiki: `tools/make_assets.py
<katalog logo D-hybrid>`; kroje: `tools/subset_fonts.py`.

## Licencje

- Space Grotesk – © 2020 The Space Grotesk Project Authors, SIL Open Font License 1.1
  (`assets/fonts/OFL.txt`).
- Space Mono – © 2016 The Space Mono Project Authors, SIL Open Font License 1.1
  (`assets/fonts/OFL-SpaceMono.txt`).
- Logo IQO i grafiki w `assets/` – znak International Quantum Olympiad; użycie wyłącznie w serwisie
  IQO. Kod paczki (CSS, szablony, skrypty) – licencja repozytorium.
