# Pliki binarne starej strony — co przenieść

Katalog źródłowy (tylko do odczytu): `C:\Users\SPCX\Documents\strona oli,mpady kwantowj\`

## Podsumowanie

Stara strona zawiera **dwa unikalne pliki binarne** (regulamin w PDF i DOCX), zduplikowane
w dwóch lokalizacjach. **Nie ma żadnego logotypu, zdjęcia, ikony ani pliku fontu** — cała
identyfikacja wizualna jest zrobiona w CSS (kółko z kropką jako „atom”, `•` w znaczniku marki).

Oba pliki regulaminu ze starej strony są już **wycofane** — organizator przekazał nowszą wersję
dokumentu (patrz sekcja niżej).

## Regulamin — pliki wycofane ze starej strony

Oba pliki regulaminu ze starej strony (wersja 1.0 z **18 sierpnia 2026**) są **nieaktualne** i nie
ma ich już w repozytorium. Zastąpiła je wersja 1.0 z **2 września 2026** przekazana przez
organizatora — inny dokument mimo tego samego numeru wersji (trzy etapy zamiast dwóch, § 12 i § 13
przepisane, zniknął akapit „WAŻNY STATUS PRAWNY” ze strony tytułowej).

| Plik ze starej strony (ścieżka względna) | Rozmiar | MD5 | Status |
|---|---|---|---|
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.pdf` | 233 536 B | `c804d36993d5cd66c307cee07cbd312f` | **wycofany** — usunięty z `fixtures/legacy/pdf/` |
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.docx` | 53 404 B | `5e38389511b369e6c1fe70cdbc427f85` | **wycofany** — podmieniony w `fixtures/regulamin/` |

## Regulamin — obowiązująca wersja (wersja 1.0 z 2 września 2026)

Wszystkie trzy postacie dokumentu leżą w jednym katalogu `backend/apps/cms/fixtures/regulamin/`
i wgrywa je **jedna** komenda, `manage.py seed_regulamin`. To nie jest kwestia porządku: kiedy PDF
wgrywała jedna komenda, a treść strony pochodziła z .docx wgrywanego przez drugą, pierwsza
aktualizacja dokumentu dała stronę z nowym tekstem i plik do pobrania ze starym.

| Plik | Rola |
|---|---|
| `Regulamin-Olimpiady-Kwantowej.pdf` | „PDF do druku” — wersja do cytowania, pierwszy plik na stronie |
| `Regulamin-Olimpiady-Kwantowej.docx` | „Wersja źródłowa (DOCX)” — oryginał redakcyjny organizatora |
| `regulamin-mammoth.html` | konwersja .docx → HTML (mammoth); z niej powstaje treść strony |
| `Regulamin-Olimpiady-Kwantowej.txt` | wyciąg tekstu z PDF-u (`pypdf`); wzorzec kompletności w testach |

**PDF powstaje z .docx po przyjęciu zmian śledzonych.** Plik organizatora ma sześć nieprzyjętych
poprawek (`<w:ins>`/`<w:del>`), w tym podmianę „dwuetapowy” → „trzyetapowy” w podtytule.
LibreOffice renderuje takie poprawki jako widoczny znacznik korektorski, więc konwersja wprost
z oryginału dawała na stronie tytułowej „trzy~~dwu~~etapowy”. Procedura:

```bash
# 1. kopia .docx z przyjętymi zmianami: <w:del> znika w całości, <w:ins> traci opakowanie
#    (oryginał zostaje nietknięty — to jego wgrywamy jako „wersję źródłową”)
# 2. konwersja tej kopii:
"C:/Program Files/LibreOffice/program/soffice.exe" --headless --convert-to pdf \
    --outdir <katalog> <kopia.docx>
# 3. wyciąg tekstu (pypdf) do Regulamin-Olimpiady-Kwantowej.txt
```

## Dokumenty spoza starej strony (dostarczone przez organizatora)

Trzy PDF-y nie pochodzą z WordPressa — organizator przekazał je osobno, już po inwentaryzacji.
Leżą w `backend/apps/cms/fixtures/legacy/pdf/`, wgrywa je `manage.py seed_legacy_content`,
a wyciągnięty z nich tekst (`pypdf`) leży obok nich, w `backend/apps/cms/fixtures/legacy/pdf-text/`.
Nie w `docs/`, bo do kontenera trafia wyłącznie `backend/`, a `apps/cms/tests/test_pdf_content.py`
porównuje z tym wyciągiem treść stron.

| Plik | Stron | Rozmiar | MD5 | Strona w portalu |
|---|---|---|---|---|
| `Polityka-RODO-Olimpiada-Kwantowa.pdf` | 3 | 86 060 B | `c9c155ed6468767c57d9e8ecf9cc347e` | `/rodo/` |
| `Standardy-ochrony-maloletnich-Olimpiada-Kwantowa.pdf` | 4 | 96 806 B | `95bded2a0ef7b7f4e8e112c309acd31f` | `/standardy-ochrony-maloletnich/` |
| `Sklad-komitetow-Olimpiady-Kwantowej.pdf` | 1 | 63 119 B | `cd15134e0afee04ab7c292c692864659` | `/komitety/` |

PDF ze składem komitetów jest powodem, dla którego `/komitety/` przestało być szkicem: nazwiska
i zakresy odpowiedzialności są w nim podpisane przez organizatora, a strona jest jego wersją
czytelną w przeglądarce. Treść `fixtures/legacy/komitety.md` przepisano z tego PDF-u.

## Duplikaty (identyczne MD5 — nie wgrywać drugi raz)

| Ścieżka | Uwaga |
|---|---|
| `output/pdf/Regulamin-Olimpiady-Kwantowej.pdf` | wyjście `scripts/build_regulamin.py`, identyczne z plikiem w motywie — **wersja z 18 sierpnia, wycofana** |
| `output/docx/Regulamin-Olimpiady-Kwantowej.docx` | j.w. |

## Do pominięcia

| Ścieżka | Powód |
|---|---|
| `tmp/regulamin-render/page-1..17.png` | podglądy stron PDF z pracy nad składem (17 szt.) |
| `tmp/regulamin-render-v2/`, `-v3/`, `-v4/` | starsze wersje robocze regulaminu + po 13 PNG podglądu każda; **PDF-y w tych katalogach różnią się od wersji finalnej — nie mylić z wersją 1.0** |
| `wp-content/themes/olimpiada-kwantowa/style.css` | motyw WordPressa; nowy portal ma własny design (patrz sekcja „Identyfikacja wizualna” w inwentarzu) |
| `wp-content/themes/olimpiada-kwantowa/assets/main.js` | 267 B, wyłącznie przełącznik menu mobilnego |
| `.env`, `.env.example`, `docker-compose.yml` | konfiguracja infrastruktury WordPressa; **`.env` zawiera hasła deweloperskie — nie kopiować** |

## Czego brakuje, a będzie potrzebne

- **logotyp Olimpiady Kwantowej** — nie istnieje w żadnej formie pliku;
- **logotypy partnerów i sponsorów** — ~~brak~~ **dostarczone przez organizatora poza eksportem
  WordPressa** (wrzesień 2026): sześć logotypów partnerów plus znak Fundacji Quantum AI leży
  w `backend/apps/cms/fixtures/partners/` z manifestem `partners.json` i wgrywa je
  `manage.py seed_partners`. Pliki przyszły w postaci „do druku” — jeden w przestrzeni CMYK,
  jeden o szerokości 8082 px — więc komenda je normalizuje (`apps/cms/images.py`).
  `PartnerBlock.logo` zostaje opcjonalny: wpis bez logotypu nadal pokazuje kółko z inicjałami;
- **zdjęcia do galerii** — brak (strona `/galeria/` to jedno zdanie zapowiedzi);
- **pliki fontów** — motyw prosi o `Inter` bez wczytywania webfontu, więc w praktyce
  renderuje się systemowym `system-ui`;
- **favicon / og:image** — brak.
