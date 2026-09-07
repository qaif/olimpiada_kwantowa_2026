# Pliki binarne starej strony — co przenieść

Katalog źródłowy (tylko do odczytu): `C:\Users\SPCX\Documents\strona oli,mpady kwantowj\`

## Podsumowanie

Stara strona zawiera **dwa unikalne pliki binarne** (regulamin w PDF i DOCX), zduplikowane
w dwóch lokalizacjach. **Nie ma żadnego logotypu, zdjęcia, ikony ani pliku fontu** — cała
identyfikacja wizualna jest zrobiona w CSS (kółko z kropką jako „atom”, `•` w znaczniku marki).

## Do wgrania

| Plik źródłowy (ścieżka względna) | Rozmiar | MD5 | Cel w nowym portalu |
|---|---|---|---|
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.pdf` | 233 536 B | `c804d36993d5cd66c307cee07cbd312f` | `/regulamin/`, plik „PDF do druku” — **wgrywany przez `seed_legacy_content`** |
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.docx` | 53 404 B | `5e38389511b369e6c1fe70cdbc427f85` | j.w., „Wersja źródłowa (DOCX)” — **wgrywana przez `seed_regulamin`** |

Stan wgrany: kopie w `backend/apps/cms/fixtures/legacy/pdf/` (PDF) i `…/fixtures/regulamin/` (DOCX).

## Dokumenty spoza starej strony (dostarczone przez organizatora)

Trzy PDF-y nie pochodzą z WordPressa — organizator przekazał je osobno, już po inwentaryzacji.
Leżą w `backend/apps/cms/fixtures/legacy/pdf/`, wgrywa je `manage.py seed_legacy_content`,
a wyciągnięty z nich tekst (`pypdf`) jest w `docs/import/pdf-text/`.

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
| `output/pdf/Regulamin-Olimpiady-Kwantowej.pdf` | wyjście `scripts/build_regulamin.py`, identyczne z plikiem w motywie |
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
- **logotypy partnerów i sponsorów** — brak; strona `/partnerzy/` nie wymienia nawet nazw;
- **zdjęcia do galerii** — brak (strona `/galeria/` to jedno zdanie zapowiedzi);
- **pliki fontów** — motyw prosi o `Inter` bez wczytywania webfontu, więc w praktyce
  renderuje się systemowym `system-ui`;
- **favicon / og:image** — brak.
