# Uniwersalny system zawodów wiedzy — Etap 1: wielokonkursowość

**Status:** w trakcie wykonania. Wydania A–C (`v0.20.0`–`v0.22.0`) są wdrożone na produkcji,
wydanie D („domknięcie”) powstaje — rozpiska co do wydania jest w § 4.6.
**Zakres:** zamiana portalu jednej olimpiady (Django 5 + Wagtail 6, `backend/`) w platformę, na
której **wielu organizatorów** prowadzi **wiele niezależnych konkursów wiedzy** z jednej instalacji.
**Poza zakresem etapu 1:** kreator konkursu w przeglądarce, rozliczenia, wielojęzyczność treści poza
istniejącym `pl`/`en`, federacja tożsamości między instalacjami.

Wszystkie nazwy funkcji, klas, pól i ścieżek w tym dokumencie zostały sprawdzone w repozytorium
(stan: gałąź robocza, commit `5dbb034`). Tam, gdzie proponowana nazwa jeszcze nie istnieje, jest to
oznaczone **(nowe)**.

---

## 0. Ograniczenie nadrzędne: Ciągłość Olimpiady Kwantowej

> Wymaganie organizatora, dosłownie: **„zachowaj działającą i skonfigurowaną obecną Olimpiadę
> Kwantową”.**

To jest ograniczenie twarde, nadrzędne wobec każdej decyzji projektowej w tym dokumencie. Działająca
produkcja na `olimpiadakwantowa.pl` — z jej kontami, edycją, etapami, terminami, pracami,
recenzjami, treściami redakcyjnymi i rewizjami stron — ma po każdym etapie wdrożenia zachowywać się
**z punktu widzenia użytkownika identycznie jak dziś**. Wielokonkursowość jest dołożeniem wymiaru,
a nie przepisaniem serwisu.

Reguła rozstrzygająca spory: **jeżeli zmiana jest widoczna dla uczestnika, recenzenta, opiekuna albo
koordynatora Olimpiady Kwantowej i nie wynika z osobnego polecenia organizatora — jest błędem, nawet
jeżeli przechodzą testy.**

### 0.1. Konkurs #1 powstaje z istniejących danych, nie z seedów

Migracja danych `tenancy.0002_competition_from_site` **(nowa)** tworzy dokładnie jeden wiersz
`Competition` i wypełnia go **odczytem z produkcyjnej bazy**, nie literałami z kodu:

| Pole `Competition` **(nowe)** | Źródło w istniejącej bazie |
|---|---|
| `site` (OneToOne → `wagtailcore.Site`) | jedyna istniejąca `Site` (`is_default_site=True`, utworzona przez `cms.0002_initial_tree` z `SITE_DOMAIN`) |
| `name` | `cms.SiteSettings.site_name` |
| `short_name` | `cms.SiteSettings.site_name` (bez zmian; skrót wpisze koordynator) |
| `slug` | stała `"kwantowa"` — jedyny literał w całej migracji |
| `tagline` | `cms.SiteSettings.tagline` |
| `organizer_name` | `cms.SiteSettings.organizer_name` |
| `organizer_address`, `organizer_registry` | odpowiadające pola `SiteSettings` |
| `contact_email`, `contact_phone`, `organizer_url` | `SiteSettings.contact_email` / `contact_phone` / `contact_url` |
| `logo` | ten sam obiekt `wagtailimages.Image`, co `SiteSettings.organizer_logo` (wskazanie, nie kopia) |
| `from_email` | `settings.DEFAULT_FROM_EMAIL` (na produkcji `noreply@olimpiadakwantowa.pl`, wpisywany przez `scripts/deploy.sh`, krok 3/7) |
| `email_subject_prefix` | `settings.EMAIL_SUBJECT_PREFIX` (`"[Olimpiada Kwantowa] "`) |
| `primary_domain` | `Site.hostname` |

**Czego migracja NIE robi:** nie tworzy stron, nie uruchamia `seed_cms`, `seed_legacy_content`,
`seed_regulamin`, `seed_partners` ani `seed_edition_kwantowa`, nie dotyka `wagtailcore.Page`, nie
zmienia slugów, nie zmienia `Site.hostname`, nie zapisuje `SiteSettings` (czyta je), nie zmienia
`Edition.year_label`, nie kasuje ani nie tworzy rewizji stron.

Migracje backfillu (`competition_id` na modelach domenowych) przypisują wszystko, co jest w bazie, do
Konkursu #1 jednym `UPDATE ... SET competition_id = <pk>` — bez `WHERE`, bo w bazie jednokonkursowej
nie ma innego właściciela.

### 0.2. Odwracalność i kopia przed wdrożeniem

- Każda migracja schematu ma `reverse_code` albo jest odwracalna z definicji (`AddField` nullowalne,
  `AlterField`). Migracje danych dostają `reverse_code=migrations.RunPython.noop` **tylko** tam,
  gdzie cofnięcie jest naprawdę no-opem (usunięcie kolumny i tak zabiera dane); w pozostałych
  przypadkach piszemy odwrotność jawnie.
- Migracja, która czyni FK `NOT NULL` (§ 4.4), jest odwracalna do `null=True`.
- **Przed każdym wdrożeniem z migracjami:** `pg_dump -Fc` do pliku z tagiem wydania. Wzorzec jest
  w README § 6 („Kopia zapasowa”, format `custom` pozwala na selektywny restore) oraz w
  `scripts/pull_prod_data.sh`. Do `scripts/deploy.sh` wchodzi **nowy krok 4a**: `pg_dump` przed
  `migrate`, z zapisem do `<REMOTE_DIR>/backups/pre-<APP_VERSION>.dump` i twardym `set -e` — brak
  kopii zatrzymuje wdrożenie.
- Odtworzenie: `docker compose exec -T db pg_restore -c -d olimpiada < pre-<tag>.dump`, potem
  `docker compose up -d` na poprzednim tagu obrazu.

### 0.3. Lista kontrolna produkcji po każdym etapie

Do wykonania **na `https://olimpiadakwantowa.pl`**, ręcznie, przez człowieka, po każdym wdrożeniu
z tego planu. Każdy punkt ma mieć wynik identyczny jak przed wdrożeniem.

| # | Sprawdzenie | Oczekiwane |
|---|---|---|
| 1 | `GET /` (anonim) | strona główna `cms.HomePage`, logotyp, hasło z `SiteSettings.tagline`, pasek etapów (`apps/cms/timeline.py`), etykieta edycji w nagłówku (`apps.web.context_processors.site_chrome`) |
| 2 | pasek osi czasu — najazd kursorem | rozwija się w dół, etapy bieżącej edycji, wydarzenia `EditionEvent` |
| 3 | `GET /dokumenty/` i `/dokumenty/regulamin/` | te same strony, ten sam PDF w załącznikach, to samo przekierowanie 301 z `/regulamin/` |
| 4 | `GET /register/` | formularz z CAPTCHĄ, kompletem zgód z `apps.accounts.consents.CONSENTS`, linkami do `/dokumenty/regulamin/` i `/dokumenty/rodo/`, wyszukiwarką szkół (`/api/schools/`) |
| 5 | rejestracja konta testowego + list aktywacyjny | temat `Aktywuj konto – Olimpiada Kwantowa`, link **bezwzględny** na `https://olimpiadakwantowa.pl/activate/…` |
| 6 | logowanie: koordynator / recenzent / uczestnik / opiekun | przekierowanie odpowiednio na `/coordinator/`, `/review/`, `/me/`, panel opiekuna — bez 403 i bez 404 |
| 7 | `GET /coordinator/` | pulpit, liczniki uwagi (`apps.web.coordinator_nav.attention_counters`), sekcje nawigacji rozwinięte jak dotąd |
| 8 | `GET /coordinator/stages/` | trzy etapy I edycji, terminy niezmienione co do minuty |
| 9 | `GET /wyniki/` i `/results/<id>/` | te same tabele, ta sama anonimizacja |
| 10 | `GET /status.json` | `"ok"`, puls workera, wersja z `APP_VERSION` |
| 11 | `GET /cms/` jako koordynator | drzewo stron bez zmian, liczba rewizji strony głównej bez zmian |
| 12 | `GET /api/docs/` | Swagger UI ładuje się (CSP + SRI nietknięte) |
| 13 | nagłówek CSP na `/` | bajt w bajt jak przed wdrożeniem, o ile `SiteSettings.ga_measurement_id` się nie zmienił |

Punkty 1–13 są też scenariuszem **testu dymnego** w `e2e/` — rozszerzeniem istniejącego przebiegu
Playwrighta, nie nowym projektem.

### 0.4. „Złota” fikstura: kształt produkcji bez danych osobowych

Powstaje moduł `backend/apps/tenancy/tests/golden.py` **(nowy)** — fabryka składająca w bazie
testowej **strukturę** produkcji: jedna `wagtailcore.Site` + jeden `Competition` + jedna `Edition`
(`is_current=True`) z etapami `ELIM`/`DISTRICT`/`FINAL` w dzisiejszych formatach oraz etapem
`TRAINING`, komplet stron CMS o produkcyjnych slugach (`aktualnosci`, `zadania`, `archiwum`,
`wyniki`, `dokumenty`, `partnerzy`, `harmonogram`, `warsztaty`, `kontakt`, `faq`), `SiteSettings`
z wartościami domyślnymi modelu, po jednym koncie każdej roli, trzech uczestników, dwie prace,
cztery recenzje.

**Zero PII:** nazwiska generowane (`Testowy 001`), adresy w domenie `example.invalid`, szkoły
z istniejącej fikstury `apps/schools/fixtures/`. Fikstura opisuje **kształt**, nie treść.

Testy złote (`apps/tenancy/tests/test_golden_single_competition.py` **(nowy)**) sprawdzają, że przy
**jednym** konkursie każda ścieżka odpowiada tak jak przed zmianą:

- `GET /` → 200, kontekst zawiera `site_edition_label` równe `year_label` edycji,
- `GET /register/` → 200, a lista pól zgód jest identyczna z `[c.field_name for c in CONSENTS]`,
- `GET /coordinator/` jako koordynator → 200; jako uczestnik → **403** (nie 404 — to jest rola, nie
  cudzy konkurs; różnicę opisuje § 3.6),
- `SubmissionQuerySet.for_user(coordinator)` zwraca **wszystkie** prace,
- `apps.accounts.activation.absolute_url("/x/", request)` → `https://<host żądania>/x/`,
- nagłówek CSP dla `/` nie wymienia hostów Google, gdy `ga_measurement_id` jest pusty.

Te testy uruchamiają się **przed** i **po** każdym zadaniu T1–T7 i są kryterium „nic nie ruszyliśmy”.

### 0.5. Flagi domyślnie „jak dziś”

Każdy nowy przełącznik ma wartość domyślną odtwarzającą stan obecny. Konkurs #1 dostaje w migracji
`tenancy.0002` dokładnie te wartości:

```python
Competition(
    slug="kwantowa",
    routing_mode=RoutingMode.DOMAIN,   # nie prefiks ścieżki
    path_prefix="",                    # brak prefiksu w adresach
    feature_flags={},                  # pusty dict = wszystko jak dotąd
    is_active=True,
    default_language="pl",
)
```

Katalog flag etapu 1 (`Competition.feature_flags`, `JSONField(default=dict)`):

| Flaga | Domyślnie | Co włącza |
|---|---|---|
| `path_prefix_routing` | `False` | rozstrzyganie konkursu po pierwszym segmencie ścieżki (§ 2.3) |
| `memberships_enforced` | `False`, po backfillu T2 → `True` | autoryzacja po `Membership`, a nie po globalnej grupie Django |
| `competition_settings_page` | `False` | ekran „Ustawienia konkursu” w panelu koordynatora |
| `per_competition_consents` | `False` | zgody z modelu bazy zamiast ze stałej `CONSENTS` |
| `supervisor_role` | `True` | rola opiekuna szkolnego (dziś zawsze obecna) |
| `appeals` | `True` | procedura odwoławcza (dziś zawsze obecna) |
| `certificates` | `True` | dyplomy i zaświadczenia |

Odczyt flagi ma **jedno** wejście: `Competition.has_feature(name: str) -> bool` **(nowe)** wraz ze
stałą modułu `FEATURE_DEFAULTS` **(nowa)**. Nigdzie indziej nie wolno pisać
`competition.feature_flags.get(...)` — to reguła egzekwowana w przeglądzie kodu i testem
`test_no_direct_feature_flag_access` (grep po repozytorium, wzorzec istniejących testów
architektonicznych).

### 0.6. Wdrożenie bez ręcznych kroków

`scripts/deploy.sh` zostaje jedynym narzędziem wdrożenia dla istniejącego konkursu. Zmiany (T6) są
**addytywne**:

- **nowy krok 4a**: `pg_dump` przed `migrate` (§ 0.2),
- krok 5 (`migrate`) bez zmian,
- krok 6: bramka `RUN_CONTENT_SEEDS` bez zmian, `seed_edition_kwantowa` bez zmian,
- **nowy, opcjonalny** krok: `manage.py create_competition …`, uruchamiany **wyłącznie** przy jawnej
  zmiennej `NEW_COMPETITION_SLUG`. Bez niej wdrożenie nie woła tej komendy ani razu.

`deploy/Caddyfile` zyskuje obsługę wielu domen (§ 2.5), ale blok `{$SITE_DOMAIN}` zostaje literalnie
taki, jaki jest — łącznie z `www.{$SITE_DOMAIN}` → 301 i `meet.{$SITE_DOMAIN}`.

---

## 1. Model wielodostępności

### 1.1. Dwa warianty

**Wariant (a) — Wagtail multi-site + `Competition` 1:1 z `wagtailcore.Site`.**
Jedna baza, jeden schemat, jedna instalacja. Każdy konkurs to jeden wiersz `wagtailcore.Site`
(hostname + port) i jeden wiersz `tenancy.Competition`, który do niego należy. Dane domenowe dostają
kolumnę `competition_id` albo dziedziczą właściciela przez `Edition`.

**Wariant (b) — schemat na dzierżawcę (`django-tenants`).**
Jedna baza, N schematów PostgreSQL, `search_path` przestawiany w middleware. Izolacja jest
gwarantowana przez bazę, nie przez kod aplikacji.

### 1.2. Rekomendacja: wariant (a)

Uzasadnienie wynika wprost z tego, jak ten system jest zbudowany:

1. **Wagtail już jest multi-site i już z tego korzystamy.** `wagtail.sites` jest w `INSTALLED_APPS`
   (`config/settings/base.py`), a `cms.SiteSettings` dziedziczy po **`BaseSiteSetting`** — czyli ma
   `site = ForeignKey(Site)` z unikalnością **od migracji `cms.0005`**. Nie trzeba niczego
   konwertować; trzeba przestać czytać je globalnie (§ 3.7). `apps.cms.context_processors.cms_menu`
   woła już `Site.find_for_request(request)` i pobiera strony przez `child_of(site.root_page)`.
   Drzewo stron per konkurs dostajemy **za darmo**: nowa `Site` z własnym `root_page`.
2. **Słownik szkół jest wspólny i ma być wspólny.** `apps.schools.School` to wykaz SIO/RSPO,
   aktualizowany komendą `seed_schools` przy każdym wdrożeniu (poza bramką `.first-deploy`, bo to
   dane referencyjne). W wariancie (b) trzeba by go zduplikować do każdego schematu; „shared apps”
   django-tenants rozwiązują to częściowo, ale `Participant.school_ref` — FK z `PROTECT` do
   słownika — przestaje być zwykłym kluczem obcym.
3. **Jedno konto na osobę.** Uczeń bywa uczestnikiem dwóch olimpiad, nauczyciel opiekunem w trzech.
   `accounts.User` z `USERNAME_FIELD="email"` i więzią `accounts_user_email_ci_uniq` musi zostać
   jeden. W (b) użytkownik żyje w schemacie dzierżawcy, czyli ta sama osoba to N kont, N haseł
   i N resetów hasła.
4. **Jedno wdrożenie, jeden `migrate`.** `scripts/deploy.sh` robi dziś `migrate` raz. W (b) każdy
   `migrate` jest pętlą po schematach, a `makemigrations --check --dry-run` z README § 7 przestaje
   odpowiadać na pytanie „czy produkcja jest spójna”.
5. **Jeden audyt i jeden support.** `core.AuditLog` i `support.SupportTicket` są przekrojowe:
   operator platformy chce widzieć zgłoszenia ze wszystkich konkursów, koordynator konkursu — tylko
   swoje. W (b) przekrój wymaga `UNION` po schematach.
6. **Testy.** Suita to 2171 funkcji testowych (≈2413 przypadków po parametryzacji) na
   `pytest-django`, z jedną bazą testową i `conftest.py` w każdej aplikacji. `django-tenants` wymaga
   własnego runnera i przepisania każdego `conftest`. To samo w sobie przekreśla § 0.

**Granice wariantu (a) — nazwane wprost:**

- **Izolacja jest własnością kodu, nie bazy.** Zapomniany filtr w querysecie = wyciek między
  konkursami. Odpowiedź: reguła z § 3.5 (filtr w managerze, nie w widoku i nie w szablonie),
  `CompetitionScopedMixin` oraz obowiązkowy test krzyżowy dla każdego zakresowanego widoku (T7).
- **Brak twardej gwarancji przy eksporcie.** Wydanie organizatorowi kopii „tylko jego danych”
  wymaga świadomego eksportu, a nie `pg_dump -n <schemat>`. Etap 1 zapisuje
  `manage.py export_competition --slug` **(nowe)** jako pozycję backlogu, nie jako blokadę.
- **Wspólne limity.** Jeden Postgres, jeden Redis, jeden worker Celery (kolejki `default`, `scan`,
  `mail`). Konkurs z 50 tys. uczestników spowolni konkurs z 500. Kolejki per konkurs są poza
  etapem 1.
- **Wspólny `SECRET_KEY`.** Sesja jest per host (§ 2.6), ale podpis jest wspólny — kompromitacja
  klucza dotyczy wszystkich konkursów. To samo ryzyko, co dziś.
- **Administrator danych.** Każdy konkurs ma **własnego** administratora danych; wspólna baza
  oznacza jednego procesora (operatora platformy). Potrzebna jest umowa powierzenia z każdym
  organizatorem oraz aktualizacja `apps/accounts/processing_register.py` (rejestr z art. 30 RODO,
  dziś dane w kodzie). To decyzja prawna, nie techniczna, ale musi paść przed pierwszym obcym
  konkursem (§ 8, D5).

### 1.3. Nowa aplikacja `apps/tenancy`

```
backend/apps/tenancy/
    __init__.py
    apps.py                 # TenancyConfig
    models.py               # Competition, RoutingMode, FEATURE_DEFAULTS
    middleware.py           # CompetitionMiddleware
    context.py              # current_competition(), set_current_competition(), competition_context()
    resolution.py           # resolve_competition(request): host -> Site -> Competition
    managers.py             # CompetitionScopedQuerySet, CompetitionScopedManager
    permissions.py          # IsCompetitionCoordinator, IsCompetitionMember (DRF)
    mixins.py               # CompetitionScopedMixin (widoki HTML)
    context_processors.py   # competition (dla szablonow)
    templates_catalog.py    # katalog szablonow startowych: kwantowa / przedmiotowa / pusty
    management/commands/create_competition.py
    migrations/0001_initial.py
    migrations/0002_competition_from_site.py
    tests/
```

Miejsce w `INSTALLED_APPS`: **po `apps.cms`, przed `apps.accounts`** — `Competition` ma FK do
`wagtailcore.Site` i `wagtailimages.Image`, a `accounts.Membership` będzie miał FK do `Competition`.

### 1.4. Model `Competition`

```python
class RoutingMode(models.TextChoices):
    DOMAIN = "DOMAIN", "własna domena"
    PATH = "PATH", "prefiks ścieżki na domenie platformy"


class Competition(models.Model):
    # --- tożsamość --------------------------------------------------------------------
    site = models.OneToOneField("wagtailcore.Site", on_delete=models.PROTECT,
                                related_name="competition", verbose_name="witryna")
    slug = models.SlugField("identyfikator", max_length=50, unique=True)
    is_active = models.BooleanField("aktywny", default=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    # --- marka ------------------------------------------------------------------------
    name = models.CharField("nazwa", max_length=200)
    short_name = models.CharField("nazwa skrócona", max_length=60, blank=True)
    genitive_name = models.CharField("nazwa w dopełniaczu", max_length=200, blank=True)
    locative_name = models.CharField("nazwa w miejscowniku", max_length=200, blank=True)
    tagline = models.CharField("hasło", max_length=200, blank=True)
    accent_colour = models.CharField("kolor akcentu", max_length=7, blank=True,
                                     validators=[validate_hex_colour])
    logo = models.ForeignKey("wagtailimages.Image", on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="+")
    favicon = models.ForeignKey("wagtailimages.Image", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")

    # --- organizator (podmiot prawny) --------------------------------------------------
    organizer_name = models.CharField("organizator", max_length=200)
    organizer_address = models.CharField("adres", max_length=200, blank=True)
    organizer_registry = models.CharField("dane rejestrowe", max_length=200, blank=True)
    organizer_url = models.URLField("strona organizatora", blank=True)
    contact_email = models.EmailField("e-mail kontaktowy", blank=True)
    contact_phone = models.CharField("telefon", max_length=40, blank=True)
    dpo_email = models.EmailField("inspektor ochrony danych", blank=True)

    # --- poczta -------------------------------------------------------------------------
    from_email = models.EmailField("nadawca listów", blank=True)
    email_subject_prefix = models.CharField("prefiks tematu", max_length=60, blank=True)

    # --- adresowanie ---------------------------------------------------------------------
    routing_mode = models.CharField("tryb adresowania", max_length=8,
                                    choices=RoutingMode.choices, default=RoutingMode.DOMAIN)
    primary_domain = models.CharField("domena główna", max_length=255, blank=True)
    path_prefix = models.SlugField("prefiks ścieżki", max_length=40, blank=True)

    # --- zachowanie -----------------------------------------------------------------------
    default_language = models.CharField("język domyślny", max_length=8, default="pl")
    time_zone = models.CharField("strefa czasowa", max_length=64, default="Europe/Warsaw")
    feature_flags = models.JSONField("przełączniki", default=dict, blank=True)
```

Reguły i uzasadnienia:

- **`site` jest `OneToOneField` z `PROTECT`.** Skasowanie witryny w `/cms/` nie może osierocić
  konkursu razem z jego edycjami, pracami i wynikami.
- **Odmiana nazwy.** `genitive_name` i `locative_name` istnieją, bo listy i dokumenty piszą
  „w serwisie **Olimpiady Kwantowej**” i „udział w **Olimpiadzie Kwantowej**”.
  `apps/accounts/consents.py` dokumentuje już ten problem przy `organizer_name` („odmieniać cudzej
  nazwy własnej w kodzie nie będziemy”). Dwa dodatkowe pola są tańsze niż reguły fleksyjne
  i uczciwsze niż mianownik w każdym zdaniu. Puste pole = użyj `name`.
- **`accent_colour` jako hex, nie jako arkusz CSS.** Szablon wystawia go jako zmienną CSS na
  `<html>`; generowanie arkusza per konkurs unieważniałoby manifest WhiteNoise
  (`CompressedManifestStaticFilesStorage`) i wymagałoby budowania statyków przy każdym nowym
  konkursie.
- **`favicon`** przez `wagtailimages.Image`, bo redaktor i tak wgrywa obrazy do biblioteki; widok
  `/favicon.ico` **(nowy)** oddaje rendition 32×32 albo 404.
- **`primary_domain` dubluje `site.hostname` celowo.** `Site` bywa zmieniana w `/cms/`, a my
  potrzebujemy wartości do `ALLOWED_HOSTS` i do budowania linków w listach z zadań Celery, gdzie
  żądania nie ma. Spójność pilnuje `clean()` oraz sygnał `post_save` na `Site`.
- **`time_zone`** jest per konkurs, bo `WARSAW` i `TRAINING_DEADLINE` w
  `apps/competitions/models.py` są dziś stałymi modułu. Etap 1 **nie zmienia** obliczeń czasu — pole
  jest wypełniane i wyświetlane, a użycie go w prezentacji terminów to etap 2.

`__str__` zwraca `self.short_name or self.name`; `Meta.ordering = ("name", "id")`.

### 1.5. `Edition.competition` i co zostaje przy edycji

```python
# apps/competitions/models.py
class Edition(models.Model):
    competition = models.ForeignKey("tenancy.Competition", on_delete=models.PROTECT,
                                    related_name="editions", verbose_name="konkurs")
    ...
```

Wszystko, co dziś wisi na `Edition`, **zostaje na `Edition`**: `year_label`, `is_current`,
`registration_enabled`, `registration_opens_at`, `registration_closes_at`,
`data_retention_months`. To są decyzje o **roczniku**, nie o konkursie, i tak mają zostać.

Dwie zmiany więzów:

```python
constraints = [
    # było: UniqueConstraint(fields=["is_current"], condition=Q(is_current=True),
    #                        name="competitions_edition_single_current")
    models.UniqueConstraint(fields=["competition", "is_current"], condition=Q(is_current=True),
                            name="competitions_edition_single_current"),
    # było: year_label = CharField(unique=True)
    models.UniqueConstraint(fields=["competition", "year_label"],
                            name="competitions_edition_unique_year_label"),
    # bez zmian:
    models.CheckConstraint(..., name="competitions_edition_registration_window_ordered"),
]
```

`year_label` traci `unique=True` na rzecz unikalności w parze — „I edycja 2026/2027” będzie istniała
w każdym konkursie. **Nazwa więzi `competitions_edition_single_current` zostaje ta sama**, żeby
migracja była `RemoveConstraint` + `AddConstraint` o tej samej nazwie, a nie zmianą, którą trzeba
potem tropić w logach.

`Edition.clean()` zmienia zakres zapytania:

```python
others = Edition.objects.filter(competition=self.competition, is_current=True).exclude(pk=self.pk)
```

### 1.6. `SiteSettings` — weryfikacja: **już jest per witryna**

`cms.SiteSettings(BaseSiteSetting)` ma od migracji `cms.0005_contentpage_sitesettings_home_steps`
pole `site` z unikalnością (tak działa `BaseSiteSetting`). Problemem nie jest model, tylko **cztery
miejsca, które czytają go globalnie**:

| Miejsce | Obecny kod | Co robi źle |
|---|---|---|
| `apps/cms/analytics.py:57` | `SiteSettings.objects.exclude(ga_measurement_id="").exists()` | „którakolwiek witryna” — jeden konkurs z GA włącza hosty Google w CSP wszystkich |
| `apps/accounts/consents.py:223` | `SiteSettings.objects.first()` | nazwa organizatora bierze się z przypadkowej witryny |
| `apps/support/services.py:161` | `SiteSettings.for_site(site)` po wyszukaniu `Site` | trzeba sprawdzić, czy `site` to witryna z żądania, a nie pierwsza z brzegu |
| `apps/cms/announcements.py:63` | `Announcement.objects.filter(is_active=True, …)` | model **nie ma** pola witryny — banery są globalne dla całej instalacji |

Poprawki są w § 3.7. Model `SiteSettings` **nie zmienia schematu** — to jest istotne dla § 0:
produkcja ma dokładnie jeden wiersz i on dalej obowiązuje.

Podział odpowiedzialności między `Competition` a `SiteSettings` (żeby nie powstały dwa źródła prawdy):

- **`Competition`** trzyma to, co jest **faktem o konkursie i jego organizatorze** i czego potrzebuje
  kod poza żądaniem HTTP (poczta, zadania Celery, komendy, migracje): nazwa, odmiana, organizator,
  `from_email`, domena, flagi.
- **`SiteSettings`** trzyma to, co jest **prezentacją serwisu** i należy do redaktora: hasło,
  logotyp w stopce, linki społecznościowe, `ga_measurement_id`, `registration_note`.
- Pola dublujące się dziś (`site_name`, `organizer_name`, `contact_email`) zostają w `SiteSettings`
  bez zmian. Etap 2 zamienia je na właściwości czytające `self.site.competition`. Etap 1 **ich nie
  rusza** — produkcja ma je wypełnione i tak mają zostać.

---

## 2. Rozstrzyganie konkursu z żądania

### 2.1. Łańcuch: host → `Site` → `Competition`

`apps/tenancy/resolution.py` **(nowy)**:

```python
def resolve_competition(request) -> Competition | None:
    """Konkurs dla żądania. Jedno miejsce, w którym ta reguła jest zapisana."""
    site = Site.find_for_request(request)          # wagtail.models.Site – host:port, potem domyślna
    if site is None:
        return None
    return Competition.objects.filter(site=site, is_active=True).select_related("site").first()
```

`Site.find_for_request` jest już używane w `apps/cms/context_processors.py:76` i w
`apps/support/services.py:155`, więc reguła dopasowania hosta jest ta sama, którą serwis zna
i której Wagtail używa do serwowania drzewa stron. Nie budujemy drugiej.

**Zapasowa ścieżka dla dewelopera.** `Site.find_for_request` przy braku trafienia po hoście oddaje
`Site` z `is_default_site=True`. To wystarcza dla `localhost`, `127.0.0.1` i `web` z
`DJANGO_ALLOWED_HOSTS` — deweloper bez rekordów DNS dostaje Konkurs #1 dokładnie tak jak dziś.
Dla drugiego konkursu lokalnie służy prefiks ścieżki (§ 2.3) albo wpis w `hosts`.

### 2.2. `CompetitionMiddleware` i `request.competition`

```python
# apps/tenancy/middleware.py
class CompetitionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        competition = resolve_competition(request)
        request.competition = competition
        token = set_current_competition(competition)
        try:
            return self.get_response(request)
        finally:
            reset_current_competition(token)
```

**Miejsce w `MIDDLEWARE`** (`config/settings/base.py`): **za**
`django.contrib.auth.middleware.AuthenticationMiddleware`, **przed**
`apps.accounts.preferences.PreferencesMiddleware`. Powody:

- za `SessionMiddleware` i `AuthenticationMiddleware`, bo docelowo rozstrzygnięcie ma móc zależeć od
  użytkownika (przełącznik konkursu dla osoby z kilkoma członkostwami),
- przed `PreferencesMiddleware`, bo język domyślny konkursu (`Competition.default_language`) jest
  **niższym** priorytetem niż wybór człowieka — `PreferencesMiddleware` ma go móc nadpisać,
- **za** `ContentSecurityPolicyMiddleware` być nie może, bo CSP musi znać konkurs (§ 2.5). Dlatego
  `ContentSecurityPolicyMiddleware` **zostaje tam, gdzie jest** (przed WhiteNoise), a konkurs czyta
  z `getattr(request, "competition", None)` **po** wywołaniu `get_response` — czyli już po przejściu
  przez `CompetitionMiddleware`. To działa, bo CSP ustawia nagłówek na **odpowiedzi**, a nie na
  żądaniu; kolejność wywołań w dół łańcucha nie ma tu znaczenia.

### 2.3. Tryb prefiksu ścieżki (jedna domena, wiele konkursów)

Dla organizatora, który nie chce własnej domeny (albo zanim DNS zadziała), `routing_mode=PATH`
i `path_prefix="fizyczna"` dają adresy `https://platforma.example/fizyczna/…`.

Implementacja **nie** dotyka `config/urls.py` — kolejność wzorców jest tam kontraktem (T-09) i nie
wolno jej ruszać. Zamiast tego `CompetitionMiddleware` **obcina prefiks z `request.path_info`**,
zanim urlconf w ogóle zobaczy adres:

```python
prefix = match_path_prefix(request.path_info)     # pierwszy segment, o ile istnieje konkurs PATH
if prefix is not None:
    competition, rest = prefix
    request.path_info = rest                      # "/fizyczna/me/" -> "/me/"
    request.competition = competition
    request.competition_script_prefix = f"/{competition.path_prefix}"
    set_script_prefix(request.competition_script_prefix)   # django.urls.set_script_prefix
```

`set_script_prefix` sprawia, że **wszystkie** `reverse()` i `{% url %}` w tym żądaniu produkują
adresy z prefiksem — bez zmiany ani jednego wzorca URL i ani jednego szablonu. To jest ten sam
mechanizm, którego Django używa dla aplikacji montowanych pod podścieżką (`FORCE_SCRIPT_NAME`).

Ograniczenia trybu `PATH`, wymienione tu, żeby nikt nie odkrył ich na produkcji:

- **Wagtail jest catch-allem w korzeniu.** Strony CMS drugiego konkursu mają `url_path` liczony
  z `Site.root_page`, więc prefiks musi być zdjęty **przed** `wagtail.views.serve` — jest, bo
  middleware działa przed urlconfem.
- **Slugi zarezerwowane.** `apps.cms.models.CMSPage.clean()` odrzuca dziś na drugim poziomie drzewa
  slugi `login, logout, me, review, coordinator, appeals, results, documents, cms, admin, healthz,
  api, static, media, register`. Do tej listy dochodzi **każdy `path_prefix`** — inaczej strona
  o slugu `fizyczna` w konkursie A przechwyciłaby adres konkursu B. Walidacja: `path_prefix` nie
  może należeć do tej listy i nie może kolidować z istniejącym slugiem drugiego poziomu.
- **Ciasteczka są wspólne.** Sesja i CSRF stoją na jednym hoście, więc zalogowanie w jednym konkursie
  loguje w drugim. To jest **poprawne** (jedno konto, § 3.8), ale znaczy, że tryb `PATH` nie daje
  rozdziału ciasteczek — i dlatego **nie jest domyślny**.
- **Presigned URL-e MinIO** nie zależą od prefiksu (osobny host `S3_PUBLIC_ADDRESS`), więc działają
  bez zmian.

Flaga `path_prefix_routing` jest domyślnie wyłączona i Konkurs #1 ma `routing_mode=DOMAIN`, więc
**cała ta gałąź nie wykonuje się dla Olimpiady Kwantowej ani razu** (`match_path_prefix` zwraca
`None`, gdy nie ma żadnego konkursu w trybie `PATH` — jeden zapytanie z cache 60 s).

### 2.4. `current_competition()` i zamiana `current_edition()`

`apps/tenancy/context.py` **(nowy)**, wzorowany na `django.utils.translation` (contextvar, nie
thread-local — kod chodzi pod ASGI z `UvicornWorker`, a Django wykonuje synchroniczne widoki
w nowym wątku na żądanie, więc `threading.local()` bywałby pusty):

```python
_current: ContextVar[Competition | None] = ContextVar("current_competition", default=None)

def current_competition() -> Competition | None:
    """Konkurs „na teraz”. W żądaniu ustawia go CompetitionMiddleware; poza żądaniem – wołający."""
    return _current.get()

def set_current_competition(competition): return _current.set(competition)
def reset_current_competition(token): _current.reset(token)

@contextmanager
def competition_context(competition):
    """Dla zadań Celery, komend i migracji: jawne wskazanie konkursu na czas bloku."""
    token = set_current_competition(competition)
    try:
        yield competition
    finally:
        reset_current_competition(token)
```

**`current_edition()` dostaje argument.** Dzisiejsza sygnatura
(`apps/competitions/services.py:121`):

```python
def current_edition() -> Edition | None:
    return Edition.objects.filter(is_current=True).first()
```

Docelowo:

```python
def current_edition(competition=None) -> Edition | None:
    """Bieżąca edycja konkursu. Bez argumentu – konkursu z kontekstu żądania.

    ``None`` znaczy „nie wiadomo, o który konkurs chodzi” i jest odpowiedzią pustą, a nie
    „weź pierwszą z brzegu”: w bazie wielokonkursowej pierwsza z brzegu jest cudza.
    """
    competition = competition or current_competition()
    if competition is None:
        return None
    return Edition.objects.filter(competition=competition, is_current=True).first()
```

Wywołań `current_edition()` jest **54** poza testami i migracjami. Rozkład (do podziału pracy):

| Plik | Liczba | Kto poprawia |
|---|---|---|
| `apps/web/views/coordinator*.py` (9 plików) | 15 | T5 |
| `apps/web/views/participant.py`, `participant_tools.py`, `supervisor.py` | 6 | T5 |
| `apps/cms/timeline.py` (3), `apps/cms/models.py` (3), `apps/cms/calendar.py` (1) | 7 | T4 |
| `apps/web/context_processors.py:163` (`site_chrome`), `apps/web/coordinator_nav.py:136` | 2 | T5 |
| `apps/competitions/api.py:34`, `apps/competitions/management/commands/seed_training_problems.py:75` | 2 | T3 |
| `apps/accounts/member_card.py:213,258` | 2 | T2 |
| `apps/core/status.py:178` | 1 | T1 (kontekst: `/status/` jest per host) |

Ta sama operacja dotyczy `current_registration_status()`
(`apps/competitions/models.py:255`) — dostaje argument `competition` i przestaje wołać
`Edition.objects.filter(is_current=True)` globalnie. Wołają ją
`apps.web.context_processors.registration` i serwis rejestracji.

`current_stage(edition, now=None)` i `training_stage(edition)` **nie zmieniają sygnatury** — biorą
edycję, która już jest zakresowana.

### 2.5. CSP, Caddy i wiele domen

**CSP.** `apps/web/middleware.py` buduje dziś politykę z: `SCRIPT_CDN_SOURCES`,
`STYLE_CDN_SOURCES`, `EMBED_FRAME_SOURCES`, `PROVIDER_FORM_ACTION_SOURCES`, origin
`S3_PUBLIC_ENDPOINT_URL` oraz — warunkowo — `ANALYTICS_SCRIPT_SOURCES` / `ANALYTICS_CONNECT_SOURCES`
/ `ANALYTICS_IMG_SOURCES`, gdy `apps.cms.analytics.analytics_enabled()` zwraca `True`.

Zmiany:

1. `analytics_enabled()` przestaje pytać „czy **którakolwiek** witryna ma GA” i zaczyna pytać
   „czy **ta** witryna ma GA”. Nowa sygnatura: `analytics_enabled(site_id: int | None) -> bool`,
   cache kluczowany `site_id` (dziś jest to jedna zmienna modułu `_cache` z TTL 60 s — zamienia się
   w `dict[int | None, tuple[float, bool]]`). Sygnał `reset_cache` czyści całość, tak jak dziś.
   **Dla Konkursu #1 wynik jest identyczny**, bo witryna jest jedna.
2. Hosty S3 zostają wspólne — bucket jest jeden i tak ma zostać.
3. **Nowe:** jeśli konkurs miałby kiedyś własny CDN albo własny host mediów, dochodzi pole
   `Competition.extra_csp_sources` (JSON). **Etap 1 tego pola nie wprowadza** — nie ma potrzeby,
   a każde poszerzenie CSP jest poszerzeniem powierzchni ataku.
4. `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'` bez zmian.

**Caddy.** `deploy/Caddyfile` ma dziś cztery bloki: `www.{$SITE_DOMAIN}` (301),
`{$SITE_DOMAIN}` (aplikacja), `{$S3_PUBLIC_ADDRESS}` (MinIO), `meet.{$SITE_DOMAIN}` (Jitsi).
Rekomendacja — **lista domen w `.env`, nie on-demand TLS**:

```caddyfile
# .env: EXTRA_DOMAINS="olimpiadafizyczna.pl www.olimpiadafizyczna.pl konkurs.example"
{$EXTRA_DOMAINS} {
    encode gzip zstd
    request_body { max_size {$MAX_UPLOAD_MB}MB }
    handle_path /static/* { root * /srv/static; file_server }
    handle { reverse_proxy web:8000 { header_up X-Forwarded-Proto {scheme}
                                      header_up X-Real-IP {remote_host} } }
    header { Strict-Transport-Security "max-age=31536000"
             X-Content-Type-Options "nosniff"
             Referrer-Policy "same-origin" }
}
```

Dlaczego lista, a nie `on_demand_tls`:

- on-demand TLS wystawia certyfikat dla **dowolnego** hosta, który trafi na nasz adres IP — bez
  `ask` endpointu jest to otwarty generator certyfikatów i prosty wektor wyczerpania limitów
  Let's Encrypt (50 certyfikatów / domenę / tydzień),
- `ask` endpoint trzeba by dopisać w aplikacji (`GET /internal/tls-allowed/?domain=…` sprawdzający
  `Competition.primary_domain`) — czyli **nowy publiczny adres**, którego dziś nie ma i który musi
  być niedostępny z zewnątrz,
- konkursów będzie kilka, nie kilkaset. Dopisanie domeny do `.env` i `docker compose up -d proxy`
  jest tańsze i jawniejsze.

Dołożenie domeny wymaga **zgodnej zmiany w trzech miejscach** i to jest kontrakt T6:
`EXTRA_DOMAINS` (Caddy), `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`. Komenda
`create_competition --domain` **wypisuje** te trzy linijki na wyjściu (nie edytuje `.env` — plik
należy do administratora serwera), a `manage.py check_domains` **(nowa)** porównuje
`Competition.primary_domain` z `settings.ALLOWED_HOSTS` i zgłasza rozjazd jako `WARNING` w
`/status/`.

`meet.{$SITE_DOMAIN}` (Jitsi) zostaje jeden dla całej platformy: `Stage.video_base_url` jest już
polem etapu, więc konkurs z własną instancją wpisuje ją tam (`video_provider=custom`).

### 2.6. Ciasteczka, `ALLOWED_HOSTS`, linki w listach

**Ciasteczka.** `SESSION_COOKIE_DOMAIN` i `CSRF_COOKIE_DOMAIN` **zostają nieustawione** (`None`), co
w Django znaczy „host-only cookie”. Skutek jest dokładnie ten, którego chcemy: sesja z
`olimpiadakwantowa.pl` nie jedzie na `olimpiadafizyczna.pl`. Człowiek z kontem w obu konkursach
loguje się w każdym osobno — jednym hasłem, bo konto jest jedno.

`SESSION_COOKIE_SAMESITE` **musi zostać `"Lax"`** — powód jest udokumentowany w
`config/settings/base.py` (powrót z ekranu zgody dostawcy OAuth to nawigacja GET z obcej domeny).

`CSRF_TRUSTED_ORIGINS` musi wymieniać **każdą** domenę ze schematem (`https://…`) — Django 4+ tego
wymaga i bez tego POST-y z nowej domeny kończą się `csrf_failure`
(`apps.web.views.errors.csrf_failure`, czytelna strona zamiast surowego komunikatu).

**Adresy powrotne OAuth.** `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` i `FACEBOOK_APP_ID`/`SECRET` są dziś
globalne (`config/settings/base.py`, `SOCIALACCOUNT_PROVIDERS[...]["APPS"]`). Każda nowa domena
wymaga dopisania `https://<domena>/accounts/google/login/callback/` w konsoli Google i Facebooka.
**Etap 1 nie wprowadza kluczy per konkurs** — to jedna aplikacja OAuth obsługująca wiele domen
powrotnych, co jest standardowe i tańsze niż N kompletów sekretów w bazie. Konkurs bez
skonfigurowanego dostawcy nie pokaże przycisku, bo
`apps.web.context_processors.social_providers` czyta ustawienia, nie bazę.

**Linki w listach — istotna dziura do załatania.** `apps/accounts/activation.py:122`:

```python
def absolute_url(path: str, request=None) -> str:
    if request is not None:
        return request.build_absolute_uri(path)
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path
```

**`SITE_URL` nie jest zdefiniowane nigdzie** — ani w `config/settings/base.py`, ani w
`production.py`, ani w `.env.example`, ani w `docker-compose.yml`. Skutek dziś: każda wysyłka
**spoza żądania** (zadania Celery: `apps.grading.tasks.remind_overdue_reviews`,
`apps.competitions.tasks.remind_interviews`, `apps.submissions.notifications` wołane z
`close_due_stages`, `apps.appeals.tasks.finalize_closed_appeal_windows`) wkłada do listu **adres
względny**, czyli link, którego nie da się kliknąć w kliencie pocztowym. To jest błąd istniejący,
niezwiązany z wielokonkursowością, i naprawia go ta sama zmiana:

```python
def absolute_url(path: str, request=None, competition=None) -> str:
    if request is not None:
        return request.build_absolute_uri(path)
    competition = competition or current_competition()
    if competition is not None and competition.primary_domain:
        return f"https://{competition.primary_domain}{path}"
    base = (getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else path
```

Ostatni odwrót idzie na `WAGTAILADMIN_BASE_URL`, które **jest** zdefiniowane
(`f"https://{SITE_DOMAIN}"`) — czyli instalacja jednokonkursowa naprawia się sama, bez nowej
zmiennej środowiskowej. Zmiana dotyczy **siedmiu** wołających: `activation.py:238,245`,
`guardian.py:253`, `services.py:809`, `submissions/notifications.py:218,219,269`,
`support/services.py:176,200`.

**Nadawca i temat listu.** `DEFAULT_FROM_EMAIL` i `EMAIL_SUBJECT_PREFIX` są globalne.
`apps.accounts.activation.queue_mail` dostaje opcjonalny `competition` i używa
`competition.from_email or settings.DEFAULT_FROM_EMAIL` oraz
`competition.email_subject_prefix or settings.EMAIL_SUBJECT_PREFIX`. Uwaga operacyjna: usługa `mail`
w compose ma `ALLOWED_SENDER_DOMAINS: ${SITE_DOMAIN}` i podpis DKIM dla jednej domeny — nadawca
w obcej domenie **przejdzie przez relay, ale bez DKIM**, więc trafi do spamu. Dlatego rekomendacja:
**`from_email` w domenie platformy** (`noreply@olimpiadakwantowa.pl`) z nagłówkiem `Reply-To`
ustawionym na `Competition.contact_email`, dopóki organizator nie doda rekordów SPF/DKIM/DMARC dla
swojej domeny (procedura jest w README § 4.2 i w kroku 7/7 `scripts/deploy.sh`).

---

## 3. Izolacja danych

### 3.1. Zasada: FK bezpośredni tylko tam, gdzie nie ma drogi przez `Edition`

Model, który da się doprowadzić do konkursu przez istniejące klucze obce, **nie dostaje** kolumny
`competition_id`. Powód: druga droga do tej samej prawdy to druga okazja do rozjazdu, a rozjazd
w tabeli izolacji znaczy wyciek. Denormalizacja jest dopuszczalna wyłącznie tam, gdzie alternatywą
jest złączenie po pięciu tabelach w zapytaniu wykonywanym na każdą odsłonę — i wtedy jest pilnowana
więzem albo testem.

### 3.2. Modele z FK bezpośrednim do `Competition`

| Model | Plik | Uzasadnienie |
|---|---|---|
| `competitions.Edition` | `apps/competitions/models.py:148` | korzeń całej domeny zawodów |
| `accounts.Membership` **(nowy)** | `apps/accounts/models.py` | rola jest zawsze rolą **w konkursie** (§ 3.8) |
| `accounts.Participant` | `:232` | profil uczestnika (szkoła, klasa, `public_code`, zgody) należy do jednego konkursu — § 3.3 |
| `accounts.CommitteeMember` | `:359` | członek komitetu jest komitetem **tego** konkursu; `district` i `is_appeals_committee` też |
| `accounts.SchoolSupervisor` | `:396` | `verified` jest oświadczeniem sprawdzonym przez **tego** organizatora |
| `accounts.InvitationCode` | `:500` | kod zaproszenia nadaje status w komitecie konkursu |
| `accounts.MessageBroadcast` | `:612` | rejestr wysyłek organizatora; `BroadcastGroup.EDITION_PARTICIPANTS` jest z definicji zakresowana |
| `cms.Announcement` | `apps/cms/models.py:1179` | baner wisi na **każdej** stronie serwisu — dziś globalny, musi być per konkurs |
| `support.SupportTicket` | `apps/support/models.py:88` | zgłoszenie idzie do organizatora **tego** konkursu; `null=True` dla zgłoszeń do operatora platformy |
| `core.AuditLog` | `apps/core/models.py:30` | `null=True` — wpisy o obiektach platformowych (konto, witryna) nie mają konkursu (§ 3.9) |

Wszystkie z `on_delete=models.PROTECT`, poza `AuditLog` i `SupportTicket` (`SET_NULL`) — ślad
audytowy i korespondencja nie mogą zniknąć razem z konkursem.

### 3.3. `Participant` per konkurs — decyzja i jej cena

`Participant` ma dziś `user = OneToOneField(User, related_name="participant")`. Po zmianie:

```python
user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="participations")
competition = models.ForeignKey("tenancy.Competition", on_delete=models.PROTECT,
                                related_name="participants")
class Meta:
    constraints = [models.UniqueConstraint(fields=["user", "competition"],
                                           name="accounts_participant_unique_per_competition")]
```

Dlaczego nie jeden profil na osobę: `school`, `grade`, `district`, `birth_year`,
`supervisor_email`, `publish_full_name`, `guardian_email` i **komplet zgód** (`ConsentRecord`) są
oświadczeniami złożonymi **konkretnemu administratorowi danych pod konkretnym regulaminem**. Jeden
profil dla dwóch olimpiad znaczyłby, że zgoda złożona organizatorowi A obowiązuje organizatora B —
czego nie da się obronić ani prawnie, ani technicznie (`public_code` jest identyfikatorem w tabelach
wyników **jednego** konkursu).

Cena — do wykonania w T2, wymieniona wprost, bo to jest źródło większości pracy:

- `user.participant` (dostęp przez `OneToOne`) znika. Wszystkie wystąpienia
  `getattr(user, "participant", None)` i `hasattr(user, "participant")` zamieniają się na
  `participant_for(user, competition)` **(nowe, `apps/accounts/services.py`)**. Dotknięte pliki:
  `apps/accounts/permissions.py:36`, `apps/web/mixins.py:48,52`,
  `apps/submissions/models.py` (`SubmissionQuerySet.for_user`),
  `apps/competitions/models.py:819` (`StageEntryQuerySet.for_user`),
  `apps/appeals/models.py` (`AppealQuerySet`), `apps/accounts/profile.py`,
  `apps/accounts/data_export.py`, `apps/accounts/retention.py`,
  `apps/accounts/participant_card.py`.
- `public_code` traci `unique=True` globalne na rzecz `UniqueConstraint(["competition",
  "public_code"])`. `generate_public_code()` zostaje, ale `PUBLIC_CODE_PREFIX = "OLM-"` przenosi się
  do `Competition.public_code_prefix` **(nowe pole, domyślnie `"OLM-"`)**. Konkurs #1 dostaje
  w migracji dokładnie `"OLM-"`, więc żaden istniejący kod się nie zmienia.
- `ConsentRecord` **nie dostaje** własnego FK: dochodzi do konkursu przez `participant`.
- `CERTIFICATE_NUMBER_PREFIX = "OK"` (`apps/results/models.py:107`) przenosi się do
  `Competition.certificate_prefix` **(nowe, domyślnie `"OK"`)**. Unikalność `Certificate.number`
  zostaje globalna (numer zawiera prefiks konkursu), a `Certificate.code` zostaje globalnie
  unikalny — strona weryfikacji jest publiczna i ma działać bez wskazania konkursu.

### 3.4. Modele bez własnego FK — droga do konkursu

| Model | Droga |
|---|---|
| `competitions.Stage` | `stage.edition.competition` |
| `competitions.ScoringScale`, `QualificationRule` | `…stage.edition.competition` |
| `competitions.Problem` | `problem.stage.edition.competition` |
| `competitions.StageEntry` | `entry.stage.edition.competition` **oraz** `entry.participant.competition` — spójność pilnuje `clean()` i test |
| `competitions.InterviewSlot`, `InterviewBooking` | przez `stage` / `entry` |
| `competitions.EditionEvent` | `event.edition.competition` |
| `submissions.Submission` | `submission.entry.stage.edition.competition` |
| `submissions.SubmissionFile`, `SubmissionSimilarity` | przez `submission` / `stage` |
| `grading.Review`, `FinalGrade`, `ReviewNote`, `ReviewWorkLog`, `WorkIssue` | przez `submission` |
| `grading.ProblemReviewerRule`, `RubricCriterion`, `CommentSnippet` | przez `problem` |
| `appeals.Appeal`, `AppealDecision`, `AppealDecisionCommitteeMember` | przez `submission` |
| `results.ResultsPublication` | `publication.stage.edition.competition` |
| `results.Certificate` | `certificate.edition.competition` (FK do `Edition` już jest) |
| `accounts.ConsentRecord` | `consent.participant.competition` |
| `accounts.SchoolParticipation` | `participation.edition.competition` |
| `schools.School` | **wspólny słownik — bez zakresu** (§ 8, D2) |
| `accounts.User`, `accounts.UserPreference` | **konto platformy — bez zakresu** (§ 3.8) |

Kolumna denormalizacyjna dopuszczona **tylko jedna**: `submissions.Submission.competition`
**(nowa, `db_index=True`)** — panel koordynatora i lista przydziałów recenzenta filtrują po pracach
kilkanaście razy na żądanie, a złączenie `entry → stage → edition` przy każdym takim zapytaniu jest
mierzalne. Spójności nie da się wyrazić `CheckConstraint`-em, więc zamiast niego: wypełnienie
w `apps.submissions.services.create_submission` (jedyna droga zapisu) plus test
`test_submission_competition_matches_entry` uruchamiany na **każdej** fabryce zgłoszeń.

### 3.5. Managery: `for_competition`

`apps/tenancy/managers.py` **(nowy)**:

```python
class CompetitionScopedQuerySet(models.QuerySet):
    #: Ścieżka od modelu do konkursu, np. "stage__edition__competition".
    competition_path: str = "competition"

    def for_competition(self, competition):
        """Wiersze jednego konkursu. ``None`` nie widzi niczego – domyślnie zamknięte."""
        if competition is None:
            return self.none()
        return self.filter(**{self.competition_path: competition})
```

Każdy zakresowany queryset deklaruje `competition_path` jako atrybut klasy:

- `SubmissionQuerySet.competition_path = "competition"` (kolumna denormalizacyjna),
- `ReviewQuerySet.competition_path = "submission__competition"`,
- `StageEntryQuerySet.competition_path = "stage__edition__competition"`,
- `AppealQuerySet.competition_path = "submission__competition"`,
- `ResultsPublication`, `Certificate`, `EditionEvent` — analogicznie.

**Reguła kolejności:** `for_competition()` idzie **przed** `for_user()`, nigdy odwrotnie —
`for_user` rozstrzyga rolę, `for_competition` rozstrzyga własność. Istniejące `for_user(user)`
(`apps/submissions/models.py:45`, `apps/competitions/models.py:813`) dostają **drugi, wymagany**
argument `competition`, żeby nie dało się wywołać ich po staremu i dostać cudzych danych:

```python
def for_user(self, user, competition):
    scoped = self.for_competition(competition)
    ...  # dotychczasowa logika ról, ale zawsze na ``scoped``
```

Linia `if user.groups.filter(name=GROUP_COORDINATOR).exists(): return self` w
`SubmissionQuerySet.for_user` zamienia się na `return scoped`. To jest **jedno z najważniejszych
miejsc w całej zmianie**: dziś koordynator widzi wszystko, jutro ma widzieć wszystko **swojego
konkursu**.

### 3.6. `CompetitionScopedMixin`, uprawnienia DRF i reguła 404

`apps/tenancy/mixins.py` **(nowy)**:

```python
class CompetitionScopedMixin:
    """Widok, którego dane należą do konkursu z żądania.

    ``get_queryset`` jest tu **jedynym** miejscem zawężenia. Szablon niczego nie chroni: ukrycie
    wiersza w pętli zostawia adres szczegółu otwarty, a to jest IDOR.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "competition", None) is None:
            raise Http404("Nie ma konkursu pod tym adresem.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def competition(self):
        return self.request.competition

    def get_queryset(self):
        return super().get_queryset().for_competition(self.competition)
```

**Reguła odpowiedzi, zapisana raz i obowiązująca wszędzie:**

| Sytuacja | Odpowiedź | Gdzie egzekwowana |
|---|---|---|
| niezalogowany | 302 na `/login/?next=…` | `LoginRequiredMixin` (bez zmian) |
| zalogowany, zła **rola** w tym konkursie | **403** | `RoleRequiredMixin.dispatch` (bez zmian) |
| zalogowany, dobra rola, obiekt **z innego konkursu** | **404** | queryset (`for_competition`), nie widok |
| host bez konkursu | 404 | `CompetitionScopedMixin.dispatch` |

Różnica 403 / 404 jest merytoryczna: 403 mówi „jesteś, ale nie tobie”, 404 mówi „nie ma tego tutaj”.
Koordynator konkursu A pytający o `/coordinator/stages/17/` z etapem konkursu B ma dostać **404**,
bo istnienie tego etapu nie jest jego informacją. 404 wychodzi **samo** z zawężonego querysetu
(`get_object_or_404`), więc nie ma osobnej gałęzi kodu, która mogłaby zostać pominięta.

Uprawnienia DRF — `apps/tenancy/permissions.py` **(nowe)**:

```python
class IsCompetitionCoordinator(BasePermission):
    """Koordynator **tego** konkursu. Zastępuje apps.accounts.permissions.IsCoordinator."""
    message = "Wymagana rola koordynatora tego konkursu."

    def has_permission(self, request, view) -> bool:
        competition = getattr(request, "competition", None)
        return competition is not None and has_role(request.user, competition, CompetitionRole.COORDINATOR)
```

`apps.accounts.permissions.IsCoordinator`, `IsParticipant`, `IsActiveReviewer`,
`IsAppealsCommittee` zostają jako **cienkie opakowania** wołające `has_role(...)` — tak jak dziś
`IsActiveReviewer` woła `apps.accounts.services.active_reviewer_profile`. Dzięki temu widoki DRF
nie zmieniają deklaracji `permission_classes`, a reguła ma jedno miejsce. To samo dotyczy mixinów
w `apps/web/mixins.py`: `_in_group(user, name)` zamienia się w `has_role(user, competition, role)`,
a klasy `ParticipantRequiredMixin`, `ReviewerRequiredMixin`, `CoordinatorRequiredMixin`,
`AppealsCommitteeRequiredMixin` zachowują nazwy i komunikaty.

### 3.7. Cztery globalne odczyty do naprawienia

| Plik i linia | Dziś | Po zmianie |
|---|---|---|
| `apps/cms/analytics.py:57` | `SiteSettings.objects.exclude(ga_measurement_id="").exists()` | `SiteSettings.objects.filter(site_id=site_id).exclude(ga_measurement_id="").exists()`, cache kluczowany `site_id` |
| `apps/accounts/consents.py:223` | `SiteSettings.objects.first()` | `competition.organizer_name`, z odwrotem na `DEFAULT_ORGANIZER_NAME` |
| `apps/support/services.py:161` | `SiteSettings.for_site(site).contact_email` | `competition.contact_email or SiteSettings.for_site(competition.site).contact_email` |
| `apps/cms/announcements.py:63` | `Announcement.objects.filter(is_active=True, …)` | `.filter(competition=competition, …)`; cache `cached_announcements(competition)` kluczowany identyfikatorem konkursu; `reset_cache` czyści całość |

Piąty, mniej oczywisty: `apps/cms/context_processors.py:76` — `cms_menu` **już** woła
`Site.find_for_request(request)`, więc jest poprawne. Poprawki wymaga jednak `FALLBACK_MENU`
(stała lista czterech adresów) — dla konkursu, którego drzewo stron jeszcze nie powstało, wypada
zwrócić **pustą** listę, a nie menu Olimpiady Kwantowej.

### 3.8. Członkostwa zamiast globalnych grup

```python
class CompetitionRole(models.TextChoices):
    PARTICIPANT = "participant", "uczestnik"
    REVIEWER = "reviewer", "recenzent"
    APPEALS = "appeals", "komisja odwoławcza"
    COORDINATOR = "coordinator", "koordynator"
    SUPERVISOR = "supervisor", "opiekun szkolny"


class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    competition = models.ForeignKey("tenancy.Competition", on_delete=models.CASCADE,
                                    related_name="memberships")
    role = models.CharField("rola", max_length=16, choices=CompetitionRole.choices)
    granted_at = models.DateTimeField("nadana", default=timezone.now)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="memberships_granted")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "competition", "role"],
                                               name="accounts_membership_unique")]
        indexes = [models.Index(fields=["user", "competition"], name="accounts_membership_uc_idx")]
```

Wartości `CompetitionRole` są **identyczne** z istniejącymi stałymi `GROUP_PARTICIPANT`,
`GROUP_REVIEWER`, `GROUP_APPEALS`, `GROUP_COORDINATOR`, `GROUP_SUPERVISOR`
(`apps/accounts/models.py:24–32`) — dzięki temu backfill jest jednym `INSERT … SELECT` z
`auth_user_groups`, a nie mapowaniem nazw.

**Grupy Django zostają.** `RBAC_GROUPS` i migracja `accounts.0002_rbac_groups` nie znikają, bo od
uprawnień grupy `coordinator` zależy dostęp do `/cms/` (migracja `cms.0003_coordinator_permissions`
kopiuje tam komplet uprawnień `Editors` + `Moderators`, w tym `access_admin`). Grupa staje się
**uprawnieniem do panelu redakcyjnego**, a `Membership` — **rolą w konkursie**. Serwis nadający
rolę (`apps.accounts.services`) zapisuje oba: `Membership` i przynależność do grupy.

**Superużytkownik zostaje globalny.** `User.is_superuser` i `is_staff` nie są zakresowane — to
operator platformy, a `/admin/` jest narzędziem operatora. `IsCompetitionCoordinator` **nie**
eskaluje superusera, tak jak dziś `IsCoordinator` („bez cichej eskalacji dla superusera”).

`has_role(user, competition, role)` **(nowe, `apps/accounts/services.py`)** jest jedynym miejscem,
w którym ta reguła jest zapisana; przy `feature_flags["memberships_enforced"] is False` czyta
grupy Django (stan sprzed T2), przy `True` — `Membership`. To jest przełącznik migracji, nie
docelowa opcja: po T2 flaga jest `True` dla wszystkich konkursów i pozostaje w kodzie jeden sezon.

### 3.9. Audyt

`AuditLog` dostaje `competition = FK(null=True, blank=True, on_delete=SET_NULL, db_index=True)`.
Helper `audit(actor, action, obj, diff=None, request=None)` (`apps/core/models.py:107`) ustala
konkurs w kolejności: `request.competition` → `current_competition()` → `None`. Wpisy o obiektach
platformowych (`accounts.user`, `wagtailcore.site`) mają `None` i to jest poprawna odpowiedź.
Panel audytu koordynatora filtruje `AuditLog.objects.filter(competition=request.competition)`;
`/admin/` (operator) widzi wszystko. `target_type`/`target_id` bez zmian.

---

## 4. Strategia migracji

### 4.1. Kolejność, bez przestoju

Wdrożenie jest podzielone na **cztery wydania**. Każde jest samodzielne: aplikacja działa po każdym
z nich, a Konkurs #1 zachowuje się identycznie (§ 0.3 po każdym).

**Wydanie A — konkurs istnieje, nikt go jeszcze nie używa.**
`tenancy.0001_initial` (tabela `Competition`) + `tenancy.0002_competition_from_site` (Konkurs #1
z danych, § 0.1). Aplikacja nie czyta jeszcze `request.competition`. Ryzyko: znikome.

**Wydanie B — klucze obce, nullowalne.**
`AddField(competition, null=True)` na modelach z § 3.2 + `RunPython` backfill
(`UPDATE … SET competition_id = <pk konkursu #1>`). Kod zapisu (`create_stage`, rejestracja,
`create_submission`) zaczyna **wypełniać** pole, ale kod odczytu jeszcze go nie wymaga. Stary i nowy
kod współistnieją — to jest warunek braku przestoju.

**Wydanie C — odczyt zakresowany.**
`CompetitionMiddleware` w `MIDDLEWARE`, `current_edition(competition)`, managery
`for_competition`, mixiny, uprawnienia. Flaga `memberships_enforced` przełącza się na `True` po
backfillu `Membership`. Nadal `null=True` w bazie — gdyby coś przeoczyło zapis, jest `WARNING`
w logu, a nie `IntegrityError` w środku żądania uczestnika.

**Wydanie D — domknięcie.**
`AlterField(null=False)` na wszystkich FK z § 3.2 + zmiana więzów `Edition`
(`competitions_edition_single_current`, `competitions_edition_unique_year_label`) + usunięcie
`unique=True` z `Participant.public_code`. Poprzedza je zapytanie kontrolne, uruchamiane w migracji
jako `RunPython` i przerywające wdrożenie, gdy znajdzie choć jeden wiersz z `NULL`.

Dopiero po wydaniu D `create_competition` wolno uruchomić dla drugiego konkursu.

### 4.2. Kształt migracji backfillu

```python
def forwards(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    Edition = apps.get_model("competitions", "Edition")
    # Baza jednokonkursowa: dokładnie jeden wiersz. Brak = świeża instalacja bez danych.
    competition = Competition.objects.order_by("pk").first()
    if competition is None:
        return
    if Competition.objects.count() > 1:
        raise RuntimeError("Backfill działa wyłącznie na bazie jednokonkursowej.")
    Edition.objects.filter(competition__isnull=True).update(competition=competition)

def backwards(apps, schema_editor):
    apps.get_model("competitions", "Edition").objects.update(competition=None)
```

Reguły obowiązujące każdą migrację z tego planu:

- **modele historyczne** (`apps.get_model`), nigdy import z `apps.*.models` — wzorzec jest już
  w repozytorium (`cms.0002_initial_tree` liczy nawet ścieżki treebeardu ręcznie, żeby nie zależeć
  od `wagtail.models`),
- **idempotencja**: powtórny przebieg na tej samej bazie niczego nie psuje,
- **`elidable=False`**: nie pozwalamy Django zwinąć tych migracji przy `squashmigrations`,
- **bez wywołań sieci i bez wysyłki poczty**.

### 4.3. Co zostaje bez zmian w adresach

Żaden adres publiczny się nie zmienia. `config/urls.py` nie jest edytowany w wydaniach A–D
(kolejność wzorców jest kontraktem T-09). Prefiks ścieżki jest zdejmowany w middleware
(§ 2.3), a nie wzorcem URL, więc `/me/`, `/review/`, `/coordinator/`, `/results/<id>/`,
`/dokumenty/…`, `/zgoda/<token>/` i `/api/…` zostają dokładnie tam, gdzie są. Przekierowania
`wagtail.contrib.redirects` (np. `/regulamin/` → `/dokumenty/regulamin/`) są per `Site` z definicji.

### 4.4. Weryfikacja przed `NOT NULL`

W wydaniu D, przed `AlterField`, migracja uruchamia:

```sql
SELECT 'competitions_edition' AS t, count(*) FROM competitions_edition WHERE competition_id IS NULL
UNION ALL SELECT 'accounts_participant', count(*) FROM accounts_participant WHERE competition_id IS NULL
UNION ALL SELECT 'submissions_submission', count(*) FROM submissions_submission WHERE competition_id IS NULL
-- … po jednym wierszu na każdą tabelę z § 3.2
```

Niezerowy wynik = `RuntimeError` i przerwane wdrożenie **przed** zmianą schematu. Kopia z § 0.2
i tak leży obok.

### 4.5. Seedy → szablony konkursu

Dzisiejsze komendy (`seed_cms`, `seed_legacy_content`, `seed_regulamin`, `seed_partners`,
`seed_edition_kwantowa`, `seed_demo`, `seed_training_problems`) są **narzędziami importu treści
Olimpiady Kwantowej** — `scripts/deploy.sh` trzyma je za bramką `.first-deploy`/`RUN_CONTENT_SEEDS`
właśnie dlatego, że nadpisują strony treścią z repozytorium.

Etap 1 **nie kasuje ani jednej z nich** (§ 0: produkcja ma działać dalej). Dokłada natomiast
`manage.py create_competition` **(nowa)**, która składa nowy konkurs z **szablonu**:

```
manage.py create_competition \
    --slug fizyczna --name "Olimpiada Fizyczna" --domain olimpiadafizyczna.pl \
    --organizer "Polskie Towarzystwo Fizyczne" --contact-email biuro@example.org \
    --from-template przedmiotowa \
    [--accent "#1f6feb"] [--path-prefix fizyczna] [--coordinator-email a@example.org] [--dry-run]
```

Co robi, w jednej transakcji:

1. `wagtailcore.Page` — nowy węzeł `HomePage` jako dziecko `Root`, ustawiony jako `root_page` nowej
   `wagtailcore.Site(hostname=--domain, is_default_site=False)`. Ścieżki treebeardu liczy
   `page.add_child(instance=...)` (nie migracja, więc wolno użyć `wagtail.models`).
2. Sekcje drugiego poziomu z szablonu — te same slugi, co `cms.0002_initial_tree`
   (`aktualnosci`, `zadania`, `archiwum`, `wyniki`) plus `dokumenty` przez
   `apps.cms.site_tree.ensure_document_index`.
3. `cms.SiteSettings` dla nowej witryny z wartościami z argumentów.
4. `tenancy.Competition` powiązany z witryną.
5. `competitions.Edition` (`year_label` z `--edition-label`, domyślnie bieżący rocznik,
   `is_current=True`) + etapy z szablonu przez istniejący `apps.competitions.services.create_stage`
   (który tworzy też `ScoringScale` i `QualificationRule` — nie duplikujemy tej wiedzy).
6. `accounts.Membership(role=COORDINATOR)` dla `--coordinator-email`, o ile podany.
7. Wypisuje na stdout trzy linijki do `.env` (§ 2.5) i **nie** dotyka plików.

Katalog szablonów — `apps/tenancy/templates_catalog.py` **(nowy)**, dane, nie kod:

| `--from-template` | Etapy | Formaty | Zgody | Uwaga |
|---|---|---|---|---|
| `kwantowa` | ELIM → DISTRICT (rozmowa) → FINAL + TRAINING | `pdf`, `ipynb`, `py`, `jpg` | pełny zestaw z `CONSENTS` | odwzorowanie dzisiejszej konfiguracji; **nie** kopiuje treści redakcyjnych |
| `przedmiotowa` | ELIM → DISTRICT → FINAL | `pdf` | `TERMS`, `PRIVACY`, `GUARDIAN` | typowa olimpiada MEN |
| `pusty` | brak | `pdf` | `TERMS`, `PRIVACY` | organizator układa wszystko sam |

Szablon `kwantowa` **nie jest** `seed_edition_kwantowa`: ta komenda wpisuje konkretne daty
z harmonogramu organizatora i zostaje osobno, wyłącznie dla Konkursu #1. Szablon podaje
**strukturę i domyślne odstępy** (`review_deadline_at` = deadline + 14 dni, okno reklamacji
+2/+9 dni — te same reguły, co w `seed_edition_kwantowa`), a daty wpisuje koordynator w panelu.

Etapy w katalogu są opisane dwiema liczbami dni: `opens_after_days` (ile po terminie oddania
poprzedniego etapu ten się otwiera) i `length_days` (długość okna oddawania prac; `None` znaczy
piaskownicę bez terminu, czyli `StageKind.TRAINING`). Komenda odkłada je od **dnia bazowego**,
którym jest **pierwszy dzień następnego miesiąca**: data rozpoznawalna na pierwszy rzut oka jako
wartość początkowa, a zarazem zostawiająca od dwóch do pięciu tygodni na poprawienie harmonogramu.
Oznaczenie edycji bez `--edition-label` to `I edycja <rocznik szkolny liczony od września>`.

### 4.6. Stan wdrożenia

| Wydanie | Wersja | Data | Co naprawdę weszło |
|---|---|---|---|
| A | `v0.20.0` | 2026-09-17 | model `tenancy.Competition` 1:1 z witryną Wagtaila, `CompetitionMiddleware` i `current_competition()`, Konkurs #1 zbudowany migracją z istniejącej witryny (§ 0.1), `create_competition` bez edycji i etapów |
| B | `v0.21.0` | 2026-09-17 | `accounts.Membership` i role per konkurs za flagą `memberships_enforced`, nullowalne `competition` na modelach z § 3.2 z backfillem do Konkursu #1, `Caddyfile` z `EXTRA_DOMAINS`, `pg_dump` przed migracjami, testy izolacji i niezmienniczości |
| C | `v0.22.0` | 2026-09-17 | odczyty w panelach zakresowane przez `for_competition` i `current_edition(competition)`, CMS per witryna Wagtaila, `Announcement.competition`, strona „Ustawienia konkursu” za flagą `competition_settings_page`, 14 z 15 testów izolacji zielonych |
| D | w toku | — | domknięcie: `NOT NULL` na kolumnach konkursu, uczestnik per konkurs, więzy `Edition` per konkurs, kolumny platformowe, `create_competition` z edycją i etapami, `check_memberships` |

Wydanie D zamyka odstępstwa, które zostały po T5 — każde z nich było świadomą ceną za brak
przestoju (§ 4.1), a nie przeoczeniem:

- **lista kont i audyt zakresowane „przez wykluczenie”.** Panel koordynatora filtrował konta
  i wpisy audytu odejmowaniem tego, co na pewno cudze, zamiast doborem tego, co na pewno swoje —
  działa to wyłącznie dopóki „cudze” da się wyliczyć, czyli dopóki kolumna konkursu bywa pusta.
- **`SchoolSupervisor` z tolerancją `NULL`.** Profil opiekuna bez konkursu był widziany przez
  każdy konkurs, bo inaczej opiekunowie sprzed backfillu zniknęliby z paneli w dniu wdrożenia.
- **przebieg retencji bez zakresu.** `apps.accounts.retention` liczył okres z ostatniego deadline'u
  etapu bez pytania, czyj to etap — przy jednym konkursie to ta sama odpowiedź, przy dwóch nie.
- **integracje i szablony dyplomów zakresowane przez edycję.** Klucze API, webhooki i szablony
  dochodziły do właściciela łańcuchem relacji zamiast własną kolumną; druga droga do tej samej
  prawdy jest drugą okazją do rozjazdu (§ 3.1).

---

## 5. Marka i dokumenty jako konfiguracja (projekt teraz, wykonanie w etapie 2)

### 5.1. Inwentarz zaszytych napisów

Grep po `Olimpiada Kwantowa`, `Quantum AI`, `contact@qaif.org`, `olimpiadakwantowa` (bez testów
i fikstur) daje **21 plików**. Podział na trzy kategorie:

**(A) Tematy i treści listów — zamienia je kontekst konkursu:**

| Plik | Co zawiera | Czym zastąpić |
|---|---|---|
| `apps/accounts/activation.py:70–72,141,159,169,197,205` | `ACTIVATION_SUBJECT`, `EMAIL_CHANGE_SUBJECT`, `EMAIL_CHANGED_NOTICE_SUBJECT`, podpis listu | szablon z `%(competition)s`; podpis = `competition.name` |
| `apps/accounts/guardian.py:60,61,173,193,204,212` | `GUARDIAN_SUBJECT`, `GUARDIAN_CONFIRMED_SUBJECT`, treść prośby | `competition.locative_name` („udział w …”) |
| `apps/accounts/services.py:649,711,738` | `INVITATION_SUBJECT`, treść zaproszenia do komitetu | `competition.genitive_name` („komitet …”) |
| `apps/submissions/notifications.py:48–51,63,108,145` | cztery tematy + podpisy | jw. |
| `apps/grading/reports.py:194,213`, `apps/grading/deadlines.py:142,144` | `REMINDER_SUBJECT`, tematy przypomnień | `competition.short_name` |
| `apps/support/services.py:186,212` | tematy zgłoszeń | jw. |
| `apps/competitions/video.py:208` | nazwa w zaproszeniu na rozmowę | jw. |
| `config/settings/base.py:296` | `EMAIL_SUBJECT_PREFIX` | `Competition.email_subject_prefix` (odwrót na ustawienie) |
| `config/settings/base.py:418` | `WAGTAIL_SITE_NAME` | zostaje jako nazwa instalacji w `/cms/` |

**(B) Dokumenty i treści redakcyjne — konfiguracja, nie kod:**
`apps/accounts/consents.py:129,133,145` (etykiety zgód), `apps/cms/site_tree.py:32`
(`INDEX_INTRO`), `apps/cms/management/commands/seed_*` (import treści Olimpiady Kwantowej — zostają
jako import, § 4.5), `apps/cms/management/commands/build_guardian_consent_pdf.py:73,351–353`
(metadane PDF-a wzoru zgody).

**(C) Prezentacja i dokumenty wydawane przez system:**
`apps/results/certificates.py:71,76,273` (`SIGNATURE_LINE` „Przewodniczący Komitetu Głównego …”,
autor PDF-a) → `Competition.certificate_signature_line` **(nowe pole)**;
`apps/results/models.py:107` (`CERTIFICATE_NUMBER_PREFIX`) → `Competition.certificate_prefix`;
`apps/cms/calendar.py:46,49,296` (`UID_DOMAIN`, `PRODID`, `X-WR-CALNAME`) →
`competition.primary_domain` i `competition.name`;
`apps/accounts/profile.py:51` (`ANONYMISED_EMAIL_DOMAIN = "invalid.olimpiadakwantowa.pl"`) →
`f"invalid.{competition.primary_domain}"`, **z zachowaniem starej wartości dla istniejących kont**
(domena adresu anonimowego jest częścią danych, nie konfiguracji);
`backend/templates/500.html:6,20` — strona błędu renderowana **bez** kontekstu bazy (to jest jej
sens), więc napis zostaje; zamiana na neutralny („Skontaktuj się z organizatorem”) bez adresu;
`apps/web/captcha.py:56,66` (adres w komunikacie o niedostępności CAPTCHY) →
`competition.contact_email`;
`backend/templates/web/certificate_verify.html:17`, `statistics.html:13` → `{{ competition.name }}`;
`apps/support/models.py:3` i `apps/competitions/training.py:9` — komentarze, bez zmian.

### 5.2. Zgody: ze stałej modułu do modelu

Dziś zestaw zgód jest krotką `CONSENTS` w `apps/accounts/consents.py` z literalnymi wersjami
(`TERMS_VERSION = "1.0 z 2 września 2026"`, `PRIVACY_VERSION`, `GUARDIAN_VERSION`,
`PUBLISH_NAME_VERSION`). Moduł uzasadnia to wprost: wersja ma zmieniać się razem z wgraniem
dokumentu, czyli razem ze zmianą w repozytorium, a nie po edycji strony w `/cms/`.

Ta zasada zostaje — zmienia się tylko to, **czyje** to zgody:

```python
class ConsentDefinition(models.Model):
    competition = FK(Competition, related_name="consent_definitions")
    kind = CharField(choices=ConsentKind.choices)          # bez zmian
    field_name = CharField(max_length=40)                  # bez zmian
    text = TextField()                                     # szablon z {link} i {organizer}
    link_text = CharField(blank=True)
    document_slug = SlugField(blank=True)
    version = CharField(max_length=100)                    # wpisywana ręcznie przy nowelizacji
    required = BooleanField(default=False)
    required_for_minor = BooleanField(default=False)
    help_text = CharField(blank=True)
    missing_message = CharField(blank=True)
    ordering = PositiveSmallIntegerField(default=0)
    class Meta:
        constraints = [UniqueConstraint(fields=["competition", "kind"], name="accounts_consentdef_unique")]
```

- `CONSENTS` **zostaje w kodzie** jako `DEFAULT_CONSENTS` — zestaw startowy, z którego
  `create_competition` zakłada wiersze nowemu konkursowi. Kod nie przestaje znać poprawnego
  brzmienia zgód; przestaje być jedynym miejscem, gdzie ono stoi.
- `ConsentRecord.document_version` (dowód) **nie zmienia się w niczym** — dalej jest kopią napisu,
  a nie kluczem obcym do definicji. To jest dokładnie ten sam argument, który stoi już w docstringu
  modelu: dowód ma zamarznąć.
- Zmiana wersji w panelu wymaga **potwierdzenia** („od tej chwili nowe zgody będą zapisywane pod
  wersją X”) i idzie do audytu jako `consent_definition.version_changed`.
- Migracja dla Konkursu #1 wpisuje wiersze **dokładnie** z dzisiejszych `CONSENTS`, z tymi samymi
  wersjami. Formularz rejestracji, `GET /api/auth/consents/` i panel uczestnika pokazują ten sam
  komplet i to samo brzmienie — punkt 4 listy z § 0.3.
- Flaga `per_competition_consents` przełącza źródło; dopóki jest `False`, czyta się stałą.

### 5.3. Szablony dokumentów z podstawieniami

Dokumenty wydawane przez system (dyplomy, zaświadczenia, wzór zgody opiekuna) dostają w etapie 2
model `DocumentTemplate` **(nowy, `apps/tenancy`)**: `competition`, `kind`
(`CertificateKind` + `GUARDIAN_FORM`), `version`, `body` (tekst z podstawieniami),
`signature_line`, `is_current`. Podstawienia: `{competition}`, `{competition_genitive}`,
`{organizer}`, `{edition}`, `{participant_code}`, `{stage}`, `{date}`, `{number}`, `{code}`.
Wersjonowanie **buduje na istniejącym**: tak samo jak `ConsentRecord.document_version`,
`Certificate` dostaje `template_version` (kopię napisu), więc dokument wydany dziś da się odtworzyć
po nowelizacji szablonu. Renderowanie zostaje w `apps/results/certificates.py` (ReportLab) — zmienia
się źródło napisów, nie skład.

---

## 6. Podział prac

Zadania są pomyślane do **równoległego** wykonania przez agentów. Każde ma **wyłączną własność
plików** — dwa zadania nie edytują tego samego pliku. Tam, gdzie to niemożliwe, zależność jest
jawna i zadanie czeka.

| Zadanie | Zależy od | Ryzyko | Istota |
|---|---|---|---|
| **T1** tenancy: model, middleware, kontekst, Konkurs #1 | — | **niskie** | nowa aplikacja, nikt jej jeszcze nie czyta |
| **T2** konta: `Membership`, `Participant` per konkurs, role | T1 | **wysokie** | rusza `OneToOne` na `User`, dotyka każdej ścieżki autoryzacji |
| **T3** domena: zakresowanie `competitions`/`submissions`/`grading`/`results`/`appeals` | T1, T2 | **wysokie** | najwięcej zapytań i najwięcej testów |
| **T4** CMS: ustawienia per witryna, menu, banery, dokumenty | T1 | **średnie** | cztery globalne odczyty (§ 3.7) |
| **T5** web: widoki, mixiny, nawigacja, „Ustawienia konkursu” | T1–T4 | **średnie** | 23 wywołania `current_edition()` w samych widokach |
| **T6** wdrożenie: Caddy, `.env`, `create_competition`, `pg_dump` | T1 | **niskie** | poza aplikacją; nie może zmienić kroków dla Konkursu #1 |
| **T7** testy: fikstura dwóch konkursów, reguła 404, testy złote | T1 | **średnie** | warunek przyjęcia T2–T5 |

Kolejność wydań z § 4.1: **A** = T1; **B** = T2 + T3 (schemat, nullowalny) + T6; **C** = T4 + T5
i przełączenie flag; **D** = domknięcie `NOT NULL`. T7 biegnie równolegle od początku.

### T1 — aplikacja `apps/tenancy`, middleware, Konkurs #1

*Własność plików:* cały `backend/apps/tenancy/`; w `config/settings/base.py` wyłącznie dwie listy
(`INSTALLED_APPS`, `MIDDLEWARE`) i jeden wpis w `TEMPLATES[0]["OPTIONS"]["context_processors"]`.
*Zakres:* modele z § 1.4, `resolve_competition`, `CompetitionMiddleware`, `current_competition()`,
`competition_context()`, `CompetitionScopedQuerySet`, `CompetitionScopedMixin`,
`IsCompetitionCoordinator`, migracje `0001`/`0002`, context processor `competition`. Dodatkowo:
`apps/core/status.py:178` i poprawka `absolute_url` (§ 2.6) — to jedyne pliki poza `tenancy`,
których T1 dotyka.
*Czego NIE wolno ruszyć:* żadnego modelu domenowego, żadnego widoku, `config/urls.py`,
`deploy/Caddyfile`, seedów.
*Testy strzegące:* wszystkie istniejące — middleware bez konkursu nie może zmienić ani jednej
odpowiedzi (`apps/web/tests/test_public.py`, `apps/cms/tests/test_security.py`); nowe
`apps/tenancy/tests/test_resolution.py`, `test_middleware.py`, `test_migration_0002.py`.

### T2 — role i członkostwa

*Własność plików:* `apps/accounts/` (`models.py`, `permissions.py`, `services.py`,
`supervisors.py`, `profile.py`, `data_export.py`, `retention.py`, `member_card.py`,
`participant_card.py`, `consents.py`, `guardian.py`, `activation.py`, `messaging.py`, `admin.py`,
`api.py`, `serializers.py`, migracje) oraz `apps/web/mixins.py`.
*Zakres:* `Membership` + `CompetitionRole` + `has_role()`; FK na `Participant`, `CommitteeMember`,
`SchoolSupervisor`, `InvitationCode`, `MessageBroadcast`; `participant_for(user, competition)`;
backfill z `auth_user_groups`; przełącznik `memberships_enforced`.
*Czego NIE wolno zmienić:* brzmienia zgód, wersji dokumentów, treści listów, wartości `GROUP_*`,
uprawnień grupy `coordinator` w `/cms/` (migracja `cms.0003`), zachowania
`purge_unactivated_accounts` i `anonymise_account`.
*Testy strzegące:* `apps/accounts/tests/test_permissions.py`, `test_registration.py`,
`test_consents.py`, `test_invitations.py`, `test_retention.py`, `test_migrations.py`,
`test_public_code.py`; `apps/web/tests/test_consents.py`, `test_account_deletion.py`.
*Ryzyko:* zamiana `user.participant` (`OneToOne`) na relację odwrotną — jedno pominięte
`hasattr(user, "participant")` daje uczestnikowi 403 na własnym panelu. Mitygacja: usunięcie
`related_name="participant"` **wymusza** `AttributeError` w każdym przeoczonym miejscu, więc nie
ma cichej awarii.

### T3 — zakresowanie domeny zawodów

*Własność plików:* `apps/competitions/` (bez `management/commands/seed_edition_kwantowa.py`),
`apps/submissions/`, `apps/grading/`, `apps/results/`, `apps/appeals/` — modele, managery, serwisy,
API, zadania Celery, migracje.
*Zakres:* `Edition.competition`, `Submission.competition`, `competition_path` na każdym querysecie,
`for_user(user, competition)`, `current_edition(competition)`,
`current_registration_status(competition)`; zakresowanie zadań
`apps.competitions.tasks.remind_interviews`, `apps.grading.tasks.remind_overdue_reviews`,
`apps.submissions.tasks.close_due_stages`, `apps.appeals.tasks.finalize_closed_appeal_windows` —
chodzą po **wszystkich** konkursach, ale każdy list buduje link z konkursu pracy przez
`competition_context()`.
*Czego NIE wolno zmienić:* przepływu ocen (`supersede_earlier_versions`, `lock_for_review`,
`revise_review`, `unassign_reviewer`), reguł skali (`validate_scoring_values`), progów
(`QualificationRule`), anonimizacji (`Anonymization`, k-anonimowość `INITIALS_SCHOOL`), terminów
(`submission_deadline`, `grace_seconds`), kluczy obiektów w MinIO, numeracji dyplomów istniejących.
*Testy strzegące:* całe `apps/submissions/tests/`, `apps/grading/tests/`, `apps/results/tests/`,
`apps/appeals/tests/`, `apps/competitions/tests/` — w szczególności `test_concurrency.py`,
`test_critic_*.py` i testy liczby zapytań.

### T4 — CMS per witryna

*Własność plików:* `apps/cms/` w całości (modele, `analytics.py`, `announcements.py`,
`context_processors.py`, `timeline.py`, `calendar.py`, `site_tree.py`, migracje).
*Zakres:* `Announcement.competition`; `analytics_enabled(site_id)` z cache per witryna;
`cached_announcements(competition)`; `FALLBACK_MENU` pusty dla nieznanej witryny;
`HomePage`/`ProblemsPage`/`ResultsPage`/`ArchiveIndexPage` czytające `current_edition(competition)`
przez `page.get_site().competition`; `timeline.py` i `calendar.py` zakresowane; dopisanie prefiksów
ścieżki do listy slugów zarezerwowanych w `CMSPage.clean()`.
*Czego NIE wolno zmienić:* drzewa stron Konkursu #1, slugów, `WAGTAILDOCS_SERVE_METHOD`,
`WAGTAILEMBEDS_FINDERS`, polityki kolekcji, treści seedów, rewizji stron.
*Testy strzegące:* `apps/cms/tests/test_security.py`, `test_home_sections.py`,
`test_stage_timeline.py`, `test_timeline_strip.py`, `test_calendar.py`, `test_document_page.py`,
`test_partners.py`, `test_social_links.py`, `test_legacy_content.py` oraz testy liczby zapytań
`ResultsPage` i `ArchiveEditionPage`.

### T5 — interfejs WWW i „Ustawienia konkursu”

*Własność plików:* `apps/web/views/*`, `apps/web/urls.py`, `apps/web/context_processors.py`,
`apps/web/coordinator_nav.py`, `apps/web/forms.py`, `backend/templates/`.
*Zakres:* `CompetitionScopedMixin` w każdym widoku koordynatora, recenzenta, uczestnika i opiekuna;
`site_chrome` czyta konkurs; `coordinator_nav.current_stages()` zakresowane; nowa strona
`/coordinator/competition/` („Ustawienia konkursu”: marka, organizator, kontakt, flagi — pola
z § 1.4 **bez** `slug`, `site`, `routing_mode` i `path_prefix`, które należą do operatora
platformy), audyt `competition.updated`.
*Czego NIE wolno zmienić:* adresów, układu paneli, rozwijanych sekcji nawigacji, liczników uwagi,
treści komunikatów błędów, reguły 403 dla złej roli, wyglądu paska osi czasu.
*Testy strzegące:* `apps/web/tests/` w całości, w szczególności `test_coordinator_*.py`,
`test_participant_*.py`, `test_school_picker.py`, `test_antispam.py`, `test_password_reset.py`.

### T6 — wdrożenie

*Własność plików:* `deploy/Caddyfile`, `scripts/deploy.sh`, `.env.example`, `docker-compose.yml`,
`apps/tenancy/management/commands/create_competition.py`, `apps/tenancy/templates_catalog.py`,
`README.md` (§ 3 i § 6).
*Zakres:* `EXTRA_DOMAINS` w Caddy, krok 4a (`pg_dump`), opcjonalny krok `create_competition`,
komenda `check_domains`, dokumentacja dodania konkursu.
*Czego NIE wolno zmienić:* bloków `{$SITE_DOMAIN}`, `www.{$SITE_DOMAIN}`, `{$S3_PUBLIC_ADDRESS}`
i `meet.{$SITE_DOMAIN}`; kroków 1–3 i 5–7 `deploy.sh`; bramki `RUN_CONTENT_SEEDS`; wywołań
`seed_edition_kwantowa` i `seed_schools`; istniejących zmiennych `.env` na serwerze.
*Testy strzegące:* `docker compose config`, pełny przebieg `e2e/`, ręczna lista z § 0.3.

### T7 — testy

*Własność plików:* `backend/conftest.py`, `apps/*/tests/conftest.py`, `apps/*/tests/factories.py`,
`apps/tenancy/tests/`, `e2e/`.
*Zakres:* patrz § 7.
*Czego NIE wolno zmienić:* asercji istniejących testów. Test, który przestaje przechodzić po
zmianie z T2–T5, jest **sygnałem regresji**, a nie pozycją do poprawienia — poprawka idzie do kodu,
nie do testu. Wyjątkiem są sygnatury fabryk (dodanie argumentu `competition` z wartością domyślną).

---

## 7. Strategia testów

Punkt wyjścia: **2171 funkcji testowych, ≈2413 przypadków** (`pytest -q` w kontenerze `web`,
README § 7). Żaden z nich nie ma prawa zmienić wyniku bez uzasadnienia w tym dokumencie.

### 7.1. Fikstura dwóch konkursów

W `backend/conftest.py` (poziom projektu) powstają trzy fikstury:

```python
@pytest.fixture
def competition(db):
    """Konkurs #1 – „domyślny”. Każdy istniejący test dostaje go bez zmiany swojego kodu."""

@pytest.fixture
def other_competition(db):
    """Konkurs #2 – istnieje wyłącznie po to, żeby dało się sprawdzić, że go nie widać."""

@pytest.fixture(autouse=True)
def _bind_competition(request, competition):
    """Ustawia kontekst i host żądania na konkurs #1, o ile test nie wskaże inaczej."""
```

`autouse` jest tu świadomą decyzją: bez niego **każdy** z 2171 testów wymagałby edycji. Z nim
istniejące testy widzą dokładnie to, co dziś, a testy wielokonkursowe wskazują konkurs jawnie.
Fabryki (`apps/*/tests/factories.py`) dostają argument `competition` z wartością domyślną braną
z kontekstu — to jedyna dopuszczalna zmiana w istniejących plikach testowych.

### 7.2. Reguła krzyżowa — obowiązkowa

**Każdy zakresowany widok i każdy zakresowany endpoint musi mieć test, który pokazuje 404 na
obiekcie drugiego konkursu.** Wzorzec:

```python
def test_coordinator_of_a_cannot_see_stage_of_b(web_client, coordinator_a, stage_b):
    response = web_client.get(f"/coordinator/stages/{stage_b.pk}/", HTTP_HOST="a.example")
    assert response.status_code == 404      # nie 403: istnienie etapu B nie jest informacją A
```

Dopełnienie na poziomie querysetu (tańsze i bliżej reguły):

```python
def test_submissions_for_user_are_scoped(coordinator_a, competition_a, submission_b):
    assert submission_b not in Submission.objects.for_user(coordinator_a, competition_a)
```

Minimalny komplet do napisania (T7), po jednym na obszar: etapy, zadania, zgłoszenia, recenzje,
oceny końcowe, reklamacje, publikacje wyników, dyplomy, uczestnicy, członkowie komitetu, kody
zaproszeń, komunikaty organizatora, zgłoszenia do supportu, wydarzenia edycji, wpisy audytu,
banery, strony CMS. **17 testów krzyżowych** to minimum przyjęcia T3–T5.

### 7.3. Testy niezmienności (regresja Konkursu #1)

Poza testami złotymi z § 0.4:

| Test | Co chroni |
|---|---|
| `test_csp_header_unchanged_for_single_competition` | nagłówek CSP bajt w bajt (porównanie ze stałą w teście) |
| `test_consent_labels_unchanged` | brzmienie i wersje wszystkich czterech zgód |
| `test_email_subjects_unchanged` | dziewięć tematów listów |
| `test_public_code_prefix_is_olm` | `OLM-` dla Konkursu #1 |
| `test_certificate_number_prefix_is_ok` | `OK/<rok>/<nr>` |
| `test_menu_matches_seeded_tree` | kolejność i tytuły menu |
| `test_query_counts_unchanged` | liczba zapytań `/`, `/wyniki/`, `/coordinator/` (istniejące testy liczby zapytań rozszerzone o próg) |

### 7.4. Scenariusz E2E

Istniejący przebieg `scripts/e2e.sh` (rejestracja → upload → skan ClamAV → zamknięcie etapu → dwie
oceny → moderacja → reklamacja → decyzja → publikacja) zostaje **bez zmian** i biegnie na Konkursie
#1. Dochodzi drugi, krótszy przebieg: `create_competition --slug drugi --path-prefix drugi`,
rejestracja uczestnika w drugim konkursie, próba wejścia na panel koordynatora konkursu #1 tym
kontem → 403, próba otwarcia zgłoszenia z konkursu #1 → 404.

---

## 8. Otwarte decyzje organizatora

| # | Pytanie | Warianty | **Rekomendacja** |
|---|---|---|---|
| **D1** | Domena na konkurs czy prefiks ścieżki? | (a) własna domena; (b) `platforma.example/<konkurs>/` | **Domena na konkurs** jako domyślna. Konkurs jest marką organizatora, a własna domena daje rozdział ciasteczek sesji i CSRF „za darmo” (§ 2.6). Prefiks ścieżki zostaje jako **tryb startowy** — dla konkursu, który czeka na DNS, i dla dewelopera. `Competition.routing_mode` pozwala przejść z (b) do (a) bez migracji danych; przy przejściu dodajemy 301 z prefiksu na domenę przez `wagtail.contrib.redirects`. |
| **D2** | Czy słownik szkół jest wspólny? | (a) wspólny; (b) kopia na konkurs | **Wspólny.** To publiczny rejestr SIO/RSPO, nie dane organizatora; `seed_schools` chodzi przy każdym wdrożeniu i jest idempotentny (upsert po RSPO, wygaszanie zamiast kasowania). Kopia per konkurs znaczyłaby N aktualizacji i N rozjazdów. Ryzyko prywatności zerowe — w tabeli nie ma danych osobowych. |
| **D3** | Czy opiekunowie szkolni są wspólni? | (a) profil per konkurs; (b) jeden profil platformy | **Profil per konkurs** (`SchoolSupervisor.competition`). Powód: `verified` jest oświadczeniem sprawdzonym przez **tego** organizatora, a `SchoolParticipation` dotyczy edycji konkretnego konkursu. Nauczyciel ma jedno konto i widzi listę swoich konkursów; wiązanie z uczniem i tak idzie po adresie e-mail (`Participant.supervisor_email`), więc nic nie trzeba duplikować ręcznie. |
| **D4** | Jedno konto uczestnika na wszystkie konkursy? | (a) jedno konto, członkostwa per konkurs; (b) konto per konkurs | **Jedno konto, członkostwa per konkurs** (`User` globalny, `Membership` + `Participant` zakresowane). Uczeń startujący w dwóch olimpiadach ma jedno hasło i jeden reset hasła; zgody, szkoła, klasa i `public_code` są mimo to osobne dla każdego konkursu, bo są oświadczeniem wobec konkretnego administratora danych (§ 3.3). |
| **D5** | Kto jest administratorem danych? | (a) operator platformy; (b) organizator konkursu, operator jako procesor | **(b)** — organizator jest administratorem swoich uczestników, operator platformy procesorem. Wymaga: umowy powierzenia z każdym organizatorem, `Competition.dpo_email` w klauzuli informacyjnej i rozszerzenia `apps/accounts/processing_register.py` o wymiar konkursu. **Musi być rozstrzygnięte przed pierwszym obcym konkursem.** |
| **D6** | Wspólna poczta wychodząca? | (a) nadawca w domenie platformy + `Reply-To` organizatora; (b) nadawca w domenie organizatora | **(a) na start.** Usługa `mail` w compose ma `ALLOWED_SENDER_DOMAINS: ${SITE_DOMAIN}` i jeden klucz DKIM, więc nadawca w obcej domenie trafia do spamu. (b) jest możliwe, gdy organizator doda SPF/DKIM/DMARC — procedura jest w README § 4.2. |
| **D7** | Czy koordynator konkursu może zmieniać domenę i slug? | (a) tak; (b) nie, to operator | **(b).** Domena wymaga zgodnej zmiany w Caddy, `ALLOWED_HOSTS` i `CSRF_TRUSTED_ORIGINS` (§ 2.5) — czyli dostępu do serwera. Ekran „Ustawienia konkursu” pokazuje te pola tylko do odczytu. |

---

## 9. Załącznik: czego ten etap **nie** zmienia

Lista zamknięta, do sprawdzenia w przeglądzie każdego zadania:

- `config/urls.py` — kolejność wzorców, Wagtail jako catch-all w korzeniu,
- przepływ oceniania (PROJEKT.md § 2.4) i wszystkie jego bramki,
- reguły skali punktowej, progów kwalifikacji i anonimizacji wyników,
- terminy, `grace_seconds`, zamykanie etapu i okna reklamacji,
- podział bucketów MinIO, poświadczenia serwisowe, klucze obiektów, presigned URL-e,
- polityka CSP dla stron publicznych i osobna, luźniejsza dla paneli,
- `WAGTAILDOCS_SERVE_METHOD`, `WAGTAILEMBEDS_FINDERS` i lista `frame-src`,
- CAPTCHA, `ANTISPAM_MIN_FILL_SECONDS`, limity throttlingu,
- retencja danych (`Edition.data_retention_months`, `anonymise_account`) i eksport z art. 20,
- treść i wersje zgód Konkursu #1,
- treści redakcyjne, slugi, rewizje i przekierowania w drzewie stron Konkursu #1.
