# MSG-EXPORT-01: Eksport odbiorców komunikatu do Excela

## 0. Cel i granice

Prośba organizatora z 8.10.2026: „dodaj też przycisk do eksportu imię nazwisko mail do Excela”.
Na ekranie **Komunikacja → Komunikaty** (`/coordinator/messages/`) przycisk **„Eksportuj do Excela”**
pobiera plik `.xlsx` z odbiorcami **aktualnie wybranej grupy** – z jej parametrami (etap,
województwo/region, szkoła, klasa, warsztat, „także uczestnicy poprzednich edycji”). Kolumny:
**Imię, Nazwisko, E-mail**. Zastosowanie: zaproszenie (np. na warsztaty) wysyłane spoza platformy.

Czego zadanie **nie** robi:
- nie dodaje modeli, migracji ani nowej aplikacji – to rozszerzenie istniejącego ekranu
  (`apps.accounts.messaging` + `apps.web`),
- nie eksportuje grupy „wklejona lista adresów” (adresy spoza systemu, bez imion – przycisk jest przy
  niej ukryty, a żądanie kończy się błędem formularza),
- nie dodaje CSV ani innych kolumn (szkoła, klasa, telefon) – pełny eksport uczestników edycji jest
  w `/coordinator/export/`.

## 1. Jedna ścieżka rozstrzygania grupy

- `recipient_users(group, **parametry)` – **jedyne** miejsce, które zamienia grupę w zapytanie
  o konta: zakres konkursu (`_competition_for`), zakres edycji, `DELIVERABLE` (aktywne,
  z potwierdzonym adresem). Każda grupa to `User.objects.filter(DELIVERABLE, pk__in=<podzapytanie>)`,
  więc złączenia nie mnożą wierszy.
- `resolve_recipients` (wysyłka) = adresy z `recipient_users` (bez zmian w zachowaniu).
- `recipient_rows` (eksport) = trójki (imię, nazwisko, adres) z `recipient_users`, adres
  znormalizowany tak samo jak w wysyłce, jeden wiersz na adres; posortowane po nazwisku i imieniu.
  Zbiór adresów eksportu jest **z definicji** zbiorem adresów wysyłki.

## 2. Ekran

- Przycisk to trzeci przycisk tego samego formularza (`action=export`, `formnovalidate` – temat
  i treść nie są potrzebne). Walidacja parametrów grupy **tą samą formą** co podgląd
  (`BroadcastForm(for_export=True)`: temat i treść opcjonalne); błąd → strona z błędami, bez pliku.
- Rola: `CoordinatorRequiredMixin` (koordynator **tego** konkursu); zakres: `request.competition`.
- Plik: `apps.core.exports.xlsx_response` (pogrubiony, zamrożony nagłówek; ochrona przed formułami
  `_safe_text`). Nazwa: `odbiorcy-<grupa>-<RRRRMMDD-GGMM>.xlsx`.
- Audyt **przed** oddaniem pliku: `export.generated`, obiekt = konkurs,
  `{"kind": "broadcast_recipients", "format": "xlsx", "group", "target", "rows"}` – nigdy adresy.
- Bez inline JS: `broadcast-groups.js` chowa przycisk przy grupie „wklejona lista”.
- Napisy przez gettext; tłumaczenia w `apps/web/locale/<język>/` (10 języków).

## 3. Testy

`apps/accounts/tests/test_messaging.py`: zgodność `recipient_rows` z `resolve_recipients` dla kilku
grup, „wszyscy uczestnicy i nauczyciele” bez duplikatów i z imionami, wklejona lista → pusto.
`apps/web/tests/test_coordinator_messages.py`: plik i jego zawartość, audyt bez danych, recenzent →
403, cudzy konkurs nie przecieka, brak parametru → strona z błędem, wklejona lista → komunikat.
