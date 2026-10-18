# MSG-AUTO-01: Automatyczne przypomnienia (warsztaty i terminy etapów)

## 0. Cel i granice

Prośba organizatora z 18.10.2026 (pkt 7 i 10): przypomnienia, które **same** powstają z harmonogramu,
zamiast co tydzień planować ten sam komunikat ręcznie. Koordynator definiuje **regułę**, a zadanie
beat zamienia ją w zwykłe komunikaty planowane (MSG-SCHED-01) – widoczne w „Komunikacja →
Komunikaty → Zaplanowane”, z przyciskiem „Anuluj”, wysyłane tą samą drogą i z tym samym audytem.

Dwa rodzaje reguł:

- **warsztaty** – jedno przypomnienie na każdy warsztat z harmonogramu strony `/warsztaty/`
  (`apps.cms.workshops.workshop_rows`): temat, prowadzący, data, godziny i link spotkania,
- **termin etapu** – przypomnienia N dni przed `Stage.deadline_at` wskazanego etapu (lista dni,
  np. 30, 7, 1), typowo do grupy „zapisani do etapu, bez wysłanej pracy” (`STAGE_NO_SUBMISSION`).

Pierwsze użycie (produkcja): w każdą sobotę z warsztatem o 06:00 list do „wszyscy uczestnicy
i nauczyciele” ze stałym linkiem Meet.

Czego zadanie **nie** robi:
- nie zmienia wysyłki ani stanów `MessageBroadcast` (`apps.accounts.messaging` bez zmian) – reguła
  woła wyłącznie `schedule_broadcast`, więc walidacja terminu, rola autora, liczenie odbiorców
  w chwili wysyłki i audyt `broadcast.*` są te same co przy planowaniu ręcznym,
- nie wysyła wklejonej listy adresów (grupa `CUSTOM` – adresów nie przechowujemy),
- nie dodaje pola „odwołany” do bloku harmonogramu – odwołanie rozpoznajemy po tekście (§ 3).

## 1. Gdzie mieszka kod – nowa aplikacja `apps.message_rules`

Osobna mała aplikacja, a nie model w `accounts`: `accounts/models.py` ma ponad 1700 linii i jest
wspólny dla wszystkich równoległych prac, a reguła jest **klientem** wysyłki, nie jej częścią –
zależność idzie w jedną stronę (`message_rules` → `accounts.messaging`, `cms.workshops`,
`competitions`). Migracja `message_rules.0001` nie dotyka tabel innych aplikacji.

Modele:

- `MessageRule` – konkurs (`CompetitionScopedManager`), nazwa, rodzaj (`WORKSHOP`/`STAGE_DEADLINE`),
  `enabled`, grupa odbiorców i jej parametry (`group`, `parameters`, `target` – te same postaci co
  w `MessageBroadcast`), etap (dla terminu etapu), szablon tematu i treści, `link`, moment wysyłki
  (`timing`: `DAY_AT` – N dni przed o godzinie `send_time`, lista `days_before`; `HOURS_BEFORE` –
  N godzin przed początkiem warsztatu), autor, `last_run_at`, `last_error`.
- `MessageRuleOccurrence` – „ta reguła obsłużyła już ten warsztat / ten dzień przed terminem”:
  `(rule, key)` unikalne, wskazanie wygenerowanego komunikatu (`SET_NULL`), termin i odcisk
  (`fingerprint` – skrót terminu, tematu, treści i grupy). To jest cała idempotencja (§ 4).

## 2. Szablon

Pola w nawiasach klamrowych, bez wyrażeń:

| Rodzaj | Pola |
|---|---|
| warsztaty | `{temat}`, `{prowadzacy}` (też `{prowadzący}`), `{data}`, `{godziny}`, `{link}` |
| termin etapu | `{etap}`, `{termin}`, `{dni}`, `{link}` |

- `{temat}` – temat bez dopisku „(prowadzący: …)”; `{prowadzacy}` – kolumna „prowadzący”, a gdy pusta,
  dopisek z tematu; pusty prowadzący/godziny → „do potwierdzenia”,
- `{data}` – brzmienie terminu z harmonogramu (np. „17 października 2026”), `{godziny}` – kolumna godzin,
- `{termin}` – „28 lutego 2027, 23:59” (czas polski), `{dni}` – „1 dzień”, „7 dni”,
- `{link}` – pole reguły (np. stały link Meet).

**Walidacja przy zapisie reguły:** pole spoza listy rodzaju → błąd formularza z listą dozwolonych;
`{link}` w szablonie przy pustym polu „Link” → błąd. Temat po wypełnieniu ucinamy do 200 znaków
i bez znaków nowej linii (nagłówek listu).

## 3. Terminy wysyłek

Czas polski (`Europe/Warsaw`, `TIME_ZONE`):

- `DAY_AT`: data warsztatu (albo data terminu etapu w czasie polskim) minus N dni, o godzinie
  `send_time` – ściennie, więc „06:00” jest 06:00 także w dniu zmiany czasu,
- `HOURS_BEFORE` (tylko warsztaty): początek warsztatu (pierwsza godzina z kolumny „godziny”,
  np. „11:00–14:00” → 11:00) minus N **rzeczywistych** godzin (arytmetyka w UTC).

Wiersz pomijany: bez daty maszynowej (`date_value`), **odwołany** (temat, termin albo godziny
zawierają „odwoł…” lub „cancel…”), przy `HOURS_BEFORE` – bez czytelnej godziny początku.
Etap pomijany: treningowy (`has_deadline` fałszywe) albo z innego konkursu.

## 4. Generowanie – zadanie beat `generate-message-rule-broadcasts`

`apps.message_rules.tasks.generate_rule_broadcasts`, co 300 s. Dla każdej reguły (osobna transakcja,
`select_for_update(skip_locked=True)` na wierszu reguły):

1. liczy wszystkie wystąpienia reguły (§ 3) z wypełnionym tekstem,
2. **wycofuje** czekające (`SCHEDULED`) komunikaty reguły, których wystąpienie zniknęło (warsztat
   odwołany, usunięty, bez daty; reguła wyłączona) albo zmieniło odcisk (edycja reguły lub
   harmonogramu) – warunkowy `UPDATE … WHERE status = SCHEDULED`, audyt `broadcast.cancelled`
   z `reason`, i usuwa ich wiersz wystąpienia, żeby nowa wersja mogła powstać; zmiana odcisku
   wycofuje stary komunikat tylko wtedy, gdy nowy da się jeszcze zaplanować (≥ 5 min),
3. dla wystąpień w **oknie** `[teraz + 5 min, teraz + 7 dni]` bez wiersza wystąpienia woła
   `schedule_broadcast` (aktor = autor reguły, `target` z dopiskiem reguły) i zapisuje wiersz.

**Idempotencja:** wiersz `(rule, key)` zostaje także po ręcznym „Anuluj” – anulowany komunikat nie
powstaje ponownie. Klucz: `w:<klucz warsztatu>:d<N>` / `w:<klucz>:h<N>` / `s:<etap>:d<N>`.
Błąd (np. autor stracił rolę koordynatora – `schedule_broadcast` odmawia) trafia do `last_error`
i logu; reszta reguł biegnie dalej. Zapis, przełączenie i usunięcie reguły na ekranie od razu
wykonują ten sam przebieg dla tej reguły.

## 5. Ekran „Komunikacja → Automatyczne przypomnienia”

`/coordinator/messages/rules/` (`CoordinatorRequiredMixin`, throttling `chat`, bez inline JS –
pola grupy chowa istniejący `broadcast-groups.js`):

- lista reguł: nazwa, rodzaj, grupa, moment wysyłki, stan, ostatni błąd; „Dodaj: warsztaty /
  termin etapu”, „Edytuj”, „Włącz/Wyłącz”, „Usuń” (POST + CSRF, potwierdzenie `confirmSubmit`),
- formularz: `BroadcastForm` (te same grupy i parametry, bez `CUSTOM`, bez „Wyślij później”) plus
  pola reguły; przycisk „Podgląd” pokazuje bez zapisu **3 najbliższe wysyłki** z wypełnionym tekstem,
  ekran edycji pokazuje je zawsze, ze stanem („zaplanowany”, „anulowany”, „zostanie zaplanowany …”),
- reguła cudzego konkursu → 404; recenzent → 403; POST-y wyłącznie.

## 6. Audyt

`message_rule.created`, `message_rule.updated` (lista zmienionych pól), `message_rule.enabled`,
`message_rule.disabled`, `message_rule.deleted` – aktor: koordynator; bez szablonów i adresów.
Wygenerowane komunikaty: `broadcast.scheduled` (aktor = autor reguły, `target.message_rule`),
wycofane – `broadcast.cancelled` (aktor systemowy, `reason`), dalej jak MSG-SCHED-01.

## 7. Testy

`apps/message_rules/tests/test_planning.py` (bez bazy): pola szablonu i walidacja (nieznane pole,
`{link}` bez linku), wypełnianie, terminy `DAY_AT`/`HOURS_BEFORE`, DST (25.10.2026, 28.03.2027),
pominięcie warsztatów bez daty/odwołanych/bez godziny, okno czasu, etap treningowy, „1 dzień/7 dni”.
`test_generation.py` (baza): idempotencja (dwa przebiegi), anulowany nie wraca, wyłączona reguła nic
nie tworzy i wycofuje czekające, grupa „bez pracy”, zmiana harmonogramu wycofuje i tworzy na nowo,
izolacja konkursów, autor bez roli. `test_views.py`: lista, zapis z błędem pola, podgląd bez zapisu,
404 cudzej reguły, 403 recenzenta, 405 na GET akcji, wpis beat.
