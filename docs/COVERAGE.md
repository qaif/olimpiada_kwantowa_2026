# Pokrycie testami (T-10)

Pomiar z kontenera `web`, na tej samej konfiguracji, na której chodzi CI:

```bash
docker compose exec -T web pytest -q \
  --cov=apps --cov-report=term-missing:skip-covered --cov-report=json:/tmp/cov.json
```

Wynik: **476 testów przeszło**, pokrycie całości `apps/` (razem z testami i migracjami) **97 %**.
Tabele poniżej liczą **wyłącznie kod produkcyjny** – bez `apps/*/tests/` i bez `apps/*/migrations/`,
bo pokrycie własnych testów i wygenerowanych migracji nic o jakości nie mówi.

## Kod produkcyjny per aplikacja

| Aplikacja | Instrukcje | Niepokryte | Pokrycie |
|---|---:|---:|---:|
| `accounts` | 524 | 17 | 96,8 % |
| `appeals` | 387 | 18 | 95,3 % |
| `cms` | 255 | 9 | 96,5 % |
| `competitions` | 610 | 21 | 96,6 % |
| `core` | 125 | 10 | 92,0 % |
| `grading` | 502 | 27 | 94,6 % |
| `results` | 449 | 14 | 96,9 % |
| `submissions` | 773 | 72 | 90,7 % |
| `web` | 712 | 58 | 91,9 % |
| **Razem** | **4337** | **246** | **94,3 %** |

## Logika domenowa (`services.py`) – próg 85 %

Kryterium akceptacji T-10: każdy `services.py` ≥ 85 %. **Wszystkie są powyżej progu**, żaden nie
wymagał dopisywania testów w tym tasku.

| Moduł | Instrukcje | Niepokryte | Pokrycie | Próg |
|---|---:|---:|---:|---|
| `apps/submissions/services.py` | 161 | 3 | **98,1 %** | ✔ |
| `apps/accounts/services.py` | 134 | 5 | **96,3 %** | ✔ |
| `apps/results/services.py` | 293 | 12 | **95,9 %** | ✔ |
| `apps/competitions/services.py` | 56 | 3 | **94,6 %** | ✔ |
| `apps/grading/services.py` | 254 | 19 | **92,5 %** | ✔ |
| `apps/appeals/services.py` | 152 | 12 | **92,1 %** | ✔ |

`apps/cms` i `apps/web` nie mają modułu `services.py` – logika domenowa nie mieszka ani w CMS-ie,
ani w widokach HTML (obie warstwy wołają serwisy pozostałych aplikacji).

## Największe luki i dlaczego zostają

| Moduł | Pokrycie | Co nie jest pokryte | Decyzja |
|---|---:|---|---|
| `apps/submissions/antivirus.py` | 49 % | Ścieżki gniazda do clamd: `INSTREAM`, reconnect, obsługa zerwanego połączenia (linie 54–62, 102–133). | Testy jednostkowe mockują protokół tylko na poziomie kontraktu; realne wywołanie clamd jest pokryte **scenariuszem E2E**, który czeka na prawdziwy werdykt `CLEAN`. Symulowanie zerwanego gniazda w pytest dawałoby test własnego mocka. |
| `apps/web/views/coordinator.py` | 74 % | Gałęzie błędów formularzy akcji koordynatora (`INVALID_PER_SUBMISSION`, `SCORE_REQUIRED`, `REVIEWER_REQUIRED`, `DISTRICT_REQUIRED`, `INVALID_ANONYMIZATION`). | Każda z tych gałęzi to `raise DomainError` po nieudanej walidacji formularza; reguła merytoryczna jest testowana w serwisie i w API. Do dopisania przy najbliższej zmianie panelu. |
| `apps/submissions/storage.py` | 89 % | Gałęzie błędów `boto3` (brak obiektu, błąd klienta) i wariant bez presigned URL. | Częściowo pokryte przez `test_storage.py`; reszta to obsługa awarii infrastruktury. |
| `apps/core/views.py` | 78 % | Gałęzie `except` healthchecku (baza/redis niedostępne). | Wymagałoby wyłączania usług w trakcie testu; healthcheck compose sprawdza wariant pozytywny w każdym przebiegu. |
| `apps/web/views/reviewer.py` | 87 % | Gałęzie błędów zapisu szkicu i wystawienia oceny (`DomainError` z serwisu). | Reguły są testowane w `apps/grading/tests`; brakuje odpowiedników na poziomie widoku. |
| `apps/web/templatetags/web_extras.py` | 83 % | Wartości puste/`None` w filtrach czasu i `dict_get`. | Trywialne gałęzie obronne. |

## Testy poza `pytest --cov`

Pokrycie liniowe nie mierzy tego, co w tym systemie jest najdroższe w naprawie: przejść między
rolami i usługami. Zajmuje się tym scenariusz E2E (`e2e/test_full_cycle.py`), który na czystym
środowisku przechodzi całą maszynę stanów – rejestracja → upload → **prawdziwy ClamAV** →
zamknięcie etapu → dwie oceny → rozjazd → moderacja → reklamacja → decyzja komisji → publikacja →
tabela publiczna. Dokłada też asercje negatywne (recenzent nie widzi nazwiska; publiczna tabela nie
zawiera nazwiska ani szkoły), których żaden test jednostkowy nie zrobi na żywym stosie
Postgres + MinIO + Redis + Celery + ClamAV.
