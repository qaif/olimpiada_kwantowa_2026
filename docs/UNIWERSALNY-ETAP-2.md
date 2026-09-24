# Uniwersalny system zawodów wiedzy — Etap 2: konfiguracja, proces, rejestracja, wdrożenie

**Status:** projekt do wykonania. Etap 1 (wielokonkursowość) jest domknięty i wdrożony — wydania
A–D, wersje `v0.20.0`–`v0.23.0`, opis w [`UNIWERSALNY-ETAP-1.md`](UNIWERSALNY-ETAP-1.md).
**Punkt odniesienia:** gałąź `main`, commit `9fc4e32`, tag `v0.23.0`.
**Zakres etapu 2:** siedem pozycji programu organizatora, które zostały po etapie 1 — marka
i dokumenty jako konfiguracja, edytor przebiegu zawodów, druga część rejestracji, dowolny podział
terytorialny, instalator i obraz „gotowy do uruchomienia”, wielojęzyczność treści oraz finanse
i logistyka.
**Poza zakresem etapu 2 (na stałe):** SSO i zewnętrzni dostawcy tożsamości, automatyczna ocena kodu
uczestnika, samodzielna rejestracja opiekunów w Konkursie #1, podniesienie wersji frameworka.

Wszystkie nazwy klas, pól, funkcji i ścieżek w tym dokumencie zostały sprawdzone w repozytorium na
commicie `9fc4e32`. Tam, gdzie nazwa jeszcze nie istnieje, jest to oznaczone **(nowe)**. Numery
linii odnoszą się do tego samego commitu i służą do odnalezienia miejsca, a nie do liczenia diffu.

---

## 0. Ograniczenie nadrzędne: ciągłość Olimpiady Kwantowej

> Wymaganie organizatora, dosłownie: **„zachowaj działającą i skonfigurowaną obecną Olimpiadę
> Kwantową”.**

Etap 2 dokłada siedem obszarów konfiguracji. Każdy z nich jest **zamianą stałej w kodzie na wiersz
w bazie** albo **dołożeniem funkcji, której Konkurs #1 nie włącza**. Żaden nie jest przepisaniem
zachowania.

### 0.1. Reguła rozstrzygająca spory

**Jeżeli zmiana jest widoczna dla uczestnika, recenzenta, opiekuna albo koordynatora Olimpiady
Kwantowej i nie wynika z osobnego polecenia organizatora — jest błędem, nawet jeżeli przechodzą
testy.** Reguła jest przepisana z etapu 1 § 0 bez zmiany ani jednego słowa. Rozwinięcie na etap 2:

1. **Nowa zdolność wchodzi za flagą konkursu** (`Competition.feature_flags`, odczyt wyłącznie przez
   `Competition.has_feature`, `apps/tenancy/models.py:220`), a wartość domyślna flagi odtwarza
   dzisiejsze zachowanie.
2. **Dane, które dotąd były stałą w kodzie, wchodzą do bazy migracją wpisującą Konkursowi #1
   dokładnie dzisiejszą wartość** — co do znaku, wraz z wersją dokumentu i wielkością liter.
3. **Kod, który dotąd czytał stałą, czyta ją nadal**, dopóki flaga jest wyłączona. Przełącznik jest
   jeden i jest w jednym miejscu; nie ma gałęzi „nowa logika, ale bez flagi”.

### 0.2. Co jest zamrożone — lista organizatora

| # | Zamrożone | Czym to jest pilnowane |
|---|---|---|
| 1 | adresy publiczne (`/`, `/me/`, `/review/`, `/coordinator/`, `/wyniki/`, `/results/<id>/`, `/dokumenty/…`, `/zgoda/<token>/`, `/api/…`) | `config/urls.py` nie jest edytowany poza **jednym** dopisanym wzorcem `/setup/` (§ 1.7.1); test kolejności wzorców |
| 2 | bajtowa identyczność stron publicznych po każdym wydaniu | złote testy `apps/tenancy/tests/test_golden_single_competition.py` + ręczna lista § 0.5 |
| 3 | brzmienie i wersje zgód (`apps/accounts/consents.py:109,113,118,122`) | `test_consent_labels_unchanged` (`apps/tenancy/tests/test_invariants.py`), rozszerzone w § 5.3 |
| 4 | tematy i podpisy listów | `test_email_subjects_unchanged`, rozszerzone w § 5.4 |
| 5 | treść seedów (`seed_cms`, `seed_regulamin`, `seed_legacy_content`, `seed_partners`, `seed_edition_kwantowa`) | zadania etapu 2 nie mają tych plików we własności (§ 4) |
| 6 | bieżąca edycja, etapy, terminy i miejsce Konkursu #1 | migracja `competitions.00xx` pisze **tylko** wiersze opisujące dzisiejszy przebieg, nie zmienia `Stage` |
| 7 | tabela wyników każdego ogłoszonego etapu | snapshot porównawczy, § 5.2 — najtwardszy test tego etapu |
| 8 | prefiksy `OLM-` i `OK` oraz numery już wydanych dyplomów | `test_public_code_prefix_is_olm`, `test_certificate_number_prefix_is_ok` |
| 9 | uprawnienia grupy `coordinator` w `/cms/` dla Konkursu #1 | migracja `cms.0003_coordinator_permissions` nietknięta; nowa migracja **dokłada** wiersze, nie odbiera (§ 1.1.5) |
| 10 | polityka CSP strony publicznej | `test_csp_header_unchanged_for_single_competition` (stała `PUBLIC_CSP` w teście) |
| 11 | kroki `scripts/deploy.sh` dla Konkursu #1 (1/8–8/8) | zmiany są **addytywne**: nowy krok opcjonalny i podmiana źródła obrazu za zmienną (§ 1.7.2) |

### 0.3. Poza zakresem na stałe

- **SSO i zewnętrzni dostawcy tożsamości** — organizator wycofał tę pozycję. Istniejące logowanie
  przez Google i Facebooka (`apps/accounts/adapters.py`, `apps/web/social_urls.py`) zostaje bez
  zmian; nie powstaje OIDC, SAML ani federacja między instalacjami.
- **Automatyczna ocena kodu uczestnika** — jakiekolwiek uruchamianie nadesłanego kodu jest
  wykluczone decyzją o polityce bezpieczeństwa. `.ipynb` i `.py` pozostają **plikami do obejrzenia
  przez recenzenta** (`apps/grading/code_view.py`), a nie wsadem do sandboxa: nie powstaje runner,
  kolejka „judge” ani format testów.
- **2FA** — funkcja istnieje (`apps/accounts/twofactor.py`) i zostaje **wyłączona**
  (`docs/OPERACJE.md` § 5.1a). Etap 2 jej nie włącza i nie zmienia.
- **Samodzielna rejestracja opiekuna w Konkursie #1** — pozostaje ukryta (`cms.SiteSettings`,
  migracja `cms.0020_site_supervisor_registration_flag`); rejestracja opiekunów w konkursie drugim
  idzie tą samą, istniejącą flagą witryny.
- **Podniesienie wersji frameworka** (Django 5.1 → 5.2, Wagtail 6.3 → 7.x) jest osobną, późniejszą
  pozycją. Nic w tym dokumencie nie zakłada nowszej wersji — w szczególności § 1.6 opiera się
  wyłącznie na `wagtail.contrib.simple_translation`, które w 6.3 jest stabilne.

### 0.4. Stos bez zmian

> **Nota z 18.09.2026 (v0.26.0).** Zdanie „bez zmian” dotyczyło **etapu 2** i takie zostaje: etap 2
> wyszedł na Django 5.1 + Wagtail 6.3 i nic w tym dokumencie nie zakłada nowszej wersji. Po etapie 2,
> osobnym wydaniem i osobnym przebiegiem testów, stos został podniesiony do **Django 6.1 + Wagtail 8.0
> + DRF 3.18** (pozycja zapowiedziana w § 0.3 jako „osobna, późniejsza”). Reszta wyliczenia niżej —
> CSP, Postgres, Celery, MinIO, jedno wdrożenie — nie zmieniła się ani o jeden element. Przebieg
> aktualizacji, obejście `django-celery-beat` i procedura wycofania: `docs/OPERACJE.md` § 9.

Django 5.1 + Wagtail 6.3 + DRF + HTMX, ścisłe CSP bez `'unsafe-inline'` dla skryptów na stronach
publicznych (`apps/web/middleware.py`), Postgres 16, Celery (kolejki `default`, `scan`, `mail`),
MinIO z dwoma bucketami i rozdzielonymi poświadczeniami, jedno wdrożenie `docker compose` przez
`scripts/deploy.sh`. Trzy konsekwencje, wymienione tu, żeby nie trzeba było o nich pamiętać przy
każdym zadaniu:

- **żadnego JS-u ani stylu inline.** Edytor przebiegu (§ 1.2) i kreator instalacji (§ 1.7.1) są
  formularzami HTML, a nie aplikacją SPA; wzorzec jest w repozytorium
  (`backend/static/js/school-picker.js` — czysty JavaScript z własnego adresu, podpięty atrybutami
  `data-*`);
- **żadnej nowej biblioteki z CDN-u** — każdy nowy host w `script-src` łamie punkt 10 z § 0.2;
- **żadnej nowej usługi w compose** poza tym, co wymienia § 1.7.3 (profile, nie nowe kontenery).

### 0.5. Lista kontrolna produkcji po każdym wydaniu

Rozszerzenie listy z etapu 1 § 0.3 (punkty 1–13 zostają bez zmian i nadal obowiązują). Etap 2
dokłada dziewięć pozycji; każda ma dać wynik **identyczny** jak przed wdrożeniem.

| # | Sprawdzenie | Oczekiwane |
|---|---|---|
| 14 | `GET /register/` — treść każdej zgody znak w znak | cztery zgody w kolejności `terms_consent`, `gdpr_consent`, `guardian_consent`, `publish_name_consent`, ten sam tekst, te same linki do `/dokumenty/regulamin/` i `/dokumenty/rodo/` |
| 15 | rejestracja konta testowego → list aktywacyjny | temat `Aktywuj konto – Olimpiada Kwantowa`, podpis listu bez zmian |
| 16 | `GET /wyniki/` i `/results/<id>/` dla **każdej** ogłoszonej publikacji | snapshot identyczny co do bajtu z kopią sprzed wdrożenia |
| 17 | `GET /coordinator/stages/<id>/results/` (podgląd roboczy) | te same sumy, te same miejsca, ten sam próg |
| 18 | pobranie dyplomu wystawionego przed wdrożeniem | ten sam numer, kod weryfikacyjny i linia podpisu |
| 19 | `GET /kalendarz.ics` uczestnika | `PRODID`, `X-WR-CALNAME` i `UID` bez zmian |
| 20 | `GET /cms/` jako koordynator Konkursu #1 | drzewo stron, kolekcje mediów i lista uprawnień bez zmian; brak nowego wyboru języka |
| 21 | `GET /` — nagłówek `Content-Language`, brak prefiksu języka | `/` zostaje `/`, nie ma przekierowania na `/pl/` |
| 22 | `GET /setup/` na produkcji | **404** (w bazie jest konkurs i superużytkownik) |

Punkty 14–22 wchodzą do `e2e/` jako rozszerzenie istniejącego przebiegu, nie jako nowy projekt.

### 0.6. Katalog flag etapu 2

Wszystkie flagi dopisują się do `FEATURE_DEFAULTS` (`apps/tenancy/models.py:63–80`). Reguła odczytu
jest ta sama: **jedynym** wejściem jest `Competition.has_feature(name)`, a nieznana nazwa podnosi
`KeyError` (`:230`). Konkurs #1 zostaje z `feature_flags` zawierającym wyłącznie to, co ma dziś
(`memberships_enforced`, `competition_settings_page`), więc **każda** nowa flaga jest dla niego
wartością domyślną.

| Flaga **(nowa)** | Domyślnie | Co włącza | Sekcja |
|---|---|---|---|
| `per_competition_consents` | `False` *(już w katalogu, niewykorzystana)* | zgody z `ConsentDefinition` zamiast ze stałej `CONSENTS` | § 1.1.2 |
| `competition_branding_in_mail` | `False` | tematy, podpisy listów i nazwa kalendarza z konkursu | § 1.1.1 |
| `document_templates` | `False` | teksty dokumentów z `DocumentTemplate` | § 1.1.3 |
| `scoped_cms_permissions` | `False` | uprawnienia `/cms/` per konkurs | § 1.1.5 |
| `process_editor` | `False` | dowolna liczba etapów, `PipelineStep`, komponenty, reguły przejścia | § 1.2 |
| `categories` | `False` | kategorie z własnymi progami i osobnymi rankingami | § 1.2.4 |
| `team_entries` | `False` | zgłoszenia i punktacja drużynowa | § 1.2.3 |
| `weighted_scoring` | `False` | wagi zadań, punkty ujemne, porządek rozstrzygania remisów | § 1.2.6 |
| `reviewer_roles` | `False` | nazwane role recenzenckie | § 1.2.7 |
| `institution_types` | `False` | placówki inne niż szkoła ponadpodstawowa | § 1.3 |
| `custom_school_directory` | `False` | własny słownik placówek organizatora (CSV) | § 1.3.3 |
| `custom_regions` | `False` | podział terytorialny z `Region` zamiast z `Voivodeship` | § 1.4 |
| `fees` | `False` | wpisowe, zwolnienia, status płatności, dokumenty rozliczeniowe | § 1.5.1 |
| `onsite_logistics` | `False` | nocleg, wyżywienie, listy obecności, formularze przyjazdu | § 1.5.2 |
| `content_translations` | `False` | wielojęzyczność drzewa stron (`WAGTAIL_I18N_ENABLED`) | § 1.6 |

Kreator `/setup/` **nie jest flagą konkursu**: istnieje wyłącznie, gdy w bazie nie ma żadnego
konkursu ani superużytkownika (§ 1.7.1).

**Piętnaście flag to dużo i to jest świadoma cena.** Alternatywą byłaby jedna flaga „etap 2”, ale
wtedy wycofanie jednej funkcji wymagałoby wycofania wszystkich. Flagi są gruboziarniste co do
**obszaru**, a nie co do ekranu. Po sezonie od włączenia u drugiego konkursu flagi, które nigdzie
nie są `False`, znikają razem z gałęzią odwrotu — tak samo jak `memberships_enforced` w etapie 1.

### 0.7. Odwracalność, kopia, kolejność

Obowiązują bez zmian reguły z etapu 1 § 0.2 i § 4.2: każda migracja schematu ma `reverse_code` albo
jest odwracalna z definicji; migracje danych używają **modeli historycznych** (`apps.get_model`), są
idempotentne i mają `elidable=False`; `scripts/deploy.sh` krok 4a robi `pg_dump -Fc` **przed**
migracjami, a brak kopii zatrzymuje wdrożenie (`:170`); **żadna migracja etapu 2 nie ustawia
`NOT NULL` na kolumnie wypełnianej w tym samym wydaniu** (wzorzec „nullowalne → backfill → osobne
wydanie z `NOT NULL` po zapytaniu kontrolnym”, etap 1 § 4.1 i § 4.4).

Jedna reguła jest **nowa i dotyczy wyłącznie etapu 2**:

> **Migracja, która zamienia stałą w kodzie na wiersz w bazie, ma w tym samym commicie test
> porównujący wiersz ze stałą.** Nie „test, że wiersz istnieje” — test, że wiersz jest **równy**
> stałej, pole po polu. Bez tego migracja przepisująca zgody byłaby jedynym miejscem w systemie,
> w którym treść oświadczenia mogłaby się zmienić bez żadnego śladu.

---

## 1. Model

### 1.0. Trzy wzorce obowiązujące w całej sekcji

**(a) Zakresowanie.** Każdy nowy model niosący dane konkursu dostaje drogę do konkursu i manager
z `apps/competitions/scoping.py:52`:

```python
from apps.competitions.scoping import competition_scoped_manager

class ConsentDefinition(models.Model):
    competition = models.ForeignKey("tenancy.Competition", on_delete=models.PROTECT, ...)
    objects = competition_scoped_manager()                      # ścieżka domyślna: "competition"

class StageComponent(models.Model):
    stage = models.ForeignKey("competitions.Stage", ...)
    objects = competition_scoped_manager("stage__edition__competition")
```

Reguła z etapu 1 § 3.1 zostaje: **własna kolumna `competition` tylko tam, gdzie nie ma drogi przez
istniejące klucze obce.** Druga droga do tej samej prawdy to druga okazja do rozjazdu, a rozjazd
w tabeli izolacji znaczy wyciek. Jedyny wyjątek etapu 2 jest wymieniony imiennie w § 1.4.2 razem
z powodem i terminem ważności.

**(b) Odwroty.** `resolve_competition()` (miękki, `None` = pustka) dla odczytów **danych**;
`require_competition()` (twardy, `CompetitionNotResolved`) dla odczytów **konfiguracji konkursu**.
Rozróżnienie jest opisane w docstringu `apps/competitions/scoping.py:97` i etap 2 go nie zmienia:
definicja zgody, szablon dokumentu i reguła przejścia to konfiguracja, więc „nie wiadomo, o który
konkurs chodzi” ma być słyszalne, a nie ciche.

**(c) Kolejność czytania flagi.** Flaga jest sprawdzana **raz, możliwie wysoko** — w serwisie albo
w widoku, nigdy w szablonie i nigdy w pętli po wierszach:

```python
def consent_set(competition) -> tuple[Consent, ...]:
    """Zgody konkursu: z bazy przy włączonej fladze, ze stałej przy wyłączonej."""
    if competition is not None and competition.has_feature("per_competition_consents"):
        return tuple(definitions_to_consents(ConsentDefinition.objects.for_competition(competition)))
    return DEFAULT_CONSENTS
```

Wszystkie miejsca czytające zgody (formularz, serializer, panel uczestnika, eksport RODO) wołają
**tę jedną** funkcję. Test architektoniczny (§ 5.3) sprawdza grepem, że poza nią `CONSENTS` nie jest
importowane nigdzie poza `apps/accounts/consents.py` i testami niezmienności.

---

### 1.1. Marka i dokumenty jako konfiguracja

Wykonanie § 5 dokumentu etapu 1: (A) listy, (B) zgody, (C) dokumenty, (D) pozostałe napisy,
(E) domknięcie stage-1 — dostęp do `/cms/`.

#### 1.1.1. (A) Tematy i podpisy listów z konkursu

**Stan faktyczny.** Szesnaście tematów listów jest stałymi modułów; jedenaście z nich jest objętych
`EXPECTED_SUBJECTS` w `apps/tenancy/tests/test_invariants.py`, **pięć nie jest** (i to jest
najważniejsza obserwacja tej sekcji).

| Stała / miejsce | Plik:linia | W teście? |
|---|---|---|
| `ACTIVATION_SUBJECT` | `apps/accounts/activation.py:74` | tak |
| `EMAIL_CHANGE_SUBJECT` | `apps/accounts/activation.py:75` | tak |
| `EMAIL_CHANGED_NOTICE_SUBJECT` | `apps/accounts/activation.py:76` | tak |
| `GUARDIAN_SUBJECT` | `apps/accounts/guardian.py:60` | tak |
| `GUARDIAN_CONFIRMED_SUBJECT` | `apps/accounts/guardian.py:61` | tak |
| `INVITATION_SUBJECT` | `apps/accounts/services.py:838` | tak |
| `REMINDER_SUBJECT` | `apps/grading/reports.py:194` | tak |
| `SUBMISSION_RECEIVED_SUBJECT` | `apps/submissions/notifications.py:48` | tak |
| `SUBMISSION_INFECTED_SUBJECT` | `apps/submissions/notifications.py:49` | tak |
| `RESULTS_PUBLISHED_SUBJECT` | `apps/submissions/notifications.py:50` | tak |
| `APPEAL_DECIDED_SUBJECT` | `apps/submissions/notifications.py:51` | tak |
| temat zgłoszenia do organizatora | `apps/support/services.py:238` | **nie** |
| temat odpowiedzi na zgłoszenie | `apps/support/services.py:267` | **nie** |
| temat terminu rozmowy | `apps/competitions/interviews.py:269` | **nie** |
| temat przypomnienia o rozmowie | `apps/competitions/video.py:190` | **nie** |
| temat przypomnienia o recenzjach po terminie | `apps/grading/deadlines.py:149–152` | **nie** |

Dziesięć z szesnastu zawiera literał „Olimpiada Kwantowa”. Dwa wzorce są widoczne gołym okiem i to
one są projektem: **`<zdarzenie> – <nazwa>`** (dziewięć tematów) oraz **`<zdarzenie> <nazwa
w dopełniaczu>`** (`INVITATION_SUBJECT` — „komitetu **Olimpiady Kwantowej**”). `Competition` ma już
oba pola: `name` i `genitive_name` z właściwościami `genitive`/`locative`.

**Projekt.** Stałe zostają w kodzie i zamieniają się w **wzorce**:

```python
# apps/accounts/activation.py
#: Napis zostaje w kodzie: to jest **treść**, a nie konfiguracja organizatora — ten sam list
#: w dwóch konkursach ma mówić to samo zdanie, tylko o innym konkursie.
ACTIVATION_SUBJECT = _("Aktywuj konto – Olimpiada Kwantowa")           # bez zmian, odwrót
ACTIVATION_SUBJECT_TEMPLATE = _("Aktywuj konto – %(competition)s")     # (nowe)
```

Składanie ma **jedno** miejsce — `apps/tenancy/branding.py` **(nowy moduł)**:

```python
def subject(template, fallback, competition=None, **extra) -> str:
    """Temat listu: wzorzec z nazwą konkursu albo dzisiejszy napis, gdy flaga jest wyłączona.

    ``fallback`` jest **dosłownym dzisiejszym tematem**, a nie wzorcem podstawionym nazwą
    Konkursu #1. Różnica jest istotna: podstawienie dałoby ten sam napis tylko dopóty, dopóki nikt
    nie poprawi ``Competition.name`` w panelu — a wtedy temat listu Olimpiady Kwantowej zmieniłby
    się bez wdrożenia i bez śladu w audycie.
    """
    if competition is None or not competition.has_feature("competition_branding_in_mail"):
        return str(fallback)
    return template % {"competition": competition.short_name or competition.name,
                       "competition_genitive": competition.genitive,
                       "competition_locative": competition.locative, **extra}
```

Podpis listu (dziś literał w pięciu miejscach: `apps/accounts/activation.py:216,239,262`,
`apps/accounts/guardian.py:192,211`, `apps/accounts/services.py:926`,
`apps/competitions/video.py:207`, `apps/grading/reports.py:212`) idzie tą samą drogą przez
`branding.signature(competition)`, z tym samym odwrotem.

**Nadawca i prefiks tematu już są konfiguracją**, tylko **żadna ścieżka wysyłki ich nie czyta**:
`Competition.from_email` (`apps/tenancy/models.py:163`) i `email_subject_prefix` (`:164`) są
wypełniane, edytowalne (`apps/web/competition_forms.py`) i backfillowane z ustawień
(`tenancy/migrations/0002_competition_from_site.py:72`), ale `send_mail_task`
(`apps/core/tasks.py:52`) wpisuje `settings.DEFAULT_FROM_EMAIL` na sztywno (`:74`). Podłączenie ich
jest częścią tego samego zadania (T9, § 4) — szczegóły sygnatur w § 1.6.3.

#### 1.1.2. (B) Zgody per konkurs

**Stan faktyczny.** `apps/accounts/consents.py` (352 linie) trzyma dataklasę `Consent` (`:75–76`),
cztery wersje dokumentów jako literały (`TERMS_VERSION` `:109`, `PRIVACY_VERSION` `:113`,
`GUARDIAN_VERSION` `:118`, `PUBLISH_NAME_VERSION` `:122`), krotkę `CONSENTS` (`:124–181`), indeksy
`BY_KIND`/`BY_FIELD` (`:183–184`), `CONSENT_FIELD_NAMES` (`:187`), regułę `is_minor` (`:190`,
`MINOR_MAX_AGE = 18`, `:54`) oraz `document_url` (`:231`) i `document_link` (`:253`).

**Jest tam jeden niedomknięty ślad etapu 1.** `organizer_name()` (`:212–228`) czyta **globalnie**:

```python
settings_row = SiteSettings.objects.first()      # apps/accounts/consents.py:223
```

Etap 1 § 3.7 wymieniał to miejsce jako do poprawienia i **nie zostało poprawione** — przy dwóch
konkursach nazwa organizatora w treści zgody RODO bierze się z przypadkowej witryny. To jest błąd
izolacji, a nie brak funkcji, więc naprawa idzie **przed** flagą i niezależnie od niej (T8, § 4).

**Projekt modelu**, zgodnie z etapem 1 § 5.2:

```python
# apps/accounts/models.py — model należy do domeny kont, tak jak ConsentRecord
class ConsentDefinition(models.Model):
    """Definicja jednej zgody **jednego konkursu**: treść, dokument, wersja, wymagalność.

    Model nie zastępuje ``ConsentRecord`` i nie jest z nim związany kluczem obcym. Dowód
    (``ConsentRecord.document_version``) zostaje **kopią napisu** z chwili złożenia oświadczenia —
    dokładnie jak dziś. Klucz obcy do definicji znaczyłby, że poprawienie literówki w treści zgody
    zmienia wstecz to, na co ludzie się zgodzili.
    """
    competition = FK("tenancy.Competition", PROTECT, related_name="consent_definitions")
    kind = CharField("rodzaj", max_length=24, choices=ConsentKind.choices)
    field_name = CharField("nazwa pola", max_length=40)
    text = TextField("treść oświadczenia")          # wzorzec z {link} i {organizer}
    link_text = CharField("tekst odnośnika", max_length=200, blank=True)
    document_slug = SlugField("dokument", max_length=60, blank=True)
    version = CharField("wersja dokumentu", max_length=100)
    required = BooleanField("wymagana zawsze", default=False)
    required_for_minor = BooleanField("wymagana dla niepełnoletnich", default=False)
    help_text = CharField("podpowiedź", max_length=200, blank=True)
    missing_message = CharField("komunikat o braku", max_length=300, blank=True)
    ordering = PositiveSmallIntegerField("kolejność", default=0)
    is_active = BooleanField("aktywna", default=True)

    objects = competition_scoped_manager()

    class Meta:
        ordering = ("competition", "ordering", "id")
        constraints = [
            UniqueConstraint(fields=["competition", "kind"], name="accounts_consentdef_unique_kind"),
            UniqueConstraint(fields=["competition", "field_name"], name="accounts_consentdef_unique_field"),
            # Zgoda bez wersji nie jest dowodem: ``ConsentRecord.document_version`` byłby pusty
            # i po pierwszej nowelizacji regulaminu nie dałoby się odpowiedzieć na jedyne pytanie,
            # które przy zgodzie pada.
            CheckConstraint(condition=~Q(version=""), name="accounts_consentdef_version_not_empty"),
        ]
```

`ConsentKind` (`:57–63`) **zostaje w kodzie** jako zamknięta lista: `TERMS`, `PRIVACY`, `GUARDIAN`,
`PUBLISH_NAME`. Rodzaj jest kluczem, po którym poznaje zgodę `ConsentRecord`, serwis rejestracji,
`required_kinds()` i panel uczestnika; konkurs wymyślający **nowy** rodzaj dostaje
`ConsentKind.OTHER` **(nowa wartość)** z rozróżnieniem po `field_name`. Powód, dla którego rodzaje
nie idą do bazy: reguła „zgoda opiekuna jest wymagana dla niepełnoletnich” jest **kodem**
(`is_minor`, `:190`), a nie danymi, i musi wiedzieć, o którą zgodę chodzi.

**`CONSENTS` → `DEFAULT_CONSENTS`** — krotka zostaje w kodzie pod nową nazwą jako zestaw startowy dla
`create_competition` i jako odwrót przy wyłączonej fladze; alias `CONSENTS = DEFAULT_CONSENTS`
zostaje przez sezon, żeby `test_invariants.py` mógł importować jedno i drugie i porównać.

**Migracja `accounts.00xx_consent_definitions_for_competition_one`** wpisuje Konkursowi #1 cztery
wiersze **odczytane ze stałej**, a nie wpisane w migracji ręcznie:

```python
def forwards(apps, schema_editor):
    # Wyjątek od reguły „w migracji tylko modele historyczne”: ``DEFAULT_CONSENTS`` jest **stałą
    # modułu**, a nie modelem — importujemy dane, nie zachowanie. Alternatywą byłoby przepisanie
    # czterech akapitów regulaminu do migracji, czyli piąte miejsce, w którym stoi treść zgody.
    # Przepisanie da się pomylić; import nie.
    from apps.accounts.consents import DEFAULT_CONSENTS

    Competition = apps.get_model("tenancy", "Competition")
    ConsentDefinition = apps.get_model("accounts", "ConsentDefinition")
    for competition in Competition.objects.order_by("pk"):
        for order, consent in enumerate(DEFAULT_CONSENTS):
            ConsentDefinition.objects.update_or_create(
                competition_id=competition.pk, kind=str(consent.kind),
                defaults={"field_name": consent.field_name, "text": consent.text,
                          "link_text": consent.link_text, "document_slug": consent.document_slug,
                          "version": consent.version, "required": consent.required,
                          "required_for_minor": consent.required_for_minor,
                          "help_text": consent.help_text,
                          "missing_message": consent.missing_message, "ordering": order})
```

Migracja jest idempotentna (`update_or_create`) i odwracalna przez skasowanie wierszy (dowody
`ConsentRecord` zostają nietknięte — to jest cała różnica między definicją a dowodem).

**Zmiana wersji w panelu** (`/coordinator/consents/`, § 2.2) wymaga potwierdzenia zdaniem „od tej
chwili nowe zgody będą zapisywane pod wersją X” i idzie do audytu jako
`consent_definition.version_changed` z różnicą pól. Wersji **nie da się** zmienić hurtem.

**Czego migracja nie robi:** nie dotyka `ConsentRecord`, nie zmienia `Participant.gdpr_consent_at`
ani `terms_accepted_at`, nie włącza flagi. Po jej wykonaniu Konkurs #1 nadal czyta stałą, a nowy
test (§ 5.3) porównuje wiersze ze stałą — czyli sprawdza migrację, zanim ktokolwiek jej użyje.

#### 1.1.3. (C) Szablony dokumentów z podstawieniami

**Stan faktyczny.** Dokumenty składa `apps/results/certificates.py` (1002 linie). Grafika jest **już
konfiguracją**: `results.CertificateTemplate` (`apps/results/models.py:253`) ma od wydania D kolumnę
`competition` (`:281`), tło, logo, trzy bloki podpisu i `layout` (JSON, `:349`), a
`resolve_template(kind, edition, competition)` (`certificates.py:449`) dobiera go od szczegółu do
ogółu: `(rodzaj, edycja)` → `(rodzaj, —)` → `(—, edycja)` → `(—, —)` → układ wbudowany.

**Tekstem dokumentu jest nadal kod** — trzy stałe i jedna linia:

| Stała | Plik:linia | Zawartość |
|---|---|---|
| `DOCUMENT_TITLES` | `apps/results/certificates.py:76–82` | tytuł na papierze per rodzaj |
| `DOCUMENT_STATEMENTS` | `:86–92` | zdanie pod nazwiskiem per rodzaj |
| `WORKSHOP_LIST_HEADING` | `:96` | `"Tematy zajęć:"` |
| `SIGNATURE_LINE` | `:100` | `"Przewodniczący Komitetu Głównego Olimpiady Kwantowej"` — używana **wyłącznie** jako blok zapasowy w `_draw_signatures` (`:630`), gdy szablon nie ma żadnego podpisu |

Do tego `compose_pdf` (`:702`) wpisuje w metadane PDF-a `setAuthor("Olimpiada Kwantowa")`.

**Projekt.** Nowy model w `apps/tenancy` — tam, bo dokumenty wydaje **konkurs**, a nie edycja, i bo
ten sam mechanizm obsłuży fakturę (§ 1.5.1) i wzór zgody opiekuna:

```python
class DocumentKind(models.TextChoices):
    # Pierwsze pięć wartości są **kopią** ``results.CertificateKind``, a nie importem: tamto opisuje
    # wiersz rejestru dyplomów, to opisuje szablon tekstu, i obie listy będą rosły w innym tempie.
    # Zgodność pilnuje test ``test_document_kinds_cover_certificate_kinds``.
    LAUREAT = "LAUREAT", "dyplom laureata"
    FINALISTA = "FINALISTA", "dyplom finalisty"
    UCZESTNIK = "UCZESTNIK", "zaświadczenie uczestnika"
    OPIEKUN = "OPIEKUN", "zaświadczenie opiekuna"
    WARSZTATY = "WARSZTATY", "zaświadczenie z warsztatów"
    GUARDIAN_FORM = "GUARDIAN_FORM", "wzór zgody opiekuna"
    INVOICE = "INVOICE", "faktura / rachunek"                 # § 1.5.1, za flagą ``fees``
    ATTENDANCE_LIST = "ATTENDANCE_LIST", "lista obecności"    # § 1.5.2, za ``onsite_logistics``


class DocumentTemplate(models.Model):
    """Tekst dokumentu z podstawieniami — **napisy**, nie skład.

    Skład zostaje w ``apps/results/certificates.py`` (ReportLab): zmienia się źródło napisów, a nie
    rysowanie. Grafikę opisuje ``results.CertificateTemplate`` i te dwa modele celowo nie są jednym:
    winietę wgrywa grafik, a zdanie „uzyskał tytuł laureata” pisze prawnik organizatora.
    """

    competition = models.ForeignKey("tenancy.Competition", on_delete=models.PROTECT,
                                    related_name="document_templates", verbose_name="konkurs")
    kind = models.CharField("rodzaj", max_length=24, choices=DocumentKind.choices)
    #: Wersja treści jako **napis**, nie numer — ta sama konwencja, co ``ConsentRecord.document_version``.
    version = models.CharField("wersja", max_length=100)
    title = models.CharField("tytuł na dokumencie", max_length=200)
    statement = models.TextField("zdanie główne")
    signature_line = models.CharField("linia podpisu", max_length=200, blank=True)
    footer_note = models.CharField("dopisek w stopce", max_length=300, blank=True)
    is_current = models.BooleanField("obowiązująca", default=True)
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        ordering = ("competition", "kind", "-created_at", "-id")
        constraints = [
            # Jeden obowiązujący szablon na rodzaj; wersje historyczne zostają (``is_current=False``),
            # bo dokument wydany w zeszłym roku ma dać się odtworzyć.
            models.UniqueConstraint(fields=["competition", "kind"], condition=Q(is_current=True),
                                    name="tenancy_documenttemplate_single_current"),
            models.CheckConstraint(condition=~Q(version=""),
                                   name="tenancy_documenttemplate_version_not_empty"),
        ]
```

**Podstawienia** — lista zamknięta, walidowana przy zapisie: `{competition}`
(`short_name or name`), `{competition_genitive}`, `{competition_locative}`, `{organizer}`,
`{edition}` (`Edition.year_label`), `{participant_code}` (`Participant.public_code`), `{recipient}`
i `{school}` (`CertificateContent`, `certificates.py:365,373`), `{stage}` (`Stage.display_name`),
`{date}`, `{number}`, `{code}`. `DocumentTemplate.clean()` odrzuca znacznik spoza listy
komunikatem wymieniającym dozwolone: literówka `{partcipant_code}` ma zostać zauważona w panelu,
a nie w PDF-ie wydanym tysiącu osób. Podstawianie idzie przez `str.format_map` z mapą zwracającą
pusty napis dla znacznika nieistniejącego w danym kontekście (dyplom opiekuna nie ma
`{participant_code}`).

**`Certificate.template_version` (nowe pole)** — `CharField(max_length=100, blank=True)` na
`results.Certificate` (`apps/results/models.py:137`), zapisywane w `issue_certificate` (`:156`) jako
kopia `DocumentTemplate.version`. Uzasadnienie jest to samo, co przy `ConsentRecord.document_version`:
**PDF nie jest przechowywany** (docstring `certificates.py:1–28`), więc powstaje przy każdym
pobraniu — bez zapamiętanej wersji poprawka tekstu zmieniłaby treść dokumentu, który ktoś trzyma
w ręku. Puste pole (dokumenty sprzed etapu 2) znaczy „układ wbudowany”, czyli dzisiejszy.

**Migracja `tenancy.00xx_document_templates_for_competition_one`** wpisuje Konkursowi #1 pięć wierszy
z wartościami przepisanymi ze stałych `DOCUMENT_TITLES` i `DOCUMENT_STATEMENTS`,
`signature_line = SIGNATURE_LINE`, `version = "1.0 (stan z v0.23.0)"`. Import stałych z modułu — jak
przy zgodach i z tego samego powodu.

**Czego nie robimy:** nie ruszamy `compose_pdf` poza źródłem czterech napisów, `_merge_background`
(`:783`, pypdf), `register_fonts` (`:521`), `apps/results/signing.py` (pieczęć PAdES), `_next_number`
(`:125`), `pdf_filename` (`:861`), `verify` (`:930`) ani `certificate_layout.py`. Nie ruszamy też
`apps/cms/management/commands/build_guardian_consent_pdf.py` — to komenda **budowania**, której wynik
leży w repozytorium, a nie ścieżka żądania; jej powiązanie z `DocumentTemplate` jest pozycją backlogu.

#### 1.1.4. (D) Kalendarz, CAPTCHA, domena anonimowa, strona 500

Cztery drobne miejsca z inwentarza etapu 1 § 5.1 (C). Każde ma inną odpowiedź i żadna nie brzmi
„podstaw nazwę konkursu wszędzie”.

| Miejsce | Dziś | Po zmianie | Dlaczego tak |
|---|---|---|---|
| `apps/cms/calendar.py:46` `UID_DOMAIN` | `"olimpiadakwantowa.pl"` | `competition.primary_domain` | `UID` jest kluczem wydarzenia w kliencie kalendarza — jego zmiana znaczy **drugie** wydarzenie obok starego. Konkurs #1 ma w `primary_domain` dokładnie tę domenę, więc wynik jest ten sam bez wyjątku w kodzie |
| `apps/cms/calendar.py:49` `PRODID` | `-//Olimpiada Kwantowa//Kalendarz uczestnika//PL` | wzorzec z odwrotem na stałą | identyfikuje program, nie wydarzenie — zmiana niczego nie duplikuje |
| `apps/cms/calendar.py:304` `X-WR-CALNAME` | `Olimpiada Kwantowa` | `competition.short_name or name` | nazwa kalendarza w kliencie; zmiana widoczna, więc za flagą `competition_branding_in_mail` |
| `apps/cms/calendar.py:53` `ICS_FILENAME` | `olimpiada-kwantowa.ics` | `Competition.calendar_filename` **(nowe pole)** | `slugify(slug)` dałoby `kwantowa.ics`, czyli **zmianę**; migracja wpisuje Konkursowi #1 dzisiejszą nazwę |
| `apps/web/captcha.py:56,66` | `contact@qaif.org` w komunikacie | `competition.contact_email` z odwrotem | komunikat czyta człowiek, który **nie może się zarejestrować** — adres musi być adresem tego organizatora |
| `apps/accounts/profile.py:51` `ANONYMISED_EMAIL_DOMAIN` | `invalid.olimpiadakwantowa.pl`, użycie `:369` | `f"invalid.{competition.primary_domain}"` **tylko dla nowych anonimizacji** | adres anonimowy jest **daną**, nie konfiguracją: jest kluczem logowania (`USERNAME_FIELD="email"`, więz `accounts_user_email_ci_uniq`). Migracji danych **nie ma i nie będzie** |
| `backend/templates/500.html:6,20` | „…– Olimpiada Kwantowa”, `mailto:contact@qaif.org` | tytuł „Błąd serwera”, zdanie bez adresu | strona renderuje się **bez kontekstu bazy** i to jest jej sens (komentarz `:7–8`). Zdjęcie adresu jest **widoczną zmianą** — decyzja **D14**, § 6 |

#### 1.1.5. (E) Dostęp do `/cms/` per konkurs — domknięcie etapu 1

**Stan faktyczny, sprawdzony w migracji.** `cms.0003_coordinator_permissions` (79 linii) **kopiuje**
do globalnej grupy `coordinator` komplet uprawnień wbudowanych grup Wagtaila `Editors` i `Moderators`:
`Group.permissions` wraz z `wagtailadmin.access_admin` (`:39`), każdy wiersz `GroupPagePermission`
(`:40–45`) — a te są **na korzeniu drzewa**, czyli na wszystkich witrynach naraz — i każdy wiersz
`GroupCollectionPermission` (`:46–51`), na **korzeniu kolekcji**. Do tego nic w kodzie produkcyjnym
nie zakłada kolekcji per konkurs: `apps/cms/attachments.py:87` i `apps/cms/images.py:40,162` wgrywają
wszystko do `Collection.get_first_root_node()`.

**Skutek, nazwany wprost:** koordynator drugiego konkursu, dopisany do grupy `coordinator` przez
`create_competition --coordinator-email`, dostaje prawo edycji i publikacji **każdej strony każdego
konkursu** oraz dostęp do wszystkich obrazów i dokumentów. `docs/OPERACJE.md` § 6.2 opisuje to jako
znany fakt i odsyła do ręcznego ograniczenia uprawnień w `/cms/`. To jest obejście, nie rozwiązanie.

**Projekt: grupa Django na konkurs, nadawana razem z członkostwem** (`apps/cms/permissions.py`,
nowy moduł):

```python
def cms_group_name(competition) -> str:
    """Nazwa grupy redakcyjnej konkursu. Prefiks ``cms:`` jest zarezerwowany: grupa należy do
    systemu, a nie do administratora, i ``ensure_cms_group`` odtwarza jej uprawnienia."""
    return f"cms:{competition.slug}"


def ensure_cms_group(competition) -> Group:
    """Trzy rzeczy, każda z innego powodu:

    1. ``wagtailadmin.access_admin`` i uprawnienia modelowe — **kopiowane** z ``Editors`` +
       ``Moderators``, dokładnie tak, jak robi ``cms.0003``; kopiowanie zamiast wypisywania nazw
       kodowych jest odporne na zmiany między wersjami Wagtaila,
    2. ``GroupPagePermission`` na ``competition.site.root_page`` — **nie** na korzeniu drzewa; to
       jest cała różnica,
    3. ``GroupCollectionPermission`` na kolekcji konkursu (``ensure_collection``).
    """


def ensure_collection(competition) -> Collection:
    """Kolekcja mediów konkursu jako dziecko korzenia. Od tej zmiany ``attachments.py`` i
    ``images.py`` wgrywają do kolekcji konkursu z żądania, a do korzenia tylko wtedy, gdy konkursu
    nie da się rozstrzygnąć. Wiersze już wgrane **zostają tam, gdzie są** — przenoszenie biblioteki
    Konkursu #1 byłoby zmianą widoczną w ``/cms/``."""
```

| Flaga `scoped_cms_permissions` | Grupa koordynatora | Uprawnienia stron | Kolekcja |
|---|---|---|---|
| `False` (Konkurs #1, domyślnie) | globalna `coordinator` | korzeń drzewa (jak dziś) | korzeń (jak dziś) |
| `True` | `cms:<slug>` **dodatkowo** do globalnej | `site.root_page` konkursu | kolekcja konkursu |

**Kolejność, która chroni Konkurs #1.** Grupa `cms:<slug>` **dokłada** uprawnienia, nigdy nie
odbiera; koordynator Konkursu #1 zostaje w grupie `coordinator` i jego zestaw uprawnień się nie
zmienia (punkt 20 listy § 0.5, test § 5.5). Odebranie globalnej grupy koordynatorom **obcych**
konkursów jest osobną, jawną komendą `manage.py scope_cms_access --competition <slug> [--dry-run]`,
która wypisuje, kto straci dostęp do czego, i **odmawia wykonania dla `--competition kwantowa`** —
twardy warunek w kodzie, nie w dokumentacji. Wzorzec jest ten sam, co `check_memberships --fix`.

**Czego nie robimy:** nie ruszamy migracji `cms.0003` (zmiana historii migracji na produkcji), nie
przenosimy istniejących mediów między kolekcjami, nie odbieramy nikomu uprawnień automatycznie, nie
zmieniamy `WAGTAILDOCS_SERVE_METHOD` ani polityki widoczności kolekcji.

**Domknięcie (wydanie „uprawnienia CMS per konkurs”, po etapie 2).** Luka „grupa `coordinator` jest
globalna” jest zamknięta, ale inną drogą niż opisana wyżej — i to jest świadoma zmiana decyzji,
nie jej obejście:

- **odbieramy grupie, a nie ludziom.** Wyjęcie koordynatora z grupy `coordinator` przy wyłączonym
  `memberships_enforced` odbierałoby mu rolę w całości (tak jak ostrzegała stara odmowa komendy).
  Dlatego `scope_cms_access` zostawia wszystkich w grupie `coordinator` i zabiera **grupie**
  uprawnienia `/cms/`; redakcję daje wyłącznie `cms:<slug>`, do której członków wyznacza serwis
  z roli koordynatora (`apps/cms/permissions.py` `sync_user_cms_groups`, sygnały `apps/cms/signals.py`),
- **Konkurs #1 nie jest już wyjątkiem w kodzie — jest dowodem w komendzie.** Zamiast odmowy dla
  `kwantowa` komenda liczy macierz możliwości każdego koordynatora (`cms_abilities`) przed i po
  i przy jednym konkursie wycofuje całość, gdy różni się choć jedną pozycją. Test
  `test_competition_one_coordinator_keeps_identical_abilities` sprawdza to samo na bazie z migracji,
- **media z korzenia kolekcji przechodzą do kolekcji konkursu** — przy jednym konkursie same, przy
  kilku tylko wskazane (`--root-media-to`), razem z ograniczeniem widoczności korzenia. Bez tego
  zawężenie zabrałoby koordynatorowi Konkursu #1 jego bibliotekę,
- **szerokość „wszystkie konkursy” ma imię**: rola platformy *superkoordynator* (grupa
  `superkoordynator`, `apps/accounts/super_coordinator.py`), nadawana komendą z wpisem audytu.
  Obecni koordynatorzy dostają ją **przed** zawężeniem (`superkoordynator
  --all-current-coordinators`, polecenie organizatora „obecny koordynator ma nim zostać”),
- **wyciek tytułów poza drzewem i kolekcjami** zamyka `apps/cms/scope.py` (zasięg redaktora liczony
  z jego `GroupPagePermission`/`GroupCollectionPermission`) z hakami w `apps/cms/wagtail_hooks.py`
  (wybór strony, polityka i widoki komunikatów, okno wyboru komunikatu, API panelu) i warstwą
  `apps/cms/middleware.py` (rodzic w wyborze strony, raport „Użycie typów stron”); dziennik
  `ModelLogEntry` zawęża podmiana `viewable_by_user`,
- **wsteczna zgodność bez flagi:** stan „po komendzie” rozpoznaje się z danych — grupa `coordinator`
  bez `GroupPagePermission` (`global_coordinator_scoped`). Przed komendą zasięg `/cms/` jest bez
  ograniczeń, sygnały nic nie zapisują, pliki lądują w korzeniu — jak przed wydaniem. Flaga
  `scoped_cms_permissions` zostaje dla trybu z etapu 2 i po komendzie nie jest potrzebna.

Runbook: `docs/OPERACJE.md` § 6.6. Testy: `apps/cms/tests/test_cms_scope.py` (komenda, macierz przed/po),
`apps/cms/tests/test_cms_permissions_per_competition.py` (koordynator A kontra B we wszystkich
miejscach `/cms/`), `apps/accounts/tests/test_super_coordinator.py`.

---

### 1.2. Konfigurowalny przebieg zawodów (edytor procesu)

Najtrudniejszy obszar etapu 2 i jedyny, w którym błąd jest niewidoczny do chwili ogłoszenia wyników.

#### 1.2.1. Dzisiejszy przebieg jako fakty w kodzie

| Fakt | Gdzie zapisany |
|---|---|
| etapy są trzy plus trening | `StageKind` (`apps/competitions/models.py:377`): `ELIM`, `DISTRICT`, `FINAL`, `TRAINING` |
| kolejność etapów jest **stałą krotką** | `STAGE_ORDER = (ELIM, DISTRICT, FINAL)` — `apps/results/services.py:53`; `next_stage_of` (`:413`) czyta ją przez `.index()` |
| jeden etap danego rodzaju na edycję | `UniqueConstraint(["edition","kind"], name="competitions_stage_unique_kind")` — `models.py:503` |
| etap ma **jedną** formę | `Stage.format` (`:436`), `StageFormat` (`:402`): `SUBMISSIONS`, `INTERVIEW`, `QUIZ` |
| skala per etap, nadpisywalna per zadanie | `ScoringScale` (`:657`, `OneToOne`), `Problem.scoring_values` (`:790`, `null` = dziedzicz) |
| wartości skali są nieujemnymi całkowitymi **z obowiązkowym zerem** | `validate_scoring_values` (`:86–116`) |
| suma etapu = Σ `FinalGrade.score`, **bez wag** | `compute_stage_results` (`apps/results/services.py:233`) |
| etap testowy **zastępuje** sumę, nie dokłada | `quiz_scores` w `compute_stage_results`; `apps/quiz/services.py:672` `stage_scores` |
| ranking malejąco po sumie, remis = to samo miejsce | `_rank_rows` (`:180`): `sorted(key=(-total, public_code))`; `public_code` jest **porządkiem powtarzalności wydruku**, nie kryterium rozstrzygania |
| **nie ma żadnego rozstrzygania remisów** | remis na progu wpuszcza wszystkich (`_top_n_cutoff`, `:345`) |
| cztery tryby progu | `QualificationMode` (`models.py:684`) |
| decyzja komitetu bije próg | `qualified_with_manual` (`:390`), `StageEntry.manual_qualification` (`models.py:982`) |
| kwalifikacja tworzy wpisy w następnym etapie | `_sync_next_stage` (`:433`) |
| dwóch recenzentów, liczba jako **argument wywołania** | `assign_reviewers(stage, per_submission=2, …)` — `apps/grading/services.py:427` |
| rundy dwie: ślepa i rozjemcza | `ROUND_BLIND = 1`, `ROUND_TIEBREAK = 2` — `apps/grading/models.py:24–25` |
| **nie ma kategorii** | grep: `category` wyłącznie w `apps/support` (kategorie zgłoszeń) |
| **nie ma drużyn** | grep: brak modelu, punktacji i wpisu drużynowego |
| **nie ma wag** | jedyna waga w systemie to `QuizQuestion.points` (`apps/quiz/models.py:324`) |
| **punkty ujemne tylko w teście online** | `QuizQuestion.negative_points` (`:328`), `NegativeFloor` (`:82`), `award_points` (`apps/quiz/grading.py:314`) |
| **rozmowa nie ma ścieżki punktów** | `docs/BACKLOG.md`: `compute_stage_results` daje dla `INTERVIEW` same zera, punkty wpisuje koordynator w `/admin/` |

Cztery przedostatnie wiersze są istotą zadania: trzy z czterech zamawianych zdolności nie istnieją
w żadnej postaci, a czwarta (punkty ujemne) istnieje w aplikacji, która **nie przechodzi** przez
maszynę stanów oceniania.

#### 1.2.2. Kolejność etapów jako dane: `PipelineStep`

```python
class PipelineStep(models.Model):
    """Jeden krok przebiegu edycji: który etap, w którym miejscu kolejki.

    Model jest listą, a nie polem ``Stage.order``, bo krok niesie **dwie** rzeczy: miejsce
    w kolejce i regułę przejścia (``TransitionRule``). Pole porządkowe na etapie zostawiłoby regułę
    bez właściciela, a reguł bywa kilka na krok (osobny próg na kategorię).
    """

    edition = models.ForeignKey("competitions.Edition", on_delete=models.CASCADE,
                                related_name="pipeline_steps", verbose_name="edycja")
    stage = models.OneToOneField("competitions.Stage", on_delete=models.CASCADE,
                                 related_name="pipeline_step", verbose_name="etap")
    position = models.PositiveSmallIntegerField("miejsce w kolejce")
    #: Krok poza torem zawodów: trening, warsztat, sesja próbna. Nie kwalifikuje, nie jest
    #: następnikiem ani poprzednikiem i nie liczy się do osi czasu.
    off_pipeline = models.BooleanField("poza torem zawodów", default=False)

    objects = competition_scoped_manager("edition__competition")

    class Meta:
        ordering = ("edition", "position", "id")
        constraints = [models.UniqueConstraint(fields=["edition", "position"],
                                               condition=Q(off_pipeline=False),
                                               name="competitions_pipelinestep_unique_position")]
```

`next_stage_of` przestaje czytać `STAGE_ORDER`:

```python
def next_stage_of(stage: Stage) -> Stage | None:
    """Przy wyłączonej fladze ``process_editor`` odpowiedź pochodzi z ``STAGE_ORDER`` — tej samej
    krotki i tą samą drogą. Przy włączonej — z ``PipelineStep.position``. Obie odpowiedzi muszą być
    dla Konkursu #1 **równe** i to jest osobny test (§ 5.2)."""
    competition = stage.edition.competition
    if not competition.has_feature("process_editor"):
        return _next_stage_by_kind(stage)          # dzisiejsze ciało, przeniesione bez zmiany
    step = getattr(stage, "pipeline_step", None)
    if step is None or step.off_pipeline:
        return None
    following = (PipelineStep.objects
                 .filter(edition_id=stage.edition_id, off_pipeline=False, position__gt=step.position)
                 .select_related("stage").order_by("position").first())
    return following.stage if following else None
```

**`StageKind` przestaje być kolejnością, a zostaje etykietą rodzaju.** Dla przebiegu o pięciu etapach
dochodzi wartość `ROUND` („runda”) z rozróżnieniem przez `Stage.name`, a więz `(edition, kind)`
staje się warunkowy — **z zachowaniem nazwy**, żeby migracja była `RemoveConstraint` +
`AddConstraint` o tej samej nazwie, a nie zmianą, którą trzeba potem tropić w logach (ta sama reguła,
co w etapie 1 § 1.5):

```python
models.UniqueConstraint(fields=["edition", "kind"], condition=~Q(kind=StageKind.ROUND),
                        name="competitions_stage_unique_kind")
```

Konkurs #1 nie ma ani jednego etapu rodzaju `ROUND`, więc więz działa dla niego dosłownie jak dziś.

#### 1.2.3. Kilka form w jednym etapie: `StageComponent`

Dziś `compute_stage_results` ma dwie **wykluczające się** ścieżki (zadania albo test); komentarz
w kodzie mówi wprost, że etap o dwóch formach naraz „nie da się opisać ani w regulaminie, ani
w tabeli wyników”. Etap 2 znosi wykluczalność nie przez dołożenie trzeciej gałęzi, tylko przez
zamianę jednej osi na listę:

```python
class ComponentKind(models.TextChoices):
    SUBMISSIONS = "SUBMISSIONS", "rozwiązania pisemne"   # źródło: FinalGrade po zadaniach
    QUIZ = "QUIZ", "test online"                          # źródło: quiz.services.stage_scores
    INTERVIEW = "INTERVIEW", "rozmowa"                    # źródło: InterviewScore (niżej)
    ONSITE = "ONSITE", "zawody na miejscu"                # źródło: FinalGrade, wpis komisji
    TEAM = "TEAM", "praca drużynowa"                      # źródło: FinalGrade wpisu drużyny


class StageComponent(models.Model):
    """Jedna forma w etapie, z własną wagą i własnym źródłem punktów.

    Etap **bez ani jednego komponentu** zachowuje się dokładnie jak dziś: ``compute_stage_results``
    czyta wtedy ``Stage.format``. Komponenty są dołożeniem wymiaru, a nie przepisaniem — ta sama
    decyzja, co przy konkursie w etapie 1.
    """

    stage = models.ForeignKey("competitions.Stage", on_delete=models.CASCADE,
                              related_name="components", verbose_name="etap")
    kind = models.CharField("forma", max_length=16, choices=ComponentKind.choices)
    name = models.CharField("nazwa", max_length=80, blank=True)
    position = models.PositiveSmallIntegerField("kolejność", default=1)
    #: Waga jako **ułamek zwykły**, nie zmiennoprzecinkowy — uzasadnienie w § 1.2.6.
    weight_numerator = models.PositiveSmallIntegerField("licznik wagi", default=1)
    weight_denominator = models.PositiveSmallIntegerField("mianownik wagi", default=1)
    #: ``True`` odtwarza dzisiejsze ``_assert_finalized`` (``STAGE_NOT_FINALIZED``); ``False`` liczy
    #: brak wyniku jako zero i jest dla komponentu nieobowiązkowego.
    required = models.BooleanField("wymagany", default=True)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        ordering = ("stage", "position", "id")
        constraints = [
            models.UniqueConstraint(fields=["stage", "kind", "position"],
                                    name="competitions_stagecomponent_unique"),
            models.CheckConstraint(condition=Q(weight_denominator__gte=1),
                                   name="competitions_stagecomponent_denominator_positive"),
        ]
```

**Punkty z rozmowy — brakujące ogniwo, które i tak trzeba domknąć.** Komponent `INTERVIEW` wymaga
modelu, którego nie ma:

```python
class InterviewScore(models.Model):
    """Punkty z rozmowy — wpis komisji, nie recenzja.

    Osobny model, a nie ``FinalGrade`` bez ``Submission``: ``FinalGrade`` jest ``OneToOne`` ze
    zgłoszeniem (``apps/grading/models.py:194``), a cała jego semantyka — konsensus, trzeci
    recenzent, moderacja, reklamacja — dotyczy pracy oddanej jako plik. Rozmowa nie ma pliku, nie ma
    rundy ślepej i nie ma czego zastąpić nową wersją.
    """
    booking = models.OneToOneField("competitions.InterviewBooking", on_delete=models.CASCADE,
                                   related_name="score", verbose_name="termin")
    score = models.PositiveSmallIntegerField("punkty")
    rationale = models.TextField("uzasadnienie komisji")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="interview_scores")
    decided_at = models.DateTimeField("wpisane", default=timezone.now)

    objects = competition_scoped_manager("booking__entry__stage__edition__competition")
```

Wpis idzie przez `apps/competitions/interviews.py` (nowa funkcja `set_interview_score`) z audytem
`interview.scored` i walidacją względem skali etapu — tej samej, którą zna `allowed_scores`
(`apps/grading/services.py:277`). **Dla Konkursu #1 to jest zmiana widoczna** (ekran zamiast
`/admin/`), więc idzie za flagą `process_editor` i wymaga zgody organizatora — decyzja **D9**, § 6.

**Drużyny (`team_entries`).** Drużyna jest **właścicielem wpisu**, a nie nowym bytem obok uczestnika:

```python
class Team(models.Model):
    competition = FK("tenancy.Competition", PROTECT, related_name="teams")
    edition = FK("competitions.Edition", CASCADE, related_name="teams")
    name = CharField("nazwa", max_length=120)
    #: Ta sama rola, co ``Participant.public_code``, i ten sam generator z prefiksem konkursu.
    public_code = CharField("kod publiczny", max_length=16)
    school = CharField("szkoła", max_length=255, blank=True)
    supervisor_email = EmailField("opiekun", blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["competition", "public_code"],
                             name="competitions_team_public_code_per_competition"),
            UniqueConstraint(fields=["edition", "name"],
                             name="competitions_team_unique_name_per_edition"),
        ]


class TeamMember(models.Model):
    team = FK(Team, CASCADE, related_name="members")
    participant = FK("accounts.Participant", PROTECT, related_name="team_memberships")
    is_captain = BooleanField("kapitan", default=False)

    class Meta:
        constraints = [UniqueConstraint(fields=["team", "participant"],
                                        name="competitions_teammember_unique")]
```

`StageEntry` dostaje nullowalne `team = FK(Team, null=True, blank=True, on_delete=PROTECT)`, a więz
`competitions_stageentry_single_owner` pilnuje, że wpis ma uczestnika **albo** drużynę, nigdy obu.
Konsekwencja dla `compute_stage_results`: pętla chodzi po wpisach, więc **nie zmienia się wcale** —
już chodzi po `StageEntry`. Zmienia się `_display_name` (`:610`), które dla wpisu drużynowego
pokazuje nazwę drużyny.

**Cena, nazwana wprost.** `StageEntry.participant` jest dziś `NOT NULL` i czyta je kilkanaście
miejsc (`apps/results/services.py`, `apps/submissions/models.py`, `apps/grading/services.py`,
`apps/web/views/coordinator_*`). Każde musi przejść przez jeden helper `entry_owner(entry)`.
Bez flagi `team_entries` wartość jest zawsze uczestnikiem, więc zachowanie jest identyczne;
przeoczone miejsce ujawni się dopiero w konkursie drużynowym. Mitygacja: **helper podnosi
`AttributeError`, a nie oddaje `None`** — ten sam wzorzec, którym etap 1 wymusił poprawki po
zamianie `user.participant` (§ 6, T2).

#### 1.2.4. Kategorie

```python
class Category(models.Model):
    """Kategoria uczestników: rocznik, klasa, typ szkoły albo cokolwiek, co ustali organizator.

    Kategoria jest **danymi konkursu**, a nie edycji („szkoła podstawowa / ponadpodstawowa” ma
    przeżyć rocznik); przypisanie jest natomiast per **wpis do etapu**, bo uczeń zmienia klasę
    między edycjami.
    """
    competition = FK("tenancy.Competition", PROTECT, related_name="categories")
    code = SlugField("kod", max_length=32)
    name = CharField("nazwa", max_length=120)
    position = PositiveSmallIntegerField("kolejność", default=0)
    #: Reguła automatycznego przypisania — **opcjonalna**. Puste = kategorię wskazuje uczestnik albo
    #: koordynator. Zakres klas jest jedyną regułą, którą system policzy sam, bo ``Participant.grade``
    #: jest jedyną cechą o porządku.
    grade_min = PositiveSmallIntegerField("klasa od", null=True, blank=True)
    grade_max = PositiveSmallIntegerField("klasa do", null=True, blank=True)
    is_active = BooleanField("aktywna", default=True)

    objects = competition_scoped_manager()

    class Meta:
        ordering = ("competition", "position", "id")
        constraints = [
            UniqueConstraint(fields=["competition", "code"], name="competitions_category_unique_code"),
            CheckConstraint(condition=Q(grade_min__isnull=True) | Q(grade_max__isnull=True)
                            | Q(grade_min__lte=F("grade_max")),
                            name="competitions_category_grades_ordered"),
        ]
```

`StageEntry` dostaje `category = FK(Category, null=True, blank=True, on_delete=PROTECT)`.
Wpływ na wyniki jest w **trzech** miejscach i ani jednym więcej:

1. `_rank_rows` (`apps/results/services.py:180`) dostaje argument `group_by`: `None` → jedna tabela
   (dziś), `"category_id"` → osobny ranking w każdej kategorii, z miejscami od 1 wewnątrz grupy;
2. `_qualified_entry_ids` (`:361`) czyta próg z `TransitionRule` filtrowanego kategorią;
   `TOP_N_PER_DISTRICT` staje się szczególnym przypadkiem „N na grupę” z grupą = region albo kategoria;
3. `build_snapshot` (`:636`) dokłada do wiersza `category` **wyłącznie** dla konkursu z kategoriami.
   Snapshot Konkursu #1 nie zyskuje ani jednego klucza — test § 5.2 porównuje słowniki na równość,
   a nie na podzbiór.

#### 1.2.5. Reguły przejścia jako dane: `TransitionRule`

`QualificationRule` (`apps/competitions/models.py:691`) **nie znika i nie zmienia pól**; obok staje
model ogólniejszy, którego szczególnym przypadkiem jest dzisiejszy:

```python
class TransitionMode(models.TextChoices):
    MIN_POINTS = "MIN_POINTS", "minimum punktów"
    TOP_N = "TOP_N", "najlepszych N"
    TOP_N_PER_GROUP = "TOP_N_PER_GROUP", "N w grupie"      # grupa: region albo kategoria
    HYBRID = "HYBRID", "minimum punktów ORAZ top N"
    PERCENTILE = "PERCENTILE", "najlepsze P procent"
    MANUAL = "MANUAL", "wyłącznie decyzja komitetu"


class TransitionGroupBy(models.TextChoices):
    NONE = "", "bez podziału"
    REGION = "REGION", "region"
    CATEGORY = "CATEGORY", "kategoria"


class TransitionRule(models.Model):
    """Reguła przejścia z kroku do następnego. **Kilka reguł na krok = suma zakwalifikowanych.**

    Model jest listą, bo organizator pisze w regulaminie zdania typu „do finału przechodzi 30
    najlepszych **oraz** każdy, kto zdobył co najmniej 90 punktów”. Suma zbiorów jest jedyną
    dozwoloną kompozycją; iloczyn ma własny tryb (``HYBRID``), bo „oraz” w regulaminie bywa jednym
    i drugim, a obie operacje wyrażone tą samą listą byłyby nieczytelne.
    """
    step = FK(PipelineStep, CASCADE, related_name="transition_rules")
    mode = CharField("tryb", max_length=24, choices=TransitionMode.choices)
    group_by = CharField("podział", max_length=16, choices=TransitionGroupBy.choices, blank=True)
    #: Zawężenie do jednej kategorii; puste = reguła dotyczy wszystkich.
    category = FK(Category, CASCADE, null=True, blank=True, related_name="transition_rules")
    min_points = PositiveIntegerField("minimum punktów", null=True, blank=True)
    top_n = PositiveIntegerField("liczba kwalifikowanych", null=True, blank=True)
    percentile = PositiveSmallIntegerField("procent", null=True, blank=True)
    position = PositiveSmallIntegerField("kolejność", default=0)

    objects = competition_scoped_manager("step__edition__competition")

    class Meta:
        ordering = ("step", "position", "id")
        constraints = [
            CheckConstraint(condition=Q(percentile__isnull=True)
                            | (Q(percentile__gte=1) & Q(percentile__lte=100)),
                            name="competitions_transitionrule_percentile_range"),
            CheckConstraint(condition=Q(top_n__isnull=True) | Q(top_n__gte=1),
                            name="competitions_transitionrule_top_n_positive"),
        ]
```

Odwzorowanie dzisiejszych trybów jest jeden do jednego:

| `QualificationMode` | `TransitionRule` |
|---|---|
| `MIN_POINTS(min_points=N)` | `mode=MIN_POINTS, min_points=N, group_by=""` |
| `TOP_N(top_n=N)` | `mode=TOP_N, top_n=N, group_by=""` |
| `TOP_N_PER_DISTRICT(top_n=N)` | `mode=TOP_N_PER_GROUP, top_n=N, group_by=REGION` |
| `HYBRID(min_points=M, top_n=N)` | `mode=HYBRID, min_points=M, top_n=N, group_by=""` |

**Trzy zachowania brzegowe zostają nietknięte** i są osobnymi asercjami testu § 5.2, bo każde jest
decyzją, a nie skutkiem ubocznym: **zero nie kwalifikuje** w trybach z `top_n` (`_top_n_cutoff`,
`:345`), **remis na progu wpuszcza wszystkich** (`total >= cutoff`, nie `rank <= top_n`), **decyzja
komitetu bije regułę** (`qualified_with_manual`, `:390`) — także sumę reguł. `PERCENTILE` i `MANUAL`
są nowe i Konkurs #1 ich nie używa.

#### 1.2.6. Punktacja: wagi, punkty ujemne, remisy

Trzy niezależne zmiany za flagą `weighted_scoring`, ze wspólnym ograniczeniem:
**`StageEntry.total_points` zostaje `PositiveIntegerField`** (`models.py:976`), a `Review.score`
i `FinalGrade.score` zostają `PositiveSmallIntegerField`. Zmiana na `Float`/`Decimal` byłaby zmianą
schematu dotykającą każdej tabeli wyników i każdego snapshotu — czyli złamaniem punktu 7 z § 0.2.

**(a) Wagi zadań.** `Problem` dostaje `weight_numerator` i `weight_denominator`
(`PositiveSmallIntegerField`, oba `default=1`). Ułamek zwykły, a nie `FloatField`, i to jest decyzja,
nie przesada: waga `1/3` zapisana jako `0.333…` daje sumę zależną od kolejności dodawania, czyli
**tabelę wyników zmieniającą się przy przeliczeniu**. Sumowanie idzie przez `fractions.Fraction`,
a zaokrąglenie następuje **raz**, na końcu, `ROUND_HALF_UP` — tą samą metodą, którą stosuje już
`apps/quiz/services.py:672`. Dla `1/1` wynik jest identyczny z dzisiejszym sumowaniem `int`, więc
Konkurs #1 nie zmienia żadnej sumy nawet przy **włączonej** fladze (test § 5.2).

**(b) Punkty ujemne.** `validate_scoring_values` (`models.py:86`) dostaje argument
`allow_negative: bool = False` przekazywany przez wołającego na podstawie flagi; bez niego funkcja
zachowuje się bit w bit jak dziś, łącznie z komunikatami błędów, które czyta koordynator na
`/coordinator/stages/<id>/scale/`. Przy `allow_negative=True` znika wymóg nieujemności; **wymóg
obecności zera zostaje** (skala bez zera nie ma jak wyrazić „brak istotnego postępu”), a `max_value`
nadal musi równać się `max(values)`.

Skutek dla schematu: `Review.score` musiałby przyjąć wartość ujemną. **Nie zmieniamy typu.** Zamiast
tego skala z wartościami ujemnymi jest **przesunięta**: model przechowuje `score` jako
`value - scale.offset`, gdzie `ScoringScale.offset` **(nowe, `SmallIntegerField(default=0)`)** jest
liczbą dokładaną do każdej wartości, żeby najniższa wyszła zerem; suma etapu jest liczona po odjęciu
przesunięcia, raz, w `compute_stage_results`. Konkurs #1 ma `offset = 0`, czyli arytmetyki nie
przybywa.

> **To jest najbardziej ryzykowna pojedyncza decyzja tego dokumentu.** Alternatywa: `AlterField` na
> `SmallIntegerField` — technicznie prosta, nie rusza danych, nie zmienia odczytów. Cena: traci się
> bazodanową gwarancję „punkt nie bywa ujemny”, która dziś łapie błąd serwisu zanim dojdzie do tabeli
> wyników. Rekomendacja: **przesunięcie** (decyzja **D10**, § 6) — konkurs z punktami ujemnymi to
> konkurs, którego jeszcze nie ma, a gwarancja chroni ten, który jest.

**(c) Rozstrzyganie remisów jako uporządkowane dane.**

```python
class TieBreakKey(models.TextChoices):
    HIGHEST_SINGLE = "HIGHEST_SINGLE", "najwyższy wynik w jednym zadaniu"
    PROBLEM_SCORE = "PROBLEM_SCORE", "wynik we wskazanym zadaniu"
    COMPONENT_SCORE = "COMPONENT_SCORE", "wynik we wskazanym komponencie"
    SOLVED_COUNT = "SOLVED_COUNT", "liczba zadań z pełnym wynikiem"
    SUBMITTED_AT = "SUBMITTED_AT", "wcześniejsze oddanie ostatniej pracy"
    NONE = "NONE", "bez rozstrzygania (wspólne miejsce)"


class TieBreak(models.Model):
    stage = FK("competitions.Stage", CASCADE, related_name="tie_breaks")
    key = CharField("kryterium", max_length=24, choices=TieBreakKey.choices)
    problem = FK("competitions.Problem", CASCADE, null=True, blank=True, related_name="tie_breaks")
    component = FK(StageComponent, CASCADE, null=True, blank=True, related_name="tie_breaks")
    descending = BooleanField("malejąco", default=True)
    position = PositiveSmallIntegerField("kolejność", default=0)
```

`_rank_rows` buduje klucz sortowania z listy `TieBreak` etapu. **Etap bez ani jednego `TieBreak`
zachowuje się dokładnie jak dziś**; Konkurs #1 nie dostaje w migracji żadnego wiersza, bo jego
regulamin remisów nie rozstrzyga. Sortowanie po `public_code` **zostaje zawsze ostatnim kluczem**,
także przy zdefiniowanych `TieBreak` — powód jest w docstringu `_rank_rows` i nie zmienia się:
„dwie publikacje tych samych danych muszą dać ten sam plik”.

#### 1.2.7. Role recenzenckie

Dziś rola recenzenta jest **numerem rundy**, a ich liczba — **argumentem wywołania**. Nie ma więc
odpowiedzi na pytanie „ilu recenzentów ma ten etap”, jest tylko „ilu przydzielił ten, kto klikał”.

```python
class ReviewerRole(models.Model):
    """Nazwana rola recenzencka etapu: pierwszy, drugi, arbiter, przewodniczący komisji."""
    stage = FK("competitions.Stage", CASCADE, related_name="reviewer_roles")
    code = SlugField("kod", max_length=24)         # "first", "second", "arbiter"
    name = CharField("nazwa", max_length=80)
    round = PositiveSmallIntegerField("runda", default=1)
    count = PositiveSmallIntegerField("liczba recenzentów", default=1)
    counts_towards_consensus = BooleanField("liczy się do zgodności", default=True)
    position = PositiveSmallIntegerField("kolejność", default=0)

    class Meta:
        constraints = [UniqueConstraint(fields=["stage", "code"],
                                        name="grading_reviewerrole_unique_code")]
```

`Review` dostaje nullowalne `role = FK(ReviewerRole, null=True, blank=True, on_delete=PROTECT)`.
**`Review.round` zostaje i zostaje autorytatywne** — rola jest etykietą nad rundą, nie zamiast niej.
`assign_reviewers` (`apps/grading/services.py:427`) czyta liczbę z ról etapu przy włączonej fladze,
a z argumentu `per_submission` w przeciwnym razie. `_consensus_score` (`:953`) filtruje recenzje po
`counts_towards_consensus`, gdy role istnieją; gdy nie istnieją — patrzy na wszystkie nieanulowane
recenzje rundy 1, czyli zachowuje się jak dziś.

**Czego nie zmieniamy:** reguły konfliktu okręgu (`has_district_conflict`, `:170`), maszyny stanów
oceniania, `supersede_earlier_versions`, `revise_review`, `unassign_reviewer`, `ReviewCancelReason`,
bramek `SCALE_LOCKED`.

#### 1.2.8. Migracja: dzisiejszy przebieg jako dane

`competitions.00xx_pipeline_from_stages` czyta bazę i zapisuje to, co w niej jest — nie literały:

```python
def forwards(apps, schema_editor):
    """Dla każdej edycji: kroki toru z istniejących etapów, reguły z istniejących progów.

    Kolejność bierzemy z ``STAGE_ORDER``, a nie z ``opens_at``: terminy bywają poprawiane w panelu
    i etap II potrafi mieć chwilowo datę wcześniejszą niż etap I, a kolejność zawodów nie jest
    o datach. Etap treningowy dostaje ``off_pipeline=True`` — dokładnie to, co dziś znaczy jego
    nieobecność w ``STAGE_ORDER``.
    """
    Edition = apps.get_model("competitions", "Edition")
    Stage = apps.get_model("competitions", "Stage")
    PipelineStep = apps.get_model("competitions", "PipelineStep")
    TransitionRule = apps.get_model("competitions", "TransitionRule")

    order = {"ELIM": 1, "DISTRICT": 2, "FINAL": 3}
    mode_map = {"MIN_POINTS": ("MIN_POINTS", ""), "TOP_N": ("TOP_N", ""),
                "TOP_N_PER_DISTRICT": ("TOP_N_PER_GROUP", "REGION"), "HYBRID": ("HYBRID", "")}

    for edition in Edition.objects.all().iterator():
        for stage in Stage.objects.filter(edition=edition).order_by("id"):
            off = stage.kind == "TRAINING"
            step, _ = PipelineStep.objects.update_or_create(
                stage_id=stage.pk,
                defaults={"edition_id": edition.pk,
                          "position": 0 if off else order.get(stage.kind, 99),
                          "off_pipeline": off})
            rule = getattr(stage, "qualification_rule", None)
            if rule is None or off:
                continue
            mode, group_by = mode_map[rule.mode]
            TransitionRule.objects.update_or_create(
                step_id=step.pk, position=0,
                defaults={"mode": mode, "group_by": group_by,
                          "min_points": rule.min_points, "top_n": rule.top_n})
```

**Czego migracja NIE robi:** nie tworzy komponentów (etap bez nich czyta `Stage.format`), kategorii,
`TieBreak` ani `ReviewerRole`; nie zmienia ani jednego pola `Stage`, `ScoringScale`,
`QualificationRule`, `Problem` ani `StageEntry`; nie włącza flagi `process_editor`.

**Czym dowodzimy, że tabele wyników są identyczne** — § 5.2: test przelicza **każdą ogłoszoną
publikację Konkursu #1 dwiema drogami** (dzisiejszą i przez `PipelineStep`/`TransitionRule`)
i porównuje wynikowe listy słowników `compute_stage_results` oraz `build_snapshot` **na równość**,
klucz po kluczu, wraz z `rank`, `qualified` i kolejnością wierszy.

### 1.3. Rejestracja, część 2

#### 1.3.1. Stan faktyczny i granica zmiany

Dzisiejsza rejestracja zna **jeden** rodzaj placówki: szkołę ponadpodstawową z wykazu SIO.

| Fakt | Gdzie |
|---|---|
| 8118 szkół ponadpodstawowych, rok 2025/2026 | `apps/schools/fixtures/szkoly-srednie-sio-2025.json` |
| reguła doboru wierszy z wykazu | `apps/schools/sio.py` — `SECONDARY_KINDS` (`:27–37`), `EXCLUDED_STUDENT_CATEGORY = "Dorośli"` (`:42`) |
| typ szkoły to siedem etykiet | `SchoolKind` — `apps/schools/models.py:29–43`; `KIND_ORDER` (`:52`) steruje porządkiem podpowiedzi |
| słownik jest **wspólny dla instalacji** | `School` (`:55`) nie ma ani kolumny konkursu, ani managera zakresowanego — decyzja D2 etapu 1 |
| upsert po RSPO, dezaktywacja zamiast kasowania | `seed_schools` — `apps/schools/management/commands/seed_schools.py:130–149` |
| szkoła spoza wykazu jest dopuszczona | `SchoolChoiceMixin.clean` — `apps/web/forms.py:270–319`; `_resolve_school` — `apps/accounts/services.py:82` |
| województwo bierze się ze szkoły przy imporcie | `apps/accounts/bulk_registration.py:662` |
| dwa endpointy, throttle `schools` 120/min | `apps/schools/api.py`, `urls.py`, `config/settings/base.py:682` |
| „okręgi szkolne” z v0.19.0 = normalizacja gminy i dzielnicy | `apps/schools/normalise.py` (227 linii), migracja `schools.0003_school_city_parent` |

Granica zmiany jest ostra i wynika z decyzji D2 etapu 1: **wykaz SIO zostaje wspólny i nie dostaje
kolumny konkursu.** Wszystko, co etap 2 dokłada, jest albo rozszerzeniem wspólnego wykazu o nowe
typy placówek (dane publiczne, tak samo wspólne), albo **osobną tabelą** słownika organizatora.

#### 1.3.2. Typy placówek: rozszerzenie wspólnego wykazu

```python
class InstitutionType(models.TextChoices):
    """Rodzaj placówki — **szersza oś** niż ``SchoolKind``: tamten rozróżnia liceum od technikum
    wewnątrz szkół ponadpodstawowych, ten rozróżnia szkołę podstawową od uczelni i od jej braku."""
    PRIMARY = "PRIMARY", "szkoła podstawowa"
    SECONDARY = "SECONDARY", "szkoła ponadpodstawowa"
    UNIVERSITY = "UNIVERSITY", "uczelnia wyższa"
    FOREIGN = "FOREIGN", "placówka poza Polską"
    NONE = "NONE", "bez szkoły"
    OTHER = "OTHER", "inna placówka"
```

`School` dostaje **jedną** kolumnę `institution_type` (`choices`, `default=SECONDARY`,
`db_index=True`). Migracja `schools.0004_institution_type` wypełnia wszystkie 8118 istniejących
wierszy wartością `SECONDARY` — co jest prawdą, bo tyle właśnie zawiera dzisiejszy wykaz.

| Typ | Źródło | Komenda |
|---|---|---|
| `PRIMARY` | ten sam wykaz SIO, inna reguła doboru — `PRIMARY_KINDS` **(nowa stała w `apps/schools/sio.py`)** | `seed_schools --fixture szkoly-podstawowe-sio-2025.json` |
| `UNIVERSITY` | wykaz POL-on; osobny fixture, osobny skrypt budujący | `seed_schools --fixture uczelnie-polon-2025.json` |
| `FOREIGN`, `NONE`, `OTHER` | **brak wierszy w `School`** — uczestnik wpisuje wolny tekst | — |

`seed_schools` zostaje **jedną** komendą i zostaje idempotentna po RSPO. Zmiana jest jedna:
dezaktywacja wierszy nieobecnych w pliku (`_apply`, `:144–148`) zawęża się do `institution_type`
wgrywanego pliku — inaczej wgranie wykazu uczelni wygasiłoby wszystkie szkoły ponadpodstawowe. To
jest **zmiana w kodzie widocznym dla Konkursu #1**, więc ma osobny test
`test_seed_schools_does_not_deactivate_other_institution_types`.

**Uczestnik spoza Polski.** `Participant` dostaje `country = CharField(max_length=2, blank=True)`
(ISO 3166-1 alpha-2) i `institution_name = CharField(max_length=255, blank=True)`. Pusty `country`
znaczy **Polska**, a nie „nie podano”: wpisanie `"PL"` istniejącym profilom byłoby migracją danych
osobowych bez powodu, a odczyt `country or "PL"` ma jedno miejsce. `institution_name` jest
**niezależne** od `school` — `school` zostaje nazwą do pokazania (wchodzi do k-anonimowości
`INITIALS_SCHOOL`), a przy `institution_type=FOREIGN` serwis rejestracji kopiuje do niego
`institution_name`, żeby żadne miejsce liczące statystyki nie musiało o tym wiedzieć. Dokładnie tak,
jak dziś kopiuje nazwę z wykazu (`_resolve_school`).

Region uczestnika spoza Polski: `Participant.district` jest dziś wymagane (`models.py:401`).
Rozwiązaniem **nie jest** znullowanie kolumny (zmiana widoczna w każdym filtrze i w konflikcie
interesów), tylko **region typu „poza Polską”** w drzewie regionów konkursu (§ 1.4.3).

#### 1.3.3. Słownik własny organizatora

Z decyzji D2 etapu 1 wprost wynika, że słownik organizatora **musi być osobną tabelą** — dopisanie
jego wierszy do `School` znaczyłoby, że placówki wgrane przez organizatora A pojawiają się
w podpowiedziach organizatora B.

```python
class CustomInstitution(models.Model):
    """Placówka z wykazu wgranego przez organizatora — **osobna tabela**, nie wiersz w ``School``.

    Osobna, bo ``School`` jest rejestrem publicznym wspólnym dla instalacji (etap 1 § 8, D2), a to
    jest lista jednego organizatora: uczelnie partnerskie, ośrodki, kluby, szkoły zagraniczne z jego
    programu. Wspólna tabela z kolumną właściciela znaczyłaby, że każde zapytanie wyszukiwarki musi
    pamiętać o filtrze — a zapomniany filtr w wyszukiwarce publicznej jest wyciekiem listy
    kontrahentów organizatora.
    """
    competition = FK("tenancy.Competition", CASCADE, related_name="custom_institutions")
    external_id = CharField("identyfikator organizatora", max_length=64, blank=True)
    name = CharField("nazwa", max_length=255)
    institution_type = CharField(max_length=16, choices=InstitutionType.choices,
                                 default=InstitutionType.OTHER)
    country = CharField("kraj", max_length=2, blank=True)
    region_code = SlugField("region", max_length=40, blank=True)          # § 1.4
    city = CharField("miejscowość", max_length=120, blank=True)
    postal_code = CharField(max_length=12, blank=True); address = CharField(max_length=255, blank=True)
    #: Kolumny wyliczane liczy **ta sama** funkcja, co dla ``School``
    #: (``apps.schools.normalise.derived_fields``) — dwie reguły normalizacji znaczyłyby dwa
    #: zachowania wyszukiwarki na jednym ekranie.
    search_text = CharField(max_length=400, db_index=True, editable=False, default="")
    city_search = CharField(max_length=120, db_index=True, editable=False, default="")
    is_active = BooleanField("aktywna", default=True)
    source_label = CharField("źródło", max_length=120, blank=True)        # nazwa pliku + data
    created_at = DateTimeField(default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        ordering = ("name", "id")
        constraints = [UniqueConstraint(fields=["competition", "external_id"],
                                        condition=~Q(external_id=""),
                                        name="schools_custominstitution_unique_external_id")]
        indexes = [Index(fields=("competition", "search_text"), name="schools_custom_search_idx")]
```

**Import CSV** — panel `/coordinator/institutions/`, za flagą `custom_school_directory`. Format
i reguły przepisane z importu uczniów (`apps/accounts/bulk_registration.py`), bo to jest ten sam
problem i ten sam człowiek go wykonuje: nagłówek obowiązkowy `nazwa`, opcjonalne `identyfikator`,
`rodzaj`, `kraj`, `region`, `miejscowosc`, `kod_pocztowy`, `adres`; limity `MAX_ROWS = 5000`
i `MAX_UPLOAD_BYTES = 2 MiB` (`bulk_registration.py:79,84`); **podgląd przed zapisem** z podpisanym
koszykiem w sesji (`pack_rows`/`unpack_rows`, `:564,578`); upsert po `external_id`, a bez niego po
`(competition, name, city)`; wiersz nieobecny w pliku dostaje `is_active=False`, **nigdy `DELETE`**;
audyt `institutions.imported` z licznikami, bez treści.

**Wyszukiwarka pyta o obie tabele** jednym wywołaniem
`search_institutions(query, *, competition, allowed_types, region=None, limit=20, offset=0)`:
najpierw wykaz publiczny (bo tam trafia większość), potem słownik własny, w obu uporządkowane
`KIND_ORDER` i nazwą — czyli tak, jak dziś (`_kind_rank`, `apps/schools/api.py:63`). Wiersz ze
słownika własnego ma w odpowiedzi `source: "custom"`, żeby formularz wiedział, do której kolumny
zapisać dowiązanie. **Bez flagi `custom_school_directory` zapytanie do `CustomInstitution` nie
wykonuje się ani razu** — to jest warunek utrzymania budżetu zapytań na `/register/` (§ 5.6).
Endpoint zostaje publiczny, bez logowania, z tym samym throttlem `schools`.

`Participant` dostaje `custom_institution_ref = FK(CustomInstitution, null=True, blank=True,
on_delete=PROTECT)` obok istniejącego `school_ref`. Dwa nullowalne klucze zamiast relacji ogólnej —
ta sama decyzja i to samo uzasadnienie, co przy `Certificate.entry`/`supervisor`
(`apps/results/models.py:137`): odbiorców są dwa rodzaje i nigdy nie będzie ich więcej, a klucz obcy
daje integralność, której `GenericForeignKey` nie daje.

#### 1.3.4. Co wolno w konkursie: `RegistrationProfile`

Zamiast piętnastu flag — **jeden wiersz konfiguracji na konkurs**:

```python
class RegistrationProfile(models.Model):
    """Co konkurs pyta przy rejestracji i co dopuszcza jako placówkę.

    Model, a nie pola na ``Competition``: to kilkanaście pól jednej sprawy, a ``Competition`` ma już
    30 pól i jest czytany na każdym żądaniu. Wiersz jest **opcjonalny** — jego brak znaczy „jak
    dziś”, i taki jest stan Konkursu #1.
    """
    competition = OneToOneField("tenancy.Competition", CASCADE, related_name="registration_profile")
    #: Pusta lista = wyłącznie ``SECONDARY``, czyli dzisiaj.
    allowed_institution_types = JSONField("dozwolone placówki", default=list, blank=True)
    allow_custom_directory = BooleanField("słownik organizatora", default=False)
    allow_free_text_school = BooleanField("szkoła spoza wykazu", default=True)
    allow_foreign = BooleanField("uczestnicy spoza Polski", default=False)
    require_grade = BooleanField("klasa wymagana", default=True)
    require_phone = BooleanField("telefon wymagany", default=True)
    require_region = BooleanField("region wymagany", default=True)
    require_birth_year = BooleanField("rocznik wymagany", default=True)
    grade_min = PositiveSmallIntegerField(null=True, blank=True)
    grade_max = PositiveSmallIntegerField(null=True, blank=True)
    participant_picks_category = BooleanField("kategoria z wyboru", default=False)
```

**Wartości domyślne każdego pola odtwarzają dzisiejszy formularz.** Migracja **nie tworzy** wiersza
dla Konkursu #1 — brak wiersza i wiersz z domyślnymi są równoważne, a brak jest tańszy i jawniejszy.
Odczyt ma jedno miejsce: `registration_profile(competition)` **(nowe, `apps/accounts/services.py`)**.

**Formularz.** `SchoolChoiceMixin` (`apps/web/forms.py:189`) dostaje w `__init__` profil i **dokłada**
pola: `institution_type` (tylko gdy dozwolonych typów jest więcej niż jeden), `country` (tylko przy
`allow_foreign`), `institution_name` (tylko przy `FOREIGN`/`OTHER`). Dzisiejsze pięć nazw pól
(`SCHOOL_FIELD_NAMES`, `:179`) **zostaje bez zmian i w tej samej kolejności**, a
`PARTICIPANT_FIELD_ORDER` (`:400–417`) rozszerza się **tylko wtedy**, gdy profil czegoś wymaga. Bez
profilu wygenerowany HTML `/register/` jest identyczny — `test_registration_form_html_unchanged`
(§ 5.3).

**Picker.** `backend/static/js/school-picker.js` dostaje jeden nowy atrybut `data-institution-type`
na korzeniu i przy jego zmianie przekazuje typ do endpointu. Bez atrybutu skrypt zachowuje się
dokładnie jak dziś — warunek, bo ten sam plik obsługuje cztery ekrany (`/register/`,
`/rejestracja/dokoncz/`, profil, edycja konta w panelu koordynatora) i musi działać bez JavaScriptu.

**Import grupowy.** `bulk_registration.py` bierze dziś województwo ze szkoły (`:662`); przy włączonym
`custom_school_directory` ta sama linia pyta o `region_code` placówki własnej, a przy `FOREIGN`
zostawia region „poza Polską”. `COLUMNS` (`:139–153`) dostaje **opcjonalną** kolumnę `kategoria`,
więc pliki przygotowane wcześniej nadal się wczytują.

---

### 1.4. Dowolny podział terytorialny

#### 1.4.1. Stan faktyczny

`Voivodeship` (`apps/accounts/models.py:110–134`) to zamknięta lista 16 wartości z ASCII-owym
slugiem. Czytają ją: `Participant.district` (`:401`, wymagane), `CommitteeMember.district`
(`:551–553`, opcjonalne), `CommitteeMember.district_verified` (`:560`, **niczego nie bramkuje** —
decyzja organizatora z `docs/BACKLOG.md`), `InvitationCode.district` (`:733`), `School.voivodeship`
(`apps/schools/models.py:61`). Poza tym **ponad sześćdziesiąt miejsc**: `apps/web/forms.py`
(15 wystąpień `voivodeship_field`), serializery (`apps/accounts/serializers.py`), filtry panelu,
eksporty (`apps/accounts/data_export.py:95`), komunikaty organizatora
(`apps/accounts/messaging.py:84,141,144`), wyszukiwarka szkół (`apps/schools/api.py:39,102,171,222,268`),
snapshot wyników (tylko w trybie `CODE`), `normalize_voivodeship` (`:146`) oraz reguła konfliktu
interesów.

Reguła konfliktu jest jedna i krótka (`apps/grading/services.py:170–185`): etap musi być `DISTRICT`,
brak okręgu u członka komitetu **nie** wyklucza go z niczego, a konfliktem jest równość
znormalizowanych napisów. Wołają ją `apps/grading/services.py:473,481,494,644,715,793`.

#### 1.4.2. Model: `Region` jako drzewo per konkurs

```python
class RegionLevel(models.TextChoices):
    """Lista zamknięta z rozmysłem: nie jest to hierarchia dowolnej głębokości, tylko trzy poziomy,
    które organizatorzy naprawdę rozróżniają."""
    COUNTRY = "COUNTRY", "kraj"
    REGION = "REGION", "region"       # województwo, stan, land, okręg
    COUNTY = "COUNTY", "podregion"    # powiat, dystrykt


class Region(models.Model):
    """Jednostka podziału terytorialnego konkursu.

    Podział jest **danymi konkursu**, a nie instalacji: olimpiada ogólnopolska dzieli się na 16
    województw, olimpiada uczelniana na 5 okręgów akademickich, a konkurs międzynarodowy na kraje.
    """
    competition = FK("tenancy.Competition", PROTECT, related_name="regions")
    #: Kod jest **ASCII-owym slugiem** — przepisane wprost z ``Voivodeship``: trafia do adresów,
    #: filtrów administracji, kluczy grupowania wyników i wpisów audytu.
    code = SlugField("kod", max_length=40)
    name = CharField("nazwa", max_length=120)
    level = CharField("poziom", max_length=16, choices=RegionLevel.choices, default=RegionLevel.REGION)
    parent = FK("self", PROTECT, null=True, blank=True, related_name="children")
    position = PositiveSmallIntegerField("kolejność", default=0)
    is_active = BooleanField("aktywny", default=True)
    #: Region „poza Polską” ma tu ``False``: dwóch uczestników z zagranicy nie jest ze sobą
    #: w konflikcie z tytułu miejsca zamieszkania.
    counts_for_conflict = BooleanField("liczy się do konfliktu", default=True)

    objects = competition_scoped_manager()

    class Meta:
        ordering = ("competition", "position", "name", "id")
        constraints = [UniqueConstraint(fields=["competition", "code"],
                                        name="accounts_region_unique_code")]
```

`Participant`, `CommitteeMember` i `InvitationCode` dostają **nullowalne**
`region = FK(Region, null=True, blank=True, on_delete=PROTECT)` **obok** istniejącego `district`.
Kolumna `district` **zostaje i zostaje wypełniona** — to jest decyzja, nie przeoczenie.

**Dlaczego obie kolumny.** Usunięcie `district` znaczyłoby dotknięcie ponad sześćdziesięciu miejsc
w jednym wydaniu, w tym `get_district_display()` w `compute_stage_results` i `_district_key`
w `TOP_N_PER_DISTRICT` (`apps/results/services.py:408`) — czyli przepisanie tabel wyników przy
okazji zmiany słownika. Dlatego: **przy wyłączonej fladze `custom_regions`** wszystko czyta
`district`, dokładnie jak dziś; **przy włączonej** `region` jest źródłem prawdy, a `district` jest
**wypełniane z `region.code`** przy każdym zapisie (jedno miejsce: `apps/accounts/services.py`), więc
pozostaje poprawną, denormalizowaną kopią dla wszystkich odczytów, których etap 2 nie dotyka.

To jest jedyna denormalizacja tego etapu i jedyne odstępstwo od reguły § 1.0(a). Jest świadome i ma
termin ważności: wykreślenie `district` jest pozycją backlogu na sezon po wdrożeniu, gdy flaga będzie
`True` wszędzie.

#### 1.4.3. Migracja: 16 województw jako regiony

```python
def forwards(apps, schema_editor):
    """Każdy konkurs dostaje drzewo regionów odwzorowujące ``Voivodeship``.

    Kody są **dokładnie** dzisiejszymi slugami, nazwy — dzisiejszymi etykietami z diakrytykami.
    Dzięki temu backfill ``Participant.region`` jest złączeniem po ``district``, a nie mapą
    przepisaną ręcznie.
    """
    from apps.accounts.models import Voivodeship          # stała, nie model (patrz § 1.1.2)

    Competition = apps.get_model("tenancy", "Competition")
    Region = apps.get_model("accounts", "Region")
    Participant = apps.get_model("accounts", "Participant")
    CommitteeMember = apps.get_model("accounts", "CommitteeMember")

    for competition in Competition.objects.order_by("pk"):
        country, _ = Region.objects.get_or_create(
            competition_id=competition.pk, code="pl",
            defaults={"name": "Polska", "level": "COUNTRY", "position": 0})
        by_code = {}
        for position, (code, label) in enumerate(Voivodeship.choices, start=1):
            region, _ = Region.objects.update_or_create(
                competition_id=competition.pk, code=code,
                defaults={"name": str(label), "level": "REGION",
                          "parent_id": country.pk, "position": position})
            by_code[code] = region.pk
        # Region dla uczestników spoza Polski. Powstaje zawsze, ale **nieaktywny**: konkurs, który
        # nie dopuszcza zagranicy, nie pokaże go w formularzu ani razu.
        Region.objects.update_or_create(
            competition_id=competition.pk, code="poza-polska",
            defaults={"name": "poza Polską", "level": "COUNTRY", "position": 99,
                      "is_active": False, "counts_for_conflict": False})
        for code, region_id in by_code.items():
            Participant.objects.filter(competition_id=competition.pk, district=code,
                                       region__isnull=True).update(region_id=region_id)
            CommitteeMember.objects.filter(competition_id=competition.pk, district=code,
                                           region__isnull=True).update(region_id=region_id)
```

Migracja jest idempotentna i odwracalna (skasowanie regionów + wyzerowanie `region_id`); `district`
zostaje nietknięte, więc cofnięcie nie traci ani jednej informacji.

#### 1.4.4. Konflikt interesów — reguła bez zmiany

```python
def has_district_conflict(member, stage, participant_district=None, *, participant_region=None) -> bool:
    """Ta sama reguła, wyrażona na regionach tam, gdzie regiony są.

    Trzy warunki zostają co do joty: dotyczy **wyłącznie** etapu wojewódzkiego, brak wartości
    u członka komitetu **nie** wyklucza go z niczego, a ``district_verified`` nie bierze udziału.
    """
    if stage.kind != StageKind.DISTRICT:
        return False
    competition = stage.edition.competition
    if not competition.has_feature("custom_regions"):
        return _has_district_conflict_by_code(member, participant_district)   # dzisiejsze ciało
    member_region = member.region
    if member_region is None or not member_region.counts_for_conflict:
        return False
    return participant_region is not None and participant_region.pk == member_region.pk
```

Warunek „etap wojewódzki” jest przy włączonym edytorze procesu sprawdzany po
`Stage.kind == DISTRICT`, a nie po pozycji w torze — rodzaj etapu mówi, **które to zawody**, a nie
jak przebiegają, i to jest właściwe kryterium dla reguły o mieszkaniu w tym samym okręgu. Konkurs,
który chce tej reguły gdzie indziej, dostaje nullowalne pole `Stage.conflict_by_region`: `None` =
zachowaj dzisiejszą regułę po `kind`, `True`/`False` = jawna decyzja organizatora (pozycja backlogu,
nie zadanie etapu 2).

---

### 1.5. Finanse i logistyka

Cały obszar jest **nowy od zera**: grep po `fee`, `payment`, `invoice`, `vat`, `price`, `opłat*`,
`faktur*`, `płatnoś*`, `kwota`, `PLN`, `netto`, `brutto`, `currency`, `DecimalField`, `MoneyField`
w `backend/apps`, `backend/config` i `backend/templates` daje **zero trafień domenowych**; nie ma
żadnego `DecimalField` w żadnym modelu poza `apps/quiz` (punkty). Tak samo zerowe są grepy po
`nocleg`, `zakwaterowan*`, `posił*`, `catering`, `przyjazd`, `on_site`, `accommodation`.

Konkurs #1 **nie włącza ani jednej z tych flag** i to jest cała gwarancja ciągłości w tym obszarze:
na istniejących tabelach przybywają dwie nullowalne kolumny (`StageEntry.fee`, `StageEntry.team`
z § 1.2.3), a żaden ekran nie pojawia się w nawigacji bez flagi.

#### 1.5.1. Wpisowe i płatności

```python
class FeeSchedule(models.Model):
    """Cennik wpisowego: kwota za udział w edycji, ewentualnie zależna od kategorii.

    Kwota jest ``DecimalField(max_digits=10, decimal_places=2)`` — nigdy ``Float``. Waluta jest
    kodem ISO 4217 w osobnej kolumnie, a nie założeniem: „wszystko jest w złotych” przestaje być
    prawdą po podpisaniu pierwszej umowy z organizatorem spoza Polski.
    """
    competition = FK("tenancy.Competition", PROTECT, related_name="fee_schedules")
    edition = FK("competitions.Edition", CASCADE, related_name="fee_schedules")
    category = FK("competitions.Category", CASCADE, null=True, blank=True, related_name="fee_schedules")
    name = CharField("nazwa", max_length=120)
    amount = DecimalField("kwota", max_digits=10, decimal_places=2)
    currency = CharField("waluta", max_length=3, default="PLN")
    #: Wyłącznie **zapis** decyzji organizatora — system nie wylicza podatku i nie jest programem
    #: księgowym (decyzja D15). ``None`` = zwolnione.
    vat_rate = PositiveSmallIntegerField("stawka VAT (%)", null=True, blank=True)
    due_days = PositiveSmallIntegerField("termin płatności (dni)", default=14)
    is_active = BooleanField("aktywny", default=True)

    objects = competition_scoped_manager()

    class Meta:
        constraints = [
            CheckConstraint(condition=Q(amount__gte=0), name="tenancy_feeschedule_amount_not_negative"),
            UniqueConstraint(fields=["edition", "category"],
                             name="tenancy_feeschedule_unique_per_category"),
        ]


class FeeStatus(models.TextChoices):
    DUE = "DUE", "do zapłaty"; PAID = "PAID", "zapłacone"; EXEMPT = "EXEMPT", "zwolniony"
    WAIVED = "WAIVED", "umorzone"; REFUNDED = "REFUNDED", "zwrócone"


class ParticipantFee(models.Model):
    """Należność jednego uczestnika w jednej edycji. **Rejestr**, nie operacja finansowa:
    model zapisuje, ile się należy i czy wpłynęło, a nie przeprowadza transakcji."""
    schedule = FK(FeeSchedule, PROTECT, related_name="fees")
    participant = FK("accounts.Participant", PROTECT, related_name="fees")
    amount = DecimalField(max_digits=10, decimal_places=2); currency = CharField(max_length=3, default="PLN")
    status = CharField(max_length=16, choices=FeeStatus.choices, default=FeeStatus.DUE)
    exemption_reason = CharField("powód zwolnienia", max_length=200, blank=True)
    due_on = DateField(null=True, blank=True); paid_at = DateTimeField(null=True, blank=True)
    external_reference = CharField("identyfikator wpłaty", max_length=120, blank=True)
    recorded_by = FK(User, SET_NULL, null=True, blank=True, related_name="fees_recorded")

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        constraints = [
            UniqueConstraint(fields=["schedule", "participant"], name="tenancy_participantfee_unique"),
            # Zapłacone musi mieć datę, zwolnione musi mieć powód: bez tego rejestr odpowiada
            # „zapłacone” bez odpowiedzi na pytanie „kiedy”, a to jest pytanie, które pada.
            CheckConstraint(condition=~Q(status="PAID") | Q(paid_at__isnull=False),
                            name="tenancy_participantfee_paid_has_date"),
            CheckConstraint(condition=~Q(status="EXEMPT") | ~Q(exemption_reason=""),
                            name="tenancy_participantfee_exempt_has_reason"),
        ]
```

`StageEntry` dostaje nullowalne `fee = FK(ParticipantFee, null=True, blank=True, on_delete=SET_NULL)`,
żeby ekran etapu pokazał „nieopłacone” bez złączenia przez edycję. **Nieopłacone wpisowe niczego nie
blokuje** — bramka „bez wpłaty nie oddasz pracy” jest wyborem organizatora, a nie systemu, i wchodzi
jako pole `FeeSchedule.blocks_submission` dopiero po decyzji **D16**.

**Webhook dostawcy — stub, i dokładnie stub.** W repozytorium **nie ma ani jednego** endpointu
przyjmującego webhooka: grep po `csrf_exempt`, `CsrfExemptMixin`, `@api_view` w `apps/` i `config/`
daje zero trafień; wszystkie webhooki są **wychodzące** (`apps/integrations/webhooks.py`). Stub
przychodzący jest więc pierwszym takim miejscem i dostaje komplet zabezpieczeń od razu
(`apps/integrations/inbound.py`, nowy moduł):

```python
class PaymentWebhookView(APIView):
    """Odbiór potwierdzenia płatności. **Bez integracji z jakimkolwiek dostawcą.**

    Weryfikacja podpisu jest **symetryczna** wobec tej, którą sami wysyłamy
    (``apps/integrations/webhooks.py:81`` ``signature_header``): HMAC-SHA256 z ``f"{t}."`` + surowe
    bajty ciała, tolerancja 300 s, nagłówek ``t=<unix>,v1=<hex>``.

    Cztery reguły, każda z powodem:
    1. **sekret per konkurs i per dostawca** (``PaymentEndpoint.secret``) — sekret instalacji
       znaczyłby, że jeden wyciek dotyczy wszystkich organizatorów;
    2. **podpis z surowych bajtów** ``request.body``, nigdy z JSON-a odtworzonego z obiektu
       (kolejność kluczy zmienia podpis — ta sama uwaga stoi w ``docs/API.md`` § 3.3);
    3. **idempotencja po ``external_reference``**: powtórzone doręczenie nie tworzy drugiej wpłaty
       i nie zmienia ``paid_at``;
    4. **2xx po zapisaniu wiersza**, i wiersz zapisujemy **zawsze**, także dla nieznanej należności
       (``PaymentEvent.matched=False``) — dostawca przestanie ponawiać, a organizator musi mieć co
       dopasować ręcznie.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payments"
```

Adres: `path("payments/<slug:provider>/", PaymentWebhookView.as_view())` w
`apps/integrations/urls.py`, czyli **pod istniejącym prefiksem `/api/v1/`** — `config/urls.py` nie
jest edytowany.

**Faktury i rachunki** powstają jako dokumenty z `DocumentTemplate` (§ 1.1.3), rodzaj `INVOICE`,
składane tą samą ścieżką ReportLab. System **nie jest programem księgowym**: nie numeruje faktur
zgodnie z ustawą, nie prowadzi rejestru VAT i nie wystawia korekt — decyzja **D15**, do zapisania
wprost w `docs/PODRECZNIK-ORGANIZATORA.md`.

#### 1.5.2. Logistyka etapu stacjonarnego

Dziś w systemie istnieje **jeden** ślad świata fizycznego: `Stage.location`
(`apps/competitions/models.py:444`, wolny tekst) i `EditionEvent.note` (`:1147`). Obecność jest
notowana wyłącznie dla warsztatów online (`cms.WorkshopAttendance`, `apps/cms/models.py:1436`),
**tekstowym kluczem** `workshop_key` (`:1463`), bo warsztat nie jest obiektem w bazie. Nie ma modelu
sali, adresu, przyjazdu ani posiłku.

Cztery modele, wszystkie za flagą `onsite_logistics`, w nowym module
`apps/competitions/logistics.py`:

```python
class Venue(models.Model):
    """Miejsce zawodów. Per konkurs, nie per etap: finał i warsztaty bywają w tym samym budynku,
    a organizator wpisuje adres raz."""
    competition = FK(Competition, PROTECT, related_name="venues")
    name = CharField(max_length=160); address = CharField(max_length=255, blank=True)
    city = CharField(max_length=120, blank=True); region = FK(Region, SET_NULL, null=True, blank=True)
    capacity = PositiveSmallIntegerField(null=True, blank=True); note = TextField(blank=True)


class LogisticsNeed(models.TextChoices):
    ACCOMMODATION = "ACCOMMODATION", "nocleg"; MEAL = "MEAL", "wyżywienie"
    DIET = "DIET", "dieta szczególna"; ACCESSIBILITY = "ACCESSIBILITY", "dostępność"
    TRANSPORT = "TRANSPORT", "dojazd"


class ArrivalForm(models.Model):
    """Deklaracja uczestnika przed etapem stacjonarnym. Potrzeby są **listą wybranych rodzajów**
    plus jednym polem tekstowym, a nie piętnastoma kolumnami: organizatorzy pytają o różne rzeczy
    i lista pytań jest konfiguracją, nie schematem.

    **Uwaga o danych szczególnych.** ``DIET`` i ``ACCESSIBILITY`` bywają danymi o zdrowiu (art. 9
    RODO). Dlatego: ``note`` ma limit 500 znaków i etykietę mówiącą wprost, czego **nie** wpisywać;
    dostęp ma wyłącznie koordynator (nigdy recenzent, nigdy opiekun); pole wchodzi do retencji razem
    z profilem; nie trafia ani do eksportu integracyjnego, ani do webhooka. Wpis w rejestrze
    czynności (``apps/accounts/processing_register.py``) jest **warunkiem przyjęcia** zadania.
    """
    entry = OneToOneField(StageEntry, CASCADE, related_name="arrival_form")
    venue = FK(Venue, PROTECT, null=True, blank=True, related_name="arrivals")
    arrives_on = DateField(null=True, blank=True); departs_on = DateField(null=True, blank=True)
    needs = JSONField(default=list, blank=True)        # lista wartości LogisticsNeed
    note = CharField(max_length=500, blank=True)
    submitted_at = DateTimeField(default=timezone.now)


class AttendanceRecord(models.Model):
    """Obecność na etapie stacjonarnym — wpis komisji, jeden na wpis do etapu.

    Osobny model od ``cms.WorkshopAttendance``: tamten wisi na tekstowym kluczu wiersza harmonogramu
    w CMS-ie, bo warsztat nie jest obiektem w bazie. Etap jest, więc tutaj jest klucz obcy — i nie
    ma powodu dziedziczyć ograniczenia, które tam wynikało z braku modelu.
    """
    entry = OneToOneField(StageEntry, CASCADE, related_name="attendance")
    present = BooleanField(default=False); checked_in_at = DateTimeField(null=True, blank=True)
    recorded_by = FK(User, SET_NULL, null=True, blank=True, related_name="attendance_recorded")
```

**Listy do druku** (obecności, noclegowa, żywieniowa) idą przez `DocumentTemplate` rodzaju
`ATTENDANCE_LIST` i tę samą ścieżkę ReportLab, którą składa się już protokół etapu
(`apps/integrations/exports.py:164` `render_stage_protocol`, `LongTable` z `repeatRows=1`). Nie
powstaje ani jeden nowy generator PDF.

---

### 1.6. Wielojęzyczność treści

#### 1.6.1. Stan faktyczny

| Fakt | Gdzie |
|---|---|
| `LANGUAGE_CODE = "pl"`, `LANGUAGES = [("pl",…),("en",…)]`, `USE_I18N = True` | `config/settings/base.py:410,417,412` |
| jeden katalog tłumaczeń, 783 wpisy; CI sprawdza `msgfmt --check` | `backend/locale/en/LC_MESSAGES/django.po`; `.github/workflows/ci.yml`, zadanie `translations` |
| **`WAGTAIL_I18N_ENABLED` nie istnieje w repozytorium**; `WAGTAIL_CONTENT_LANGUAGES` też nie | grep |
| `wagtail.contrib.simple_translation` **nie jest** w `INSTALLED_APPS` | `config/settings/base.py:71–81` |
| **`i18n_patterns` nie jest używane nigdzie** | `config/urls.py` — zamontowane jest wyłącznie `django.conf.urls.i18n` (`:52`) |
| decyzja i jej uzasadnienie zapisane w kodzie | `config/settings/base.py:421–425` |
| język: konto → sesja/ciasteczko → `Accept-Language` → `LANGUAGE_CODE` | `apps/accounts/preferences.py:97–116` `resolve` |
| `LocaleMiddleware` → `CompetitionMiddleware` → `PreferencesMiddleware` | `config/settings/base.py:155,165,169` |
| listy składane poza żądaniem idą w języku odbiorcy | `preferences.language_for(user)` (`:147`) |

Wagtail ma `Locale` i `Page.locale` niezależnie od naszych ustawień (migracja
`wagtailcore.0094_alter_page_locale` jest w zależnościach `cms.0003`), więc instalacja ma dziś
dokładnie jeden `Locale` (`pl`) i wszystkie strony w nim.

#### 1.6.2. `i18n_patterns` — dowód, że nie wolno go dołożyć

1. **`i18n_patterns` dokłada prefiks także domyślnemu językowi**, chyba że przekaże się
   `prefix_default_language=False`. Nawet wtedy prefiks pojawia się dla **każdego innego** języka,
   czyli `/en/dokumenty/regulamin/` zaczyna istnieć obok `/dokumenty/regulamin/`.
2. **`config/urls.py` jest kontraktem** (docstring `:1–7`, T-09): Wagtail jest catch-allem w korzeniu
   i wszystko, co ma własną obsługę, musi być dopasowane wcześniej. Opakowanie części wzorców
   w `i18n_patterns` zmieniłoby kolejność dopasowania, a `path("<str:token>.html", …)` (`:48`)
   i `path("statystyki/", …)` (`:56`) stoją tam w konkretnych miejscach z konkretnych powodów.
3. **Adresy trafiają do listów, regulaminu i pism** (`/me/`, `/results/12/`, `/zgoda/<token>/`).
   Prefiks zrobiłby z każdego dwa adresy, z których jeden zawsze byłby wklejony nie tam. To jest
   dosłownie argument z `config/settings/base.py:421–425` i etap 2 go nie zmienia.

**Wniosek: `i18n_patterns` nie wchodzi w żadnej postaci.** Wielojęzyczność realizuje **drzewo stron
per `Locale`**:

```python
WAGTAIL_I18N_ENABLED = env.bool("WAGTAIL_I18N_ENABLED", default=False)
WAGTAIL_CONTENT_LANGUAGES = LANGUAGES        # pl + en, ta sama lista, co interfejs
```

Zmienna środowiskowa **domyślnie `False`**, więc produkcja Konkursu #1 nie zmienia zachowania nawet
po wdrożeniu kodu; włączenie jest decyzją operatora wpisaną w `.env`, a nie skutkiem `git pull`.

Przy `WAGTAIL_I18N_ENABLED = True` strona w drugim `Locale` jest **osobnym węzłem drzewa** z własnym
slugiem. Jeden `Site` ma jeden `root_page`, więc drugie drzewo językowe wymaga **drugiego `Site`**
(np. `en.olimpiadafizyczna.pl`). **Rekomendacja: drugi `Site` na subdomenie** (decyzja **D17**), bo
nie dotyka `config/urls.py` ani razu i daje rozdział `SiteSettings`, menu i przekierowań za darmo.
Jedyna przeszkoda: `Competition.site` jest `OneToOne`, więc drugi `Site` znaczyłby drugi konkurs.
Rozwiązanie: `Competition.site` **zostaje `OneToOne`**, a dochodzi `CompetitionSiteAlias`
**(nowy model: `competition` + `site` + `locale`)**; `resolve_competition`
(`apps/tenancy/resolution.py`) po nieudanym dopasowaniu `Site → Competition` sprawdza aliasy.
Konkurs #1 nie ma ani jednego aliasu, więc ta gałąź nie wykonuje się dla niego ani razu — dokładnie
tak, jak gałąź prefiksu ścieżki z etapu 1 § 2.3.

#### 1.6.3. Język listów per konkurs

`Competition.default_language` istnieje od etapu 1 (`apps/tenancy/models.py:194`) i **nie jest dziś
czytane nigdzie**. `language_for(user, competition=None)` (`apps/accounts/preferences.py:147`) dostaje
drugi argument: język odbiorcy, a gdy go nie ma — język domyślny **konkursu**, nie serwera. Dziś
odwrotem jest `settings.LANGUAGE_CODE`, czyli polszczyzna; dla konkursu prowadzonego po angielsku
znaczyłoby to list po polsku z serwisu, który uczestnik widział wyłącznie po angielsku.

`queue_mail` (`apps/accounts/activation.py:269`) **nie przyjmuje dziś konkursu** — sygnatura to
`queue_mail(subject, message, recipient)`, a `send_mail_task` (`apps/core/tasks.py:52`) wpisuje
nadawcę z `settings.DEFAULT_FROM_EMAIL` na sztywno (`:74`). Wraz z § 1.1.1 zmienia się to na
`queue_mail(subject, message, recipient, *, competition=None)` i
`send_mail_task(subject, message, recipient_list, from_email=None)` — argument jest **słowem
kluczowym z wartością domyślną**, więc jedenaście istniejących wywołań działa bez zmiany. Wołających
`queue_mail` jest jedenaście (`activation.py:297,304,308`, `bulk_registration.py:780`,
`guardian.py:254,306`, `services.py:981`, `video.py:271`, `submissions/notifications.py:90,234`,
`support/services.py:237,267`) plus cztery wywołujące `send_mail_task.delay` wprost
(`accounts/messaging.py:183`, `competitions/interviews.py:307`, `grading/reports.py:233`,
`grading/tasks.py:58`).

#### 1.6.4. Zakres tłumaczenia — bez zmiany

Zakres pozostaje wąski (decyzja z `README.md` § 5.9): panel uczestnika, logowanie i rejestracja,
ekrany konta, pasek konta i stopka, statystyki, listy do uczestników. **Ekrany koordynatora zostają
po polsku** — etap 2 nie dokłada ani jednego `gettext` w `apps/web/views/coordinator_*.py`.
`Problem.title_en` i `Problem.statement_pdf_en` (`apps/competitions/models.py:746,759`) zostają bez
zmian i są wzorcem dla ewentualnych dalszych pól: treść zadania to nie interfejs.

---

### 1.7. Instalator i obraz „gotowy do uruchomienia”

#### 1.7.1. Kreator pierwszego uruchomienia `/setup/`

**Stan faktyczny:** kreatora nie ma. Pierwsze uruchomienie to `scripts/deploy.sh` krok 3/8
(generowanie `.env` + znacznik `.first-deploy`, `:69–122`), krok 6/8 (seedy za bramką
`RUN_CONTENT_SEEDS`, `:212–247`, w tym `manage.py bootstrap_coordinator`, gdy podano
`COORDINATOR_EMAIL` **i** `COORDINATOR_PASSWORD`, `:242–245`) oraz opcjonalny krok 6a/8
(`create_competition`, `:249–280`). Wszystko przez SSH i zmienne środowiskowe.

**Projekt.** `/setup/` jest **jednym** nowym wzorcem w `config/urls.py` — i to jedyna zmiana w tym
pliku w całym etapie 2, wstawiona **przed** `path("cms/", …)` i przed catch-allem Wagtaila:

```python
path("setup/", include("apps.tenancy.setup_urls")),
```

**Bramka jest twarda i ma jedno miejsce** (`apps/tenancy/setup.py`):

```python
def setup_available() -> bool:
    """Dwa warunki naraz i oba są konieczne: w bazie **nie ma ani jednego** ``Competition``
    **i nie ma ani jednego** ``User`` z ``is_superuser=True``.

    Sam brak konkursu nie wystarcza: instalacja po awaryjnym przywróceniu kopii może mieć konta
    i nie mieć konkursu, a kreator pozwoliłby wtedy założyć konto operatora komuś, kto akurat
    trafił pod adres. Odwrotnie też: superużytkownik bez konkursu to stan przejściowy między
    ``createsuperuser`` a ``create_competition``.

    Odpowiedź **nie jest buforowana**: to dwa zapytania ``EXISTS`` wykonywane wyłącznie na ścieżce
    ``/setup/``, a wynik fałszywie dodatni z cache'u znaczyłby otwarty kreator na produkcji.
    """
```

Gdy `setup_available()` jest `False`, **każdy** widok pod `/setup/` odpowiada **404** (nie 403 — to
reguła z etapu 1 § 3.6: „nie ma tego tutaj”). Punkt 22 listy § 0.5 sprawdza to na produkcji.

| Krok | Co zbiera | Co robi |
|---|---|---|
| 1 — operator | e-mail, hasło (dwukrotnie), imię i nazwisko | tworzy `User(is_superuser=True, is_staff=True)` przez `bootstrap_coordinator` — **tę samą** komendę, którą woła dziś `deploy.sh:242`; nie powstaje druga droga zakładania operatora |
| 2 — konkurs | nazwa, skrót, slug, domena, organizator, e-mail kontaktowy, szablon (`kwantowa`/`przedmiotowa`/`pusty`) | woła `create_competition_from_template(...)` — funkcję serwisową wydzieloną z `handle()` dzisiejszej komendy, którą od tej pory wołają i komenda, i kreator |
| 3 — podsumowanie | — | wypisuje trzy linijki do `.env` (`EXTRA_DOMAINS`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`) i listę dokumentów wymaganych przez szablon; loguje operatora i przekierowuje na `/coordinator/` |

**Bezpieczeństwo kreatora**, wypisane, bo to publiczny adres tworzący superużytkownika: throttle
`ScopedRateThrottle` scope `setup` (10/h/IP, nowy wpis w `DEFAULT_THROTTLE_RATES`,
`config/settings/base.py:682`); CAPTCHA z `apps/web/captcha.py` na kroku 1; audyt `setup.completed`
z adresem IP; **`setup_available()` sprawdzane w każdym widoku osobno, także w `POST`** — wyścig
„dwie przeglądarki otwarte na kroku 1” kończy się drugim 404, a nie drugim superużytkownikiem; krok 1
w `transaction.atomic` pod blokadą doradczą (`pg_advisory_xact_lock`, wzorzec z
`apps/grading/services.py`).

**Czego kreator nie robi:** nie uruchamia seedów treści, nie zmienia `.env`, nie konfiguruje DNS-u,
poczty ani Jitsi, nie dotyka `docker compose`.

#### 1.7.2. Obrazy budowane w CI i publikowane w GHCR

**Stan faktyczny.** `docker-compose.yml` ma `x-app-base` z `build:` (`:21`) **i**
`image: olimpiada/web:${APP_VERSION:-dev}` (`:25`); `scripts/deploy.sh` krok 4/8 buduje obraz **na
serwerze produkcyjnym** (`docker compose build --pull web`, `:160`); CI ma zadanie `image`
z `docker/build-push-action@v6`, ale z **`push: false`** (`.github/workflows/ci.yml:187`).

```yaml
# docker-compose.yml — jedna linia
x-app-base: &app-base
  build: …                                   # zostaje
  image: ${WEB_IMAGE:-olimpiada/web:${APP_VERSION:-dev}}
```

```bash
# scripts/deploy.sh, krok 4/8 — rozgałęzienie, nie podmiana
if [ -n "${WEB_IMAGE:-}" ]; then
    log "4/8 Obraz z rejestru: $WEB_IMAGE"; docker compose pull web
else
    log "4/8 Konfiguracja proxy (EXTRA_DOMAINS), build obrazu i start samej bazy"   # bez zmian
    docker compose build --pull web
fi
```

Bez `WEB_IMAGE` w `.env` wdrożenie Konkursu #1 wykonuje **dokładnie te same polecenia, co dziś** —
warunek z § 0.2, punkt 11.

**CI.** Zadanie `image` dostaje warunek `if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')`,
`permissions: packages: write`, logowanie do `ghcr.io` przez `GITHUB_TOKEN` i `push: true` z tagami
`ghcr.io/<org>/olimpiada-web:<tag>` oraz `:sha-<short>`. Pull request **nadal** buduje bez
publikowania — workflow z forka nie dostaje sekretów (to samo uzasadnienie, które stoi już
w nagłówku `ci.yml`). Nie podpisujemy obrazów (cosign — osobna pozycja), nie publikujemy `worker`
ani `beat` (to **ten sam** obraz z innym poleceniem), nie zmieniamy `Dockerfile`.

#### 1.7.3. Profil compose dla świeżego operatora

`docker-compose.yml` ma dziś dwa profile: `monitoring` (`:285`) i `dev` (`:299`; plik `dev` dokłada
`e2e`). Realizacja jest odwrotna do intuicji, bo **profile w compose dodają usługi, a nie
odejmują**: `clamav` i `mail` dostają `profiles: [full]`, a `deploy.sh` uruchamia `--profile full`
w kroku 4b/8 (`:199`). Świeży operator, który uruchamia `docker compose up -d` bez profilu, dostaje
`proxy`, `web`, `worker`, `beat`, `db`, `redis`, `minio`, `minio-init` — i działający `/setup/`.

To jest zmiana w pliku, którego dotyczy punkt 11 z § 0.2, więc warunek jest ostry: istniejący
`scripts/tests/render_caddyfile_test.sh` dostaje odpowiednik dla compose'a —
`scripts/tests/compose_profiles_test.sh` porównujący listę usług `docker compose --profile full config`
z dzisiejszym `docker compose config` **na równość**.

**Health i gotowość już są** i etap 2 ich nie dubluje: `/healthz/` (`apps/core/views.py:6–23`,
200/503, używany przez healthchecki compose'a i Caddy'ego) oraz `/status.json`
(`apps/core/status.py:244–283`, kontrakt jedenastu pól wyliczony jawnie, cache 30 s). Kreator dokłada
do `/status.json` **jedną** wartość `"setup_pending": bool` — dodatek do słownika, nie zmiana
istniejących kluczy; test kontraktu (`test_status_json_keeps_its_contract`,
`apps/tenancy/tests/test_golden_single_competition.py:91`) dostaje ją w oczekiwanym zestawie w tym
samym commicie.

---

## 2. Interfejs

### 2.1. Zasada: nowy ekran ma flagę, stary ekran nie ma nowych pól

Trzy reguły obowiązujące każdy ekran z tego etapu. Wynikają wprost z § 0 i z tego, czego etap 1
zakazał zmieniać w zadaniu T5 („adresów, układu paneli, rozwijanych sekcji nawigacji, liczników
uwagi, treści komunikatów błędów”).

1. **Nowa pozycja w menu koordynatora pojawia się wyłącznie za flagą.** Wzorzec istnieje i jest
   jedyny dopuszczalny — `apps/web/coordinator_nav.py:313`:
   ```python
   if competition is not None and competition.has_feature("competition_settings_page"):
       settings_items += (Item("Ustawienia konkursu", ("web:coordinator-competition",), …),)
   ```
   Konkurs z domyślnymi flagami ma mieć menu **bajt w bajt** takie, jak dziś. Pozycja prowadząca
   do 404 byłaby zresztą gorsza niż jej brak.
2. **Istniejący formularz nie zyskuje pola bez flagi.** `/register/` z konkursem bez
   `RegistrationProfile` renderuje ten sam HTML, co dziś — sprawdza to
   `test_registration_form_html_unchanged` (§ 5.3).
3. **`has_feature` nie jest wołane w szablonie.** Dziś nie jest wołane w żadnym
   (potwierdzone grepem) i tak ma zostać: flagę czyta widok albo `coordinator_nav`, a szablon
   dostaje gotową listę. Powód: flaga w szablonie jest flagą, której nie widać w teście widoku.

Konwencje ekranów koordynatora, przepisane z istniejącego kodu i obowiązujące każdy nowy widok
(wzorzec: `apps/web/views/coordinator_integrations.py`, `coordinator_events.py`,
`apps/web/mixins.py`):

- `class X(CoordinatorRequiredMixin, View)` — zwykłe `View` z `django.views.generic`, **nigdy**
  `ListView`/`FormView`/`UpdateView`,
- obiekt przez `get_object_or_404(Model.objects.for_competition(competition), pk=pk)` — zakres
  z querysetu, nie z widoku,
- jedno `_page_context()` używane przez `GET` i przez nieudany `POST`,
- `TemplateResponse`, nigdy `render()`,
- nieudany formularz → pełne przerenderowanie z **HTTP 400**; `DomainError` →
  `messages.error(request, str(exc.detail))` + przerenderowanie **ze statusem `exc.status_code`**,
- każda zmiana to `POST` z `{% csrf_token %}`, sukces kończy się `redirect`,
- reguły w serwisie, widok tylko orkiestruje; audyt pisze **serwis**,
- HTMX w panelu koordynatora **nie występuje** (zero wystąpień `hx-` w
  `templates/web/coordinator/*.html`) i etap 2 go tam nie wprowadza.

### 2.2. Panel koordynatora — mapa zmian

Tabela jest kompletna: każdy ekran, który powstaje albo się zmienia, ze wskazaniem pliku widoku,
szablonu, pozycji w menu i flagi. Sekcje menu są dzisiejsze (`apps/web/coordinator_nav.py:403–471`):
**Pulpit, Etapy, Ocenianie, Uczestnicy i konta, Komitet, Komunikacja, Raporty, Ustawienia.**
Etap 2 **nie dokłada ani jednej nowej sekcji** — wszystkie nowe pozycje wchodzą do istniejących.

| Ekran | Adres | Widok **(nowy/zmiana)** | Szablon | Sekcja menu | Flaga |
|---|---|---|---|---|---|
| Zgody konkursu | `/coordinator/consents/` | `coordinator_consents.py` **(nowy)** | `coordinator/consents.html` **(nowy)** | Ustawienia | `per_competition_consents` |
| Szablony dokumentów | `/coordinator/documents/` | `coordinator_documents.py` **(nowy)** | `coordinator/documents.html` **(nowy)** | Raporty (obok „Dyplomy: szablony”) | `document_templates` |
| Marka i poczta | `/coordinator/competition/` | `coordinator_competition.py` **(zmiana)** — dochodzą pola odmiany nazwy, podpisu listu, nazwy pliku kalendarza | `coordinator/competition.html` **(zmiana)** | Ustawienia | `competition_settings_page` (istniejąca) |
| Redakcja konkursu (`/cms/`) | — | `apps/cms/permissions.py` **(nowy)**; brak własnego ekranu | — | — | `scoped_cms_permissions` |
| Przebieg edycji (tor etapów) | `/coordinator/pipeline/` | `coordinator_pipeline.py` **(nowy)** | `coordinator/pipeline.html` **(nowy)** | Etapy (pozycja nad listą etapów) | `process_editor` |
| Reguły przejścia kroku | `/coordinator/pipeline/<step>/rules/` | `coordinator_pipeline.py` **(nowy)** | `coordinator/pipeline_rules.html` **(nowy)** | dziecko pozycji etapu | `process_editor` |
| Komponenty etapu | `/coordinator/stages/<id>/components/` | `coordinator_stages.py` **(zmiana)** | `coordinator/stage_components.html` **(nowy)** | dziecko etapu | `process_editor` |
| Punkty z rozmowy | `/coordinator/stages/<id>/interviews/` | `coordinator_stages.py` **(zmiana)** — kolumna „punkty” w istniejącej tabeli terminów | `coordinator/interviews.html` **(zmiana)** | istniejąca pozycja „Rozmowy” | `process_editor` |
| Kategorie | `/coordinator/categories/` | `coordinator_categories.py` **(nowy)** | `coordinator/categories.html` **(nowy)** | Ustawienia | `categories` |
| Drużyny | `/coordinator/teams/` | `coordinator_teams.py` **(nowy)** | `coordinator/teams.html` **(nowy)** | Uczestnicy i konta | `team_entries` |
| Role recenzenckie etapu | `/coordinator/stages/<id>/reviewer-roles/` | `coordinator_quality.py` **(zmiana)** | `coordinator/reviewer_roles.html` **(nowy)** | Ocenianie | `reviewer_roles` |
| Rozstrzyganie remisów | `/coordinator/stages/<id>/scale/` | `coordinator_stages.py` **(zmiana)** — druga sekcja na istniejącym ekranie skali | `coordinator/stage_scale.html` **(zmiana)** | istniejąca „Skala punktacji” | `weighted_scoring` |
| Wagi zadań | `/coordinator/problems/<id>/edit/` | `coordinator_problem_detail.py` **(zmiana)** — dwa pola w istniejącym formularzu | `coordinator/problem_form.html` **(zmiana)** | istniejąca | `weighted_scoring` |
| Regiony | `/coordinator/regions/` | `coordinator_regions.py` **(nowy)** | `coordinator/regions.html` **(nowy)** | Ustawienia | `custom_regions` |
| Profil rejestracji | `/coordinator/registration/` | `coordinator.py` **(zmiana)** — druga sekcja na istniejącym ekranie „Rejestracja uczestników” | `coordinator/registration_form.html` **(zmiana)** | istniejąca | `institution_types` |
| Słownik placówek | `/coordinator/institutions/` | `coordinator_institutions.py` **(nowy)** | `coordinator/institutions.html`, `institutions_import.html` **(nowe)** | Ustawienia | `custom_school_directory` |
| Wpisowe: cennik | `/coordinator/fees/` | `coordinator_fees.py` **(nowy)** | `coordinator/fees.html` **(nowy)** | Raporty | `fees` |
| Wpisowe: należności | `/coordinator/fees/participants/` | `coordinator_fees.py` **(nowy)** | `coordinator/fees_participants.html` **(nowy)** | dziecko „Wpisowe” | `fees` |
| Miejsca zawodów | `/coordinator/venues/` | `coordinator_logistics.py` **(nowy)** | `coordinator/venues.html` **(nowy)** | Ustawienia | `onsite_logistics` |
| Przyjazdy i potrzeby | `/coordinator/stages/<id>/logistics/` | `coordinator_logistics.py` **(nowy)** | `coordinator/logistics.html` **(nowy)** | dziecko etapu | `onsite_logistics` |
| Lista obecności (druk) | `/coordinator/stages/<id>/attendance/` | `coordinator_logistics.py` **(nowy)** | `coordinator/attendance.html` **(nowy)** | dziecko etapu | `onsite_logistics` |
| Tłumaczenia treści | — | brak własnego ekranu; `wagtail.contrib.simple_translation` dokłada przycisk w `/cms/` | — | — | `content_translations` |

**Liczniki uwagi** (`attention_counters`, `apps/web/coordinator_nav.py:101–157`) **nie zmieniają
się**: pięć kluczy (`moderation`, `activations`, `committee`, `issues`, `tickets`), pięć zapytań
agregujących, 60 s cache'u per konkurs. Nowe ekrany **nie dostają odznak** — każda nowa odznaka to
szóste zapytanie na każdą odsłonę pulpitu i przesunięcie budżetu z § 5.6.

### 2.3. Panel uczestnika

| Ekran | Zmiana | Flaga |
|---|---|---|
| `/register/` | pola rodzaju placówki, kraju i nazwy instytucji — **tylko** gdy `RegistrationProfile` na to wskazuje | `institution_types` |
| `/register/` | wybór kategorii — **tylko** gdy `participant_picks_category` | `categories` |
| `/register/` | zgody z `ConsentDefinition` zamiast ze stałej — **treść identyczna** dla Konkursu #1 | `per_competition_consents` |
| `/me/` | kafel „Wpisowe” ze stanem należności i terminem | `fees` |
| `/me/` | kafel „Formularz przyjazdu” przed etapem stacjonarnym | `onsite_logistics` |
| `/me/` | „Moja drużyna” na karcie etapu drużynowego | `team_entries` |
| `/me/results/` | kolumna „kategoria” i miejsce w rankingu kategorii | `categories` |
| `/wyniki/`, `/results/<id>/` | osobne tabele per kategoria | `categories` |
| `/kalendarz.ics` | `PRODID` i `X-WR-CALNAME` z konkursu; **`UID` bez zmian** | `competition_branding_in_mail` |

Bez flag panel uczestnika renderuje się identycznie — sprawdzają to złote testy
(`apps/tenancy/tests/test_golden_single_competition.py`, 13 przebiegów) i budżet zapytań `/me/`
(§ 5.6).

### 2.4. Opiekun, recenzent, komisja

| Rola | Zmiana | Flaga |
|---|---|---|
| opiekun (`/supervisor/`) | kolumna „wpisowe” na liście uczniów | `fees` |
| opiekun (`/supervisor/import/`) | kolumna `kategoria` w pliku CSV — opcjonalna | `categories` |
| recenzent (`/review/`) | etykieta roli („pierwszy recenzent”, „arbiter”) zamiast numeru rundy | `reviewer_roles` |
| recenzent (ekran oceny) | skala z wartościami ujemnymi — obsługiwana tym samym widżetem | `weighted_scoring` |
| komisja odwoławcza | **bez zmian** | — |

Reguła 403/404 z etapu 1 § 3.6 obowiązuje bez wyjątku także nowe ekrany: niezalogowany → 302 na
`/login/`, zła rola → **403**, obiekt cudzego konkursu → **404** z zawężonego querysetu.

### 2.5. Strony publiczne, CMS i kreator

- **`/` i drzewo stron**: bez zmian. Przy `content_translations` Wagtail dokłada w `/cms/` przycisk
  „Translate” (`wagtail.contrib.simple_translation`); dla instalacji z jednym `Locale` przycisk
  nie ma dokąd prowadzić i się nie pojawia.
- **`/dokumenty/`**: bez zmian.
- **`/wyniki/` i `/results/<id>/`**: struktura tabeli bez zmian; kategorie dokładają nagłówki
  sekcji, ale **tylko** gdy snapshot je niesie. Snapshot Konkursu #1 ich nie niesie.
- **`/statystyki/`**: bez zmian (`apps/results/statistics.py` czyta snapshot).
- **`/status.json`**: jeden nowy klucz `"setup_pending"` (§ 1.7.3); pozostałe jedenaście kluczy
  bez zmian, w tej samej kolejności.
- **`/setup/`**: trzy ekrany, `apps/tenancy/templates/tenancy/setup_*.html` **(nowe)**, własny,
  minimalny szablon bazowy — kreator renderuje się na instalacji bez `SiteSettings` i bez drzewa
  stron, więc **nie może** dziedziczyć po `base.html` (ten czyta `cms_menu` i `site_chrome`).

---

## 3. Wydania

Lettering ciągnie się od etapu 1 (A–D = `v0.20.0`–`v0.23.0`). Etap 2 to wydania **E–K**.

**Reguła wspólna:** każde wydanie jest samodzielne — po nim aplikacja działa, a Konkurs #1
zachowuje się identycznie. Bramką przyjęcia każdego z nich są **trzy** rzeczy naraz:

1. cała suita (`pytest -q`, dziś 3235 testów, 0 xfail) na zielono, w tym złote testy
   (`apps/tenancy/tests/test_golden_single_competition.py`) i testy niezmienności
   (`apps/tenancy/tests/test_invariants.py`),
2. budżety zapytań z `QUERY_BUDGET` (`test_invariants.py`, `/`: 32, `/me/`: 46,
   `/coordinator/`: 48) **niezmienione** — nie podniesione,
3. ręczna lista kontrolna na produkcji: punkty 1–13 z etapu 1 § 0.3 **oraz** punkty 14–22 z § 0.5
   tego dokumentu.

| Wydanie | Wersja | Zawartość | Migracje | Ryzyko |
|---|---|---|---|---|
| **E** | `v0.24.0` | **Marka i dokumenty.** Domknięcie `organizer_name()`; `apps/tenancy/branding.py`; `queue_mail(competition=…)` i `send_mail_task(from_email=…)`; `ConsentDefinition` + migracja z `DEFAULT_CONSENTS`; `DocumentTemplate` + `Certificate.template_version` + migracja; kalendarz, CAPTCHA, domena anonimowa, `500.html`; ekrany „Zgody” i „Szablony dokumentów” | `accounts.00xx_consent_definition`, `accounts.00xx_consent_definitions_for_competition_one`, `tenancy.00xx_document_template`, `tenancy.00xx_document_templates_for_competition_one`, `results.00xx_certificate_template_version`, `tenancy.00xx_calendar_filename` | **średnie** — dotyka treści zgód i listów, czyli tego, co czyta uczestnik. Cała mitygacja jest w testach § 5.3 i § 5.4 |
| **F** | `v0.25.0` | **Dostęp do `/cms/` per konkurs.** `apps/cms/permissions.py`, kolekcja konkursu, grupa `cms:<slug>`, komenda `scope_cms_access`, flaga `scoped_cms_permissions` | brak migracji schematu; jedna migracja danych zakładająca kolekcje istniejącym konkursom | **niskie** dla Konkursu #1 (uprawnienia wyłącznie dokładane), **średnie** dla instalacji z drugim konkursem |
| **G** | `v0.26.0` | **Podział terytorialny i rejestracja, część 1.** `Region` + migracja 16 województw + `Participant.region`/`CommitteeMember.region` (nullowalne, wypełnione); `has_district_conflict` na regionach za flagą; `InstitutionType` na `School` + migracja; `RegistrationProfile`; ekran „Regiony” | `accounts.00xx_region`, `accounts.00xx_regions_from_voivodeships`, `schools.00xx_institution_type`, `accounts.00xx_registration_profile`, `accounts.00xx_participant_country_institution` | **średnie** — `district` zostaje i jest wypełniane, więc odczyty nietknięte; ryzyko siedzi w regule konfliktu interesów (test § 5.7) |
| **H** | `v0.27.0` | **Rejestracja, część 2.** `CustomInstitution` + import CSV + podgląd; wyszukiwarka pytająca obie tabele; formularz i picker sterowane profilem; `bulk_registration` z regionem i kategorią; ekran „Słownik placówek” | `schools.00xx_custom_institution`, `accounts.00xx_participant_custom_institution_ref` | **niskie** — cała gałąź za flagą, a bez flagi zapytanie do nowej tabeli nie wykonuje się |
| **I** | `v0.28.0` | **Edytor procesu, część 1 (struktura).** `PipelineStep` + `TransitionRule` + migracja z `STAGE_ORDER` i `QualificationRule`; `next_stage_of` dwudrożne; `Category` + `StageEntry.category`; `_rank_rows(group_by=…)`; ekrany „Przebieg edycji”, „Reguły przejścia”, „Kategorie” | `competitions.00xx_pipeline_step_transition_rule`, `competitions.00xx_pipeline_from_stages`, `competitions.00xx_category`, `competitions.00xx_stage_kind_constraint` (ta sama nazwa więzi) | **wysokie** — dotyka kwalifikacji i tabel wyników. Bramka: test § 5.2 na **każdej** ogłoszonej publikacji |
| **J** | `v0.29.0` | **Edytor procesu, część 2 (punktacja i formy).** `StageComponent`, `InterviewScore`, `Team`/`TeamMember` + `StageEntry.team`, `entry_owner()`; wagi zadań (`Fraction`), `ScoringScale.offset`, `TieBreak`; `ReviewerRole` + `Review.role`; ekrany komponentów, drużyn, ról i remisów | `competitions.00xx_stage_component`, `competitions.00xx_interview_score`, `competitions.00xx_team`, `competitions.00xx_problem_weight`, `competitions.00xx_scoring_offset`, `competitions.00xx_tie_break`, `grading.00xx_reviewer_role` | **wysokie** — `StageEntry.participant` staje się nullowalne, `compute_stage_results` zyskuje drugą drogę sumowania |
| **K** | `v0.30.0` | **Instalator, obrazy, wielojęzyczność, finanse, logistyka.** `/setup/` + `setup_pending` w `/status.json`; `WEB_IMAGE` w compose i `deploy.sh`, publikacja w GHCR, profil `full`; `WAGTAIL_I18N_ENABLED` (domyślnie `False`) + `simple_translation` + `CompetitionSiteAlias`; `FeeSchedule`/`ParticipantFee`/`PaymentEndpoint`/`PaymentEvent` + webhook stub; `Venue`/`ArrivalForm`/`AttendanceRecord` + listy do druku | `tenancy.00xx_competition_site_alias`, `tenancy.00xx_fees`, `competitions.00xx_logistics` | **niskie w aplikacji, średnie we wdrożeniu** — zmiana `docker-compose.yml` i `deploy.sh` dotyka kroków, których punkt 11 z § 0.2 broni |

**Dlaczego taka kolejność.** Trzy zależności są twarde i wyznaczają resztę:

- **F po E**, bo grupa redakcyjna konkursu jest potrzebna dopiero, gdy konkurs ma własne dokumenty
  do wpisania,
- **I przed J**, bo komponenty i wagi dokładają się do **kroku**, a krok powstaje w I. Odwrotna
  kolejność znaczyłaby komponenty wiszące na etapie, a potem ich przenoszenie,
- **H po G**, bo słownik własny organizatora ma kolumnę `region_code`, a regiony powstają w G.

**Dlaczego K jest jedno, a nie trzy.** Instalator, obrazy, wielojęzyczność, finanse i logistyka nie
mają ze sobą nic wspólnego — ale każde z nich jest **całkowicie za flagą albo całkowicie poza
ścieżką Konkursu #1**, więc ryzyko regresji jest w nich najniższe w całym etapie. Rozbicie na trzy
wydania kosztowałoby trzy przebiegi ręcznej listy kontrolnej i nie kupiłoby ani jednej dodatkowej
możliwości wycofania: wycofanie i tak jest per flaga, a nie per wydanie.

**Kopia przed każdym wydaniem** — `scripts/deploy.sh` krok 4a/8 (`:170`), bez zmian. Wydania I i J
dostają dodatkowo **eksport wszystkich snapshotów przed migracją**:

```bash
docker compose exec -T web python manage.py dumpdata results.ResultsPublication \
    --indent 2 > /opt/olimpiada-backups/publications-pre-<tag>.json
```

To jest materiał porównawczy do punktu 16 listy § 0.5 i do testu § 5.2 uruchamianego na kopii
produkcyjnej — nie zastępuje `pg_dump`, tylko daje plik, który da się porównać `diff`-em bez
odtwarzania bazy.

### 3.1. Stan wdrożenia

Wydania E–K zostały **zbudowane równolegle i scalone w jednym tagu `v0.24.0`** (2026-09-18),
na polecenie organizatora („zrównolegl agentów”). Każde z nich spełnia regułę wspólną z osobna —
cała nowa funkcjonalność stoi za flagami z § 0.6 (na produkcji `feature_flags = {}` dla Konkursu #1),
a migracje danych zapisują Konkursowi #1 dokładnie dzisiejsze wartości (zgody, szablony dokumentów,
regiony z 16 województw, przebieg etapów z `STAGE_ORDER` i `QualificationRule`). Bramki przyjęcia
z tej sekcji przeszły dla całości: pełna suita na zielono, budżety zapytań (32/46/48) niezmienione,
strony publiczne bajt w bajt, lista kontrolna z `docs/OPERACJE.md`. Numeracja `v0.25.0`–`v0.30.0`
z tabeli powyżej **nie została użyta**; kolejne tagi idą dalej od `v0.24.0`.

Odstępstwa od tabeli wydań, przyjęte w trakcie i opisane w kodzie: `Region.competition`,
`Region.parent`, `ConsentDefinition.competition` i `DocumentTemplate.competition` są `CASCADE`
(konfiguracja własna konkursu, dowody pozostają zamrożonymi napisami); gating profilu compose
`full` stoi w nakładce `docker-compose.operator.yml`, nie w pliku bazowym; kreator `/setup/` wymaga
jednorazowego tokenu (`SETUP_TOKEN` z `.env` albo z logu kontenera `web`); adres na `500.html`
pochodzi z `ERROR_PAGE_CONTACT_EMAIL` z domyślną dzisiejszą wartością; `InterviewScore` jest
kluczowany parą (zgłoszenie, komponent); `email_subject_prefix` konkursu **nie** jest doklejany do
tematów (dziś żaden list Konkursu #1 go nie niesie); dane szczególne w formularzu przyjazdu są za
osobnym przełącznikiem `LogisticsSettings.collect_special_needs` (domyślnie wyłączone).

---

## 4. Podział prac

### 4.1. Reguły podziału

Zadania są pomyślane do **równoległego** wykonania przez agentów na jednym drzewie roboczym.
Obowiązują reguły z etapu 1 § 6 plus trzy uzupełnienia, które wynikły z tego, że etap 2 dotyka
więcej plików wspólnych:

1. **Wyłączna własność plików.** Dwa zadania nie edytują tego samego pliku. Tam, gdzie to
   niemożliwe, zależność jest jawna i zadanie czeka.
2. **Trzy pliki są wspólne dla całego etapu i mają jednego właściciela każdy:**
   - `apps/tenancy/models.py` (katalog `FEATURE_DEFAULTS`) — **wyłącznie T8**, który dopisuje
     **wszystkie szesnaście flag naraz**, także te czytane dopiero w wydaniu K. Flaga w katalogu,
     której nikt jeszcze nie czyta, jest nieszkodliwa (`has_feature` podnosi `KeyError` tylko dla
     nazwy **spoza** katalogu) — i tak właśnie zrobił etap 1 z `per_competition_consents`;
   - `apps/web/urls.py` i `apps/web/coordinator_nav.py` — **wyłącznie zadanie „montaż” danego
     wydania** (T15, T20, T23, T27, T34, T42), które wchodzi **na końcu** fali i podłącza ekrany
     napisane równolegle;
   - `backend/conftest.py` i `apps/tenancy/tests/` — **wyłącznie zadania testowe** (T16, T28, T43).
3. **Migracja i model w jednym zadaniu.** Zadanie, które dokłada pole, dokłada też jego migrację —
   inaczej `makemigrations --check` w CI (`.github/workflows/ci.yml`, zadanie `migrations`) padnie
   na gałęzi, której nikt nie jest właścicielem.

**Rozmiar.** Każde zadanie jest wycenione na **2–5 godzin agenta** (kod + testy + przebieg suity
obszaru). Zadania powyżej tej wyceny są podzielone; zadania poniżej są scalone z sąsiadem.

### 4.2. Fale

| Fala | Wydanie | Zadania równoległe | Warunek wejścia |
|---|---|---|---|
| **1** | E | T8, T16 | — |
| **2** | E | T9, T10, T12, T14 | T8; T9 **dopiero po T16** (patrz niżej) |
| **3** | E | T11, T13 | T10, T12 |
| **4** | E | T15 | T11, T13 |
| **5** | F | T17 | T8 |
| **6** | G | T18, T21 | T8 |
| **7** | G | T19, T22 | T18, T21 |
| **8** | G | T20 | T19, T22 |
| **9** | H | T24 | T22 |
| **10** | H | T25, T26 | T24 |
| **11** | H | T23 | T25 |
| **12** | I | T29, T31 | T8 |
| **13** | I | T30, T32 | T29, T31 |
| **14** | I | T27, T28 | T30, T32 |
| **15** | J | T35, T37, T38 | T30 |
| **16** | J | T36, T39, T40 | T35, T38, T32 |
| **17** | J | T34 | fale 15–16 |
| **18** | K | T41, T44, T45, T46, T47, T48 | T8; T46 po T9 |
| **19** | K | T42, T43, T49 | fala 18 |

Wewnątrz fali zadania nie mają między sobą zależności ani wspólnych plików. **T16 jest pierwsze
razem z T8 i to nie jest pomyłka:** sześć tematów listów (zgłoszenia, rozmowy, przypomnienia) nie
jest dziś objętych żadnym testem niezmienności, więc T9 bez T16 zmieniałoby napisy, których nikt
nie pilnuje.

Trzy zadania dzielą pliki i dlatego **wykonuje je ten sam agent po kolei**, a nie dwaj równolegle:
T19 → T38 → T39 (`apps/grading/services.py`, podział po funkcjach) oraz T30 → T32 → T35
(`apps/results/services.py`).

### 4.3. Tabela zadań

| ID | Tytuł | Własność plików (skrót) | Zależy od | Ryzyko | h |
|---|---|---|---|---|---|
| **T8** | Katalog flag etapu 2 i moduł marki | `apps/tenancy/models.py` (tylko `FEATURE_DEFAULTS`), `apps/tenancy/branding.py` **(nowy)**, `apps/accounts/consents.py` (tylko `organizer_name`) | — | niskie | 3 |
| **T9** | Poczta: nadawca, prefiks, temat i podpis z konkursu | `apps/accounts/activation.py`, `apps/core/tasks.py`, `apps/accounts/guardian.py`, `apps/accounts/services.py` (tematy), `apps/submissions/notifications.py`, `apps/grading/reports.py`, `apps/grading/deadlines.py`, `apps/support/services.py`, `apps/competitions/video.py`, `apps/competitions/interviews.py`, `apps/accounts/messaging.py`, `apps/grading/tasks.py` | T8 | **wysokie** | 5 |
| **T10** | `ConsentDefinition`: model, migracja, `consent_set()` | `apps/accounts/models.py` (`ConsentDefinition`), `apps/accounts/migrations/`, `apps/accounts/consents.py` (`DEFAULT_CONSENTS`, `consent_set`) | T8 | **wysokie** | 5 |
| **T11** | Ekran „Zgody konkursu” | `apps/web/views/coordinator_consents.py` **(nowy)**, `templates/web/coordinator/consents.html` **(nowy)**, `apps/web/coordinator_forms.py` (nowa klasa) | T10 | średnie | 4 |
| **T12** | `DocumentTemplate`, `Certificate.template_version`, teksty dyplomów z bazy | `apps/tenancy/documents.py` **(nowy)**, `apps/tenancy/migrations/`, `apps/results/models.py` (jedno pole), `apps/results/migrations/`, `apps/results/certificates.py` (źródło czterech napisów) | T8 | **wysokie** | 5 |
| **T13** | Ekran „Szablony dokumentów” | `apps/web/views/coordinator_documents.py` **(nowy)**, `templates/web/coordinator/documents.html` **(nowy)** | T12 | niskie | 3 |
| **T14** | Kalendarz, CAPTCHA, domena anonimowa, strona 500 | `apps/cms/calendar.py`, `apps/web/captcha.py`, `apps/accounts/profile.py` (stała + użycie), `backend/templates/500.html`, `apps/tenancy/migrations/` (`calendar_filename`) | T8 | średnie | 3 |
| **T15** | Montaż wydania E: adresy i nawigacja | `apps/web/urls.py`, `apps/web/coordinator_nav.py` | T11, T13 | niskie | 2 |
| **T16** | Testy niezmienności: zgody, tematy, dokumenty | `apps/tenancy/tests/test_invariants.py`, `apps/tenancy/tests/test_branding.py` **(nowy)**, `apps/tenancy/tests/golden.py` | T8 | średnie | 4 |
| **T17** | Redakcja per konkurs: grupa, kolekcja, komenda | `apps/cms/permissions.py` **(nowy)**, `apps/cms/attachments.py`, `apps/cms/images.py`, `apps/cms/management/commands/scope_cms_access.py` **(nowy)**, `apps/cms/migrations/` (kolekcje), `apps/tenancy/management/commands/create_competition.py` (jedno wywołanie) | T8 | średnie | 5 |
| **T18** | `Region`: model, migracja z województw, pola profili | `apps/accounts/models.py` (`Region`, trzy pola `region`), `apps/accounts/migrations/` | T8 | **wysokie** | 5 |
| **T19** | Konflikt interesów i formularze na regionach | `apps/grading/services.py` (`has_district_conflict` + wołający), `apps/web/forms.py` (`voivodeship_field` → `region_field`), `apps/accounts/serializers.py` | T18 | **wysokie** | 4 |
| **T20** | Montaż wydania G + ekran „Regiony” | `apps/web/urls.py`, `apps/web/coordinator_nav.py`, `apps/web/views/coordinator_regions.py` **(nowy)**, `templates/web/coordinator/regions.html` **(nowy)** | T18, T19, T21 | niskie | 3 |
| **T21** | `InstitutionType` w wykazie i `seed_schools` | `apps/schools/models.py`, `apps/schools/migrations/`, `apps/schools/sio.py`, `apps/schools/management/commands/seed_schools.py`, `apps/schools/fixtures/README.md` | T8 | średnie | 4 |
| **T22** | `RegistrationProfile` i formularz sterowany profilem | `apps/accounts/models.py` (`RegistrationProfile`), `apps/accounts/migrations/`, `apps/accounts/services.py` (`registration_profile`, `register_participant`), `apps/web/forms.py` (`SchoolChoiceMixin`) | T21 | **wysokie** | 5 |
| **T23** | Montaż wydania H + ekran „Słownik placówek” | `apps/web/urls.py`, `apps/web/coordinator_nav.py`, `apps/web/views/coordinator_institutions.py` **(nowy)**, dwa szablony **(nowe)** | T24, T25 | średnie | 4 |
| **T24** | `CustomInstitution` i import CSV | `apps/schools/custom.py` **(nowy)**, `apps/schools/models.py` (`CustomInstitution`), `apps/schools/migrations/`, `apps/accounts/models.py` (`custom_institution_ref`) | T22 | średnie | 5 |
| **T25** | Wyszukiwarka dwóch słowników i picker | `apps/schools/api.py`, `apps/schools/serializers.py`, `backend/static/js/school-picker.js`, `templates/web/_school_picker.html` | T24 | średnie | 4 |
| **T26** | Import grupowy: region, kategoria, placówka własna | `apps/accounts/bulk_registration.py`, `apps/web/views/supervisor.py`, `apps/web/supervisor_forms.py` | T22, T24 | średnie | 4 |
| **T27** | Montaż wydania I + ekrany procesu | `apps/web/urls.py`, `apps/web/coordinator_nav.py`, `apps/web/views/coordinator_pipeline.py` **(nowy)**, `apps/web/views/coordinator_categories.py` **(nowy)**, trzy szablony **(nowe)** | T29, T30, T31 | średnie | 5 |
| **T28** | **Test snapshotu tabel wyników** | `apps/tenancy/tests/test_results_snapshot.py` **(nowy)**, `apps/results/tests/conftest.py` | T29, T30 | **wysokie** | 5 |
| **T29** | `PipelineStep`, `TransitionRule`, migracja z `STAGE_ORDER` | `apps/competitions/models.py` (dwa modele + więz `unique_kind`), `apps/competitions/migrations/` | T8 | **wysokie** | 5 |
| **T30** | Kwalifikacja dwudrożna: `next_stage_of`, `_qualified_entry_ids` | `apps/results/services.py`, `apps/results/simulation.py` | T29 | **wysokie** | 5 |
| **T31** | `Category` i wpis do kategorii | `apps/competitions/models.py` (`Category`, `StageEntry.category`), `apps/competitions/migrations/` | T8 | średnie | 4 |
| **T32** | Rankingi i snapshot per kategoria | `apps/results/services.py` (`_rank_rows`, `build_snapshot`), `apps/results/serializers.py`, `apps/results/statistics.py` | T30, T31 | **wysokie** | 4 |
| **T34** | Montaż wydania J + ekrany punktacji i drużyn | `apps/web/urls.py`, `apps/web/coordinator_nav.py`, `apps/web/views/coordinator_teams.py` **(nowy)**, `apps/web/views/coordinator_stages.py` (dwie sekcje), `templates/web/coordinator/stage_scale.html`, `stage_components.html` **(nowy)**, `teams.html` **(nowy)**, `reviewer_roles.html` **(nowy)** | fala 10 | średnie | 5 |
| **T35** | `StageComponent` i sumowanie komponentów | `apps/competitions/models.py` (`StageComponent`), migracje, `apps/results/services.py` (gałąź komponentów) | T30, T32 (wspólny plik) | **wysokie** | 5 |
| **T36** | `InterviewScore` i wpis punktów z rozmowy | `apps/competitions/interviews.py`, `apps/competitions/models.py` (`InterviewScore`), migracje | T35 | średnie | 4 |
| **T37** | Drużyny: `Team`, `TeamMember`, `entry_owner()` | `apps/competitions/models.py` (`Team`, `TeamMember`, `StageEntry.team`), migracje, `apps/competitions/services.py` (`entry_owner`) | T29 | **wysokie** | 5 |
| **T38** | Wagi zadań, przesunięcie skali, punkty ujemne | `apps/competitions/models.py` (`validate_scoring_values`, `Problem.weight_*`, `ScoringScale.offset`), migracje, `apps/competitions/services.py` (`set_scoring_scale`), `apps/grading/services.py` (`allowed_scores`) | T8 | **wysokie** | 5 |
| **T39** | `ReviewerRole` i przydział wg ról | `apps/grading/models.py` (`ReviewerRole`, `Review.role`), migracje, `apps/grading/services.py` (`assign_reviewers`, `_consensus_score`) | T38 (wspólny plik) | **wysokie** | 5 |
| **T40** | `TieBreak` i porządek rozstrzygania remisów | `apps/competitions/models.py` (`TieBreak`), migracje, `apps/results/services.py` (`_rank_rows`) | T38, T32 | **wysokie** | 4 |
| **T41** | Kreator `/setup/` | `apps/tenancy/setup.py`, `setup_urls.py`, `setup_views.py`, `templates/tenancy/setup_*.html` **(wszystkie nowe)**, `config/urls.py` (**jedna linia**), `apps/core/status.py` (`setup_pending`) | T8 | średnie | 5 |
| **T42** | Montaż wydania K + ekrany finansów i logistyki | `apps/web/urls.py`, `apps/web/coordinator_nav.py`, `apps/web/views/coordinator_fees.py`, `coordinator_logistics.py` **(nowe)**, cztery szablony **(nowe)** | fala 12 | średnie | 5 |
| **T43** | E2E i lista kontrolna produkcji | `e2e/`, `scripts/e2e.sh`, `apps/tenancy/tests/test_golden_single_competition.py` | fala 12 | średnie | 4 |
| **T44** | Obrazy z CI, profil compose, `deploy.sh` | `.github/workflows/ci.yml`, `docker-compose.yml`, `scripts/deploy.sh`, `.env.example`, `scripts/tests/compose_profiles_test.sh` **(nowy)**, `README.md` § 3 | T8 | średnie | 4 |
| **T45** | Wagtail i18n i alias witryny | `config/settings/base.py` (`INSTALLED_APPS`, dwie stałe), `apps/tenancy/models.py` — **nie**: `apps/tenancy/aliases.py` **(nowy)**, `apps/tenancy/resolution.py`, migracje `tenancy` | T8 | średnie | 5 |
| **T46** | Język listów per konkurs | `apps/accounts/preferences.py` (`language_for`) | T9 | niskie | 2 |
| **T47** | Finanse: modele, cennik, należności | `apps/tenancy/fees.py` **(nowy)**, migracje `tenancy`, `apps/competitions/models.py` (`StageEntry.fee`) | T8 | średnie | 5 |
| **T48** | Logistyka: miejsca, przyjazdy, obecność | `apps/competitions/logistics.py` **(nowy)**, migracje `competitions`, `apps/integrations/exports.py` (listy do druku) | T8 | średnie | 5 |
| **T49** | Webhook płatności (stub) | `apps/integrations/inbound.py` **(nowy)**, `apps/integrations/models.py` (`PaymentEndpoint`, `PaymentEvent`), `apps/integrations/urls.py`, migracje `integrations`, `config/settings/base.py` (jedna stawka throttlingu) | T47 | średnie | 4 |

**Suma:** 41 zadań, 177 godzin agenta. Numeru **T33** nie ma — zadanie „rankingi per
kategoria” zostało scalone z T32, a numeracja nie jest przenumerowywana, żeby odwołania
w § 4.2 i § 5 pozostały prawdziwe.

### 4.4. Zadania szczegółowo

Format jak w etapie 1 § 6: *własność plików*, *zakres*, *czego NIE wolno zmienić*, *testy strzegące*.
Zadania o niskim ryzyku i oczywistym zakresie są opisane jednym akapitem.

#### T8 — katalog flag i moduł marki

*Własność:* `apps/tenancy/models.py` **wyłącznie** w obrębie `FEATURE_DEFAULTS`;
`apps/tenancy/branding.py` **(nowy, cały)**; `apps/accounts/consents.py` **wyłącznie** funkcja
`organizer_name` (`:212–228`).
*Zakres:* szesnaście flag z § 0.6 dopisanych do katalogu; `branding.subject()`,
`branding.signature()`, `branding.calendar_name()`; naprawa `organizer_name()` — zamiast
`SiteSettings.objects.first()` (`:223`) odczyt `competition.organizer_name` z odwrotem na
`SiteSettings.for_site(competition.site)`, a potem na `DEFAULT_ORGANIZER_NAME`.
*Czego NIE wolno:* zmienić `DEFAULT_ORGANIZER_NAME`, zmienić brzmienia zgód, dodać flagi spoza
listy z § 0.6, wołać `feature_flags.get(...)` gdziekolwiek.
*Testy strzegące:* `apps/accounts/tests/test_consents.py`, `apps/tenancy/tests/test_invariants.py`;
nowy `test_organizer_name_comes_from_the_request_competition`.

#### T9 — poczta

*Własność:* dwanaście plików z tabeli. To jest największe zadanie etapu pod względem liczby plików
i najmniejsze pod względem logiki — dlatego jest jednym zadaniem, a nie dwunastoma: rozbicie
znaczyłoby dwanaście równoległych zmian sygnatury `queue_mail`.
*Zakres:* `queue_mail(subject, message, recipient, *, competition=None)`
(`apps/accounts/activation.py:269`); `send_mail_task(subject, message, recipient_list,
from_email=None)` (`apps/core/tasks.py:52`, dziś wpisuje `settings.DEFAULT_FROM_EMAIL` na sztywno
w `:74`); szesnaście stałych tematów zamienionych na pary „stała + wzorzec” (§ 1.1.1); podpisy
listów przez `branding.signature()`; przekazanie konkursu w piętnastu wywołaniach.
*Czego NIE wolno:* zmienić ani jednego dzisiejszego napisu tematu, zmienić treści listów poza
podpisem, zmienić nagłówka `Reply-To`, zmienić kolejki (`CELERY_TASK_ROUTES`), zmienić
`EMAIL_SUBJECT_PREFIX` w ustawieniach.
*Testy strzegące:* `test_email_subjects_unchanged` i
`test_competition_one_keeps_the_installation_mail_settings`
(`apps/tenancy/tests/test_invariants.py`); `apps/accounts/tests/test_activation.py`,
`test_guardian.py`, `test_invitations.py`; `apps/submissions/tests/test_notifications.py`;
`apps/support/tests/`.
*Ryzyko:* **wysokie**, bo dwa tematy (`apps/support/services.py:238,267`) **nie są** dziś objęte
testem niezmienności. T16 dokłada je do `EXPECTED_SUBJECTS` **przed** T9 — to jest jedyny powód,
dla którego T16 jest w fali 1, a nie na końcu.

#### T10 — `ConsentDefinition`

*Własność:* `apps/accounts/models.py` (klasa `ConsentDefinition`), `apps/accounts/migrations/`
(dwie migracje: schemat i dane), `apps/accounts/consents.py` (`DEFAULT_CONSENTS`, alias `CONSENTS`,
`consent_set(competition)`).
*Zakres:* model z § 1.1.2 z trzema więzami; migracja danych czytająca `DEFAULT_CONSENTS`; funkcja
`consent_set` jako jedyne wejście; podmiana czytania `CONSENTS` w `apps/web/forms.py`
(`ConsentFieldsMixin.__init__`, `:338–350`), `apps/accounts/serializers.py`,
`apps/accounts/services.py` (`record_consents`, `required_kinds`) i `descriptions()` (`:315–342`).
*Czego NIE wolno:* dotknąć `ConsentRecord` (model dowodowy), zmienić `ConsentKind`, zmienić
`is_minor`/`MINOR_MAX_AGE`, zmienić kolejności pól w formularzu (`CONSENT_FIELD_NAMES`, `:187`),
włączyć flagi `per_competition_consents`.
*Testy strzegące:* `apps/accounts/tests/test_consents.py`, `apps/web/tests/test_consents.py`,
`test_golden_single_competition.py::test_registration_page_asks_for_exactly_the_documented_consents`,
`test_consent_labels_unchanged`; **nowy** `test_consent_definitions_match_the_constant` (§ 5.3).

#### T12 — `DocumentTemplate` i `template_version`

*Własność:* `apps/tenancy/documents.py` **(nowy)**, `apps/tenancy/migrations/`,
`apps/results/models.py` **wyłącznie** pole `Certificate.template_version`,
`apps/results/migrations/`, `apps/results/certificates.py` **wyłącznie** w miejscach czytających
`DOCUMENT_TITLES` (`:76`), `DOCUMENT_STATEMENTS` (`:86`), `WORKSHOP_LIST_HEADING` (`:96`)
i `SIGNATURE_LINE` (`:100`).
*Zakres:* model, walidacja podstawień, migracja danych ze stałych, zapis `template_version` w
`issue_certificate` (`:156`), odczyt w `certificate_content` (`:417`).
*Czego NIE wolno:* zmienić `compose_pdf` poza źródłem czterech napisów, dotknąć
`_merge_background` (`:783`), `register_fonts` (`:521`), `apps/results/signing.py`,
`_next_number` (`:125`), `pdf_filename` (`:861`), `verify` (`:930`), układu
(`apps/results/certificate_layout.py`), `CertificateTemplate` (grafika).
*Testy strzegące:* `apps/results/tests/` w całości, w szczególności testy składu PDF-a i weryfikacji;
**nowy** `test_document_templates_match_the_constants`.

#### T14 — kalendarz, CAPTCHA, domena anonimowa, 500

*Własność:* `apps/cms/calendar.py`, `apps/web/captcha.py`, `apps/accounts/profile.py`,
`backend/templates/500.html`, jedna migracja `tenancy` (`Competition.calendar_filename`).
*Zakres:* tabela z § 1.1.4.
*Czego NIE wolno:* **zmienić sposobu liczenia `UID`** (`apps/cms/calendar.py:132`) — to jest
klucz wydarzenia w kliencie kalendarza; zmienić `ANONYMISED_EMAIL_DOMAIN` dla kont już
zanonimizowanych (żadnej migracji danych); zmienić układu `500.html` poza dwoma napisami.
*Testy strzegące:* `apps/cms/tests/test_calendar.py`, `apps/web/tests/test_antispam.py`,
`apps/accounts/tests/test_account_deletion.py`; **nowy**
`test_calendar_uid_is_stable_across_branding_change`.

#### T16 — testy niezmienności

*Własność:* `apps/tenancy/tests/test_invariants.py`, `apps/tenancy/tests/test_branding.py`
**(nowy)**, `apps/tenancy/tests/golden.py`.
*Zakres:* rozszerzenie `EXPECTED_SUBJECTS` o dwa tematy zgłoszeń
(`apps/support/services.py:238,267`), o dwa tematy rozmów
(`apps/competitions/interviews.py:269`, `apps/competitions/video.py:190`) i o dwa tematy
przypomnień (`apps/grading/deadlines.py:149–152`) — **szesnaście** zamiast jedenastu; nowy zestaw
`EXPECTED_SIGNATURES`; nowe stałe `EXPECTED_DOCUMENT_TITLES` i `EXPECTED_DOCUMENT_STATEMENTS`;
nowa stała `EXPECTED_CALENDAR = {"uid_domain": …, "prodid": …, "calname": …, "filename": …}`.
*Czego NIE wolno:* **zmienić ani jednej istniejącej asercji.** Test, który przestaje przechodzić po
zmianie z T9–T14, jest sygnałem regresji, a nie pozycją do poprawienia.
*Uwaga wykonawcza:* to zadanie wchodzi **przed** T9 i T12 i jest ich warunkiem przyjęcia —
inaczej sześć tematów listów zmieniłoby się bez żadnego testu, który by to zauważył.

#### T17 — redakcja per konkurs

*Własność:* `apps/cms/permissions.py` **(nowy)**, `apps/cms/attachments.py`, `apps/cms/images.py`,
`apps/cms/management/commands/scope_cms_access.py` **(nowy)**, jedna migracja danych `cms`,
`apps/tenancy/management/commands/create_competition.py` (jedno wywołanie `ensure_cms_group`).
*Zakres:* § 1.1.5.
*Czego NIE wolno:* **dotknąć migracji `cms.0003_coordinator_permissions`** (zmiana historii
migracji na produkcji); odebrać komukolwiek uprawnień automatycznie; przenieść istniejących
obrazów i dokumentów między kolekcjami; zmienić `WAGTAILDOCS_SERVE_METHOD`; zmienić polityki
widoczności kolekcji (`apps/cms/views.py:33`).
*Testy strzegące:* `apps/cms/tests/test_security.py` (`:148–181`, uprawnienia),
`apps/cms/tests/test_migrations.py` (`:65–74`), `apps/cms/tests/test_access.py`,
`apps/cms/tests/test_competition_scope.py`; **nowy**
`test_competition_one_coordinator_permissions_unchanged` (§ 5.5).

#### T18 — `Region`

*Własność:* `apps/accounts/models.py` (klasa `Region`, trzy pola `region` na `Participant`,
`CommitteeMember`, `InvitationCode`), `apps/accounts/migrations/` (schemat + dane).
*Zakres:* § 1.4.2 i § 1.4.3.
*Czego NIE wolno:* usunąć ani zmienić `Voivodeship` (`apps/accounts/models.py:110–134`), usunąć
ani znullować `Participant.district` (`:401`), zmienić `normalize_voivodeship` (`:146`), zmienić
`School.voivodeship` (`apps/schools/models.py:61`).
*Testy strzegące:* `apps/accounts/tests/test_voivodeship.py`,
`apps/accounts/tests/test_district_verification.py`, `apps/accounts/tests/test_migrations.py`;
**nowy** `test_regions_mirror_the_voivodeship_list`.

#### T19 — konflikt interesów na regionach

*Własność:* `apps/grading/services.py` (`has_district_conflict` `:170` i trzy miejsca wołające:
`:473`, `:481`, `:494`, plus `:644`, `:715`, `:793`), `apps/web/forms.py` (piętnaście wystąpień
`voivodeship_field`), `apps/accounts/serializers.py`.
*Zakres:* § 1.4.4; pole `Stage.conflict_by_region` **nie** wchodzi w tym zadaniu (jest w backlogu).
*Czego NIE wolno:* zmienić trzech zachowań z § 1.4.4; zmienić `district_verified` ani jego roli
(decyzja organizatora z `docs/BACKLOG.md`); zmienić komunikatów walidacji formularzy.
*Testy strzegące:* `apps/grading/tests/` w całości, w szczególności testy przydziału recenzentów
i konfliktu okręgu; `apps/web/tests/test_coordinator_*.py`.
*Ryzyko:* **wysokie** — pominięcie jednego z sześciu miejsc wołających daje recenzenta
przydzielonego do pracy ucznia z własnego okręgu, co jest naruszeniem regulaminu, a nie błędem
technicznym.

#### T21 — `InstitutionType` i `seed_schools`

*Własność:* `apps/schools/models.py`, `apps/schools/migrations/`, `apps/schools/sio.py`,
`apps/schools/management/commands/seed_schools.py`, `apps/schools/fixtures/README.md`.
*Zakres:* § 1.3.2; zawężenie dezaktywacji w `_apply` (`seed_schools.py:144–148`).
*Czego NIE wolno:* zmienić `SchoolKind` ani `KIND_ORDER` (`apps/schools/models.py:29,52`), zmienić
`apps/schools/normalise.py` (reguły gminy i dzielnicy), zmienić upsertu po RSPO, **skasować
któregokolwiek wiersza** (dezaktywacja zamiast kasowania), zmienić fixture'u
`szkoly-srednie-sio-2025.json`.
*Testy strzegące:* `apps/schools/tests/test_seed_schools.py` (9 testów),
`apps/schools/tests/test_sio.py` (11), `test_normalise.py` (17), `test_api.py` (36); **nowy**
`test_seed_schools_does_not_deactivate_other_institution_types`.

#### T22 — `RegistrationProfile` i formularz

*Własność:* `apps/accounts/models.py` (`RegistrationProfile`, `Participant.country`,
`Participant.institution_name`), `apps/accounts/migrations/`, `apps/accounts/services.py`
(`registration_profile`, `register_participant` `:415`, `_resolve_school` `:82`),
`apps/web/forms.py` (`SchoolChoiceMixin` `:189–319`, `PARTICIPANT_FIELD_ORDER` `:400`).
*Zakres:* § 1.3.4.
*Czego NIE wolno:* zmienić `SCHOOL_FIELD_NAMES` (`:179`) ani ich kolejności; zmienić reguły
`SchoolChoiceMixin.clean` dla dzisiejszych przypadków (tabela w `README.md` § 6.3b); zmienić
komunikatów walidacji; zmienić `ConsentFieldsMixin`.
*Testy strzegące:* `apps/web/tests/test_school_picker.py`, `apps/accounts/tests/test_school_choice.py`,
`apps/accounts/tests/test_registration.py`, `apps/web/tests/test_antispam.py`; **nowy**
`test_registration_form_html_unchanged` (§ 5.3).

#### T24 — `CustomInstitution` i import

*Własność:* `apps/schools/custom.py` **(nowy)**, `apps/schools/models.py` (`CustomInstitution`),
`apps/schools/migrations/`, `apps/accounts/models.py` **wyłącznie** pole
`Participant.custom_institution_ref`.
*Zakres:* § 1.3.3; parser i podgląd wzorowane na `apps/accounts/bulk_registration.py`
(`_decode_csv` `:272`, `pack_rows`/`unpack_rows` `:564,578`).
*Czego NIE wolno:* dopisywać wierszy do `School`; kasować wierszy przy imporcie; przekroczyć
`MAX_UPLOAD_BYTES`; wprowadzać drugiej reguły normalizacji (używamy
`apps/schools/normalise.derived_fields`).
*Testy strzegące:* nowe `apps/schools/tests/test_custom_import.py`;
`apps/accounts/tests/test_bulk_registration.py` (niezmieniony).

#### T25 — wyszukiwarka dwóch słowników

*Własność:* `apps/schools/api.py`, `apps/schools/serializers.py`,
`backend/static/js/school-picker.js`, `templates/web/_school_picker.html`.
*Zakres:* § 1.3.3, akapit „Wyszukiwarka pyta o obie tabele”.
*Czego NIE wolno:* zmienić adresów (`/api/schools/`, `/api/schools/cities/`), zmienić throttlingu
(`schools`, 120/min), zmienić `MAX_RESULTS = 20` ani `MIN_QUERY_LENGTH = 2` (`:54,60`), zmienić
`_kind_rank` (`:63`), zmienić kształtu odpowiedzi dla dzisiejszych zapytań (klucze
`results`/`has_more`), zepsuć działania strony **bez JavaScriptu**.
*Testy strzegące:* `apps/schools/tests/test_api.py` (36 testów),
`e2e/check_school_picker.py`; **nowy** test liczby zapytań: bez flagi
`custom_school_directory` zapytanie do `CustomInstitution` **nie pada ani razu**.

#### T29 — `PipelineStep` i `TransitionRule`

*Własność:* `apps/competitions/models.py` (dwa modele, `TransitionMode`, `TransitionGroupBy`,
zmiana warunku więzi `competitions_stage_unique_kind` `:503`, wartość `StageKind.ROUND`),
`apps/competitions/migrations/` (schemat + `pipeline_from_stages`).
*Zakres:* § 1.2.2, § 1.2.5, § 1.2.8.
*Czego NIE wolno:* zmienić nazwy więzi `competitions_stage_unique_kind`; usunąć ani zmienić
`QualificationRule` (`:691`) i `QualificationMode` (`:684`); zmienić `Stage.kind` istniejących
etapów; zmienić `STAGE_ORDER` (`apps/results/services.py:53`) — to robi T30; zmienić `create_stage`
(`apps/competitions/services.py:178`).
*Testy strzegące:* `apps/competitions/tests/` w całości, w szczególności `test_training.py`
(`:126` — `TRAINING not in STAGE_ORDER`); **nowy** `test_pipeline_matches_stage_order`.

#### T30 — kwalifikacja dwudrożna

*Własność:* `apps/results/services.py`, `apps/results/simulation.py`.
*Zakres:* `next_stage_of` (`:413`) i `_qualified_entry_ids` (`:361`) z gałęzią flagi; odwzorowanie
czterech trybów na `TransitionRule`; suma reguł kroku.
*Czego NIE wolno:* zmienić `_rank_rows` (`:180`) — to robi T32; zmienić `_top_n_cutoff` (`:345`),
w szczególności **odrzucania zer** i **wpuszczania remisów na progu**; zmienić
`qualified_with_manual` (`:390`); zmienić `_sync_next_stage` (`:433`) ani jego trzech reguł;
zmienić `publish_results` (`:669`) ani `build_snapshot` (`:636`); zmienić bramki
`APPEAL_WINDOW_OPEN` (`:92`) i `STAGE_NOT_FINALIZED` (`:165`).
*Testy strzegące:* `apps/results/tests/` w całości; **warunek przyjęcia to T28.**

#### T32 — rankingi i snapshot per kategoria

*Własność:* `apps/results/services.py` (`_rank_rows`, `build_snapshot`, `_display_name`),
`apps/results/serializers.py`, `apps/results/statistics.py`.
*Czego NIE wolno:* zmienić kluczy wiersza snapshotu dla konkursu **bez** kategorii; zmienić
k-anonimowości `INITIALS_SCHOOL` (`MIN_SCHOOL_GROUP = 3`, `:70`); zmienić `_may_show_full_name`
(`:595`); zmienić porządku `public_code` jako ostatniego klucza sortowania.
*Testy strzegące:* `apps/results/tests/`, `apps/cms/tests/test_pages.py` (liczba zapytań
`ResultsPage`, `:207,224`), T28.

#### T35 — `StageComponent`

*Własność:* `apps/competitions/models.py` (`StageComponent`, `ComponentKind`), migracje,
`apps/results/services.py` **wyłącznie** gałąź sumowania komponentów w `compute_stage_results`.
*Konflikt do rozstrzygnięcia:* `apps/results/services.py` jest we własności T30 i T32. **T35 wchodzi
po obu** (fala 10 po fali 9) i to jest jedyny powód, dla którego wydanie J jest osobne od I.
*Czego NIE wolno:* zmienić zachowania etapu **bez** komponentów (czyta `Stage.format`, jak dziś);
zmienić szwu z testem online (`apps/quiz/services.py:672` `stage_scores`); zmienić `_latest_submissions`
(`:132`) ani `_blocks_finalization` (`:152`).
*Testy strzegące:* `apps/quiz/tests/test_results_integration.py`, T28.

#### T37 — drużyny

*Własność:* `apps/competitions/models.py` (`Team`, `TeamMember`, `StageEntry.team`, więz
`single_owner`), migracje, `apps/competitions/services.py` (`entry_owner`).
*Czego NIE wolno:* znullować `StageEntry.participant` **bez** więzu „albo uczestnik, albo drużyna”;
zmienić `StageEntryQuerySet.for_user` (`apps/competitions/models.py:943`) inaczej niż przez
`entry_owner`; zmienić `public_code` uczestnika.
*Ryzyko:* **wysokie** — `entry.participant` czyta kilkanaście miejsc. Mitygacja: `entry_owner`
podnosi `AttributeError` dla wpisu bez właściciela, więc przeoczenie jest głośne (ten sam wzorzec,
którym etap 1 wymusił poprawki po zamianie `user.participant`).

#### T38 — wagi, przesunięcie, punkty ujemne

*Własność:* `apps/competitions/models.py` (`validate_scoring_values` `:86`, `Problem.weight_*`
`:736`, `ScoringScale.offset` `:657`), migracje, `apps/competitions/services.py`
(`set_scoring_scale` `:313`, `scores_in_use` `:268`), `apps/grading/services.py`
**wyłącznie** `allowed_scores` (`:277`) i `_assert_score_in_scale` (`:327`).
*Czego NIE wolno:* zmienić typu `Review.score`, `FinalGrade.score` ani `StageEntry.total_points`;
zmienić komunikatów `validate_scoring_values` przy `allow_negative=False`; zmienić bramki
`SCALE_LOCKED` (`:295`); zmienić `DEFAULT_SCORING_VALUES` (`:33`).
*Testy strzegące:* `apps/competitions/tests/test_scale.py` (i sąsiednie),
`apps/grading/tests/`, T28.

#### T39 — role recenzenckie

*Własność:* `apps/grading/models.py` (`ReviewerRole`, `Review.role`), migracje,
`apps/grading/services.py` **wyłącznie** `assign_reviewers` (`:427`) i `_consensus_score` (`:953`).
*Konflikt:* `apps/grading/services.py` dzielą T19 (inne funkcje) i T38 (inne funkcje). Podział jest
po funkcjach i jest wykonalny, bo pliki nie mają wspólnego stanu modułowego — ale **kolejność jest
wymuszona**: T19 (fala 4) → T38 (fala 10) → T39 (fala 10, po T38). W praktyce T38 i T39 wykonuje
**jeden agent po kolei**.
*Czego NIE wolno:* zmienić `ROUND_BLIND`/`ROUND_TIEBREAK` (`:24–25`); zmienić maszyny stanów
(`supersede_earlier_versions`, `revise_review` `:1228`, `unassign_reviewer`,
`resolve_moderation` `:1641`); zmienić `has_district_conflict`; zmienić `ReviewCancelReason`.
*Testy strzegące:* `apps/grading/tests/` w całości, w szczególności `test_concurrency.py`.

#### T41 — kreator `/setup/`

*Własność:* `apps/tenancy/setup.py`, `setup_urls.py`, `setup_views.py`, `setup_forms.py`,
`templates/tenancy/setup_*.html` **(wszystkie nowe)**; `config/urls.py` — **jedna linia**;
`apps/core/status.py` — **jeden klucz** `setup_pending`.
*Zakres:* § 1.7.1.
*Czego NIE wolno:* zmienić kolejności żadnego istniejącego wzorca w `config/urls.py`; dodać
drugiej drogi zakładania superużytkownika (woła się `bootstrap_coordinator`); uruchamiać seedów
treści; zmieniać `.env`; zmienić pozostałych jedenastu kluczy `/status.json`
(`apps/core/status.py:266–280`).
*Testy strzegące:* `test_status_json_keeps_its_contract`
(`apps/tenancy/tests/test_golden_single_competition.py:91`); **nowe**
`test_setup_is_404_when_a_competition_exists`, `test_setup_is_404_when_a_superuser_exists`,
`test_setup_creates_exactly_one_operator_under_concurrency`.

#### T44 — obrazy, profil compose, wdrożenie

*Własność:* `.github/workflows/ci.yml`, `docker-compose.yml`, `scripts/deploy.sh`, `.env.example`,
`scripts/tests/compose_profiles_test.sh` **(nowy)**, `README.md` § 3.
*Czego NIE wolno:* zmienić kroków 1/8, 2/8, 3/8, 4a/8, 5/8, 6/8, 6a/8, 7/8, 8/8 dla przebiegu
**bez** `WEB_IMAGE`; zmienić bramki `RUN_CONTENT_SEEDS` (`:219,225`); zmienić wywołań
`seed_edition_kwantowa` (`:237`) i `seed_schools` (`:241`); zmienić bloków `deploy/Caddyfile`;
zmienić `target: runtime` w `Dockerfile`; publikować obrazu z pull requesta.
*Testy strzegące:* `scripts/tests/render_caddyfile_test.sh`; **nowy**
`compose_profiles_test.sh` porównujący listę usług `docker compose --profile full config`
z dzisiejszym `docker compose config` **na równość**.

#### T45 — Wagtail i18n i alias witryny

*Własność:* `config/settings/base.py` **wyłącznie** `INSTALLED_APPS` (jeden wpis) i dwie nowe
stałe; `apps/tenancy/aliases.py` **(nowy)**; `apps/tenancy/resolution.py`; migracje `tenancy`.
*Zakres:* § 1.6.2.
*Czego NIE wolno:* dodać `i18n_patterns` **nigdzie**; zmienić kolejności `MIDDLEWARE`
(`config/settings/base.py:137–186`, w szczególności `LocaleMiddleware` `:155` →
`CompetitionMiddleware` `:165` → `PreferencesMiddleware` `:169`); zmienić `LANGUAGE_CODE`,
`LANGUAGES` ani `LOCALE_PATHS`; ustawić `WAGTAIL_I18N_ENABLED` domyślnie na `True`; zmienić
`Competition.site` z `OneToOne` na `FK`.
*Testy strzegące:* `apps/tenancy/tests/test_resolution.py`, `test_middleware.py`,
`apps/cms/tests/test_security.py`, `apps/accounts/tests/test_preferences.py`; **nowy**
`test_no_i18n_patterns_in_urlconf` (grep po `config/urls.py`).

#### T49 — webhook płatności

*Własność:* `apps/integrations/inbound.py` **(nowy)**, `apps/integrations/models.py`
(`PaymentEndpoint`, `PaymentEvent`), `apps/integrations/urls.py`, migracje `integrations`,
`config/settings/base.py` **wyłącznie** jeden wpis w `DEFAULT_THROTTLE_RATES` (`:682`).
*Zakres:* § 1.5.1, akapit o stubie.
*Czego NIE wolno:* zmienić `config/urls.py` (adres wchodzi pod istniejący `/api/v1/`); zmienić
`apps/integrations/webhooks.py` (wychodzące); zmienić `ApiKeyAuthentication`; przyjąć żądania bez
poprawnego podpisu; wprowadzić zależności od SDK dostawcy.
*Testy strzegące:* nowe `apps/integrations/tests/test_inbound.py` — poprawny podpis, zły podpis,
podpis po tolerancji czasowej, powtórzone doręczenie (idempotencja), nieznana należność
(`matched=False`, odpowiedź 2xx).

---

## 5. Strategia testów

Punkt wyjścia: **3235 testów, 0 xfail** (`v0.23.0`, `docs/CHANGELOG.md`). Zasada nadrzędna,
przepisana z etapu 1 § 6 (T7): **test, który przestaje przechodzić po zmianie z T8–T49, jest
sygnałem regresji, a nie pozycją do poprawienia.** Poprawka idzie do kodu, nie do testu. Jedynym
dopuszczalnym wyjątkiem jest dopisanie argumentu z wartością domyślną do fabryki.

### 5.1. Mapa: czym dowodzimy, że Konkurs #1 się nie zmienił

| Obszar | Czym dowodzimy | Plik |
|---|---|---|
| zgody | snapshot zestawu **i** porównanie wierszy bazy ze stałą | `test_invariants.py`, `test_consent_definitions.py` **(nowy)** |
| listy | snapshot **szesnastu** tematów i pięciu podpisów | `test_invariants.py` (rozszerzony w T16) |
| dokumenty | snapshot tytułów, zdań i linii podpisu; PDF z wersją szablonu | `test_invariants.py`, `apps/results/tests/` |
| kalendarz | `UID`, `PRODID`, `X-WR-CALNAME`, nazwa pliku | `test_invariants.py` (`EXPECTED_CALENDAR`) |
| `/cms/` | zestaw uprawnień koordynatora Konkursu #1 przed i po | `test_cms_permissions_unchanged.py` **(nowy)** |
| **edytor procesu** | **snapshot każdej ogłoszonej tabeli wyników, dwiema drogami** | `test_results_snapshot.py` **(nowy, T28)** |
| rejestracja | lista pól i etykiet formularza `/register/` | `test_registration_form_html_unchanged` |
| regiony | reguła konfliktu interesów, trzy zachowania, dwudrożnie | `apps/grading/tests/` |
| adresy | brak `i18n_patterns`, kolejność wzorców | `test_no_i18n_patterns_in_urlconf` **(nowy)** |
| koszt | budżety zapytań **niepodniesione** | `test_invariants.py::QUERY_BUDGET` |
| izolacja | 404 na obiekcie cudzego konkursu, po jednym na nowy model | `test_isolation.py` (rozszerzony o 12) |

### 5.2. Snapshot tabel wyników — test, który decyduje o wydaniach I i J

Najważniejszy test tego etapu i jedyny, którego nie da się zastąpić przeglądem kodu. Pytanie:
**czy przebieg Olimpiady Kwantowej wyrażony jako dane daje dokładnie te same tabele, co przebieg
wyrażony w kodzie.**

```python
# apps/tenancy/tests/test_results_snapshot.py  (nowy)

@pytest.mark.parametrize("mode", ["kind", "pipeline"])
def test_stage_results_are_identical_both_ways(golden_with_results, mode, competition):
    """Ta sama edycja, dwie drogi kwalifikacji, wynik na równość — nie na podzbiór.

    ``mode="kind"``     – flaga ``process_editor`` wyłączona: ``STAGE_ORDER`` + ``QualificationRule``.
    ``mode="pipeline"`` – flaga włączona: ``PipelineStep`` + ``TransitionRule`` z migracji.

    Porównujemy **cztery** rzeczy, bo każda jest osobnym sposobem na cichą regresję:

    1. ``compute_stage_results(stage)`` — lista słowników, **klucz po kluczu i w tej samej
       kolejności wierszy**. Porównanie podzbioru przepuściłoby dołożony klucz ``category``,
       czyli dokładnie ten błąd, który wyszedłby dopiero w ogłoszonej tabeli.
    2. ``rank`` każdego wiersza — remisy i porządek ``public_code`` to dwie różne decyzje.
    3. zbiór ``entry_id`` zakwalifikowanych — próg jest o tym, kto przechodzi dalej.
    4. ``build_snapshot(rows, anonymization)`` dla **każdego** z trzech trybów anonimizacji — to
       jest obiekt, który trafia do ``ResultsPublication.snapshot`` i na stronę publiczną.
    """
```

**Trzy zestawy danych, na których test biegnie:**

1. **złota fikstura** (`apps/tenancy/tests/golden.py`, `build_golden`) — cztery etapy (trening,
   `ELIM`, `DISTRICT` jako `INTERVIEW`, `FINAL`), trzy zadania, trzech uczestników w trzech stanach
   wpisu i trzech stanach zgłoszenia, recenzje i oceny końcowe: kształt produkcji bez danych
   osobowych;
2. **fikstura brzegowa**, parametryczna: cztery tryby `QualificationMode`, remis na progu, komplet
   zer, wpis `DISQUALIFIED`, `manual_qualification` w obu kierunkach, etap `QUIZ`, etap bez zadań —
   dziewięć przypadków;
3. **kopia produkcyjna** — przebieg ręczny przed wdrożeniem I i J:
   `manage.py dumpdata results.ResultsPublication` przed migracją, `diff` po migracji i po
   przeliczeniu każdej publikacji. To nie jest test w CI (dane osobowe nie wchodzą do repozytorium),
   tylko **punkt 16 ręcznej listy kontrolnej** z § 0.5, wykonany narzędziem zamiast okiem.

**Czego test pilnuje imiennie** — każde jako osobna asercja z własną nazwą, bo każde jest decyzją:
`test_zero_never_qualifies_in_top_n_modes` (`_top_n_cutoff`, `:345`),
`test_ties_at_the_cutoff_all_qualify` (próg to **wartość**, nie miejsce),
`test_manual_qualification_beats_every_rule` (także sumę reguł),
`test_training_stage_qualifies_nobody` (`off_pipeline=True` znaczy to, co dziś znaczy nieobecność
w `STAGE_ORDER`), `test_snapshot_keys_unchanged_without_categories`,
`test_weight_one_over_one_equals_integer_sum` (waga `1/1` przez `Fraction` daje tę samą liczbę),
`test_scoring_offset_zero_changes_nothing`.

### 5.3. Zgody i formularz rejestracji

```python
def test_consent_definitions_match_the_constant(competition):
    """Wiersze w bazie są **równe** ``DEFAULT_CONSENTS``, pole po polu.

    Test sprawdza migrację, zanim ktokolwiek włączy flagę. Bez niego migracja przepisująca zgody
    byłaby jedynym miejscem w systemie, w którym treść oświadczenia mogłaby się zmienić bez śladu.
    """
    definitions = ConsentDefinition.objects.for_competition(competition).order_by("ordering")
    assert [(d.field_name, d.kind, d.text, d.link_text, d.document_slug, d.version,
             d.required, d.required_for_minor, d.help_text, d.missing_message) for d in definitions] \
        == [(c.field_name, str(c.kind), c.text, c.link_text, c.document_slug, c.version,
             c.required, c.required_for_minor, c.help_text, c.missing_message) for c in DEFAULT_CONSENTS]


def test_consent_set_is_identical_with_and_without_the_flag(competition): ...
    # Włączenie ``per_competition_consents`` nie zmienia dla Konkursu #1 ani jednego znaku.

def test_registration_form_html_unchanged(client_for, competition): ...
    # Porównujemy **listę nazw pól, ich kolejność i etykiety zgód**, a nie cały dokument: cały
    # dokument zmienia się przy każdej poprawce CSS-a i test byłby alarmem, którego nikt nie czyta.

def test_no_module_imports_consents_directly(): ...
    # Grep po repozytorium, w stylu istniejącego ``test_no_direct_feature_flag_access``: poza
    # ``apps/accounts/consents.py`` i testami niezmienności nikt nie importuje ``CONSENTS``.
    # Dwa źródła zestawu zgód znaczyłyby, że formularz i API pokazują co innego.
```

### 5.4. Listy

`EXPECTED_SUBJECTS` rośnie z jedenastu do **szesnastu** pozycji (T16). Dochodzą tematy zgłoszeń
(`apps/support/services.py:238,267`), rozmów (`apps/competitions/interviews.py:269`,
`apps/competitions/video.py:190`) i przypomnień o zaległych recenzjach
(`apps/grading/deadlines.py:149–152`). Trzy z nich są **wzorcami z podstawieniem** (`#<n>`,
`<etap>`), więc porównanie idzie po wzorcu, a nie po wyniku. Dochodzi też `EXPECTED_SIGNATURES` —
pięć podpisów listów (§ 1.1.1) porównywanych jako napisy. Do tego dwa testy zachowania:
`test_mail_sender_is_unchanged_for_competition_one` (nadawca i prefiks tematu dzisiejsze, także po
przejściu na `branding.subject`) oraz `test_mail_language_falls_back_to_the_competition_default`.

### 5.5. Uprawnienia `/cms/`

```python
def test_competition_one_coordinator_permissions_unchanged(competition):
    """Zestaw uprawnień grupy ``coordinator`` po wprowadzeniu grup per konkurs — **równy**.

    Porównujemy trzy zbiory: ``Group.permissions`` (kody), ``GroupPagePermission``
    (``page_id``, ``permission_id``) i ``GroupCollectionPermission``
    (``collection_id``, ``permission_id``). Migracja ``cms.0003`` jest niezmieniona, a grupa
    ``cms:<slug>`` wyłącznie **dokłada** — różnica ma być pusta w obie strony.
    """

def test_scope_cms_access_refuses_competition_one(): ...
    # Komenda odmawia zawężenia dostępu dla Konkursu #1 — twardy warunek w kodzie, nie w README.
```

### 5.6. Budżety zapytań

`QUERY_BUDGET` (`apps/tenancy/tests/test_invariants.py:225–232`, dziś `/`: 32, `/me/`: 46,
`/coordinator/`: 48) **nie rośnie w żadnym wydaniu etapu 2.** To twarda bramka, nie zalecenie.
Dochodzą trzy progi: `/register/`: 18, `/wyniki/`: 12, `/coordinator/pipeline/`: 20.

Ważniejsze od samych progów są dwa testy kształtu kosztu (wzorzec:
`test_panel_query_count_does_not_grow_with_participants`):
`test_register_query_count_does_not_grow_with_consents` i
`test_results_query_count_does_not_grow_with_categories`. Do tego jeden test **nieobecności**
zapytania:

```python
def test_custom_directory_is_not_queried_without_the_flag(...):
    """Bez ``custom_school_directory`` zapytanie do ``CustomInstitution`` nie pada ani razu.

    Wyszukiwarka szkół jest publiczna i chodzi po niej każdy rejestrujący się uczestnik; zapytanie
    do pustej tabeli nie kosztuje wiele, ale kosztuje **zawsze**, a próg jest ustawiony na dzisiejszy
    stan.
    """
```

### 5.7. Izolacja — testy krzyżowe dla nowych modeli

Reguła etapu 1 § 7.2 obowiązuje bez zmian: **każdy zakresowany widok i endpoint ma test pokazujący
404 na obiekcie drugiego konkursu.** `apps/tenancy/tests/test_isolation.py` (dziś 20 testów) rośnie
o **dwanaście**, po jednym na nowy model: `ConsentDefinition`, `DocumentTemplate`, `Region`,
`Category`, `PipelineStep`, `TransitionRule`, `StageComponent`, `Team`, `CustomInstitution`,
`FeeSchedule`/`ParticipantFee`, `Venue`, `ReviewerRole`. Dopełnienie na poziomie querysetu, tańsze
i bliżej reguły:

```python
def test_regions_of_a_are_invisible_to_b(competition_a, competition_b):
    assert not Region.objects.for_competition(competition_a).filter(competition=competition_b).exists()
```

Do tego trzy testy reguły konfliktu interesów, po jednym na zachowanie z § 1.4.4, uruchamiane
**dwudrożnie** (z flagą `custom_regions` i bez).

### 5.8. E2E

Istniejący przebieg (`scripts/e2e.sh`, `e2e/test_full_cycle.py`) i `e2e/check_school_picker.py`
zostają **bez zmian** i biegną na Konkursie #1. Dochodzą **trzy** przebiegi, wszystkie na konkursie
drugim:

1. **kreator:** świeża baza → `GET /setup/` → trzy kroki → konkurs istnieje → `GET /setup/` = 404;
2. **przebieg konfigurowalny:** konkurs z pięcioma etapami, dwiema kategoriami, wagami zadań i regułą
   „top 20 **oraz** min. 60 punktów”; rejestracja w kategorii, oddanie prac, przeliczenie, dwa
   osobne rankingi;
3. **rejestracja rozszerzona:** uczestnik z uczelni, „bez szkoły”, z zagranicy (kraj + wolny tekst)
   oraz placówka ze słownika organizatora wgranego z CSV.

---

## 6. Otwarte decyzje organizatora

| # | Pytanie | Warianty | **Rekomendacja** |
|---|---|---|---|
| **D8** | Czy Konkurs #1 ma w ogóle przejść na zgody z bazy? | (a) zostaje na stałej przez sezon; (b) przełączamy razem z wydaniem E | **(a).** Stała i wiersze są równe (test § 5.3), więc przełączenie niczego nie zyskuje, a jest zmianą źródła treści, którą uczestnik może zobaczyć. Przełączyć po pierwszej nowelizacji regulaminu, gdy i tak trzeba wpisać nową wersję. |
| **D9** | Punkty z rozmowy: ekran w panelu zamiast `/admin/` | (a) tak, dla Konkursu #1 od razu; (b) tylko dla nowych konkursów | **(a), ale za flagą i po pokazie.** Dzisiejszy stan jest luką opisaną w `docs/BACKLOG.md`, a etap wojewódzki I edycji jest rozmową. Ekran z audytem jest **poprawą**, nie zmianą zachowania — ale jest widoczny, więc wymaga „tak”. |
| **D10** | Punkty ujemne: przesunięcie skali czy zmiana typu kolumn? | (a) `ScoringScale.offset`, kolumny zostają `Positive*`; (b) `AlterField` na `SmallIntegerField` | **(a).** (b) jest prostsze i traci bazodanową gwarancję „punkt nie bywa ujemny”, która dziś łapie błąd serwisu zanim dojdzie do tabeli wyników. Konkurs z punktami ujemnymi to konkurs, którego jeszcze nie ma; gwarancja chroni ten, który jest. Do rewizji, gdy pierwszy taki konkurs powstanie. |
| **D11** | Kategorie: osobne publikacje wyników czy jedna z podziałem? | (a) jedna `ResultsPublication` ze snapshotem podzielonym na sekcje; (b) publikacja na kategorię | **(a).** `ResultsPublication` jest `OneToOne` ze `Stage` (`apps/results/models.py:51`) i cała ścieżka publikacji, reklamacji i statystyk na tym stoi. (b) znaczyłoby zmianę tej relacji, czyli dotknięcie `publish_results`, `published_results`, `/results/<id>/` i eksportów — w wydaniu, które ma nie ruszyć tabel Konkursu #1. |
| **D12** | Kto definiuje kategorie: panel czy szablon konkursu? | (a) panel; (b) `templates_catalog.py`; (c) oba | **(c), z panelem jako źródłem prawdy.** Szablon `przedmiotowa` dostaje dwie kategorie startowe, a organizator poprawia je w panelu. Szablony `kwantowa` i `pusty` nie zakładają żadnej — i to jest właściwe, bo Olimpiada Kwantowa kategorii nie ma. |
| **D13** | Czy słownik własny może zawierać polskie szkoły spoza wykazu SIO? | (a) tak; (b) nie — polskie szkoły wyłącznie z SIO | **(a).** Wykaz nie zna szkół założonych po jego dacie, a jego nieaktualność nie może zamykać drogi do olimpiady (ta sama zasada, co dla wolnego tekstu, `README.md` § 6.3b). Cena: wiersz spoza rejestru nie grupuje się z wierszem z rejestru przy k-anonimowości `INITIALS_SCHOOL`. Do zapisania w podręczniku organizatora. |
| **D14** | Strona 500: zdjąć adres `contact@qaif.org`? | (a) tak, tekst neutralny; (b) zostaje | **(a).** Strona renderuje się bez bazy i nie ma jak podstawić konkursu; adres jednego organizatora na stronie błędu drugiego jest gorszy niż brak adresu. **To jest zmiana widoczna dla Konkursu #1** i dlatego stoi tutaj, a nie w § 1. |
| **D15** | Czy system ma wystawiać faktury zgodnie z ustawą o VAT? | (a) nie — rejestr należności i dokument z szablonu; (b) tak — numeracja ustawowa, rejestr VAT, korekty | **(a), i to napisane wprost w podręczniku.** (b) to program księgowy: numeracja ciągła bez luk, korekty, ewidencja, JPK — osobny produkt, a nie pole w modelu. Etap 2 daje rejestr „ile się należy, czy wpłynęło, kiedy” i dokument z `DocumentTemplate`. **Musi być rozstrzygnięte przed pierwszym konkursem z wpisowym.** |
| **D16** | Czy nieopłacone wpisowe blokuje udział? | (a) nie blokuje; (b) blokuje oddanie pracy; (c) blokuje zapis do etapu | **(a) domyślnie, (b) jako pole `FeeSchedule.blocks_submission`.** Bramka na uploadzie jest decyzją regulaminową organizatora i bywa okrutna (przelew idzie dwa dni, deadline nie czeka). System ma ją umieć, ale nie ma jej włączać sam. |
| **D17** | Wielojęzyczność: druga subdomena czy drugie drzewo w tej samej witrynie? | (a) `en.<domena>` jako drugi `Site` z aliasem konkursu; (b) strony tłumaczone jako rodzeństwo w tym samym drzewie; (c) `i18n_patterns` | **(a).** (c) odpada — zmienia adresy, czyli łamie wymaganie nadrzędne (dowód w § 1.6.2). (b) znaczy dwa komplety slugów w jednym drzewie i dwa menu w jednym `SiteSettings`. (a) daje rozdział menu, ustawień i przekierowań za darmo i kosztuje jeden nowy model plus jedną gałąź w `resolve_competition`. |
| **D18** | Który dostawca płatności później? | (a) Przelewy24; (b) PayU; (c) Tpay; (d) Stripe | **Nie rozstrzygać teraz.** Stub z § 1.5.1 jest neutralny: HMAC z tolerancją czasową i idempotencja po identyfikatorze operacji pokrywają wszystkich czterech. Decyzja ma zapaść, gdy organizator ma umowę — wtedy dochodzi adapter na 100–200 linii, a nie zmiana modelu. Kryterium wyboru: czy dostawca podpisuje webhooka HMAC-em z surowego ciała. |
| **D19** | Kto tłumaczy treści redakcyjne i w jakim trybie? | (a) redakcja w `/cms/`, strona po stronie; (b) eksport/import XLIFF; (c) tłumaczenie maszynowe z korektą | **(a) na start.** `wagtail.contrib.simple_translation` daje przycisk „Translate” kopiujący stronę do drugiego `Locale`; redaktor nadpisuje treść. XLIFF (`wagtail-localize`) to osobna zależność i osobna zmiana w `INSTALLED_APPS` — nie w tym etapie. |
| **D20** | Czy kreator `/setup/` ma prawo zakładać superużytkownika przez publiczny adres? | (a) tak, z CAPTCHĄ i throttlingiem, tylko na pustej bazie; (b) nie — operator zakłada konto przez SSH, kreator tylko konkurs | **(a).** Cały sens instalatora jest w tym, żeby pierwsze uruchomienie nie wymagało SSH. Bramka jest twarda (brak konkursu **i** brak superużytkownika, sprawdzane w każdym widoku, także w `POST`, pod blokadą doradczą), a okno istnieje wyłącznie między `docker compose up` a pierwszym zalogowaniem. **Wymaga świadomej zgody organizatora**, bo to jedyny publiczny adres tworzący konto z pełnymi uprawnieniami. |
| **D21** | Dane szczególne w formularzu przyjazdu (dieta, dostępność) | (a) pole tekstowe z ostrzeżeniem, dostęp tylko koordynatora; (b) zamknięta lista bez wolnego tekstu; (c) nie zbierać — organizator pyta mailem | **(a), warunkowo.** „Dieta bezglutenowa” i „potrzebuję pokoju na parterze” bywają danymi o zdrowiu (art. 9 RODO). Warunkiem wdrożenia jest wpis w `apps/accounts/processing_register.py` (art. 30) **w tym samym commicie** oraz etykieta mówiąca wprost, czego nie wpisywać. Bez wpisu do rejestru — wariant (c). |

---

## 7. Załącznik: czego etap 2 **nie** zmienia

Lista zamknięta, do sprawdzenia w przeglądzie każdego zadania.

**Z etapu 1 § 9, bez zmian:**

- `config/urls.py` — kolejność wzorców i Wagtail jako catch-all w korzeniu; etap 2 dokłada **jedną**
  linię (`path("setup/", …)`, T41) i nie przestawia ani jednej istniejącej;
- przepływ oceniania (`docs/PROJEKT.md` § 2.4) i wszystkie jego bramki:
  `supersede_earlier_versions`, `lock_for_review`, `revise_review`, `unassign_reviewer`,
  `resolve_moderation`, `SCALE_LOCKED`, `APPEAL_WINDOW_OPEN`, `STAGE_NOT_FINALIZED`,
  `SUBMISSION_FINALISED`;
- reguły anonimizacji wyników: `Anonymization`, k-anonimowość `INITIALS_SCHOOL` z progiem 3,
  `_may_show_full_name`, `district` wyłącznie w trybie `CODE`;
- terminy, `grace_seconds`, `submission_deadline`, zamykanie etapu przez `beat`, okna reklamacji;
- podział bucketów MinIO, poświadczenia serwisowe (`S3_PUBLIC_*`/`S3_PRIVATE_*`), klucze obiektów,
  presigned URL-e, `S3SubmissionStorage`, `private_media_storage`;
- polityka CSP stron publicznych (stała `PUBLIC_CSP`) i osobna, luźniejsza dla paneli;
  `'strict-dynamic'`, lista CDN-ów, `frame-ancestors 'none'`;
- `WAGTAILDOCS_SERVE_METHOD = "serve_view"`, `WAGTAILEMBEDS_FINDERS` i zgodna z nimi lista `frame-src`;
- CAPTCHA, `ANTISPAM_MIN_FILL_SECONDS`, pułapka, limity throttlingu — poza **dwiema nowymi**
  stawkami (`setup`, `payments`);
- retencja danych (`Edition.data_retention_months`, `anonymise_account`, `retention_report`,
  `/coordinator/retention/`) i eksport z art. 20 (`/account/export/`);
- treść i wersje zgód Konkursu #1 — cztery zgody, cztery wersje, znak w znak;
- treści redakcyjne, slugi, rewizje i przekierowania w drzewie stron Konkursu #1 oraz wszystkie
  komendy seedujące.

**Dodatkowo, specyficznie dla etapu 2:**

- **numeracja i weryfikacja dokumentów:** `_next_number` (`apps/results/certificates.py:125`), format
  `<prefiks>/<rok>/<nr>`, globalna unikalność `Certificate.number` i `Certificate.code`, alfabet
  i długość kodu, publiczna strona weryfikacji;
- **skład dokumentów:** ReportLab, DejaVu z `backend/static/fonts/`, A4 landscape dyplomu,
  `_merge_background` przez pypdf, pieczęć PAdES (`apps/results/signing.py`) i jej reguła „brak
  konfiguracji = dokument bez pieczęci, nigdy brak dokumentu”;
- **`UID` wydarzeń kalendarza** (`apps/cms/calendar.py:132`) — zmiana wartości zduplikowałaby
  wydarzenia w kliencie każdego uczestnika;
- **adresy anonimowe już zapisanych kont** (`apps/accounts/profile.py:369`) — żadnej migracji danych,
  bo adres jest kluczem logowania;
- **wykaz SIO jako słownik wspólny dla instalacji** (decyzja D2 etapu 1): `School` nie dostaje
  kolumny konkursu ani managera zakresowanego, a `seed_schools` zostaje idempotentne po RSPO
  i wygasza zamiast kasować;
- **`Voivodeship` jako lista wartości** (`apps/accounts/models.py:110`) i kolumny `district` —
  zostają wypełnione i czytane, dopóki flaga `custom_regions` jest wyłączona;
- **reguła konfliktu interesów**: wyłącznie etap `DISTRICT`, brak okręgu u członka komitetu nikogo
  nie wyklucza, `district_verified` niczego nie bramkuje;
- **`i18n_patterns`** — nie wchodzi nigdzie, w żadnej postaci (test grepujący `config/urls.py`);
- **zakres tłumaczenia interfejsu**: panel koordynatora zostaje po polsku, także nowe ekrany etapu 2;
- **kolejność `MIDDLEWARE`** (`config/settings/base.py:137–186`), w szczególności `LocaleMiddleware`
  → `CompetitionMiddleware` → `PreferencesMiddleware`;
- **`Competition.site` jako `OneToOneField` z `PROTECT`** — wielojęzyczność dokłada alias, a nie drugą
  relację;
- **kroki `scripts/deploy.sh`** dla przebiegu bez `WEB_IMAGE`; bramka `RUN_CONTENT_SEEDS` i znacznik
  `.first-deploy`; bloki `deploy/Caddyfile`;
- **`/healthz/` i `/status.json`** — pierwszy bez zmian, drugi z **jednym dodanym** kluczem
  `setup_pending` i jedenastoma istniejącymi w tej samej postaci;
- **wychodzące webhooki** (`apps/integrations/webhooks.py`): schemat podpisu, pięć zdarzeń, brak
  danych osobowych w ładunku, ponowienia i wygaszanie odbiorcy;
- **liczniki uwagi** w nawigacji koordynatora — pięć kluczy, pięć zapytań, 60 s cache'u; etap 2 nie
  dokłada szóstego;
- **2FA** zostaje dostępne i wyłączone; **SSO** nie powstaje; **automatyczna ocena kodu uczestnika**
  nie powstaje; **samodzielna rejestracja opiekunów w Konkursie #1** zostaje ukryta;
- **wersja frameworka** — Django 5.1 i Wagtail 6.3 bez zmiany.
