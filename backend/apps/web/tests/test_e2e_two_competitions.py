"""Scenariusz „dwa konkursy na jednej instalacji” przejechany klientem testowym (T43).

Ten moduł jest **serwerowym odpowiednikiem** przebiegu z ``e2e/check_stage2_screens.py``
i ``e2e/check_stage2_isolation.py``. Oba pytają o to samo — czy drugi konkurs założony komendą
``create_competition`` z garścią flag etapu 2 ma komplet ekranów, czy te same adresy dają
w Konkursie #1 **404** i czy uczestnicy jednego konkursu są niewidoczni z panelu drugiego — tyle
że tamte pytają przeglądarką żywego środowiska compose, a ten klientem Django.

**Po co dwa razy to samo.** Przebieg w przeglądarce sprawdza rzeczy, których serwer nie widzi
(CSP, brakujący skrypt, formularz bez tokenu) i wymaga za to działającego compose z demo danymi;
ten moduł chodzi w zwykłej suicie na każdym pushu i łapie regresję **zanim** ktokolwiek uruchomi
środowisko. ``docs/UNIWERSALNY-ETAP-2.md`` § 5.8 wymienia przebieg w przeglądarce jako
rozstrzygający, więc ten plik go nie zastępuje — pilnuje tylko, żeby nie startował z listą
adresów, które i tak by nie odpowiedziały.

**Adresowanie idzie prefiksem ścieżki, a nie drugim hostem** (``docs/UNIWERSALNY-ETAP-1.md``
§ 2.3): żądanie na hosta Konkursu #1 ze ścieżką ``/druga/…`` rozstrzyga się na konkurs drugi,
bo ``resolve_for_request`` daje pierwszeństwo pierwszemu segmentowi ścieżki. Dzięki temu ani ten
moduł, ani przebieg E2E nie potrzebują wpisu w DNS-ie ani drugiej pozycji w ``ALLOWED_HOSTS``.

**Flagi ustawiamy po założeniu konkursu, a nie szablonem.** ``templates_catalog`` daje każdemu
szablonowi puste ``feature_flags`` i tak ma zostać (§ 0.6: nowa zdolność wchodzi decyzją
organizatora, a nie przy okazji wyboru szablonu). Drugi konkurs jest tu konkursem **po** tej
decyzji, więc flagi wpisujemy wprost — dokładnie tak, jak robi to operator w ``/admin/``
(``docs/OPERACJE.md`` § 6.4).
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import CompetitionRole
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.models import Edition, PipelineStep, Stage, TransitionMode
from apps.tenancy.models import Competition

pytestmark = pytest.mark.django_db

#: Identyfikator, domena i prefiks drugiego konkursu. Te same wartości, co w ``scripts/e2e.sh`` —
#: przebieg w przeglądarce i ten moduł mają opisywać **jeden** scenariusz, a nie dwa podobne.
SECOND_SLUG = "e2e-druga"
SECOND_DOMAIN = "e2e-druga.test"
SECOND_PREFIX = "druga"

#: Flagi zapalane drugiemu konkursowi — zestaw z zadania T43 (§ 4.3). Nie komplet katalogu:
#: ``competition_branding_in_mail``, ``scoped_cms_permissions`` i ``content_translations`` nie
#: mają własnego ekranu (§ 2.2), więc w przebiegu po ekranach nie miałyby czego dołożyć.
SECOND_FLAGS: dict[str, bool] = {
    "per_competition_consents": True,
    "document_templates": True,
    "custom_regions": True,
    "categories": True,
    "process_editor": True,
    "weighted_scoring": True,
    # ``reviewer_roles`` nie stoi w zestawie z § 4.3, a ekran „Role recenzenckie” — tak. Flaga
    # wchodzi tu **razem z ekranem**, bo bez niej ten adres jest 404 i przebieg sprawdzałby
    # wyłącznie własną listę adresów.
    "reviewer_roles": True,
    "team_entries": True,
    "fees": True,
    "onsite_logistics": True,
    "institution_types": True,
    "custom_school_directory": True,
}


def path(address: str) -> str:
    """Adres drugiego konkursu: ``"/coordinator/consents/"`` → ``"/druga/coordinator/consents/"``."""
    return f"/{SECOND_PREFIX}{address}"


@pytest.fixture
def no_seeds(monkeypatch):
    """``create_competition`` bez ``seed_schools`` — wykaz SIO to kilka tysięcy cudzych wierszy.

    Ten sam zabieg, co w ``apps/tenancy/tests/test_create_competition.py``: scenariusz nie dotyka
    wyszukiwarki szkół, więc nie ma powodu czekać na jej słownik przy każdym przebiegu.
    """
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: None,
    )


@pytest.fixture
def coordinator(competition):
    """Koordynator z **globalnej** grupy Django — czyli tak, jak wygląda dzisiejsza produkcja.

    Rola jest globalna dopóki ``memberships_enforced`` jest wyłączone, i to jest tu celowe:
    dzięki temu ta sama osoba przechodzi bramkę roli w **obu** konkursach, więc różnica
    „200 tam, 404 tu” pochodzi wyłącznie z flagi konkursu, a nie z braku uprawnień.
    """
    user = UserFactory()
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)
    return user


@pytest.fixture
def second(competition, coordinator, no_seeds):  # noqa: ARG001 - no_seeds działa efektem ubocznym
    """Drugi konkurs założony komendą, w trybie prefiksu ścieżki, z flagami etapu 2."""
    call_command(
        "create_competition",
        slug=SECOND_SLUG,
        name="Olimpiada Druga",
        domain=SECOND_DOMAIN,
        from_template="przedmiotowa",
        path_prefix=SECOND_PREFIX,
        coordinator_email=coordinator.email,
    )
    row = Competition.objects.get(slug=SECOND_SLUG)
    row.feature_flags = dict(SECOND_FLAGS)
    row.save(update_fields=["feature_flags"])
    return row


@pytest.fixture
def panel(client_for, competition, coordinator):
    """Klient zalogowanego koordynatora pod domeną Konkursu #1 — jeden na oba konkursy.

    Jeden klient, bo w trybie prefiksu ścieżki ciasteczka **są** wspólne (§ 2.3) i scenariusz ma
    chodzić tą samą drogą, co człowiek: ten sam zalogowany koordynator wchodzi raz pod ``/``,
    raz pod ``/druga/``.
    """
    client = client_for(competition)
    client.force_login(coordinator)
    return client


def first_stage(second: Competition) -> Stage:
    """Pierwszy etap edycji drugiego konkursu — po osi czasu, bo ``Stage`` nie ma numeru miejsca."""
    return Stage.objects.for_competition(second).order_by("opens_at", "id").first()


def screens(second: Competition) -> tuple[tuple[str, str], ...]:
    """Ekrany drugiego konkursu: pary (adres bez prefiksu, napis, który musi być na stronie).

    Adresy etapowe i adres reguł przejścia biorą się z **danych** tego konkursu, a nie ze stałej:
    identyfikatory nadaje baza, a scenariusz ma chodzić po tym, co naprawdę powstało.
    """
    stage = first_stage(second)
    step = first_step(second)
    return (
        ("/coordinator/consents/", "Zgody konkursu"),
        ("/coordinator/documents/", "Szablony dokumentów"),
        ("/coordinator/regions/", "Regiony"),
        ("/coordinator/categories/", "Kategorie"),
        ("/coordinator/pipeline/", "Przebieg edycji"),
        (f"/coordinator/pipeline/{step.pk}/rules/", "Reguły przejścia"),
        (f"/coordinator/stages/{stage.pk}/components/", "Komponenty etapu"),
        (f"/coordinator/stages/{stage.pk}/interview-scores/", "Punkty z rozmowy"),
        (f"/coordinator/stages/{stage.pk}/tie-breaks/", "Rozstrzyganie remisów"),
        (f"/coordinator/stages/{stage.pk}/reviewer-roles/", "Role recenzenckie"),
        ("/coordinator/teams/", "Drużyny"),
        ("/coordinator/fees/", "Wpisowe: cennik"),
        ("/coordinator/fees/register/", "Wpisowe: należności"),
        ("/coordinator/venues/", "Miejsca zawodów"),
        (f"/coordinator/stages/{stage.pk}/logistics/", "Przyjazdy i potrzeby"),
        (f"/coordinator/stages/{stage.pk}/attendance/", "Obecność"),
        ("/coordinator/institutions/", "Słownik placówek"),
        ("/coordinator/institutions/import/", "Wgranie wykazu z pliku"),
        ("/coordinator/registration-profile/", "Profil rejestracji"),
    )


def first_step(second: Competition) -> PipelineStep:
    """Pierwszy krok toru drugiego konkursu, dopisany tak, jak robi to koordynator ekranem.

    ``create_competition`` zakłada etapy, ale **nie** zakłada kroków: ``PipelineStep`` powstaje
    z migracji ``competitions.0024_pipeline_from_stages`` dla edycji istniejących w chwili jej
    wykonania, a konkurs założony później dostaje pusty tor i pozycję „dopisz krok” na ekranie
    przebiegu. Scenariusz przechodzi tę samą drogą, bo to jest droga koordynatora nowego konkursu.

    Idempotentnie, bo woła to i sam przebieg po ekranach, i lista adresów, którą ten przebieg
    dostaje — a krok ma być jeden, tak jak po jednym kliknięciu w panelu.
    """
    existing = PipelineStep.objects.for_competition(second).order_by("position", "id").first()
    if existing is not None:
        return existing
    edition = Edition.objects.get(competition=second, is_current=True)
    step = PipelineStep(edition=edition, stage=first_stage(second), position=1)
    step.full_clean()
    step.save()
    return step


# --- (a) Konkurs #1 bez zmian ----------------------------------------------------------------


def test_competition_one_public_pages_answer_next_to_the_second_competition(
    panel, second, client_for, competition
):
    """Strony publiczne Konkursu #1 odpowiadają tak samo, gdy obok stoi konkurs z flagami."""
    anon = client_for(competition)

    assert anon.get("/").status_code == 200
    assert anon.get("/register/").status_code == 200
    assert anon.get("/status.json").status_code == 200


def test_competition_one_registration_form_keeps_its_consent_set(client_for, competition, second):
    """Punkt 14 listy § 0.5 sprawdzony **obok** drugiego konkursu ze zgodami z bazy.

    Drugi konkurs ma ``per_competition_consents`` zapalone, Konkurs #1 nie — i to jest cały
    przedmiot tej asercji: zestaw zgód w formularzu Konkursu #1 nie zależy od tego, co robi
    konkurs stojący obok.
    """
    from apps.accounts.consents import CONSENT_FIELD_NAMES

    response = client_for(competition).get("/register/")
    fields = [name for name in response.context["form"].fields if name in CONSENT_FIELD_NAMES]

    assert tuple(fields) == CONSENT_FIELD_NAMES


def test_competition_one_coordinator_menu_has_no_stage_two_items(panel, second):
    """Menu koordynatora Konkursu #1 nie zyskuje ani jednego **odnośnika** z flag konkursu drugiego.

    Porównujemy adresy, a nie napisy: „Obecność” jest zarazem początkiem dzisiejszej pozycji
    „Obecność na warsztatach”, więc asercja po napisie mówiłaby o przypadkowym podciągu, a nie
    o pozycji menu. Adres jest tym, co odróżnia ekran od ekranu.
    """
    response = panel.get("/coordinator/")
    content = response.content.decode()

    assert response.status_code == 200
    for address, _ in screens(second):
        assert f'href="{address}"' not in content, f"Odnośnik {address} pojawił się w menu Konkursu #1"


# --- (b) ekrany drugiego konkursu -------------------------------------------------------------


def test_the_second_competition_answers_under_its_path_prefix(panel, second):
    """Prefiks ścieżki rozstrzyga konkurs: pulpit pod ``/druga/`` opisuje edycję konkursu drugiego.

    Przedmiotem jest **rozstrzygnięcie**, a nie marka strony głównej: pod prefiksem ścieżki
    ``Site.find_for_request`` dopasowuje nadal witrynę platformy (host się nie zmienił), więc
    drzewo stron CMS jest drzewem platformy. Rozstrzyganie konkursu idzie osobną drogą
    (``apps.tenancy.resolution``) i to ono decyduje o danych zawodów — patrz raport T43.
    """
    response = panel.get(path("/coordinator/"))

    assert response.status_code == 200
    assert response.context["edition"].competition_id == second.pk
    # Adresy złożone przez ``reverse`` w tym żądaniu niosą prefiks (``set_script_prefix``),
    # inaczej każdy odnośnik w panelu wracałby do Konkursu #1.
    assert f'href="/{SECOND_PREFIX}/coordinator/' in response.content.decode()


def test_every_flagged_screen_of_the_second_competition_answers(panel, second):
    """Komplet ekranów wydań E–K: 200 i napis, po którym poznaje go człowiek."""
    first_step(second)
    failures = []
    for address, label in screens(second):
        response = panel.get(path(address))
        if response.status_code != 200:
            failures.append(f"{address}: HTTP {response.status_code}")
        elif label not in response.content.decode():
            failures.append(f"{address}: brak napisu {label!r}")

    assert not failures, "; ".join(failures)


def test_the_rules_preview_answers_without_saving_anything(panel, second):
    """Podgląd reguły przejścia (``POST …/rules/preview/``) liczy skład i **nic nie zapisuje**.

    Podgląd jest jedynym miejscem edytora, w którym koordynator widzi skutek reguły przed jej
    zapisaniem, więc scenariusz przechodzi przez niego jawnie — a asercja na liczbie reguł
    pilnuje, żeby „podgląd” nie zaczął kiedyś zapisywać.
    """
    step = first_step(second)
    # Krok z ``create_competition`` niesie już regułę z progu kwalifikacji szablonu
    # (``apps.competitions.pipeline.ensure_pipeline``), więc pilnujemy różnicy, nie zera.
    rules_before = step.transition_rules.count()

    response = panel.post(
        path(f"/coordinator/pipeline/{step.pk}/rules/preview/"),
        {"mode": TransitionMode.HYBRID, "group_by": "", "min_points": "60", "top_n": "20", "position": "1"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    assert "Reguły przejścia" in response.content.decode()
    assert step.transition_rules.count() == rules_before


def test_the_institution_import_preview_writes_nothing(panel, second):
    """Wgranie wykazu „na sucho”: podgląd liczy wiersze i nie zakłada ani jednej placówki."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.schools.models import CustomInstitution

    csv = b"nazwa,miejscowosc\nLiceum Testowe,Warszawa\n"
    response = panel.post(
        path("/coordinator/institutions/import/"),
        {"file": SimpleUploadedFile("wykaz.csv", csv, content_type="text/csv")},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    assert "Wgranie wykazu z pliku" in response.content.decode()
    assert not CustomInstitution.objects.for_competition(second).exists()


# --- (b2) te same adresy w Konkursie #1 ---------------------------------------------------------


def test_the_same_addresses_are_404_in_competition_one(panel, second):
    """Reguła § 2.1: adres ekranu za flagą **nie istnieje** w konkursie, który jej nie ma.

    404, a nie 403: rola koordynatora jest tu ta sama osoba (grupa Django jest globalna), więc
    odpowiedź mówi wyłącznie o tym, czego w tej instalacji nie ma — a nie o tym, czego komuś nie
    wolno. Gdyby ekran oddawał 403, konfiguracja jednego konkursu byłaby czytelna z drugiego.
    """
    first_step(second)
    failures = []
    for address, _ in screens(second):
        # Adresy etapowe i krokowe niosą identyfikator obiektu **drugiego** konkursu, więc
        # w Konkursie #1 są 404 z dwóch powodów naraz (wyłączona flaga i zawężony queryset).
        # To jest w porządku: reguła brzmi „404”, a nie „404 z tego konkretnego powodu”.
        status = panel.get(address).status_code
        if status != 404:
            failures.append(f"{address}: HTTP {status}")

    assert not failures, "; ".join(failures)


def test_a_participant_screen_of_the_second_competition_is_404_from_the_first(panel, second, competition):
    """Obiekt cudzego konkursu daje 404 także wtedy, gdy ekran istnieje w obu (§ 3.6)."""
    theirs = ParticipantFactory(competition=second)

    assert panel.get(f"/coordinator/participants/{theirs.pk}/").status_code == 404
    assert panel.get(path(f"/coordinator/participants/{theirs.pk}/")).status_code == 200


# --- (c) izolacja uczestników -------------------------------------------------------------------


def test_participants_of_the_two_competitions_are_invisible_to_each_other(panel, second, competition):
    """Lista kont koordynatora pokazuje **wyłącznie** uczestników swojego konkursu.

    Obie strony naraz, bo wyciek jest kierunkowy: zakresowanie zepsute w jedną stronę
    przechodziłoby test sprawdzający tylko drugą.
    """
    ours = ParticipantFactory(competition=competition)
    theirs = ParticipantFactory(competition=second)

    first = panel.get("/coordinator/accounts/?role=participant").content.decode()
    other = panel.get(path("/coordinator/accounts/?role=participant")).content.decode()

    assert ours.user.email in first
    assert theirs.user.email not in first
    assert theirs.user.email in other
    assert ours.user.email not in other


def test_public_codes_come_from_the_competition_row_of_the_profile(panel, second, competition):
    """Kod publiczny bierze prefiks z **wiersza konkursu** profilu, a nie ze stałej modułu.

    Prefiks ``OLM-`` Konkursu #1 jest zamrożony (§ 0.2 punkt 8) i to jest tu pierwsza asercja.
    Druga jest o konkursie drugim: ``create_competition`` **nie** nadaje mu własnego prefiksu
    (pole ma wartość domyślną ``OLM-``), więc obie olimpiady mogą dziś wydawać kody wyglądające
    tak samo. Unikalność kodu jest per konkurs (``accounts_participant_public_code_per_competition``),
    więc nie jest to kolizja danych — jest to kwestia czytelności listy i należy do operatora,
    który prefiks wpisuje w ``/admin/``. Test zapisuje **dzisiejszy** stan, żeby zmiana tej
    decyzji nie przeszła po cichu.
    """
    ours = ParticipantFactory(competition=competition)
    theirs = ParticipantFactory(competition=second)

    assert ours.public_code.startswith(competition.public_code_prefix)
    assert theirs.public_code.startswith(second.public_code_prefix)
    assert ours.public_code != theirs.public_code

    second.public_code_prefix = "DRU-"
    second.save(update_fields=["public_code_prefix"])
    assert ParticipantFactory(competition=second).public_code.startswith("DRU-")
