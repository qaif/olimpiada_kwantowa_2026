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

### 1a. Doprecyzowanie formatu (stan wdrożony – **obowiązuje**, zgodne z paczką `iqo-quantum`)

> Struktura plików z § 1 nie zmieniła się. Poniżej to, czego § 1 nie rozstrzygał – walidator
> (`backend/apps/themes/package.py`, `tokens.py`, `slots.py`) trzyma się dokładnie tego.

- **Katalog nadrzędny w ZIP** jest dopuszczalny (`iqo-quantum/manifest.json`) – zdejmowany przy odczycie.
- **Pliki tekstowe** `*.txt`/`*.md` (licencje OFL krojów, README) są dopuszczone w dowolnym miejscu
  paczki, także w `assets/`, ale **nie są publikowane** w buckecie (zostają w paczce prywatnej).
- Nazwy plików: `[A-Za-z0-9._/-]`; `assets/` tylko `png jpg jpeg webp svg woff2` (sprawdzane też
  magicznymi bajtami); `screenshot.png` 1200×900 (inny rozmiar = ostrzeżenie).
- `manifest.json`: `layouts.<klucz>` to napis `"a|b|c"` albo lista `["a","b"]`; **pierwsza wartość
  jest domyślna**, reszta to warianty do wyboru w panelu. Słownik: `header` centered|split|minimal,
  `home_hero` full-bleed|split|compact, `footer` columns|compact, `cards` flat|elevated|outline.
  Wybrany wariant trafia na `<html>` jako `data-layout-<klucz>` (`home_hero` → `data-layout-home-hero`);
  wariant **rysuje `theme.css` motywu** – aplikacja nie ma własnych reguł dla wariantów.
  `min_app_version` porównywane z `APP_VERSION` (`dev` = pominięte). Slug `classic` jest zarezerwowany.
- `tokens.json`: grupy `colors` (paleta jasna), `dark` (paleta ciemna; może też nieść `shadow-*`),
  `tokens` (płaska lista pozostałych), a także `typography`, `radius`, `space`, `shadow`, `layout`,
  `extra`. **Każdy klucz staje się zmienną `--t-<klucz>`** w wygenerowanym `tokens.css` (także nazwy
  spoza rejestru – motyw używa ich w swoim `theme.css`). W grupach `radius`/`space`/`shadow` klucz
  krótki dostaje prefiks (`"sm"` → `radius-sm`, `"md"` → `radius`). Kolory: `#hex` (3/4/6/8) albo
  `rgb()`/`rgba()`; pozostałe wartości bez `url()`, `;`, `{}`, `<>`, `\`, `@`, `!`.
  Synonimy (zapisywane pod obiema nazwami): `accent-contrast`→`on-accent`, `primary-hover`→`primary-700`,
  `radius-md`→`radius`, `shadow-md`→`shadow`, `size-md`→`font-size-base`.
  Rejestr tokenów czytanych przez arkusze aplikacji wraz z wartościami `classic`:
  `themes/classic/tokens.json` – role `bg bg-muted surface surface-2 surface-inset text text-soft
  muted border border-strong primary primary-contrast primary-soft accent accent-ink accent-soft
  accent-on-primary accent-rule success* warning* danger* info* neutral-ink neutral-soft link
  link-hover focus on-accent on-primary-strong on-status logo-plaque`, skale `primary-600…900`
  i `accent-300…800`, `space-*`, `radius-sm|radius|radius-lg`, `shadow-sm|shadow|shadow-inset`,
  `max-width`, `reading`, `font-body`, `font-display`, `font-mono`, `font-size-base`. Tokeny niepodane,
  a wyprowadzalne z podanych (skale, „soft”, kolory „na …”, składowe `*-rgb`) liczy serwer; reszta –
  wartości `classic` **tego samego schematu**.
- **`color_scheme`** (atrybut `data-color-scheme` na `<html>` zawsze równa się manifestowi):
  - `light` – `tokens.css` ma wyłącznie paletę `colors` w `:root` (komplet tokenów kolorów, więc
    ciemny wariant `app.css` niczego nie przebija), `color-scheme: light`;
  - `dark` – wyłącznie paleta `dark` (bez niej `colors`) w `:root`, `color-scheme: dark`; zestaw
    `colors` jest wtedy **nieużywany** przez aplikację (motyw może go czytać sam);
  - `auto` – `colors` w `:root` i `dark` w `@media (prefers-color-scheme: dark)`; bez zestawu `dark`
    motyw jest zawsze jasny (ostrzeżenie w raporcie).
- `url()` w `theme.css` są przepisywane na postać kanoniczną **względną** `url("assets/…")` – arkusz
  leży w storage obok `assets/` pod tym samym prefiksem, więc adres trafia do pliku paczki, a zmiana
  domeny bucketu nie wymaga ponownego wgrania. Szablony slotów nie znają adresu paczki wprost –
  dostają zmienną `theme` (`theme.assets` = adres katalogu paczki z `/` na końcu, `theme.slug`,
  `theme.name`, `theme.version`, `theme.layouts`, `theme.color_scheme`, `theme.preview`).
- **Sloty** (§ 3, lista zamknięta – `apps/themes/slots.py`): `theme/header.html` – **oba paski**
  nagłówka (pasek konta i menu serwisu; domyślny zawiera zagnieżdżony slot `brand`),
  `theme/brand.html`, `theme/footer.html`, `theme/page_wrapper.html` (wewnątrz `<main class="page"
  id="tresc">`, treść w `{{ slot_content }}`), `theme/home_hero.html` (pełny kontekst strony głównej),
  `theme/news_card.html` (zmienna `item`), `theme/page_header.html` (strona treści CMS) oraz pomocnicze
  `theme/partials/*.html`. Domyślne treści: `backend/templates/theme/*.html`; z paczki dostępne pod
  aliasem `{% include "classic/<slot>.html" %}`.
- Lint szablonów: `{% load %}` tylko `static i18n wagtailcore_tags wagtailimages_tags cms_extras
  web_extras`; bez `extends`, `debug`, `autoescape off`, `|safe`, `{% filter safe %}`; `include` tylko
  literałem: `theme/…` z paczki, `classic/…`, fragmenty aplikacji `cms/_*`, `web/_*`; bez odwołań do
  `password`, `session`, `csp_nonce`, `META`, `COOKIES`, `headers`, `secret*`, `_*`; HTML bez `<script>`,
  `<style>`, `<link>`, `<meta>`, `<base>`, ramek/osadzeń, `on*=`, `style=`, `srcdoc=`, `formaction=`,
  `javascript:`, `data:`. **Formularze są dozwolone** (nagłówek IQO rysuje „Wyloguj” z `{% csrf_token %}`;
  cel formularza ogranicza `form-action` w CSP). Tekst w `{% comment %}` nie podlega lintowi HTML.

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

## 9. Realizacja i odstępstwa (gałąź `feature/motywy`, 4.10.2026)

Zbudowane: `backend/apps/themes/` (modele, walidator, generator tokenów, runtime, silnik slotów,
znaczniki, komenda `theme_install`), sloty w `base.html` i szablonach CMS, panele
(`apps/web/views/coordinator_themes.py`), rozszerzenie CSP, `Cache-Control: immutable` dla plików
motywu w S3, motyw wbudowany `themes/classic/`, dokumentacja (`OPERACJE.md` § 28,
`PODRECZNIK-ORGANIZATORA.md` § 10b, CHANGELOG).

Odstępstwa od § 0–§ 8 (z powodami):

1. **Tokeny (§ 5):** role arkuszy (`--bg`, `--ink`, `--brand`…) zostały jako aliasy `var(--t-…)`
   zdefiniowane **raz** w `:root`, zamiast przepisywać 10 tys. wierszy CSS na `--t-*`. Wariant ciemny
   przestawia tokeny `--t-*`, nie role – dzięki temu motyw jasny wygrywa kolejnością arkuszy, a tryb
   wysokiego kontrastu (nadpisuje role) wygrywa z każdym motywem. Literały poza blokami tokenów
   zostały świadomie: wydruk, plakaty (grafika do druku), tryb wysokiego kontrastu, kreator instalacji
   (`setup.css`, osobna strona bez `app.css`), czerń/biel z przezroczystością (cienie, maski),
   plakietki logotypów i obrazka CAPTCHA (oznaczone „stała”). Pilnuje tego test
   `test_brand_colors_live_only_in_tokens`. Promienie inne niż 4/8/14 px (2, 3, 6 px, pigułki) i skala
   rozmiarów pisma (poza `font-size-base`) nie są tokenami w v1. Kroje dla arabskiego, dewanagari,
   bengalskiego i chińskiego (`:root:lang(…)`) wygrywają z krojem motywu – motyw łaciński nie ma tych
   glifów. Równoważność `classic` sprawdzona porównaniem wszystkich deklaracji po rozwinięciu `var()`
   (przed/po) i zrzutów HTML 15 stron Konkursu #1 (bez różnic poza godzinami renderu).
2. **CSP (§ 4):** origin bucketu był wyłącznie w `img-src`, nie w `style-src`/`font-src`. Strona
   **z motywem** dostaje go w obu (znacznik `{% theme_head %}` ustawia flagę na żądaniu); `script-src`
   bez zmian; strona bez motywu – polityka co do bajtu dawna. Kroje cross-origin wymagają CORS –
   MinIO odpowiada `Access-Control-Allow-Origin` dla każdego originu (sprawdzone w devie), Caddy go
   nie zmienia (`OPERACJE.md` § 28.2).
3. **Prefiks plików:** `themes/<slug>/<wersja>-<sha8>/` zamiast `themes/<slug>/<wersja>/` – usunięcie
   wersji i ponowne wgranie tego samego numeru z inną treścią nie może trafić w cache przeglądarki.
4. **Szablony slotów w bazie** (`ThemeVersion.templates`), nie w buckecie: bucket podałby je jako
   plik, a render potrzebuje ich bez dostępu do storage. Loader paczki powtarza lint przy każdym
   załadowaniu (obrona w głąb); błąd renderu slotu motywu = slot domyślny + wpis w logu, nie 500.
   Silnik slotów ma biblioteki aplikacji (fragmenty `web/_*` dołączane przez slot ładują własne),
   a ograniczenie `{% load %}` dla szablonów paczki egzekwują lint i kompilacja ograniczonym silnikiem.
5. **Podgląd:** wyłącznie parametr `?theme_preview=<podpisany token>` (wersja, konkurs, opcje, ważny
   24 h), bez zapisu w sesji – odnośniki na podglądanej stronie prowadzą już do motywu aktywnego.
   Token z innego konkursu, przeterminowany albo w rękach kogoś, kto nie jest koordynatorem tego
   konkursu, jest ignorowany (strona jak bez parametru).
6. **„Koordynator innego konkursu → 404” (§ 8):** ekran nie ma identyfikatora konkursu (konkurs
   wskazuje domena), więc nie ma „cudzego obiektu”, który mógłby dać 404. Zgodnie z kontraktem
   `apps/web/mixins.py` zła rola daje **403** (koordynator konkursu B pod adresem A przy
   `memberships_enforced`), a 404 dają: wyłączona flaga `themes`, nieznana albo odrzucona wersja
   w POST. Recenzent i uczestnik – 403, gość – 302 na logowanie.
7. **Flaga `themes`** (domyślnie wyłączona) bramkuje wyłącznie **ekran koordynatora i pozycję menu**
   (menu Konkursu #1 bez zmian). Motyw ustawiony komendą działa niezależnie od flagi. Katalog
   platformy nie ma pozycji w menu (odnośnik z ekranu „Motyw serwisu” widzi tylko superkoordynator,
   adres `/coordinator/platform/themes/`).
8. **Akcent marki** (`theme_options.brand_accent`): arkusz `/_theme/overrides.css?v=<wersja>-<kolor>`
   z własnej domeny (bez stylu inline); **nie** nadpisuje `--t-focus` (kontrast fokusu dobiera motyw).
9. **`supports` i zakres stron** (przegląd 4.10.2026, H2): szablony slotów **z paczki** działają
   wyłącznie na stronach **publicznych** – lista dozwolona `apps.themes.runtime.PUBLIC_VIEWS`: strony
   CMS (`wagtail_serve`), `statystyki`, `web:posters`, `web:results`, `web:certificate-verify`.
   Każdy inny ekran (panele `/coordinator/`, `/me/`, `/review/`…, logowanie, rejestracja, reset
   hasła, konto) ma sloty aplikacji – nowy ekran jest panelem, dopóki ktoś go świadomie nie dopisze.
   Tokeny i `theme.css` dostaje strona publiczna, gdy manifest ma `public`, a panel – gdy ma `panels`.
   Ekrany zarządzania motywem (`/coordinator/competition/theme/`, `/coordinator/platform/themes/…`)
   renderują się **zawsze bez motywu**, a superkoordynator może wyłączyć motyw na jedno żądanie
   parametrem `?theme=off` (sprawdzana rola, innym parametr nic nie robi).
10. **Fikstura testowa** leży w `backend/apps/themes/tests/package_example/` (nie `themes/_example/`):
    kontener deweloperski montuje wyłącznie `backend/`. Prawdziwa paczka IQO jest fiksturą
    `backend/apps/themes/tests/fixtures/iqo-quantum-1.0.0.zip` (walidacja bez błędów, `theme_install
    --activate`, render strony głównej w `en` i `ar`, `/me/`).
11. **Nowe napisy** „Koordynator”, „Panel koordynatora” (nagłówek paczki IQO) – w 10 katalogach,
    oznaczone w `apps/themes/slots.py` (`gettext_noop`), bo szablony aplikacji ich nie tłumaczą.

12. **Kontekst szablonów paczki** (przegląd, H1): szablon slotu z paczki **nie** dostaje kontekstu
    strony. Dostaje kopię z listy dozwolonej (`apps/themes/safe_context.py`): napisy, liczby, daty,
    listy i słowniki (`site_root`, języki, `cms_menu`, flagi ról, `registration`, `user`
    = `{is_authenticated, email}`, `request` = `{path, user}`, `settings.cms.SiteSettings` – pola
    jawne ze stopki, `competition` – marka i kontakt, `csrf_token` dla formularza „Wyloguj”),
    pośrednika strony `PageProxy` (tytuł, adres, pola SEO i treści; `{% pageurl %}` w silniku motywu
    go przyjmuje) i pośrednika obrazu `ImageProxy` (wyłącznie `get_rendition` dla `{% image %}`).
    Żadnych obiektów modeli i żadnego obiektu żądania: `{{ page.unpublish }}`,
    `{{ user.set_unusable_password }}`, `competition.participants.all` dają pusty napis.
    Fragmenty **aplikacji** dołączone przez slot (`web/_*`, `cms/_*`, `classic/*`) renderują się
    z pełnym kontekstem (to kod aplikacji). Strona główna: `page`, `hero_slides_visible` (plansze jako
    `{block_type, value}`), `hero_show_intro`, `latest_news`, `news_index`, `edition` (`year_label`),
    `current_stage` (`display_name`, `opens_at`, `deadline_at`); karta aktualności: `item`.
13. **Lint i kontrola wyniku** (przegląd, M1): napisy w cudzysłowach wewnątrz `{{ }}`/`{% %}` nie mogą
    zawierać `<`, `>`, `` ` ``, `javascript:`/`data:` (Django wstawia literały bez escapowania);
    reguły HTML sprawdzane też na tekście sklejonym bez znaczników (`<scr{# #}ipt>`); filtr `dict_get`
    zablokowany. Wynik renderu slotu paczki jest sprawdzany (`<script>`, ramki, `<meta>`, `<link>`,
    `<style>`, `on*=`, `javascript:`, formularz pod adres bezwzględny) – trafienie = slot aplikacji
    i jeden wpis w logu na wersję i slot. **Granicą bezpieczeństwa pozostaje CSP**; lint i kontrola
    wyniku zamykają to, czego CSP nie obejmuje. Uwaga: treść edytora w slocie (np. film osadzony
    w `hero_text`) też jest sprawdzana – taki slot wraca do domyślnego.
14. **Pamięć wersji** (przegląd, M2): w pamięci procesu wyłącznie wersje znalezione; arkusz akcentu
    odpowiada gościowi tylko dla wersji aktywnej w konkursie (koordynatorowi – dla każdej poprawnej,
    na potrzeby podglądu). **SVG** (L1): lista dozwolona elementów i atrybutów w przestrzeni SVG,
    `<style>` z elementem w środku wypada, plik spoza UTF-8 – błąd. **Storage** (L3): pliki wgrywane
    przed transakcją i sprzątane przy jej porażce; usunięcie wersji kasuje pliki po zatwierdzeniu.

Znane luki v1: djcms (`dj.`) bez motywów; brak E2E Playwright (render en/ar sprawdzany klientem
testowym, strona główna IQO obejrzana w przeglądarce na devie z prawdziwym MinIO – krój Space Grotesk
załadowany, bez błędów CSP); warianty układów rysuje wyłącznie `theme.css` motywu; brak edytora
tokenów w panelu (zmiana = nowa wersja paczki).

