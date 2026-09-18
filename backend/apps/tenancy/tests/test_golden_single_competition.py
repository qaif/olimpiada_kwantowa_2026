"""Testy złote: przy **jednym** konkursie serwis odpowiada tak, jak odpowiadał przed zmianą.

Kryterium przyjęcia całego etapu 1 brzmi „nic nie ruszyliśmy” (``docs/UNIWERSALNY-ETAP-1.md``
§ 0.4) i te testy są jego zapisem. Różnica wobec reszty pakietu: świat nie jest tu zbudowany pod
jedno pytanie, tylko odtwarza **kształt produkcji** (``golden.py``) – edycję z czterema etapami,
komplet stron, konta wszystkich ról, prace w różnych stanach, recenzje, wyniki i reklamację.
Dopiero na takim świecie „strona główna się renderuje” znaczy to samo, co na produkcji.

Testy chodzą przez klienta HTTP pod domeną Konkursu #1 (``client_for``), bo od wprowadzenia
wielokonkursowości **host jest częścią wejścia**: żądanie bez hosta konkursu przechodziłoby przez
odwrót do witryny domyślnej i sprawdzałoby inną ścieżkę niż ta, którą chodzi produkcja.
"""

from __future__ import annotations

import inspect
import re

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.core.management import call_command
from wagtail.models import Locale, Page

from apps.accounts.consents import CONSENT_FIELD_NAMES
from apps.accounts.models import GROUP_COORDINATOR
from apps.cms.tests.test_cms_scope import _snapshot as cms_permission_snapshot
from apps.submissions.models import Submission
from apps.tenancy.models import FEATURE_DEFAULTS
from apps.tenancy.tests.golden import build_golden, file_appeal, publish_results
from apps.web.urls_consents import urlpatterns as consent_urlpatterns
from apps.web.urls_documents import urlpatterns as document_urlpatterns
from apps.web.urls_fees import urlpatterns as fee_urlpatterns
from apps.web.urls_institutions import urlpatterns as institution_urlpatterns
from apps.web.urls_pipeline import urlpatterns as pipeline_urlpatterns
from apps.web.urls_regions import urlpatterns as region_urlpatterns
from apps.web.urls_scoring import urlpatterns as scoring_urlpatterns

pytestmark = pytest.mark.django_db


@pytest.fixture
def golden(competition):
    """Świat w kształcie produkcji. Jedna fikstura, bo świat ma sens wyłącznie w całości."""
    return build_golden(competition)


@pytest.fixture
def anon(client_for, competition):
    return client_for(competition)


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- strony publiczne -------------------------------------------------------------------------


def test_home_page_renders_with_the_current_edition_label(anon, golden):
    """Punkt 1 listy kontrolnej produkcji (§ 0.3): strona główna z etykietą bieżącej edycji."""
    response = anon.get("/")

    assert response.status_code == 200
    assert response.context["site_edition_label"] == golden.edition.year_label


def test_timeline_strip_shows_the_stages_of_the_current_edition(anon, golden):
    """Punkt 2 listy kontrolnej: pasek osi czasu z etapami bieżącej edycji i wydarzeniami."""
    content = anon.get("/").content.decode()

    assert "timeline-strip" in content
    assert golden.elim.display_name in content
    assert "Gala finałowa" in content


def test_registration_page_asks_for_exactly_the_documented_consents(anon, golden):
    """Punkt 4 listy kontrolnej: komplet zgód ze stałej ``CONSENTS``, w tej samej kolejności.

    Porównujemy z listą pól, a nie z liczbą: zgoda, która zniknie z formularza, ma zatrzymać
    wdrożenie, bo brak pola to brak oświadczenia, a nie kosmetyka układu.
    """
    response = anon.get("/register/")
    fields = [name for name in response.context["form"].fields if name in CONSENT_FIELD_NAMES]

    assert response.status_code == 200
    assert tuple(fields) == CONSENT_FIELD_NAMES


def test_published_results_render_for_anonymous_readers(anon, golden):
    """Punkt 9 listy kontrolnej: ogłoszona tabela wyników, z tą samą anonimizacją."""
    publication = publish_results(golden)

    response = anon.get(f"/results/{publication.stage_id}/")
    content = response.content.decode()

    assert response.status_code == 200
    for participant in golden.participants:
        assert participant.public_code in content
    # Anonimizacja kodem znaczy: kod **zamiast** nazwiska, a nie kod obok nazwiska.
    assert golden.participants[0].user.last_name not in content


def test_status_json_keeps_its_contract(anon, golden):
    """Punkt 10 listy kontrolnej: kształt ``/status.json`` jest kontraktem dla monitoringu."""
    payload = anon.get("/status.json").json()

    assert set(payload) == {
        "status",
        "time",
        "version",
        "services",
        "backup_last_ok",
        "backup_last_verified",
        "registration_open",
        "edition",
        "stage",
        "stage_deadline",
        "announcements",
        # Jedyny klucz dopisany w etapie 2 (§ 1.7.3, zadanie T41): „czy instalacja czeka jeszcze
        # na kreator ``/setup/``”. Pozostałe jedenaście zostaje bez zmian i w tej samej kolejności.
        "setup_pending",
    }
    assert payload["edition"] == golden.edition.year_label
    # Konkurs #1 jest skonfigurowany, więc kreatora nie ma – to ta sama odpowiedź, co 404 na
    # ``/setup/`` z punktu 22 listy kontrolnej § 0.5.
    assert payload["setup_pending"] is False


def test_documents_section_and_partners_page_answer(anon, golden):
    """Punkt 3 listy kontrolnej: sekcja dokumentów i jej podstrony pod produkcyjnymi adresami."""
    assert anon.get("/dokumenty/").status_code == 200
    assert anon.get("/dokumenty/regulamin/").status_code == 200
    assert anon.get("/dokumenty/rodo/").status_code == 200
    assert anon.get("/partnerzy/").status_code == 200


# --- panele -----------------------------------------------------------------------------------


def test_participant_panel_renders_for_a_participant(client_for, competition, golden):
    """Punkt 6 listy kontrolnej: uczestnik wchodzi na swój panel – bez 403 i bez 404."""
    participant = golden.participants[0]
    client = logged_in(client_for, competition, participant.user)

    response = client.get("/me/")

    assert response.status_code == 200
    assert golden.elim.display_name in response.content.decode()


def test_reviewer_queue_renders_for_a_reviewer(client_for, competition, golden):
    client = logged_in(client_for, competition, golden.reviewer.user)

    response = client.get("/review/")

    assert response.status_code == 200


def test_coordinator_dashboard_renders_with_attention_counters(client_for, competition, golden):
    """Punkt 7 listy kontrolnej: pulpit koordynatora razem z licznikami „co wymaga uwagi”."""
    client = logged_in(client_for, competition, golden.coordinator)

    response = client.get("/coordinator/")

    assert response.status_code == 200
    assert response.context["edition"] == golden.edition
    assert response.context["attention"]


def test_coordinator_dashboard_lists_every_stage_of_the_edition(client_for, competition, golden):
    """Punkt 8 listy kontrolnej: komplet etapów edycji – razem z treningowym.

    Karty etapów stoją na pulpicie, a nie pod osobnym adresem ``/coordinator/stages/``: ten
    ostatni jest w serwisie wyłącznie prefiksem adresów szczegółowych. Test pilnuje liczby
    i tożsamości kart, bo etap, który wypadnie z pulpitu, znika koordynatorowi z oczu.
    """
    client = logged_in(client_for, competition, golden.coordinator)

    rows = client.get("/coordinator/").context["stage_rows"]

    assert {row["stage"].pk for row in rows} == {
        golden.training.pk,
        golden.elim.pk,
        golden.district.pk,
        golden.final.pk,
    }


def test_appeals_queue_renders_for_the_appeals_committee(client_for, competition, golden):
    """Właściwa rola widzi swoją kolejkę. Reguła „zła rola = 403” jest przedmiotem testu niżej."""
    appeal = file_appeal(golden)
    client = logged_in(client_for, competition, golden.appeals_member.user)

    response = client.get("/appeals/")

    assert response.status_code == 200
    assert str(appeal.pk) in response.content.decode()


# --- reguła 403 kontra 404 ---------------------------------------------------------------------


def test_participant_asking_for_the_coordinator_panel_gets_403_not_404(client_for, competition, golden):
    """Zła **rola** to 403, nie 404 – różnicę opisuje § 3.6 i ona się w tym etapie nie zmienia.

    404 znaczy „nie ma tego tutaj” i jest odpowiedzią na pytanie o **cudzy konkurs**. Panel
    koordynatora istnieje w tym konkursie i uczestnik ma prawo o tym wiedzieć; czego nie ma, to
    jego uprawnienia.
    """
    client = logged_in(client_for, competition, golden.participants[0].user)

    assert client.get("/coordinator/").status_code == 403


def test_anonymous_visitor_is_redirected_to_login(anon, golden):
    response = anon.get("/me/")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


# --- zapytania domeny ---------------------------------------------------------------------------


def test_coordinator_sees_every_submission_of_the_competition(golden, competition):
    """``for_user`` koordynatora oddaje **wszystkie** prace – po zakresowaniu: wszystkie swoje.

    Argument ``competition`` podajemy wtedy, gdy metoda już go przyjmuje: zadanie T3 dokłada go
    jako **wymagany** (``for_user(user, competition)``), żeby nie dało się wywołać jej po staremu
    i dostać cudzych danych (§ 3.5). Most jest tu, bo ten test opisuje regułę, która się nie
    zmienia („koordynator widzi wszystko swoje”), a nie sygnaturę, która się zmienia.
    """
    signature = inspect.signature(Submission.objects.for_user)
    arguments = (
        (golden.coordinator, competition) if "competition" in signature.parameters else (golden.coordinator,)
    )

    visible = Submission.objects.for_user(*arguments)

    assert set(visible) == set(Submission.objects.all())


# =================================================================================================
# Lista kontrolna produkcji etapu 2 (``docs/UNIWERSALNY-ETAP-2.md`` § 0.5), zadanie T43
# =================================================================================================
#
# Lista § 0.5 ma dwadzieścia dwie pozycje i część z nich jest z definicji ręczna: „snapshot
# identyczny co do bajtu z kopią sprzed wdrożenia” wymaga kopii sprzed wdrożenia, a „ten sam numer
# dyplomu” – dyplomu wystawionego przed nim. Te pozycje zostają w ``docs/OPERACJE.md`` § 8 jako
# komendy dla operatora. Tutaj stoi **reszta**: wszystko, co da się zapisać jako asercja, żeby
# regresja zatrzymała się na CI, a nie na kimś czytającym listę o 23:00 w dniu wdrożenia.
#
# Każdy test z tej sekcji jest sprawdzeniem stanu **Konkursu #1**, a nie zdolności platformy.
# Dlatego stoją w złotym pliku, a nie przy funkcjach, których dotyczą: przedmiotem jest zdanie
# „u Olimpiady Kwantowej nic się nie zmieniło”, a nie „mechanizm X działa”.


#: Przełączniki, które **mają** być domyślnie włączone, bo opisują dzisiejsze zachowanie serwisu,
#: a nie nową zdolność: rola opiekuna szkolnego, procedura odwoławcza i dyplomy istnieją
#: w Olimpiadzie Kwantowej od pierwszej edycji (``apps/tenancy/models.py``, komentarz przy
#: ``FEATURE_DEFAULTS``). Każda pozostała pozycja katalogu – także ta, której pierwszy czytelnik
#: powstanie za kilka wydań – ma być ``False``.
FLAGS_ON_BY_DESIGN = frozenset({"supervisor_role", "appeals", "certificates"})

#: Listy wzorców wydań E–K. Ekran za flagą ma **nie mieć adresu** w menu konkursu, który tej flagi
#: nie ma, więc porównujemy nie napisy, tylko adresy – napis „Obecność” jest zarazem początkiem
#: dzisiejszej pozycji „Obecność na warsztatach”.
STAGE_TWO_PATTERNS = (
    consent_urlpatterns,
    document_urlpatterns,
    region_urlpatterns,
    institution_urlpatterns,
    pipeline_urlpatterns,
    scoring_urlpatterns,
    fee_urlpatterns,
)

#: Kolejność kluczy ``/status.json``. § 2.5: „jeden nowy klucz ``setup_pending``; pozostałe
#: jedenaście bez zmian, **w tej samej kolejności**”. Monitoring czyta ten dokument skryptem,
#: a skrypt czytający pozycję zamiast nazwy jest w świecie rzeczywisty – porównanie zbiorów
#: (test wyżej) przepuściłoby przestawienie.
EXPECTED_STATUS_KEYS = (
    "status",
    "time",
    "version",
    "services",
    "backup_last_ok",
    "backup_last_verified",
    "registration_open",
    "edition",
    "stage",
    "stage_deadline",
    "announcements",
    "setup_pending",
)


def stage_two_links() -> dict[str, re.Pattern[str]]:
    """Adresy ekranów wydań E–K jako wzorce odnośnika, po jednym na wzorzec adresu.

    Konwerter zamieniamy na „jeden segment” (``<int:stage_id>`` → ``[^/"]+``), a nie ucinamy
    adresu na pierwszym konwerterze: ``coordinator/stages/<id>/components/`` ucięte do
    ``/coordinator/stages/`` trafiałoby w **dzisiejsze** ekrany etapu i test alarmowałby o menu,
    którego nikt nie zmienił.
    """
    links = {}
    for patterns in STAGE_TWO_PATTERNS:
        for pattern in patterns:
            route = str(pattern.pattern)
            if not route.startswith("coordinator/"):
                continue
            links["/" + route] = re.compile('href="/' + re.sub(r"<[^>]+>", '[^/"]+', route) + '"')
    return links


def rendered_menu(content: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Menu **wyrenderowanej** strony panelu jako pary „sekcja → pozycje”.

    Czytamy HTML, a nie strukturę z ``coordinator_nav.groups`` (to porównuje
    ``apps/web/tests/test_coordinator_nav_flags.py``): punkt listy kontrolnej brzmi „koordynator
    otwiera panel i widzi to, co widział”, więc przedmiotem jest to, co naprawdę wyszło
    z szablonu.
    """
    sections = re.findall(
        r'panel-nav__heading-label">([^<]*)</span>.*?<ul class="panel-nav__list">(.*?)</ul>',
        content,
        flags=re.DOTALL,
    )
    return tuple(
        (label.strip(), tuple(item.strip() for item in re.findall(r'panel-nav__label">([^<]*)<', body)))
        for label, body in sections
    )


def row_counts() -> dict[str, int]:
    """Liczba wierszy każdej tabeli instalacji, po etykiecie modelu.

    ``_base_manager``, a nie ``objects``: menedżery zakresowane konkursem
    (``competition_scoped_manager``) filtrują po kontekście, a tu pytamy o **tabelę**, nie o widok
    konkursu. Pytanie o widok dałoby test, który przechodzi, bo nowe wiersze powstały w konkursie,
    którego akurat nie ma w kontekście.
    """
    return {
        model._meta.label: model._base_manager.count()
        for model in django_apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }


@pytest.fixture
def no_seeds(monkeypatch):
    """``create_competition`` bez ``seed_schools`` – wykaz SIO to kilka tysięcy cudzych wierszy.

    Ten sam zabieg, co w ``apps/tenancy/tests/test_create_competition.py``. Dla testu próby na
    sucho jest dodatkowo **konieczny**: ``safe_seeds`` chodzą poza transakcją komendy (i tak ma
    być, bo są globalne), więc ich wiersze byłyby różnicą w liczniku tabel, której ``--dry-run``
    nie obiecuje wycofać.
    """
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: None,
    )


def test_every_stage_two_flag_is_off_for_competition_one(competition):
    """Katalog flag: wszystko ``False`` poza trzema, które opisują dzisiejszy stan (§ 0.6).

    Test czyta ``has_feature``, a nie ``FEATURE_DEFAULTS``: przedmiotem jest odpowiedź, którą
    dostanie kod pytający o flagę dla **tego** konkursu, a ona składa się z wartości domyślnej
    i z tego, co ktoś wpisał wierszowi.
    """
    on = {name for name in FEATURE_DEFAULTS if competition.has_feature(name)}

    assert on == FLAGS_ON_BY_DESIGN
    # Druga strona tej samej prawdy: katalog nie zgubił żadnej z trzech pozycji włączonych
    # z założenia (inaczej zbiory byłyby równe, bo oba puste).
    assert FLAGS_ON_BY_DESIGN <= set(FEATURE_DEFAULTS)


def test_competition_one_carries_no_feature_flags_of_its_own(competition):
    """Konkurs #1 ma w bazie ``feature_flags`` **puste** albo wyłącznie flagi etapu 1 (§ 0.6).

    Wpis etapu 2 w tym polu byłby przełączeniem funkcji u działającej olimpiady i ma zatrzymać
    wdrożenie – niezależnie od tego, czy przełącznik akurat coś widocznego zmienia.
    """
    stage_one = {"path_prefix_routing", "memberships_enforced", "competition_settings_page"}

    assert set(competition.feature_flags or {}) <= stage_one


def test_the_rendered_coordinator_menu_has_no_address_of_a_flagged_screen(client_for, competition, golden):
    """Punkt § 2.1: menu konkursu z domyślnymi przełącznikami nie prowadzi do ekranu za flagą.

    Sekcje porównujemy co do nazwy i kolejności (osiem, tak jak dziś – § 2.2 „etap 2 nie dokłada
    ani jednej nowej sekcji”), a pozycje – co do adresu. Pozycja prowadząca do 404 byłaby gorsza
    niż jej brak, a pozycja prowadząca do **działającego** ekranu, którego organizator nie
    zamawiał, byłaby złamaniem § 0.1.
    """
    client = logged_in(client_for, competition, golden.coordinator)

    content = client.get("/coordinator/").content.decode()

    assert [section for section, _ in rendered_menu(content)] == [
        "Pulpit",
        "Etapy",
        "Ocenianie",
        "Uczestnicy i konta",
        "Komitet",
        "Komunikacja",
        "Raporty",
        "Ustawienia",
    ]
    found = sorted(address for address, link in stage_two_links().items() if link.search(content))
    assert not found, f"Menu Konkursu #1 prowadzi do ekranów za flagą: {found}"


def test_status_json_keeps_the_order_of_its_keys(anon, golden):
    """Punkt 10 listy etapu 1, zaostrzony przez § 2.5: kolejność kluczy też jest kontraktem."""
    payload = anon.get("/status.json").json()

    assert tuple(payload) == EXPECTED_STATUS_KEYS


def test_the_installation_of_competition_one_still_has_a_single_locale(golden):
    """Punkty 20–21 listy § 0.5: jeden język treści, więc żadnego przycisku „Translate” i żadnego ``/pl/``.

    Świat złoty ma komplet stron (drzewo, dokumenty, partnerzy, wyniki), więc jest to zarazem
    sprawdzenie, że **żadna** z nich nie powstała w drugiej lokalizacji – a to jest jedyny sposób,
    w jaki drugi ``Locale`` mógłby wejść niezauważony.
    """
    assert Locale.objects.count() == 1
    assert Page.objects.exclude(locale=Locale.objects.get()).count() == 0


def test_the_coordinator_cms_permissions_survive_a_second_competition(competition, no_seeds):  # noqa: ARG001
    """Punkt 20 listy § 0.5: uprawnienia grupy ``coordinator`` w ``/cms/`` **równe** przed i po.

    Założenie drugiego konkursu zakłada mu własną grupę redakcyjną ``cms:<slug>`` i własną
    kolekcję mediów (§ 1.1.5). Test porównuje trzy zbiory grupy globalnej – uprawnienia, prawa do
    stron i prawa do kolekcji – bo migracja ``cms.0003`` ma zostać nietknięta, a nowa grupa ma
    wyłącznie **dokładać**. Różnica w którąkolwiek stronę znaczy, że koordynator Olimpiady
    Kwantowej po wdrożeniu widzi w ``/cms/`` co innego niż przed nim.

    Pomocnik jest importowany z ``apps/cms/tests/test_cms_scope.py``, a nie przepisany: dwie
    definicje „kompletu uprawnień grupy” rozjechałyby się przy pierwszym dołożeniu rodzaju prawa,
    a objawem byłby zielony test niezmienności obok czerwonej produkcji.
    """
    coordinator = Group.objects.get(name=GROUP_COORDINATOR)
    before = cms_permission_snapshot(coordinator)

    call_command(
        "create_competition",
        slug="kontrolny",
        name="Olimpiada Kontrolna",
        domain="kontrolna.test",
        from_template="przedmiotowa",
    )

    assert cms_permission_snapshot(Group.objects.get(name=GROUP_COORDINATOR)) == before
    # Bez tego test byłby pusty na instalacji, na której ``cms.0003`` nic nie zdążyła wpisać.
    assert before["permissions"] and before["pages"] and before["collections"]


def test_create_competition_dry_run_leaves_every_table_untouched(competition, no_seeds):  # noqa: ARG001
    """Próba na sucho nie zostawia śladu w **żadnej** tabeli – liczniki przed i po, model po modelu.

    ``apps/tenancy/tests/test_create_competition.py`` liczy trzy tabele, bo pyta o konkurs,
    witrynę i strony. Tutaj pytanie jest inne i jest pytaniem operatora z § 0.5: „uruchomiłem
    ``--dry-run`` na produkcji, czy na pewno nic się nie stało”. Odpowiedź „nic” musi obejmować
    także tabele, o których nikt nie pomyślał – definicje zgód, szablony dokumentów, regiony,
    grupy, kolekcje mediów i dziennik audytu.
    """
    before = row_counts()

    call_command(
        "create_competition",
        slug="na-sucho",
        name="Olimpiada Na Sucho",
        domain="na-sucho.test",
        from_template="przedmiotowa",
        dry_run=True,
    )

    after = row_counts()
    changed = {label: (before[label], after[label]) for label in before if before[label] != after[label]}
    assert not changed, f"Próba na sucho zmieniła liczbę wierszy: {changed}"
