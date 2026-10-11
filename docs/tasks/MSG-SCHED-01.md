# MSG-SCHED-01: Komunikaty z datą przyszłą (zaplanowana wysyłka)

## 0. Cel i granice

Prośba organizatora z 8.10.2026: „dodaj funkcję wysyłania maili z datą przyszłą”. Na ekranie
**Komunikacja → Komunikaty** (`/coordinator/messages/`) komunikat da się **zaplanować** na wskazaną
datę i godzinę (czas polski, Europe/Warsaw) zamiast wysyłać od razu. Pierwsze użycie: przypomnienie
na sobotę 10.10.2026, 06:00.

Czego zadanie **nie** robi:
- nie zakłada nowej aplikacji – to rozszerzenie istniejącej wysyłki (`apps.accounts.messaging`,
  model `MessageBroadcast`, ekran w `apps.web`),
- nie pozwala **edytować** zaplanowanego komunikatu – zmiana = „Anuluj” i zaplanowanie od nowa
  (jedna ścieżka walidacji i podglądu zamiast drugiej),
- nie planuje **wklejonej listy adresów** (`CUSTOM`): jej adresów z zasady nigdzie nie zapisujemy
  (`BroadcastGroup`), a zaplanowanie wymagałoby przechowania ich do chwili wysyłki. Taką listę
  wysyła się od razu,
- nie dodaje cyklicznych wysyłek ani przypomnień „N dni przed etapem”.

## 1. Model i stany

`MessageBroadcast` (migracja `accounts.0041`):
- `scheduled_for` (`DateTimeField`, puste przy wysyłce natychmiastowej, indeks) – termin,
- `parameters` (`JSONField`) – parametry grupy w postaci, z której da się odtworzyć argumenty
  `resolve_recipients`: identyfikatory (`stage`, `region`), wartości (`district`, `school`, `grade`,
  `workshop`) i `include_past_editions`. `target` zostaje opisem do historii (etykieta z chwili
  zaplanowania); osobne pole, bo `target` nie niesie przełącznika edycji w postaci argumentu,
  a region trzyma kodem, nie kluczem. Adresów pole nie zawiera nigdy.

`BroadcastStatus` dostaje stany:
- `SCHEDULED` „zaplanowana” → przy wysyłce `QUEUED` → `SENT` (dotychczasowy tok porcji),
- `CANCELLED` „anulowana” – koordynator kliknął „Anuluj” przed terminem,
- `EXPIRED` „przeterminowana – nie wysłano” – zob. § 3,
- `EMPTY` „bez odbiorców – nic nie wysłano” – grupa w chwili wysyłki była pusta.

`recipient_count` przy zaplanowanym komunikacie jest zerem aż do wysyłki – wtedy dostaje liczbę
policzoną w chwili wysyłki.

## 2. Odbiorców liczymy w chwili wysyłki

Rejestr niesie grupę, `parameters`, konkurs, temat, treść i autora – **nie** listę adresów.
W chwili wysyłki `resolve_scheduled_recipients(broadcast)` woła tę samą `resolve_recipients`, co
wysyłka natychmiastowa, z **bieżącą edycją policzoną w tej chwili** (`current_edition`). Uczestnik
zarejestrowany między zaplanowaniem a terminem dostaje list; konto zablokowane w tym czasie – nie.
Etap albo region skasowany w międzyczasie (albo z cudzego konkursu) to pusta grupa → `EMPTY`.

## 3. Zadanie beat `dispatch_scheduled_broadcasts`

`apps.accounts.messaging.dispatch_scheduled_broadcasts`, wpis `dispatch-scheduled-broadcasts`
w `CELERY_BEAT_SCHEDULE`, co 60 s. Przebieg:
1. wybiera do `DISPATCH_BATCH` (25) komunikatów `SCHEDULED` z `scheduled_for <= teraz`, najstarsze
   najpierw (reszta – w następnym przebiegu),
2. każdy w osobnej transakcji: `select_for_update(skip_locked=True)` **i** warunek
   `status=SCHEDULED`. Drugi worker, ponowiony przebieg albo równoległe „Anuluj” nie dostaną tego
   samego wiersza – list wychodzi dokładnie raz. Porcje idą do kolejki `on_commit`, tak jak dziś,
3. w kontekście konkursu komunikatu (`competition_context`) – audyt i bieżąca edycja należą do
   właściciela wiersza.

**Spóźnienie.** Wysyłka spóźniona (worker albo beat leżał) idzie przy najbliższym przebiegu, ale
tylko do **24 h** po terminie (`SCHEDULE_GRACE`). Później komunikat dostaje `EXPIRED` i nie
wychodzi: przypomnienie „jutro o 9:00 etap” wysłane dzień po terminie szkodzi bardziej niż brak
listu, a koordynator widzi stan w historii i może wysłać list ręcznie. Ta sama granica zamyka
pętlę przy błędzie trwałym: wyjątek w trakcie rozstrzygania odbiorców zostawia wiersz
`SCHEDULED` (błąd chwilowy – baza, restart – naprawi się w następnym przebiegu), a błąd trwały
kończy się po 24 h stanem `EXPIRED` zamiast ponawiania w nieskończoność.

## 4. Serwis

```python
schedule_broadcast(*, group, subject, body, scheduled_for, competition, actor,
                   parameters=None, target=None, request=None) -> MessageBroadcast
cancel_scheduled_broadcast(broadcast, *, actor, request=None) -> bool
```

`schedule_broadcast` jest jedyną drogą zaplanowania (widok i `manage.py shell`). Sprawdza:
- termin: strefa – wartość bez strefy jest czasem polskim (`make_aware` w `TIME_ZONE`); co
  najmniej `SCHEDULE_MIN_LEAD` (5 min) od teraz i nie dalej niż `SCHEDULE_MAX_AHEAD` (90 dni),
- grupa: znana, nie `CUSTOM`; wymagany parametr grupy obecny (etap, szkoła, klasa…),
- `actor` – koordynator **tego** konkursu (`has_role`), konkurs podany.

Błąd → `django.core.exceptions.ValidationError`. `cancel_scheduled_broadcast` robi warunkowy
`UPDATE … WHERE status = SCHEDULED` – anulowanie po wysyłce nic nie zmienia i zwraca `False`.

## 5. Ekran

- Pole **„Wyślij później”** (`datetime-local`, opcjonalne) pod treścią; puste = wysyłka od razu.
  Walidacja tej samej reguły, co w serwisie (`schedule_time_error`). Termin wchodzi do podpisu
  podglądu – zmiana terminu po podglądzie unieważnia podpis.
- Podgląd z terminem pokazuje **bieżącą** liczbę odbiorców z dopiskiem, że lista zostanie policzona
  ponownie w chwili wysyłki; przycisk brzmi „Zaplanuj”. Pusta grupa da się zaplanować (może się
  zapełnić do terminu) – wysyłka natychmiastowa do pustej grupy dalej nic nie robi.
- Sekcja **„Zaplanowane”**: termin, grupa z parametrem, temat, autor i przycisk „Anuluj”
  (`POST /coordinator/messages/<id>/cancel/`, CSRF, `CoordinatorRequiredMixin`, wiersz szukany
  w `for_competition(request.competition)` → cudzy komunikat = 404).
- Historia pokazuje kolumnę „Termin” i nowe stany; komunikaty zaplanowane stoją wyłącznie
  w sekcji „Zaplanowane”.
- Bez inline JS; nowe napisy przez gettext, tłumaczenia w `apps/web/locale/<język>/` (10 języków).

## 6. Audyt

- `broadcast.scheduled` (aktor: koordynator) – `group`, `target`, `scheduled_for`,
  `recipients_now` (liczba w chwili planowania),
- `broadcast.cancelled` (aktor: koordynator) – `group`, `target`, `scheduled_for`,
- `broadcast.sent` przy wysyłce zaplanowanej – jak dziś, aktor = **autor planu**, dodatkowo
  `scheduled_for`,
- `broadcast.expired` i `broadcast.empty` (aktor systemowy) – z `scheduled_for`.

Nigdy adresów ani treści.

## 7. Testy

`apps/accounts/tests/test_messaging.py`: planowanie (stan, parametry, audyt), walidacja terminu
(przeszłość, za blisko, za daleko, bez strefy = czas polski), `CUSTOM` odrzucone, rola aktora,
wysyłka przez zadanie dokładnie raz (dwa wywołania), odbiorca dodany po zaplanowaniu dostaje list,
anulowanie (także po wysyłce – bez skutku), przeterminowany, pusta grupa, zakres konkursu.
`apps/web/tests/test_coordinator_messages.py`: podgląd z terminem i dopiskiem, zaplanowanie z ekranu,
termin w przeszłości → błąd, lista „Zaplanowane”, anulowanie (POST, audyt), cudzy komunikat → 404,
recenzent → 403, GET na anulowanie → 405, wpis beat w harmonogramie.
