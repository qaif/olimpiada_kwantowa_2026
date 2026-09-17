# Testy wielokonkursowości — jak z nich korzystać

Zadanie **T7** z `docs/UNIWERSALNY-ETAP-1.md`. Ten plik jest instrukcją dla zadań **T3** i **T5**:
co jest gotowe, jak tego użyć i co zrobić, kiedy test zaświeci na czerwono.

## Fikstury (`backend/conftest.py`, widoczne w całej suicie)

| Fikstura | Co daje | Kiedy jej użyć |
|---|---|---|
| `competition` | Konkurs #1 — ten z migracji `tenancy.0002`, z hostem przypiętym do `kwantowa.invalid` | zawsze, gdy test mówi o „naszym” konkursie |
| `other_competition` | Konkurs #2 (`inny.test`, slug `inny`) z **własnym** poddrzewem stron | test krzyżowy: obiekt, którego nie wolno zobaczyć |
| `as_competition` | menedżer kontekstu `with as_competition(x):` | kod wołany poza żądaniem (zadanie Celery, komenda, serwis pocztowy) |
| `client_for` | `client_for(competition)` → `Client` z `HTTP_HOST` i `SERVER_NAME` tego konkursu | każde żądanie, którego przedmiotem jest konkurs |
| `unbound_competition` | zdejmuje autouse'owe związanie kontekstu | **tylko** test, którego przedmiotem jest pusty kontekst |
| `_bind_competition` (autouse) | ustawia `current_competition()` na Konkurs #1 dla każdego testu z bazą | nic nie trzeba robić |

Autouse jest decyzją dokumentu (§ 7.1): bez niego każdy z blisko trzech tysięcy istniejących
testów wymagałby dopisania fikstury, żeby kod czytający `current_competition()` zachowywał się tak,
jak w żądaniu. Fikstura **niczego nie tworzy** — baza bez konkursu wiąże `None`, czyli stan sprzed
wielokonkursowości — i **nie dotyka bazy** w teście, który bazy nie ma.

```python
def test_coordinator_of_a_cannot_see_stage_of_b(client_for, competition, other_competition):
    stage_b = StageFactory(competition=other_competition)
    client = client_for(competition)
    client.force_login(CoordinatorFactory())

    assert client.get(f"/coordinator/stages/{stage_b.pk}/edit/").status_code == 404
```

## Fabryki

Każda fabryka domenowa przyjmuje `competition=`; wartość domyślna bierze się z kontekstu, więc
istniejące testy nie zmieniają ani linijki. Mechanika jest w `apps/tenancy/tests/factories.py`:

- `CompetitionScopedFactory` — baza fabryk. Argument `competition` trafia do modelu **tylko wtedy,
  gdy model ma już to pole** (introspekcja `_meta`). Dzięki temu suita jest zielona na każdym stanie
  pośrednim T2/T3, a w dniu, w którym dochodzi kolumna, testy krzyżowe zaczynają sprawdzać izolację
  bez zmiany choćby jednego znaku,
- `SAME_COMPETITION` — propagacja na podfabryki (`StageEntryFactory(competition=x)` zakłada
  uczestnika **i** etap tego samego konkursu),
- `create_scoped(model, competition, **kwargs)` — dla modeli bez fabryki (`core.AuditLog`,
  `accounts.MessageBroadcast`),
- `grant_membership(user, competition, role)` — nadaje rolę; przed T2 nie robi nic.

**Dla T3:** po dodaniu `Edition.competition` i `Submission.competition` nie trzeba tu niczego
zmieniać. Sprawdź natomiast, czy `apps/tenancy/tests/test_isolation.py` nie zaczął zgłaszać
`XPASS(strict)` — to jest sygnał „obszar zamknięty”, i wtedy zdejmuje się z testu znacznik
`xfail`.

**Dla T5:** to samo dotyczy testów oznaczonych `reason=T5`.

## Pliki

| Plik | Co pilnuje |
|---|---|
| `test_isolation.py` | 17 przypadków reguły krzyżowej (§ 7.2) — po jednym na obszar danych |
| `test_invariants.py` | 7 niezmienników Konkursu #1 (§ 7.3): CSP, zgody, tematy listów, prefiksy kodów, menu, liczba zapytań |
| `golden.py` | „złota” fikstura: kształt produkcji bez danych osobowych (§ 0.4) |
| `test_golden_single_competition.py` | ścieżki uczestnika, recenzenta i koordynatora na złotej fiksturze |
| `test_resolution.py`, `test_middleware.py`, `test_migration_0002.py`, `test_absolute_url.py` | zadanie T1 |

## Kiedy test świeci na czerwono

1. **`XPASS(strict)` w `test_isolation.py`** — zakresowanie tego obszaru właśnie weszło. Zdejmij
   znacznik `xfail` z tego testu (i tylko z tego), w tym samym commicie, co zmiana kodu.
2. **Zmiana w `test_invariants.py`** — jedna z wartości, które miały zostać nietknięte, zmieniła
   się. Stałą wolno zmienić tylko razem ze zmianą zachowania i tylko świadomie; nad każdą stoi
   komentarz mówiący, kto o taką zmianę pyta.
3. **Test spoza tego katalogu przestał przechodzić** — to jest sygnał regresji, a nie pozycja do
   poprawienia. Poprawka idzie do kodu, nie do testu (§ 6, T7: „czego nie wolno zmienić”).

## Przebiegi

```bash
# z korzenia repozytorium, testy w kontenerze, wyłącznie baza olimpiada_d
PW=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T \
  -e DATABASE_URL=postgres://olimpiada:$PW@db:5432/olimpiada_d \
  web python -m pytest -q -p no:cacheprovider apps/tenancy
```

Scenariusz przeglądarkowy dwóch konkursów (`e2e/check_two_competitions.py`) wymaga żywego
środowiska compose i drugiego konkursu założonego komendą `create_competition` — instrukcja jest
w docstringu tego pliku. W zwykłej suicie nie chodzi i chodzić nie ma.
