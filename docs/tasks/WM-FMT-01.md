# WM-FMT-01: Materiały z warsztatów – pliki Office, Markdown, tekst i kod

## 0. Cel i granice

Prośba organizatora z 9.10.2026: „dodaj też funkcję dodawania do warsztatów innych materiałów niż
filmy – plików, wszystkich plików Office, plików Markdown, kodu”. Decyzja organizatora z tego samego
dnia: **„nie zawężaj listy, co najwyżej ją rozszerz”** – żaden format przyjmowany dotąd nie przestaje
być przyjmowany ani nie zmienia zachowania na bardziej restrykcyjne, a przy wątpliwości „przyjąć czy
odrzucić” nowy format – przyjmujemy.

Zadanie **rozszerza** istniejącą aplikację `apps/workshop_materials` (prośba z 24.09.2026). Nie
zmienia drogi pliku (przeglądarka → MinIO, sprawdzenie „po fakcie” na pierwszych
`HEADER_PROBE_BYTES`, potem ClamAV), limitów (plik: 100 MB = limit strumienia ClamAV), dostępu
(`access.can_view`). Model dostaje jedno pole – `charset` (kodowanie tekstu, migracja
`workshop_materials.0002`, `AddField`, pusty domyślnie); `file_format` nie ma `choices`, więc nowe
formaty migracji nie wymagają.

Czego zadanie **nie** robi:

- nie dodaje filmów w innych kontenerach (MOV/MKV nadal z podpowiedzią przepakowania),
- nie renderuje HTML-a ani SVG jako strony – nigdy (patrz § 3),
- nie dokłada zależności Pythona ani nowego JavaScriptu (patrz § 4).

## 1. Nowe formaty

Format rozpoznajemy **po treści** – rodzina bajtów – a rozszerzenie wybiera nazwę i `Content-Type`
w obrębie rodziny (tak jak dotąd w rodzinie ZIP). Niezgodność rozszerzenia z rodziną = odrzucenie.

| Rodzina (po bajtach) | Sygnatura | Rozszerzenia |
|---|---|---|
| `cfb` (OLE/Compound File, stary Office) | `D0 CF 11 E0 A1 B1 1A E1` | `doc`, `dot`, `xls`, `xlt`, `ppt`, `pps`, `pot` |
| `zip` (jak dotąd) | `PK 03 04` | dotychczasowe + `docm`, `dotx`, `dotm`, `xlsm`, `xlsb`, `xltx`, `xltm`, `pptm`, `ppsx`, `ppsm`, `potx`, `potm`, `odg`, `epub` |
| `rtf` | `{\rtf` | `rtf` |
| `text` (brak sygnatury) | patrz niżej | tekst i dane: `md` (alias `markdown`), `txt`, `csv`, `tsv`, `tex`, `bib`, `json`, `yaml`, `yml`, `toml`, `xml`, `rst`; kod: `py`, `c`, `h`, `cpp`, `hpp`, `cc`, `java`, `js`, `ts`, `rs`, `go`, `jl`, `r`, `m`, `qasm`, `qs`, `sh`, `sql`, `cs`, `kt`, `rb`, `php`, `lua`, `hs`, `scala`, `swift`, `f90`, `html` (alias `htm`), `css`, `svg` |

`ipynb` zostaje w rodzinie `json` (obiekt JSON po opcjonalnym BOM) – **bez zmiany** reguły.

**Rodzina `text`.** Plik tekstowy nie ma sygnatury, więc „rozpoznanie po treści” to: próbka nagłówka
(pierwsze `HEADER_PROBE_BYTES`) po zdjęciu BOM-u UTF-8 jest **poprawnym UTF-8** (znak urwany na
granicy próbki nie jest błędem), **nie zawiera bajtu NUL** i ma najwyżej 5 % znaków sterujących
(poza tabulacją, końcami wierszy i wysuwem strony). Plik binarny z rozszerzeniem `.py` (program, obraz,
archiwum) – odrzucony. Kod i Markdown w innym kodowaniu – odrzucony z podpowiedzią, jak zapisać go
w UTF-8.

**CSV, TSV i TXT – także inne kodowania** (decyzja organizatora z 9.10.2026, „przy wątpliwości
przyjmij”): polski Excel zapisuje „CSV (rozdzielany przecinkami)” w Windows-1250, a Notatnik
„Unicode” to UTF-16 z BOM-em. Dla tych trzech rozszerzeń `formats.detect_text_encoding` przyjmuje
kolejno: UTF-16 z BOM-em (LE/BE; NUL-e są tu częścią znaków, sprawdzany jest odkodowany tekst),
UTF-8, a w końcu tekst 8-bitowy – bez NUL i z najwyżej 5 % bajtów sterujących (bajty 0x80–0x9F to
w Windows-1250 litery, więc się nie liczą). Kodowanie zapisujemy w `WorkshopMaterial.charset`
(`utf-8`, `utf-16`, `windows-1250` – 8-bitowy tekst nazywamy Windows-1250 jako najczęstszy w Polsce;
ISO-8859-2 różni się kilkoma literami i w podglądzie mogą wyjść niedokładnie). Plik binarny z NUL-ami
(bez BOM-u UTF-16) – odrzucony.

**Makra.** Pliki z makrami (`docm`, `xlsm`, `pptm`, szablony `*m`, `xlsb` i stare formaty binarne
`doc`/`xls`/`ppt`) są **przyjmowane** (decyzja organizatora). ClamAV skanuje je jak każdy plik;
strona materiału pokazuje neutralną informację „plik może zawierać makra – program biurowy zapyta
o ich włączenie”. Etykieta formatu mówi „z makrami”.

## 2. Limity i skan

Bez zmian: każdy nowy format to **plik** (`MaterialKind.FILE`) – limit `formats.file_max_bytes()`
(100 MB, nie więcej niż `CLAMAV_STREAM_MAX_BYTES`) i skan ClamAV-em przed publikacją.

## 3. Serwowanie

- Wszystkie formaty tekstowe dostają `Content-Type: text/plain; charset=utf-8` (Markdown:
  `text/markdown; charset=utf-8`; CSV/TSV/TXT – `charset` rozpoznany przy wgrywaniu: `utf-16` albo
  `windows-1250`) i **zawsze** `Content-Disposition: attachment` – także `html`
  i `svg`, które nigdy nie są serwowane jako strona ani obraz (XSS). Oba nagłówki wchodzą do podpisu
  adresu (`ResponseContentType`/`ResponseContentDisposition` – jak dotąd dla PDF), więc widz ich nie
  podmieni.
- `X-Content-Type-Options: nosniff` i `Content-Security-Policy: … sandbox` dokłada Caddy każdej
  odpowiedzi magazynu (`deploy/Caddyfile`, blok `{$S3_PUBLIC_ADDRESS}`) – podpisany adres S3 nie
  umie ustawić `nosniff` (S3 przyjmuje tylko `response-content-type`, `-disposition`, `-language`,
  `-expires`, `-cache-control`, `-encoding`), więc nagłówek jest na poziomie proxy, nie podpisu.
- Office/RTF/EPUB: właściwy typ MIME, `attachment`.

## 4. Podgląd na platformie

Strona materiału (`/warsztaty/materialy/<id>/`) pokazuje podgląd pliku tekstowego do **1 MB**
(`preview.PREVIEW_MAX_BYTES`, nadpisywalne `WORKSHOP_PREVIEW_MAX_KB`); większy – tylko pobranie
z informacją. Podgląd czyta obiekt z magazynu po stronie serwera; przeglądarka nie dostaje adresu
pliku do podglądu.

- **Markdown** – istniejący konwerter `apps.problem_translations.markup.render` (TR-01): **najpierw**
  cały tekst przechodzi przez `html.escape`, dopiero potem dokładane są znaczniki z zamkniętej listy
  (nagłówki, akapity, listy, cytaty, tabele, pogrubienie/kursywa, kod). Surowy HTML (`<script>`,
  `<img onerror=…>`) zostaje **widocznym tekstem**, odnośników i obrazów konwerter nie tworzy wcale,
  więc `javascript:` nie ma gdzie trafić. Formuły `$…$`/`$$…$$` składa KaTeX z plików statycznych
  TR-01 (`trust: false`), bez CDN-u i bez skryptu inline.
- **Kod i tekst** (w tym `html`, `svg`, `csv`, `json`) – `<pre><code>` z autoescape szablonu Django.
  Podświetlania składni nie ma: Pygments jest w środowisku wyłącznie jako zależność pytesta (ekstra
  `dev`), a nowej zależności ani JavaScriptu zadanie nie dokłada.
- Dostęp do podglądu = dostęp do pobrania: ten sam widok, te same bramki (`MaterialsAccessMixin`,
  `services.visible_materials`). Podgląd liczy się jako wyświetlenie (jak pobranie); podgląd
  koordynatora (`…/materials/<id>/preview/`) pokazuje tę samą stronę także dla szkicu i nie liczy się.
- Odpowiedź ma `Cache-Control: no-store, private` (jak cała strona materiału).

## 5. Formularz koordynatora

`accept=` pola pliku zawiera wszystkie rozszerzenia z listy i aliasy (`.jpeg`, `.markdown`, `.htm`);
podpowiedź pod polem i komunikat odrzucenia wymieniają formaty **pogrupowane** (dokumenty, tekst
i dane, kod, obrazy, archiwa). Komunikaty po polsku – panel koordynatora nie ma gettextu.

## 6. Testy

`apps/workshop_materials/tests/test_formats.py`, `test_preview.py`, `test_viewing.py`:

- CFB `doc`/`xls`/`ppt`, `rtf`, OOXML z makrami i szablony, `epub`/`odg` (rodzina ZIP),
- tekst UTF-8 z BOM i bez, znak urwany na granicy próbki, binarny plik z rozszerzeniem `.py`,
  kod/Markdown w UTF-16 i Windows-1250 odrzucone, CSV w Windows-1250 („Łódź”) i UTF-16 z BOM-em
  przyjęte z właściwym `charset` (pobranie i podgląd), binarny CSV z NUL-ami odrzucony, niezgodność rozszerzenia z rodziną (CFB jako `.docx`, ZIP jako
  `.doc`, PDF jako `.md`),
- dotychczasowe formaty bez zmian (regresja: `ipynb` zaczynający się od `{\rtf…` itp.),
- `html`/`svg`/`py` serwowane jako `attachment` z `text/plain; charset=utf-8`,
- podgląd Markdown: `<script>`, `onerror=`, `javascript:` nie stają się znacznikami ani atrybutami;
  podgląd kodu – escape; limit podglądu; podgląd tylko dla mających dostęp (anonim → logowanie,
  obcy → 403, szkic → 404); podgląd koordynatora szkicu.

## 7. Dokumentacja

PODRĘCZNIK-ORGANIZATORA § 4.11 (lista formatów, makra, podgląd), CHANGELOG `[Unreleased]`.
