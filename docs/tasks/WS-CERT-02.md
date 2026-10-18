# WS-CERT-02: Automatyczne zaświadczenia za warsztaty na podstawie obecności

Prośba organizatora z 18.10.2026 (pkt 8 listy). Dziś zaświadczenie z warsztatów wystawia koordynator
ręcznie przyciskiem „Wystaw zaświadczenia z warsztatów (ZIP)” na ekranie obecności – każdemu, kto ma
choć jedną odhaczoną obecność. Zadanie dodaje **regułę**: po zapisie obecności serwis sam wystawia
zaświadczenie uczestnikom, którzy przekroczyli próg ustawiony przez organizatora.

## 0. Granice

- Ekran obecności (`/coordinator/workshops/attendance/`), import CSV i import z Google Meet (osobne
  zadanie, gałąź `feature/obecnosc-google-meet`) **zostają bez zmian**. Reguła reaguje na zapis
  `cms.WorkshopAttendance`, niezależnie od tego, która droga go zrobiła.
- Dokument jest tym samym dokumentem co dotąd: `results.Certificate` rodzaju `warsztaty`, przypięty do
  najwcześniejszego wpisu uczestnika w edycji, z listą tematów na papierze, numerem i kodem
  weryfikacyjnym. Ręczne „Wystaw (ZIP)” działa dalej i oddaje te same numery.
- Bez nowych danych osobowych: nowa tabela trzyma klucze obce i liczby.

## 1. Ustawienie – gdzie i jakie

1. **Miejsce:** nowy model `workshop_certificates.AutoIssuePolicy`, jeden wiersz na **edycję**
   (OneToOne → `competitions.Edition`). Edycja, bo dokument należy do edycji (`Certificate.edition`,
   jeden na osobę i edycję), szablony graficzne dopasowuje się po (rodzaj, edycja), a ekrany zaświadczeń
   działają na edycji bieżącej. Konkurs wynika z edycji. Brak wiersza = reguła wyłączona (stan domyślny).
2. **Pola:** `enabled` („Wystawiaj zaświadczenie automatycznie”), `min_workshops` (próg, ≥ 1),
   `template` (szablon graficzny, opcjonalny), `notify_participants` (list „Twoje zaświadczenie jest
   gotowe”, domyślnie wyłączony), `updated_at`, `updated_by`.
3. **Próg: liczba warsztatów, nie procent.** Uczestnik spełnia próg, gdy był na co najmniej
   `min_workshops` **różnych** warsztatach z harmonogramu konkursu (obecności ∩ wiersze bloku
   „harmonogram” strony „Warsztaty” – ta sama reguła, z której powstaje lista tematów na zaświadczeniu,
   więc liczba na ekranie zgadza się z listą na papierze). Uzasadnienie:
   - procent zależy od długości harmonogramu, a ta rośnie w trakcie edycji (redakcja dopisuje
     terminy) – osoba, która spełniała „50 %” w listopadzie, w styczniu przestawałaby spełniać, choć
     nic jej nie ubyło; liczba jest stała i da się ją ogłosić w regulaminie („co najmniej 3 warsztaty”),
   - procent liczony od warsztatów, które już się odbyły, zmienia się z każdym dniem, a od całego
     harmonogramu – wlicza zajęcia przyszłe, więc nikt nie spełnia go przed końcem cyklu,
   - próg `1` daje dokładnie dzisiejszą regułę ręcznego „Wystaw (ZIP)”.
   Zaświadczenia per warsztat **nie** wprowadzamy: dzisiejszy model to jeden dokument na osobę
   i edycję z listą tematów (unikalność wpis + rodzaj).
4. **Szablon:** pole `template` wskazuje aktywny szablon graficzny **tego konkursu** o rodzaju
   „warsztaty” albo „wszystkie rodzaje”. Puste = dotychczasowa reguła dopasowania
   (`resolve_template`: rodzaj+edycja → … → układ wbudowany). Wskazany szablon obowiązuje
   **wszystkie** zaświadczenia z warsztatów tej edycji (także wystawione ręcznie – to ten sam
   dokument); składa je skład zarejestrowany przez `register_composer`, więc `apps.results` nie wie
   o tej aplikacji. Szablon wyłączony później = powrót do reguły dopasowania. Brzmienie tekstu
   (tytuł, zdanie) – jak dotąd z „Szablonów dokumentów” (`tenancy.DocumentTemplate`), wersja
   zapamiętana przy wystawieniu.

## 2. Wyzwalacz – jedno zadanie na zapis

1. `cms.WorkshopAttendance.objects` dostaje własny `QuerySet`: `bulk_create` po zapisie wysyła sygnał
   `apps.cms.workshops.attendance_recorded(participant_ids=…)`. `bulk_create` nie wysyła `post_save`,
   a właśnie nim zapisują kratki (`save_attendance`), import CSV i (najpewniej) import z Meet – sygnał
   w `QuerySet` łapie wszystkie trzy drogi bez dotykania którejkolwiek. Pojedynczy `create()/save()`
   łapie zwykły `post_save`.
2. Odbiornik (`workshop_certificates.triggers`) **nie robi zapytań**: zbiera identyfikatory uczestników
   w pamięci połączenia i rejestruje **jeden** `transaction.on_commit` na transakcję. Po commicie –
   jedno zapytanie „czy jakakolwiek reguła jest włączona” i **jedno** zadanie Celery
   `issue_for_participants(ids)`. Import 300 osób = jedno zadanie; kilka `bulk_create` w jednej
   transakcji = nadal jedno zadanie. Wycofana transakcja nie zleca nic.
3. Zadanie grupuje uczestników po konkursie, bierze **edycję bieżącą** konkursu i jej regułę
   (wyłączona → nic), liczy obecności jednym zapytaniem z `GROUP BY`, wpisy jednym zapytaniem,
   istniejące dokumenty jednym zapytaniem, i wystawia brakujące (`issue_certificate`).
4. Błąd brokera po commicie nie zamienia zapisu obecności w 500 (`robust=True`, wpis w logu);
   „Przelicz teraz” nadrabia.

## 3. Idempotencja i brak odbierania

1. Jedno zaświadczenie z warsztatów na osobę i edycję: przed wystawieniem sprawdzamy dokumenty
   rodzaju `warsztaty` uczestnika w edycji (po uczestniku, nie po wpisie), a wyścig dwóch zadań kończy
   unikalność (wpis, rodzaj) – drugie zadanie bierze istniejący dokument i nie wysyła listu.
2. **Zmiana obecności poniżej progu nie odbiera zaświadczenia.** Dokument z numerem i kodem jest
   w obiegu (wydruk, rekrutacja, strona weryfikacji); zniknięcie go po zdjęciu kratki byłoby cichym
   unieważnieniem dokumentu publicznego przez literówkę w tabeli. Lista tematów na PDF-ie jest – jak
   dotąd – liczona przy pobraniu z bieżących obecności, więc poprawka obecności poprawia treść. Celowe
   unieważnienie (błąd organizatora) to decyzja człowieka poza regułą – panel nie ma przycisku
   unieważnienia dla żadnego dyplomu i to zadanie go nie dodaje (zostaje operator, jak dziś).
3. Konta usunięte (anonimizacja) nie dostają nowego dokumentu – ta sama reguła co przy „Wystaw (ZIP)”.

## 4. List „Twoje zaświadczenie jest gotowe”

1. Tylko gdy `notify_participants` jest włączony i tylko dla dokumentu **nowo** wystawionego przez
   regułę (nie dla istniejących, nie dla ręcznego ZIP-a). Jeden list na dokument
   (`AutoIssuedCertificate.notified_at`).
2. Język odbiorcy (`language_for`), temat przez `branding.subject`, podpis konkursu, link do
   `/me/certificates/`. List nieobowiązkowy (`essential=False`, MAIL-02) – adres, który twardo odbił,
   nie dostaje go. Bez danych w treści poza edycją i linkiem.

## 5. Ekran koordynatora – `/coordinator/workshops/auto-certificates/`

1. Menu: Raporty → „Zaświadczenia z warsztatów” (pod „Obecnością na warsztatach”).
2. Formularz reguły (edycja bieżąca): cztery pola z § 1.2; zapis = audyt.
3. Liczby: warsztatów w harmonogramie, wystawionych automatycznie, wszystkich zaświadczeń z warsztatów
   w edycji, „spełnia próg, czeka na wystawienie” (np. przed pierwszym przeliczeniem albo przy
   wyłączonej regule).
4. **„Przelicz teraz”** (POST) – zleca zadanie `recalculate_edition` dla całej edycji. Przy wyłączonej
   regule przycisk jest nieaktywny, a serwer odmawia. Ochrona przed seryjnym klikaniem: jedno zlecenie
   na edycję na 30 s (`cache.add`).
5. **Poniżej progu** (informacyjnie): uczestnicy z co najmniej jedną obecnością, ale poniżej progu,
   bez zaświadczenia – nazwisko, kod, „był na X z N” – najwyżej 200 wierszy.
6. Izolacja: wszystko z `request.competition` i jego edycji bieżącej; szablon spoza konkursu nie
   przechodzi walidacji formularza; widok za `CoordinatorRequiredMixin`.

## 6. Audyt

- `workshop_certificates.policy_updated` (na regule): zmienione pola i wartości
  (`enabled`, `min_workshops`, `template_id`, `notify_participants`),
- `workshop_certificates.recalculate_requested` (na edycji): kto kliknął „Przelicz teraz”,
- `workshop_certificates.auto_issued` (na edycji, raz na przebieg z co najmniej jednym nowym dokumentem):
  `issued`, `notified`, `threshold`, `trigger` (`attendance`/`recalculate`) – **liczby, bez nazwisk**,
- każdy dokument jak dotąd `certificate.issued` (numer, `entry_id`, bez nazwisk; aktor pusty = system).

## 7. Testy

Próg (poniżej/na/powyżej, tylko warsztaty z harmonogramu), idempotencja (dwa przebiegi, zaświadczenie
wystawione wcześniej ręcznie), brak odebrania (zdjęcie obecności), masowy zapis 300 obecności →
dokładnie jedno zadanie (oraz kilka `bulk_create` w jednej transakcji → jedno, rollback → zero), list
włączony/wyłączony (i jeden list na dokument), izolacja konkursów (obecność w B nie wystawia w A, szablon
B niedostępny w A, ekran A nie pokazuje B), ekran: zapis, „Przelicz teraz”, lista poniżej progu, 404/403
dla nie-koordynatora, skład z wybranym szablonem.
