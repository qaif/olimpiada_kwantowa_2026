# VISA-01: Listy zapraszające do wizy – wnioski, decyzja, weryfikacja i unieważnienie

## 0. Cel i granice (polecenie organizatora, 4.10.2026)

Członkowie delegacji krajowych na finał olimpiady międzynarodowej (IQO), którzy potrzebują wizy,
dostają od organizatora **oficjalny, sprawdzalny** list zapraszający w PDF – do pokazania w konsulacie.

Zadanie jest **przyrostem na LOG-01** (`docs/tasks/LOG-01.md`, aplikacja `apps.delegation_logistics`),
a nie osobną aplikacją. LOG-01 ma już: dane dokumentu podróży członka delegacji wpisywane przez
opiekuna drużyny (szyfrowane w bazie), przydział „oficer logistyki”, rejestr listów z numerem
`PREFIKS/ROK/NNNN`, PDF z zaszyfrowanej migawki, pobranie listu przez opiekuna, retencję po finale,
eksport danych konta i wiersz rejestru czynności. Pierwotny plan (osobna aplikacja `apps/visas`
z własnym modelem paszportu) został porzucony decyzją koordynatora prac – dwa modele tych samych
danych paszportowych to dwa miejsca do zabezpieczenia, usunięcia i opisania w rejestrze.

| Wymaganie | LOG-01 (było) | VISA-01 (dochodzi) |
|---|---|---|
| dane osoby (nazwisko z paszportu, obywatelstwo, data urodzenia, nr i ważność paszportu) | ✔ szyfrowane, wpisuje opiekun | – |
| przyjazd/wyjazd | ✔ (sekcja „Przyjazd i wyjazd”) | – |
| numer listu, rejestr, PDF z migawki, podpisy, pieczęć | ✔ | język listu, QR, kod weryfikacyjny |
| wniosek opiekuna → decyzja (zatwierdź / odrzuć z powodem) → list | ✗ (list wystawiał tylko oficer z własnej inicjatywy) | ✔ |
| powiadomienie opiekuna o decyzji w jego języku | ✗ | ✔ |
| lista wniosków z filtrami (kraj, stan), decyzje hurtowe, CSV | ✗ | ✔ |
| publiczna weryfikacja `/visa/verify/<kod>/` | ✗ (stopka: „skontaktuj się z organizatorem”) | ✔ |
| unieważnienie listu | ✗ | ✔ |
| retencja, usunięcie konta, eksport, rejestr czynności | ✔ | doprecyzowanie (wnioski, strona weryfikacji) |

Czego zadanie **nie** robi: nie zmienia bramki LOG-01, nie dodaje nowej flagi (funkcja działa tam,
gdzie logistyka finału: `onsite_logistics` + tryb `DELEGATIONS`), nie zmienia retencji LOG-01
(30 dni po finale, ustawienie finału – krócej niż 90 dni z pierwotnego zamówienia, więc ostrzej),
nie dodaje zależności.

## 1. Role

| Kto | Co może |
|---|---|
| opiekun drużyny (`DelegationLeader` w bieżącej edycji) | złożyć wniosek o list imienny dla członka **swojej** delegacji (uczeń, opiekun, gość) z kompletnym dokumentem podróży; wycofać wniosek oczekujący; pobrać ważny list swojej delegacji |
| oficer logistyki (przydział `OFFICER`) | lista wniosków (filtry kraj/stan), zatwierdzenie (= wystawienie listu) i odrzucenie z powodem – pojedynczo i hurtowo; CSV; unieważnienie listu z powodem; dotychczasowe wystawienie listu bez wniosku (LOG-01) |
| koordynator bez przydziału | nic nowego – nie widzi danych osób (LOG-01 § 2) |
| każdy z kodem z listu (konsulat) | strona weryfikacji: numer, data, stan (ważny/unieważniony), wydarzenie i daty, imię i nazwisko oraz obywatelstwo osób z listu |

**Decyzja: wniosek składa wyłącznie opiekun drużyny, nie sam członek delegacji.** W trybie delegacji
dane członków (także dokument podróży) wpisuje opiekun – członek delegacji nie ma w LOG-01 ekranu
logistyki, uczniowie są zwykle niepełnoletni, a konto ucznia bywa jeszcze nieuruchomione. Drugie
wejście do najwrażliwszych danych (z innym zestawem uprawnień) nie jest warte wygody; osoba widzi
swoje dane i wnioski w eksporcie danych konta (art. 15/20 RODO).

**Decyzja: zatwierdza oficer logistyki, nie każdy koordynator.** Zatwierdzenie wystawia list z numerem
paszportu – czyli dotyka danych, które w LOG-01 widzi wyłącznie oficer.

## 2. Model (migracja `delegation_logistics.0002`)

- `LetterRequest` – wniosek: delegacja, członek (`DelegationMember`, `CASCADE` – znika z danymi
  osoby przy retencji, wypisaniu i usunięciu konta), język listu, stan `PENDING` / `APPROVED` /
  `REJECTED` / `WITHDRAWN`, kto i kiedy złożył, kto i kiedy rozstrzygnął, powód odrzucenia (≤ 500
  znaków), wystawiony list. Więz bazy: jeden **oczekujący** wniosek na osobę.
- `InvitationLetter` – nowe pola: `verification_code` (12 znaków z alfabetu kodów dyplomów, losowy,
  unikalny; stare wiersze dostają kod w migracji), `language`, migawka wydarzenia (`event_name`,
  `event_city`, `event_starts_on`, `event_ends_on` – strona weryfikacji pokazuje to, co stało na
  papierze, a nie dzisiejsze ustawienia finału), `revoked_at`, `revoked_by`, `revoke_reason`.

## 3. Przepływ

```
opiekun: „Poproś o list” (wybrane osoby z kompletnym dokumentem, język)
   → PENDING ──oficer: „Zatwierdź i wystaw”──→ APPROVED + list imienny (nowy numer i kod)
            ──oficer: „Odrzuć” + powód──────→ REJECTED   (opiekun może złożyć nowy wniosek)
            ──opiekun: „Wycofaj”────────────→ WITHDRAWN
list ──oficer: „Unieważnij” + powód──→ unieważniony (strona weryfikacji: „unieważniony”; PDF nie do pobrania)
```

- Zatwierdzenie woła `letters.issue_letter` LOG-01 (świeży odczyt danych, numer pod blokadą finału).
  Wcześniejszy **ważny list imienny** tej samej osoby jest przy tym unieważniany z powodem „zastąpiony
  listem …” – dwa ważne listy jednej osoby z różnymi numerami paszportu to pytanie konsulatu, na które
  organizator nie chce odpowiadać.
- Decyzje hurtowe: każdy wniosek osobno (osobna transakcja); wniosek, którego nie da się zatwierdzić
  (brak danych, finał bez dat), zostaje oczekujący, a ekran mówi który i dlaczego.
- Powiadomienia: **jeden list na opiekuna na decyzję** (przy decyzji hurtowej – zbiorczy), do wszystkich
  czynnych opiekunów delegacji, każdy w języku odbiorcy (`language_for`). Treść: imiona i nazwiska,
  numery listów, powody odrzucenia – bez danych paszportowych.

## 4. Język listu

Opiekun wybiera język z języków interfejsu konkursu, **ograniczonych do pisanych alfabetem łacińskim
albo cyrylicą** (`en`, `pl`, `es`, `fr`, `pt`, `ru`, `id`): krój DejaVu w dokumentach nie ma znaków
chińskich, dewanagari i bengalskich, a arabski wymaga kształtowania i kierunku od prawej – list
w tych językach wyszedłby jako prostokąty. Angielski jest zawsze dostępny i domyślny. Teksty listu
w siedmiu językach są w kodzie (`letter_texts.py`), a nie w katalogu gettext – to treść dokumentu,
którą prawnik organizatora czyta w całości, a nie napisy interfejsu. Szablon z bazy
(`document_templates`) jest jednojęzyczny i ma pierwszeństwo – wtedy język zmienia tylko etykiety
tabeli i blok weryfikacji.

## 5. Weryfikacja publiczna

- `/visa/verify/` (formularz kodu) i `/visa/verify/<kod>/`. Nowy pierwszy segment `visa`
  (`RESERVED_SLUGS`, kontrakt `djcms_contract` wygenerowany `djcms_routes --write`).
- Kod: 12 znaków (≈ 59 bitów), losowy – nie numer listu (numer jest kolejny i jawny). Na liście:
  QR z adresem i kod w grupach `XXXX-XXXX-XXXX`; wpisany kod jest normalizowany (wielkość liter,
  myślniki, spacje).
- Pokazuje: numer, datę wystawienia, stan (ważny / unieważniony z datą), wydarzenie, miasto, daty,
  imiona i nazwiska oraz obywatelstwo osób z listu. **Nie** pokazuje numeru paszportu, daty urodzenia,
  powodu unieważnienia ani kraju delegacji poza obywatelstwem. Po retencji: „dane osób usunięte”.
- Zakres konkursu żądania (kod z innego konkursu = „nie znaleziono”), bramka LOG-01 (404 bez niej).
  Nieznany kod to ta sama strona z komunikatem, nie 404 (jak `/dyplomy/<kod>/`).
- Limit żądań `visa_verify` – 60/h na adres IP (GET), `Cache-Control: private, no-store`,
  `noindex`.

Uzasadnienie pokazywania nazwiska: konsulat musi dopasować list do osoby, a kod zna wyłącznie ten,
komu list pokazano – inaczej niż przy dyplomie, gdzie nazwisko zależy od zgody na publikację.

## 6. RODO

- wnioski: dane osobowe tylko przez członka (imię i nazwisko z konta) i powód odrzucenia – znikają
  razem z wierszem członka (retencja, usunięcie konta, wypisanie z delegacji),
- audyt: identyfikatory, numery listów i liczniki – bez powodów odrzucenia/unieważnienia (wolny tekst),
- eksport danych konta: sekcja logistyki finału dostaje wnioski osoby (stan, język, numer listu),
- rejestr czynności (wersja 1.13): nowy odbiorca – osoba znająca kod z listu (konsulat) – oraz
  kategorie „wnioski o list” i „kod weryfikacyjny listu”.

## 7. Testy

Izolacja (opiekun kraju A nie złoży wniosku dla osoby kraju B, nie wycofa cudzego wniosku; koordynator
bez przydziału → 403), przepływ (wniosek → zatwierdzenie → list z kodem; odrzucenie z powodem; jeden
oczekujący wniosek), powiadomienia w języku opiekuna, zastąpienie poprzedniego listu, decyzje hurtowe
z częściowym niepowodzeniem, CSV, PDF (numer, nazwisko, kod w treści), strona weryfikacji (dane
minimalne, brak numeru paszportu, unieważnienie, inny konkurs, bramka 404, limit żądań), retencja
(wnioski znikają, weryfikacja mówi „dane usunięte”), usunięcie konta, eksport.

## 8. Realizacja (4.10.2026) – gdzie co jest, odstępstwa, znane luki

Gdzie: `apps/delegation_logistics/` – `letter_requests.py` (wnioski, decyzje, powiadomienia, CSV),
`verification.py` (dane strony weryfikacji), `letter_texts.py` (teksty listu w 7 językach),
`letters.py` (LOG-01 + kod, język, migawka wydarzenia, ramka weryfikacji z QR, `revoke_letter`),
`views_letters.py` + wpisy na końcu `urls.py`, szablony `leader_letters.html`, `letter_requests.html`,
`verify.html`, `email/letter_decision_*.txt`; migracja `0002_visa_letter_workflow`; katalogi `.po`
aplikacji (41 napisów × 10 języków, maszynowe). Dokumentacja: OPERACJE § 29.7, podręcznik organizatora
§ 10c, przewodnik opiekuna § 7.

Odstępstwa od pierwotnego zamówienia (z powodem):
1. **Brak osobnej aplikacji, modelu paszportu i flagi `visa_letters`** – decyzja koordynatora prac:
   LOG-01 ma już dane dokumentu podróży (szyfrowane), rejestr i PDF; bramka jest bramką LOG-01.
2. **Retencja 30 dni po finale (ustawienie finału), nie 90** – termin LOG-01, ostrzejszy.
3. **Dane wniosku nie zawierają ambasady, celu ani dat przyjazdu** – przyjazd i wyjazd są w sekcji
   „Przyjazd i wyjazd” LOG-01, a list ich nie cytuje (cytuje daty wydarzenia); miasto konsulatu nie jest
   potrzebne do treści listu i byłoby kolejną daną bez celu (minimalizacja). Do dopisania, jeśli
   organizator tego zażąda (list zaadresowany do konkretnej placówki).
4. **Kto pokrywa koszty, podpisujący, podpis** – nie jako osobne pola: tekst listu jest szablonem
   dokumentu (`document_templates`, rodzaj „list zapraszający (wiza)”), a podpisy biorą się z szablonu
   graficznego dyplomów (LOG-01). Zdanie o kosztach organizator dopisuje w szablonie.
5. **Zatwierdzenie = wystawienie** (stan `APPROVED` z wystawionym listem) – osobny krok „wystaw po
   zatwierdzeniu” byłby drugim kliknięciem bez decyzji.
6. **Pobrania listów** są w dzienniku zdarzeń od LOG-01 (`logistics.letter_downloaded`); strona
   weryfikacji nie jest audytowana (publiczna – zalałaby dziennik), chroni ją limit żądań.

Znane luki: brak e-maila do osoby, której dotyczy list (dostaje go od opiekuna); tłumaczenia tekstów
listu i napisów do przeglądu; po wyłączeniu flagi strona weryfikacji znika razem z resztą logistyki.
