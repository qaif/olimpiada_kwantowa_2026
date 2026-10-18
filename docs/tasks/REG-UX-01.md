# REG-UX-01: Literówki w adresach, czytelny wynik importu, automatyczny zapis do treningu

Punkty 3, 4 i 5 z listy organizatora (18.10.2026). Trzy niezależne części, osobne commity.

## 0. Cel i granice

1. **Literówka w adresie e-mail** – na produkcji z literówek powstały duplikaty kont i odbicia
   (`gmail.con`, `gmial.com`, `.pl.pl`, `wp.p`, `onet.pll`). Rozbudowa MAIL-02
   (`apps.email_delivery`), bez drugiego mechanizmu obok.
2. **Wynik importu uczniów** – wiersz ucznia, który miał już konto i wskazał tego nauczyciela,
   był „po cichu” liczony jako „adres z istniejącym kontem”; nauczyciel zgłosił „nie widać ucznia”.
   Podgląd i podsumowanie mówią odtąd, co stało się z każdym wierszem.
3. **Automatyczny zapis nowych kont do etapu treningowego** – przełącznik na etapie i akcja
   „Zapisz wszystkich obecnych uczestników”.

Czego zadanie **nie** robi:
- nie zmienia reguł importu (kto dostaje konto, kto prośbę o zgodę, kto nic) ani reguły zapisu
  do etapu (`apps.competitions.services.register_for_stage`),
- nie pyta DNS o domeny z pliku importu (500 wierszy × do 3 s) – w imporcie wyłącznie podpowiedź,
- nie blokuje domen szkolnych ani żadnych innych z powodu podpowiedzi – podpowiedź zawsze da się
  odrzucić; twarda blokada (MAIL-02 § 1.2) zostaje wyłącznie dla domen bez MX/A.

## 1. Literówki (rozbudowa MAIL-02 § 1)

### 1.1. Algorytm (`apps.email_delivery.typos`)
Dotychczasowe reguły zostają (lista znanych dostawców, OSA ≤ 1 dla domen do 6 znaków i ≤ 2 dla
dłuższych, jawna mapa TLD). Próg 1 dla krótkich domen jest celowy: `wp.pl`, `op.pl`, `vp.pl`,
`o2.pl` leżą od siebie o 1–2 zmiany, więc „≤ 2” zamieniałoby prawdziwe krótkie domeny szkół
(`ab.pl`, `lo.pl`) w „literówki”. Dochodzi `int.pl` (lista organizatora) i **znane wzorce**,
sprawdzane po odległości, a przed mapą TLD:
- **podwojony TLD**: `.pl.pl`, `.com.com` … → jeden (`szkola.edu.pl.pl` → `szkola.edu.pl`),
  wynik przechodzi jeszcze raz przez całą podpowiedź (`gmial.com.com` → `gmail.com`),
- **znana domena z doklejoną etykietą**: `gmail.com.pl` → `gmail.com`, `wp.pl.com` → `wp.pl`,
- **marka z cudzym TLD**: `gmail.pl`, `icloud.pl`, `hotmail.pl`, `outlook.pl`, `yahoo.pl`
  → `.com`; `wp.com`, `onet.com`, `o2.com`, `interia.com.pl`… → wariant `.pl` (wyłącznie dla
  etykiet z jawnej mapy `BRAND_DOMAINS`; domena znana – np. `interia.com`, `onet.eu` – nie dostaje
  podpowiedzi),
- **adres bez kropki w domenie** (`jan@gmailcom`, `jan@wppl`, `jan@gmail`): składnia Django go
  odrzuca, więc podpowiedź idzie w komunikacie o błędzie składni („Adres jest niepełny – czy
  chodziło Ci o jan@gmail.com?”) z przyciskiem „Popraw” i bez „zostaw” (takiego adresu nie da się
  zostawić).

Ta sama logika w `email-check.js`; test pilnuje list i odpowiedzi na wspólnych przypadkach
(`tests/typo_cases.json`).

### 1.2. Formularz: „Popraw” i „Tak, adres jest poprawny”
Pole wyboru „Użyj adresu …” (MAIL-02 § 1.3) zastępują **dwa przyciski wysyłki** w bloku
podpowiedzi, bez inline JS:
- **„Tak, adres … jest poprawny”** – pierwszy w DOM (to on jest domyślnym przyciskiem formularza:
  Enter w dowolnym polu = zostaw, czyli dotychczasowe zachowanie ponownego wysłania),
- **„Popraw na …”** – `name="<pole>__accept"`, `value=<podpowiedź>`. Serwer przyjmuje go
  **tylko wtedy, gdy podpowiedź dla wartości, która właśnie przyszła w polu, jest dokładnie tą
  samą podpowiedzią** – ręcznie poprawiony adres nie zostanie nadpisany starą poprawką.

Z JS (`email-check.js`) oba przyciski stają się `type="button"`: „Popraw” podmienia wartość
w polu bez wysyłki (hasła i reszta formularza nie giną), „Tak, adres jest poprawny” chowa
podpowiedź (ukryte „zostaw” jedzie z formularzem). Domyślnym przyciskiem zostaje wtedy
przycisk formularza. Bez JS przycisk wysyła formularz – pola haseł trzeba wpisać ponownie,
tak samo jak przy każdym innym błędzie formularza.

Formularze: jak w MAIL-02 § 1.5 (rejestracja uczestnika, opiekuna szkolnego, komitetu, zmiana
adresu w profilu – już korzystają z `CheckedEmailField`) – zmiana w widżecie obejmuje je wszystkie.

### 1.3. Import uczniów (podgląd)
Wiersz z podpowiedzią dla adresu ucznia dostaje w kolumnie „Co zrobimy” zdanie „Czy chodziło
o …?” i parę przycisków radiowych **„Popraw”** (zaznaczone domyślnie) / **„Tak, adres jest
poprawny”**, przypiętych do formularza zatwierdzenia atrybutem `form`. Zatwierdzenie podmienia
adres wierszy z „Popraw” na podpowiedź **liczoną od nowa na serwerze** (`apply_email_fixes`;
wartość z formularza nie jest używana) i dopiero potem rozstrzyga wiersze (`validate_rows`) –
poprawiony adres może okazać się zajęty albo powtórzony i wtedy dostaje swoje rozstrzygnięcie.

## 2. Wynik importu (`bulk_registration.describe_rows`, szablon `_import_row_status.html`)

### 2.1. Statusy wiersza
| Status | Import nauczyciela | Import koordynatora |
|---|---|---|
| `invite` | „zaproszenie zostanie wysłane” | to samo |
| `on_list` | „ma już konto i jest na Twojej liście” + link do `/supervisor/students/` | „ma już konto i jest na liście tego opiekuna” + link do konta |
| `existing` | „ma już konto, ale nie ma go na Twojej liście” + wyjaśnienie (niżej) | – |
| `other_supervisor` | – | „ma już konto, ale wskazał innego opiekuna – dostanie prośbę o zgodę” |
| `no_supervisor` | – | „ma już konto, ale nie wskazał opiekuna – dostanie prośbę o zgodę” |
| `unchanged` | – | „ma już konto w tym konkursie – bez zmian” (wiersz bez opiekuna) |
| `not_participant` | – | „ma konto, ale nie jest uczestnikiem tego konkursu – import go nie zmieni” |
| `error` | „błąd: …” | to samo |

**Odstępstwo od listy organizatora (bezpieczeństwo).** Nauczycielowi nie rozdzielamy „wskazał
innego opiekuna / nikogo” od „ma konto w innym konkursie”. Od v0.38.7 (audyt) podgląd celowo
nie mówi, czy zajęty adres należy do ucznia tego konkursu: rejestracja opiekunów bywa otwarta,
a ta różnica jest wyrocznią „ten adres to uczeń tego konkursu / członek komitetu” (plik z jednym
wierszem). Nauczyciel dostaje więc wszystko, do czego ma prawo – „jest na Twojej liście” (to i tak
widzi na swojej liście) – a pozostałe zajęte adresy jednym zdaniem, które mówi, co się stanie
w każdym przypadku: „Jeśli to uczeń tego konkursu (wskazał innego opiekuna albo nikogo), dostanie
prośbę o zgodę na dopisanie Cię jako opiekuna i pojawi się na Twojej liście po jego zgodzie. Jeśli
startuje w innym konkursie albo to konto innej osoby, import go nie zmieni.” Pełny podział
widzi koordynator, który i tak ma wgląd w konta swojego konkursu; „konto w innym konkursie”
i „konto bez profilu ucznia” są u niego jednym statusem (izolacja konkursów).

Zapytanie „na Twojej liście” dotyczy wyłącznie profili z adresem opiekuna = adres zalogowanego
nauczyciela (to samo, co pokazuje `/supervisor/students/`), więc ani treść, ani czas odpowiedzi
nie mówią nic o profilach, których nauczyciel nie widzi.

### 2.2. Podsumowanie
- podgląd: „Nowe zaproszenia: N · już na liście: M · inne istniejące konta: L · pominięte: K”,
- komunikat po imporcie: „Import zakończony. Wysłano zaproszeń: N, uczniów już było na liście: M,
  pominięto: K.” + (gdy L > 0) zdanie o pozostałych zajętych adresach. Bez liczby wysłanych próśb
  o zgodę u nauczyciela (wyrocznia – patrz wyżej); koordynator dostaje ją osobno.
- „pominięto” = wiersze z błędem w podglądzie (nie jadą w koszyku; ich liczba w ukrytym polu
  `skipped`, wyłącznie do komunikatu) + wiersze pominięte przy zatwierdzeniu,
- `import_students` zwraca dodatkowo `on_list` (wchodzi też do audytu `accounts.students_imported`).

### 2.3. Błędy znalezione przy okazji
- reguł importu – brak. Przyczyna zgłoszenia: wiersz ucznia już przypisanego do nauczyciela był liczony
jako „adres z istniejącym kontem (uczniowie tego konkursu dostali prośbę o zgodę)”, choć żadna
prośba nie szła (`request_consent` zwraca `False` dla tego samego opiekuna) – komunikat sugerował,
że uczeń „czeka”, a lista była już kompletna,
- szablon podglądu: dwuwierszowy komentarz `{# … #}` (Django zna tylko jednowierszowy) trafiał na
  ekran nauczyciela jako tekst w nagłówku tabeli – poprawiony na `{% comment %}`. Ten sam błąd jest
  w kilku szablonach koordynatora (`assignments.html`, `participant_detail.html`, `problems.html`,
  `members.html`, `member_detail.html`, `dashboard.html`) i w `invite/accept.html` – poza zakresem.

## 3. Automatyczny zapis do etapu treningowego

### 3.1. Gdzie ustawienie – decyzja
Pole na etapie `Stage.auto_register_new_participants` (domyślnie `False`, migracja
`competitions.0035`, więz `competitions_stage_auto_register_training`: `True` tylko dla
`TRAINING`). Prościej niż ustawienie konkursu: etap treningowy jest jeden na edycję, a nowa
edycja ma nowy etap – ustawienie konkursu musiałoby wskazywać, **który** etap, i przeżywać zmianę
edycji. Przełącznik stoi tam, gdzie koordynator i tak zarządza etapem (oś czasu etapu).

### 3.2. Kiedy zapis (`apps.competitions.auto_registration`)
Chwila „nowe konto uczestnika” = **pierwsza zgoda RODO na profilu**: profil powstały ze zgodą
(rejestracja formularzem i przez Google) albo przejście `gdpr_consent_at` z pustego na ustawione
(przyjęcie zaproszenia z importu, zgłoszenie z delegacji). Profil z importu bez zgód nie może się
zapisać (`CONSENTS_MISSING`), więc zapis czeka na przyjęcie zaproszenia. Ponowna zgoda na nową
wersję dokumentu (`apps.consent_gate`) **nie** zapisuje (to nie jest nowe konto).

Sygnały `pre_save`/`post_save` na `Participant` (bez zmian w serwisach kont) →
`transaction.on_commit(..., robust=True)` → dla każdego etapu `TRAINING` bieżącej edycji konkursu
profilu z włączonym przełącznikiem: `register_for_stage`. `DomainError` (zamknięty, już zapisany)
= cisza w logu `debug`; każdy inny wyjątek = `logger.exception`, **nigdy** błąd rejestracji.
Idempotentnie: drugi zapis kończy się `ALREADY_REGISTERED` i jest pomijany.

### 3.3. „Zapisz wszystkich obecnych uczestników”
Na ekranie etapu treningowego (`/coordinator/stages/<id>/edit/`) blok „Zapis do treningu”:
przełącznik (POST `/coordinator/stages/<id>/training/auto/`) i przycisk (POST
`/coordinator/stages/<id>/training/register-all/`, potwierdzenie `confirmSubmit` jak przy
retencji). Zapisuje profile **tego konkursu** z kontem aktywnym, zgodą RODO, bez anonimizacji
i bez wpisu w etapie, każdy przez `register_for_stage`. Raport w komunikacie: zapisano N, już
zapisanych M, pominięto K (bez zgód / nieaktywne konta); audyt `stage.bulk_registered`
z liczbami. Etap innego konkursu – 404; etap inny niż treningowy albo zamknięty – 409 z komunikatem.
Przełącznik: audyt `stage.auto_registration_changed`.

## 4. Testy
- `apps/email_delivery/tests/test_typos.py`, `test_fields.py`, `typo_cases.json`, `js_tests/email-check.test.js`,
- `apps/accounts/tests/test_import_status.py` (statusy, poprawki adresów, licznik `on_list`),
  `apps/web/tests/test_supervisor_import_status.py` (podgląd i komunikat obu importów),
- `apps/competitions/tests/test_auto_registration.py` (sygnał, idempotencja, błąd nie blokuje,
  akcja zbiorcza, uprawnienia i izolacja).

## 5. Dokumentacja
PODRĘCZNIK-ORGANIZATORA (etap treningowy, import), PODRĘCZNIK-OPIEKUNA? – nie dotyczy (to opiekun
drużyny IQO); opis importu nauczyciela jest w PODRĘCZNIK-ORGANIZATORA, rejestracja – PODRĘCZNIK-UCZESTNIKA,
CHANGELOG.
