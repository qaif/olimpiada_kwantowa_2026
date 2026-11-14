# PUB-02: Załączniki (pliki danych) do zadań

## 0. Cel

Zadanie D2 Etapu I każe uczestnikom pobrać „z załączników do tego zadania” pliki `jony2.npy`,
`jony3.npy`, `jony4.npy`. Załącznik to plik danych przy treści zadania, jawny dokładnie wtedy,
kiedy treść (PUB-01).

## 1. Model

1. `competitions.ProblemAttachment` (migracja `competitions.0038_problem_attachment`, nowa tabela):
   zadanie, plik (prywatny storage `private_media` – ten sam, co PDF treści; nazwa obiektu losowa
   `problems/attachments/<uuid>.<ext>`), oczyszczona nazwa do pobrania (`filename`, unikalna
   w zadaniu), opis, kolejność, rozmiar, SHA-256, utworzono/przez (`created_by`, `SET_NULL` –
   reguła scalania kont domyślna MOVE).
2. Biała lista rozszerzeń: `.npy .npz .csv .txt .json .dat .h5 .hdf5 .ipynb .py .zip .pdf .png .jpg
   .jpeg` (bez HTML, SVG, JS, plików wykonywalnych); limit 50 MB na plik; pusty plik – odmowa.
   Reguły i serwis: `apps/competitions/attachments.py`.

## 2. Bramka i pobieranie

1. `GET /api/competitions/problems/<pk>/attachments/<id>/` (`competitions:problem-attachment`).
   Warunek jawności to ta sama funkcja, co dla PDF-u treści (`competitions.api.problem_content_visible`):
   zadanie opublikowane i etap otwarty dla tej osoby (okna TZ-01) albo koordynator. Inaczej 404.
   Zadanie cudzego konkursu, załącznik innego zadania – 404. Adres stoi w bramce nadzoru
   (`proctoring.middleware.GATED_VIEWS`, test `test_gate.py`).
2. Odpowiedź: `application/octet-stream`, `Content-Disposition: attachment; filename=…`,
   `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`.
3. Listy załączników stoją przy odnośniku do PDF-u treści: karta zadania w panelu uczestnika,
   strona „Zadania” (Wagtail i djcms – pole `attachments` w `ProblemDTO`), archiwum uczestnika.
   Jedno zapytanie (`prefetch_related`) na listę zadań, nie na kartę.

## 3. Zarządzanie

1. Panel koordynatora: edycja zadania → sekcja „Załączniki” (dodaj, opis i kolejność, usuń).
   Rola sprawdzana w serwisie (`attachments.require_coordinator`). Audyt:
   `problem.attachment_added|updated|replaced|deleted`. Wgranie z panelu podlega limitowi Caddy
   (`MAX_UPLOAD_MB`); większe pliki – komendą.
2. Komenda: `manage.py add_problem_attachment --competition <slug> --stage <id|nazwa> --number <n>
   [--description …] [--replace] <plik> [<plik> …]` – idempotentna po (zadanie, nazwa, SHA-256).
   Ta sama nazwa z inną treścią: błąd, z `--replace` podmiana (audyt).

## 4. RODO i kopie

Załączniki nie są danymi osobowymi – rejestr czynności bez zmian. Kopia nocna (`scripts/backup.sh`)
lustruje cały bucket `submissions`, w którym leży storage `private_media` – obejmuje też załączniki.

## 5. Dalsze kroki

1. Notatniki (`apps/notebooks`, QC-01): udostępnić załączniki zadania w laboratorium notatnika
   (np. kopiowane do katalogu roboczego przy starcie notatnika zadania) – dziś uczestnik pobiera je
   ręcznie i wgrywa do notatnika.
2. Tłumaczenie dla ucznia (TR-01): lista załączników także na stronie tłumaczenia zadania.
