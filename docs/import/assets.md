# Pliki binarne starej strony — co przenieść

Katalog źródłowy (tylko do odczytu): `C:\Users\SPCX\Documents\strona oli,mpady kwantowj\`

## Podsumowanie

Stara strona zawiera **dwa unikalne pliki binarne** (regulamin w PDF i DOCX), zduplikowane
w dwóch lokalizacjach. **Nie ma żadnego logotypu, zdjęcia, ikony ani pliku fontu** — cała
identyfikacja wizualna jest zrobiona w CSS (kółko z kropką jako „atom”, `•` w znaczniku marki).

## Do wgrania

| Plik źródłowy (ścieżka względna) | Rozmiar | MD5 | Cel w nowym portalu |
|---|---|---|---|
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.pdf` | 233 536 B | `c804d36993d5cd66c307cee07cbd312f` | `DocumentPage.attachment` (regulamin) — **importowany osobno** |
| `wp-content/themes/olimpiada-kwantowa/assets/documents/Regulamin-Olimpiady-Kwantowej.docx` | 53 404 B | `5e38389511b369e6c1fe70cdbc427f85` | j.w., wersja edytowalna — **importowana osobno** |

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
