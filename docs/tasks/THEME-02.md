# THEME-02: Zarządzanie motywem z panelu (menu, kolory, opcje) + IQO Quantum 1.1.0

## 0. Cel (polecenie organizatora, 4.10.2026)

THEME-01 dał motywy wgrywane paczkami, ale wszystko poza wyborem wersji i wariantami układów
wymagało nowej paczki. Teraz koordynator ma na ekranie „Motyw serwisu”
(`/coordinator/competition/theme/`) – **bez wgrywania paczki** – zarządzać:

1. **menu serwisu** – kolejność, ukrywanie, zmiana nazw (osobno dla każdego języka interfejsu),
   własne odnośniki (strona serwisu albo adres `http(s)`, opcjonalnie w nowej karcie) i proste
   grupy rozwijane (jeden poziom),
2. **kolorami motywu** – nadpisanie tokenów kolorów, które deklaruje `tokens.json` wersji
   (paleta jasna i/lub ciemna), kontrola kontrastu WCAG AA, podgląd, powrót do domyślnych,
3. **opcjami motywu** – schemat kolorów (jasny/ciemny/systemowy, gdy motyw ma obie palety), wariant
   logo i para krojów zadeklarowane w manifeście, warianty układów (już były).

Oraz: **IQO Quantum 1.1.0** – motyw wyraźnie odchodzący od wyglądu Olimpiady Kwantowej.

Twarde granice (jak w THEME-01):
- **Konkurs #1 bez nadpisań wygląda co do bajtu i co do liczby zapytań jak dziś** (testy złote,
  budżety zapytań). Brak nadpisań = zero nowych zapytań na stronie publicznej.
- CSP bez zmian: żadnego stylu inline, żadnego nowego originu. Arkusz nadpisań idzie z własnej
  domeny (`'self'`), jak dzisiejszy arkusz akcentu marki.
- Nadpisania to **dane**, nie kod: etykiety są escapowane przez szablon, adresy przechodzą listę
  dozwolonych schematów, wartości kolorów – wyłącznie `#rrggbb`.

## 1. Menu serwisu

### 1.1. Źródło i warstwa nadpisań

Menu buduje dziś `apps/cms/context_processors.cms_menu` (drzewo Wagtaila witryny żądania, lista
zapasowa dla witryny domyślnej, pozycja „Dla szkół/nauczycieli”). THEME-02 **nie zmienia** tej
funkcji poza dwoma punktami:

- każda pozycja dostaje stabilny klucz `key`: `home`, `p<id strony>` (strony i dokumenty wyniesione),
  `teachers` (pozycja dla nauczycieli), `f:<ścieżka>` (lista zapasowa) – szablony go nie wypisują,
- na końcu, **przed** wyliczeniem `cms_menu_primary`, wołana jest `apps.themes.menu.apply_overrides`.

Nadpisania leżą w modelu `themes.SiteMenu` (OneToOne do konkursu): lista `items` (JSON), numer
rewizji, kto i kiedy zapisał. Konkurs bez wiersza – menu jak dziś. Żeby nie płacić zapytaniem na
każdej stronie, rewizja jest powielona w `Competition.theme_options["menu"]` (kolumna wiersza, który
warstwa konkursu i tak pobiera): brak klucza = brak zapytania; jest klucz = jedno zapytanie na proces
na rewizję (pamięć procesu, jak `ThemeRuntime`).

### 1.2. Kształt `items`

Kolejność listy = kolejność menu. Wpis:

| pole | znaczenie |
|---|---|
| `key` | klucz pozycji automatycznej albo `link-<hex>` / `group-<hex>` dla własnych |
| `type` | `auto` (pozycja z drzewa), `link` (własny odnośnik), `group` (nagłówek listy rozwijanej) |
| `hidden` | pozycja ukryta (adres strony działa dalej) |
| `labels` | `{kod_języka: etykieta}`; brak języka = etykieta domyślna (tytuł / tłumaczenie ramy) |
| `parent` | klucz grupy (jeden poziom); pozycja z własną listą rozwijaną (Dokumenty) nie wchodzi do grupy |
| `url` / `page` | tylko `link`: adres albo identyfikator strony serwisu (adres liczony przy renderze) |
| `new_tab` | tylko `link`: `target="_blank" rel="noopener noreferrer"` |

Render: pozycje automatyczne nieobecne w `items` (nowa strona dodana później) stają **na końcu**
w kolejności domyślnej; wpisy wskazujące pozycję, której już nie ma (strona wycofana) – pomijane.
Grupa bez widocznych członków nie jest rysowana. Własny odnośnik do strony, która przestała być
opublikowana albo nie leży w drzewie witryny konkursu – pomijany.

### 1.3. Walidacja (serwis, nie widok)

- adres: ścieżka wewnętrzna (`/…`, bez `//`, `\`, znaków sterujących) albo `http://`/`https://`
  z hostem; wszystko inne (`javascript:`, `data:`, `mailto:`, względne) – błąd; ≤ 500 znaków,
- strona: opublikowana, w drzewie witryny **tego** konkursu (inaczej błąd – izolacja konkursów),
- etykieta: ≤ 60 znaków, bez znaków sterujących; escapowana w szablonie (żadnego `|safe`),
- języki etykiet: wyłącznie `Competition.ui_languages`,
- najwyżej 40 wpisów, 15 własnych odnośników, 8 grup; grupa nie może mieć rodzica.

## 2. Kolory, schemat, logo, kroje

### 2.1. Co jest do wyboru

- **Kolory:** każdy klucz palety podanej w `tokens.json` wersji (`colors` – jasna, `dark` – ciemna).
  Do edycji jest paleta (palety) schematu efektywnego: `dark` → ciemna, `light` → jasna, `auto` → obie.
  Pochodne (skale, „soft”, kolory „na …”, składowe RGB) liczy ponownie ten sam generator, który
  zrobił `tokens.css` przy wgraniu (`apps.themes.tokens.build_tokens_css`).
- **Schemat:** `light|dark|auto`, gdy `tokens.json` ma obie palety; domyślnie `color_scheme` manifestu.
  `data-color-scheme` na `<html>` i `theme.color_scheme` w slotach = schemat **efektywny**.
- **Logo** (nowe, opcjonalne pole manifestu `logos`): lista `{"id", "label", "light", "dark"}` –
  `light` = plik na jasne tło, `dark` = na ciemne (ścieżki pod `assets/`, obrazy z paczki). Pierwszy
  wpis jest domyślny. Sloty dostają `theme.logo` = `{"id", "label", "light", "dark"}` (pełne adresy).
- **Kroje** (nowe, opcjonalne pole manifestu `fonts`): lista `{"id", "label", "body", "display",
  "mono"}` – stosy krojów (reguły wartości tokenów, `SAFE_VALUE`); `@font-face` musi być w `theme.css`.
  Pierwszy wpis = domyślny (powinien odpowiadać `tokens.json`).

### 2.2. Kontrast

Dla każdej palety po scaleniu z nadpisaniami (i pochodnymi) sprawdzane są pary `CONTRAST_PAIRS`
z THEME-01 oraz pary wynikające z nazw tokenów motywu: `X-contrast`/`X`, `X-text`/`X`, `on-X`/`X`
i `X-ink`/`X` (tylko dla `X-ink` spoza rejestru `classic`), próg 4.5:1; `focus` i `ring` na `bg`
– próg 3:1 (element nietekstowy, WCAG 1.4.11). Para poniżej progu, w której **którykolwiek kolor
nadpisał koordynator**, **blokuje zapis**; para słaba już w motywie (bez udziału nadpisań) – ostrzeżenie.

### 2.3. Arkusz nadpisań

`GET /_theme/custom.css?s=<podpis>` – podpisany (`django.core.signing.Signer`, sól
`apps.themes.custom`) zestaw `{konkurs, wersja, schemat, kolory, krój}`. Podpis jest deterministyczny
(ten sam zestaw = ten sam adres), więc arkusz ma `Cache-Control: immutable`, a strona w pełnostronicowym
cache gościa linkuje go bez zmian. Widok sprawdza podpis, konkurs żądania i poprawność wersji, oczyszcza
opcje jeszcze raz i generuje arkusz: komplet tokenów kolorów schematu efektywnego (jak `tokens.css`)
plus `--t-font-*`. `{% theme_head %}` dołącza go **po** `theme.css` i po arkuszu akcentu marki – tylko
gdy coś odbiega od wersji (schemat, kolory, krój). Tryb wysokiego kontrastu dalej wygrywa (nadpisuje
role, nie `--t-*`).

### 2.4. Przechowywanie

`themes.ThemeCustomization` (konkurs + wersja motywu, unikalne): `options` = `{scheme, logo, font,
colors: {light: {…}, dark: {…}}}`. Kopia opcji wersji **aktywnej** leży w `Competition.theme_options`
(render bez zapytań). Aktywacja wersji w galerii wczytuje zapisane dla niej dostosowanie – powrót do
wcześniejszej wersji przywraca jej kolory. „Przywróć domyślne” czyści kolory (i opcje) tej wersji.

## 3. Panel

- `/coordinator/competition/theme/` – galeria (bez zmian) + odnośniki „Menu serwisu” i „Dostosuj
  motyw” (ten drugi przy aktywnej wersji).
- `/coordinator/competition/theme/menu/` – tabela pozycji (kolejność liczbami i przyciskami
  w górę/w dół, widoczność, etykiety per język, grupa), formularze „Dodaj odnośnik” i „Dodaj grupę”,
  usuwanie własnych pozycji, „Przywróć menu domyślne”. Bez JavaScriptu.
- `/coordinator/competition/theme/customize/` – schemat, logo, kroje, kolory (`<input type="color">`),
  wynik kontroli kontrastu, „Podgląd” (ten sam mechanizm `?theme_preview=` co galeria), „Zapisz”,
  „Przywróć domyślne”.
- Wszystkie trzy: koordynator konkursu z domeny żądania, za flagą `themes`, bez motywu (THEME_FREE_VIEWS),
  POST-y z limitem `theme_settings` (60/h, per konto), każdy zapis z wpisem audytu
  (`theme.menu_saved`, `theme.customized`, `theme.customization_reset`, `theme.menu_reset`), po zapisie
  unieważnienie pełnostronicowego cache gościa konkursu (`invalidate_competition`).

## 4. Nowy slot `nav`

`theme/nav.html` – sam `<nav class="nav nav--cms">` menu serwisu (do tej pory wewnątrz slotu
`header`). Domyślna treść przeniesiona co do bajtu; domyślny `header.html` woła `{% theme_slot "nav" %}`.
Motyw może zostawić nagłówek aplikacji i przerysować samo menu (albo w swoim `header.html`
dołączyć `{% include "theme/nav.html" %}`). Pozycje mają nowe pola `key`, `new_tab`, `external`
(dzieci: `new_tab`, `external`).

## 5. IQO Quantum 1.1.0

Paczka `themes/iqo-quantum/` (README opisuje decyzje): nagłówek przezroczysty nad planszą
z podzielonym menu, inna skala typografii, karty, rytm sekcji, stopka, kompozycja planszy, przyciski,
tabele wyników, ikonografia orbitali/fal (SVG w `assets/`), estetyka ciemna z dopracowanym trybem
jasnym (`tokens.json` z obiema paletami; koordynator przełącza schemat), `logos` i `fonts` w manifeście,
sloty `header`, `nav`, `home_hero`, `page_header`, `news_card`, `footer`. Dostępność (kontrast, fokus,
`data-contrast="high"` – żółć na czerni i powrót), RTL i telefon jak w 1.0.0. Fikstura testowa
`backend/apps/themes/tests/fixtures/iqo-quantum-1.1.0.zip` przechodzi walidator aplikacji bez błędów.

## 6. Testy

Menu (kolejność, ukrywanie, etykiety per język, własne odnośniki, grupy, walidacja adresów, XSS
w etykiecie, strona cudzego konkursu), kolory (kontrast blokuje/ostrzega, generator arkusza, podpis,
cudzy konkurs → 404, CSP bez zmian, trafienie cache gościa z motywem i nadpisaniami), uprawnienia
(recenzent/uczestnik 403, koordynator innego konkursu 403, flaga 404), audyt, limit POST, unieważnienie
cache, Konkurs #1 bez zmian (testy złote/niezmienności `apps/tenancy/tests`), paczka IQO 1.1.0 przechodzi
walidację i renderuje się w en i ar.

## 7. Realizacja i odstępstwa (gałąź `feature/motywy-2`, 4.10.2026)

Zbudowane: `apps/themes/menu.py`, `customize.py`, modele `SiteMenu`/`ThemeCustomization` (migracja
`themes.0003`), slot `nav`, ekrany `apps/web/views/coordinator_theme_settings.py`, arkusz `custom.css`,
paczka IQO Quantum 1.1.0 (fikstura `backend/apps/themes/tests/fixtures/iqo-quantum-1.1.0.zip`).

1. **Promienie** (dopisane po przeglądzie paczki): poza kolorami koordynator nadpisuje tokeny
   `radius-*` zadeklarowane w `tokens.json` (`0–48px` albo `0–3rem`; IQO: `radius-leaf`). Opcja
   `radius` w `theme_options` i w podpisie arkusza.
2. **Pary kontrastu z nazw** obejmują też `X-accent` na `X` (IQO: `cover-accent` na `cover`);
   `cover-text` na `cover` – reguła `X-text`.
3. **`sponsor_slider`** trafił do kontekstu szablonów paczek (słownik napisów i liczb) – motyw stawia
   taśmę sponsorów we własnym miejscu tylko, gdy są wpisy (IQO: stopka). W 1.0.0 nagłówek IQO pytał
   o `sponsor_slider.entries`, którego kontekst paczki nie miał – taśma nigdy się nie pokazywała.
4. **Wymiary logo** – `theme.logo.id` wybiera szerokość/wysokość `<img>` w szablonie paczki
   (`partials/logo.html`); platforma przekazuje tylko adresy i identyfikator.
5. **Menu bez podglądu** – zapis menu obowiązuje od razu (podgląd dotyczy wyłącznie motywu i kolorów).
6. **„Klasyczny” bez dostosowania kolorów** – kolory marki Olimpiady Kwantowej żyją w „Ustawieniach
   konkursu”; ekran dostosowania wyjaśnia to i odsyła do galerii.
7. **Akcent marki ma pierwszeństwo** przed kolorem `accent` z dostosowania (arkusz akcentu stoi za
   `theme.css`, `custom.css` – przed nim).
8. Napisy panelu – katalog `apps/themes/locale/en` (panel jest polski, IQO angielski); jedyny nowy napis
   stron publicznych („Menu” w nagłówku IQO) – w 10 katalogach `apps/themes/locale/*`.

Znane luki: brak przeciągania pozycji menu (kolejność liczbami i przyciskami – bez JavaScriptu); brak
podglądu kontrastu na żywo przed wysłaniem formularza (kontrola po stronie serwera); djcms bez motywów.
