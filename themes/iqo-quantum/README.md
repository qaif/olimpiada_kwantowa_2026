# IQO Quantum – motyw International Quantum Olympiad

Paczka motywu w formacie THEME-01 (§ 1–3): wygląd „laboratorium nocą” dla
[iqo-official.org](https://iqo-official.org). Ciemny domyślnie, z kompletem tokenów jasnych.

![zrzut](screenshot.png)

## Zawartość

| Plik | Rola |
|---|---|
| `manifest.json` | schema 1, `iqo-quantum` 1.0.0, układy `header=minimal`, `home_hero=full-bleed`, `footer=compact`, `cards=outline`, `color_scheme=dark` |
| `tokens.json` | `dark` (paleta używana – manifest ma `color_scheme: dark`), `colors` (wariant jasny – **nieużywany** przy `dark`, czeka na wersję z `color_scheme: auto`), `tokens` (kroje, skala, promienie, odstępy, cienie, `header-height`, `max-width`). Każdy klucz → `--t-<klucz>`. `primary` = powierzchnia marki (granat #14123A) z napisem `primary-contrast`/`on-primary-strong`, jak w aplikacji; periwinkle wezwań do działania to własne `cta`, `cta-hover`, `cta-ink`, a pierścień fokusu – `ring` (odporny na akcent marki konkursu) |
| `theme.css` | jedyny arkusz motywu: kroje, most `--t-*` → role `app.css` (`--bg`, `--ink`, `--brand`…, z których korzystają panele), nagłówek, hero, sekcje, karty, stopka. Bez `@import`, `url()` tylko do `assets/`, właściwości logiczne, bez animacji |
| `templates/theme/header.html` | slot nagłówka: logo (`<img>` z `theme.assets`), menu serwisu, konto; w jednym wierszu od 1400 px (wtedy przyklejony), niżej menu w drugim wierszu i pasek nieprzyklejony; taśma sponsorów za `<header>` |
| `templates/theme/home_hero.html` | slot hero strony głównej: ta sama mechanika slidera (`data-hero-slider*`) co `cms/home_page.html`, rama full-bleed |
| `templates/theme/footer.html` | slot stopki: dwa poziome pasy, metadane w kroju mono |
| `assets/logo/` | logo „hybryda D” (medal + paczka falowa, napis \|IQO⟩): pełne (`logo*.svg`), znak (`mark*.svg`), poziomy lockup do nagłówka (`lockup*.svg`), favikona |
| `assets/hero/wave-field.svg` | tło hero: medal przecięty paczką falową na prążkach interferencji dwóch szczelin (statyczny SVG) |
| `assets/fonts/` | Space Grotesk (oś `wght` 300–700) i Space Mono 400 jako podzbiory woff2 (łacina, łacina rozszerzona, wietnamski, ⟨⟩) + licencje OFL |
| `screenshot.png` | 1200×900, strona główna en, wariant ciemny |

Poza ZIP-em (narzędzia w repo): `_preview/` (makieta i zrzuty), `tools/` (generatory grafik
i podzbiorów krojów), `build_zip.py`, ten plik.

## Decyzje projektowe

- **Ciemny**: tło `#0B0A24`, powierzchnie i pasek marki `#14123A`, tekst `#F4F4FF`, przyciski
  `#7C88FF` (`cta`), malina `#FF6B98` do wyróżnień (ornament ket, otwarty etap, fokus). Pary
  tekst/tło ≥ 4.5:1 (najsłabsza: `muted` na `surface-2` 7.4:1; napis na pasku marki 16:1).
- **Hero, stopka i dok osi czasu mają stałe ciemne kolory** z księgi znaku (`--iqo-deep`,
  `--iqo-on-deep…`), bo tło SVG jest rysowane pod granat – także gdyby kiedyś włączyć wariant jasny.
- **Wydruk**: arkusz przestawia aliasy na czerń na bieli i chowa nagłówek, tło hero i dok.
- **Karty „outline”**: półprzezroczyste, linia 1 px, znaczniki narożników jak kadr przyrządu.
- **Ornament ket**: `|Nagłówek⟩` przy `.section__head h2` i `.page-head h1` (kreska = pudełko CSS,
  nawias = glif U+27E9 ze Space Grotesk); kroki jako `|01⟩` (zawsze LTR – zapis matematyczny).
- **Mono** (Space Mono) tylko dla dat, etykiet, odznak, stanu etapu i metadanych stopki; dla ar, hi,
  bn, zh, ru mono przechodzi na krój treści (Space Mono nie ma tych pism).
- **11 języków**: Space Grotesk niesie łacinę, reszta – kroje systemowe per `:lang()`; wyższa
  interlinia dla hi/bn/ar, bez wersalików i rozstrzelenia poza łaciną; RTL przez właściwości
  logiczne + lustrzane odbicie tła hero. Sprawdzone zrzutami en/ar/ru/hi (brak przewijania w bok).
- **Dostępność**: fokus 2 px malinowy z odstępem 3 px; tryb wysokiego kontrastu aplikacji
  (`data-contrast="high"`) wygrywa z paletą motywu; logo to `<img>`, więc zostaje w
  `forced-colors` i na wydruku; ornamenty mają pusty tekst zastępczy (`content: "⟩" / ""`);
  motyw nie dodaje żadnej animacji.

## Zależności od systemu motywów (`backend/apps/themes`)

1. Tokeny: `tokens.py` – przy `color_scheme: dark` do `:root` trafia wyłącznie zestaw `dark`
   (uzupełniony pochodnymi). `theme.css` nigdy nie ustawia `--t-*` – tylko je czyta (z zapasem =
   wartości ciemne). Własne tokeny `cta*`, `ring`, `brand*`, `primary-fill` przechodzą jako `--t-*`.
2. Szablony slotów korzystają z `theme.assets` (adres wersji w storage) i `theme.color_scheme`
   (wybór pliku logo). Tło hero to `url(assets/…)` w CSS, przepisywane przez walidator.
3. Sloty: `theme/header.html` zastępuje **oba** paski (`.topbar--account` + `header.topbar`);
   `theme/footer.html` – `<footer class="footer">`; `theme/home_hero.html` – sekcję
   `.hero--slider` (może stać w `<main class="page">` – hero sam wychodzi na 100vw).
4. `assets/` zawiera licencje `.txt` (OFL wymaga dołączenia).
5. Kroje z publicznego bucketu innego originu wymagają nagłówka CORS
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

Odświeża `_preview/tokens*.css` (generatorem `apps/themes/tokens.py`, gdy jest w repo albo
w `IQO_TOKENS_PY`), `_preview/index.html`, `screenshot.png` i zapisuje zrzuty wariantów (ar/RTL,
ru, hi, jasny, wysoki kontrast, telefon, wydruk). `IQO_APP_CSS` wskazuje inny `app.css`. Grafiki: `tools/make_assets.py
<katalog logo D-hybrid>`; kroje: `tools/subset_fonts.py`.

## Licencje

- Space Grotesk – © 2020 The Space Grotesk Project Authors, SIL Open Font License 1.1
  (`assets/fonts/OFL.txt`).
- Space Mono – © 2016 The Space Mono Project Authors, SIL Open Font License 1.1
  (`assets/fonts/OFL-SpaceMono.txt`).
- Logo IQO i grafiki w `assets/` – znak International Quantum Olympiad; użycie wyłącznie w serwisie
  IQO. Kod paczki (CSS, szablony, skrypty) – licencja repozytorium.
