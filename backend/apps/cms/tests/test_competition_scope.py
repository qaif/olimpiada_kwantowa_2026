"""Część informacyjna w instalacji wielokonkursowej: co czyta czytelnik konkursu A.

Reguła, której pilnuje cały ten moduł, brzmi tak samo dla każdego z ekranów: **strona konkursu A
nie może pokazać ani jednej informacji konkursu B** – ani komunikatu organizatora, ani pozycji
menu, ani terminu etapu, ani tabeli wyników. Odwrotnie niż w panelach, gdzie regułą jest 404 na
cudzym obiekcie (``apps/tenancy/tests/test_isolation.py``), tutaj odpowiedzią jest **pustka**:
adres ``/wyniki/`` konkursu, który niczego jeszcze nie ogłosił, ma się otworzyć i powiedzieć
„wyników jeszcze nie ogłoszono”, a nie oddać wyniki sąsiada.

Dlaczego drugi konkurs dostaje w tych testach **bieżącą** edycję, a Konkurs #1 nie: do wydania D
więz ``competitions_edition_single_current`` jest globalny, więc bieżąca edycja w bazie może być
tylko jedna (§ 4.1). Układ jest więc najostrzejszy z możliwych i dokładnie odwzorowuje błąd, który
te zmiany zamykają: przed zakresowaniem ``current_edition()`` oddawało „jedyną bieżącą w bazie”,
czyli **cudzą**, i strona główna Olimpiady Kwantowej ogłaszałaby harmonogram drugiego konkursu.

Testy niezmienności Konkursu #1 (menu, nagłówek CSP, liczby zapytań) stoją osobno, w
``apps/tenancy/tests/test_invariants.py`` – tam jest ich miejsce, bo dotyczą całego serwisu,
a nie samej części informacyjnej.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone
from wagtail.models import GroupPagePermission, Page, Site

from apps.accounts.models import GROUP_COORDINATOR
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory
from apps.cms import analytics
from apps.cms.announcements import active_announcements, cached_announcements, reset_cache
from apps.cms.calendar import participant_calendar
from apps.cms.context_processors import cms_menu
from apps.cms.models import (
    Announcement,
    ContentPage,
    HomePage,
    NewsIndexPage,
    ProblemsPage,
    ResultsPage,
    SiteSettings,
)
from apps.cms.tests.factories import AnnouncementFactory
from apps.cms.timeline import stage_rows, timeline_events, timeline_strip
from apps.cms.workshops import WORKSHOPS_SLUG, workshops_page
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.results.tests.factories import ResultsPublicationFactory
from apps.tenancy.models import RoutingMode

pytestmark = pytest.mark.django_db

#: Napisy szukane w treści odpowiedzi. Każdy jest na tyle nietypowy, że trafienie go w HTML-u nie
#: może być przypadkiem – to jest cała różnica między testem izolacji a testem, który przechodzi,
#: bo szukanego słowa nie ma nigdzie.
OTHER_EDITION_LABEL = "Rocznik obcego konkursu 2031/2032"
OTHER_ANNOUNCEMENT = "Przerwa techniczna u obcego organizatora"
OWN_ANNOUNCEMENT = "Przedłużamy termin do piątku"


# --- świat drugiego konkursu ---------------------------------------------------------------------


@pytest.fixture
def other_home(other_competition) -> HomePage:
    """Drzewo stron drugiego konkursu, ułożone tak, jak układa je produkcja.

    Korzeniem witryny jest ``HomePage`` na **drugim** poziomie drzewa – dokładnie tak, jak po
    migracji ``cms.0002`` u Konkursu #1. To nie jest kosmetyka: od głębokości zależy
    ``CMSPage.is_second_level()``, czyli walidacja slugów zarezerwowanych, a od korzenia witryny –
    adresy stron (``/zadania/``, a nie ``/cokolwiek/zadania/``).

    Fikstura ``other_competition`` daje witrynie zwykłą stronę-korzeń bez treści; tutaj podmieniamy
    ją na stronę główną i dokładamy sekcje, których dotyczą testy niżej. ``Site.save()`` czyści
    pamięć ścieżek korzeni Wagtaila, więc adresy działają od razu.
    """
    tree_root = Page.objects.filter(depth=1).order_by("path").first()
    home = tree_root.add_child(instance=HomePage(title="Druga olimpiada", slug="druga-olimpiada"))
    site = other_competition.site
    site.root_page = home
    site.save()
    home.add_child(instance=NewsIndexPage(title="Ogłoszenia", slug="ogloszenia", show_in_menus=True))
    home.add_child(instance=ProblemsPage(title="Zadania drugiej", slug="zadania", show_in_menus=True))
    home.add_child(instance=ResultsPage(title="Wyniki drugiej", slug="wyniki", show_in_menus=True))
    return home


@pytest.fixture
def other_edition(other_competition):
    """Bieżąca edycja **drugiego** konkursu razem z otwartym etapem.

    Jedyna bieżąca edycja w bazie (patrz docstring modułu) i należy do konkursu, którego
    czytelnik Konkursu #1 nie ma prawa zobaczyć.
    """
    edition = CurrentEditionFactory(competition=other_competition, year_label=OTHER_EDITION_LABEL)
    StageFactory(competition=other_competition, edition=edition, kind=StageKind.ELIM)
    return edition


# --- 1. komunikaty organizatora --------------------------------------------------------------


def test_the_banner_of_one_competition_does_not_render_on_the_other(
    client_for, competition, other_competition, other_home
):
    """Baner wisi na każdej stronie serwisu, więc komunikat globalny byłby komunikatem cudzym."""
    AnnouncementFactory(competition=competition, text=OWN_ANNOUNCEMENT)
    AnnouncementFactory(competition=other_competition, text=OTHER_ANNOUNCEMENT)

    own = client_for(competition).get("/").content.decode()
    other = client_for(other_competition).get("/").content.decode()

    assert OWN_ANNOUNCEMENT in own
    assert OTHER_ANNOUNCEMENT not in own
    assert OTHER_ANNOUNCEMENT in other
    assert OWN_ANNOUNCEMENT not in other


def test_active_announcements_are_scoped_to_the_competition(competition, other_competition):
    own = AnnouncementFactory(competition=competition, text=OWN_ANNOUNCEMENT)
    AnnouncementFactory(competition=other_competition, text=OTHER_ANNOUNCEMENT)

    assert active_announcements(competition) == [own]
    assert [item.text for item in active_announcements(other_competition)] == [OTHER_ANNOUNCEMENT]


def test_the_cache_does_not_serve_one_competition_the_answer_of_another(competition, other_competition):
    """Wspólny klucz bufora znaczyłby, że izolacja zależy od tego, kto pierwszy wszedł na serwis."""
    AnnouncementFactory(competition=competition, text=OWN_ANNOUNCEMENT)

    # Kolejność ma znaczenie: pierwszy odczyt zapisuje wpis, drugi musi go **minąć**.
    warmed = cached_announcements(competition)

    assert [item.text for item in warmed] == [OWN_ANNOUNCEMENT]
    assert cached_announcements(other_competition) == []


def test_saving_an_announcement_clears_the_memory_of_every_competition(competition, other_competition):
    """Zapis unieważnia całość, bo komunikat da się przepiąć między konkursami."""
    cached_announcements(competition)
    cached_announcements(other_competition)

    AnnouncementFactory(competition=other_competition, text=OTHER_ANNOUNCEMENT)

    assert cached_announcements(competition) == []
    assert [item.text for item in cached_announcements(other_competition)] == [OTHER_ANNOUNCEMENT]


def test_a_new_announcement_belongs_to_the_competition_of_the_context(competition, as_competition):
    """Wartość domyślna jest w modelu, bo komunikat zapisują trzy różne ekrany (``save()``)."""
    with as_competition(competition):
        row = AnnouncementFactory.build(text="Bez wskazania właściciela")
        row.competition = None
        row.save()

    assert row.competition == competition


def test_the_announcement_cache_is_cleared_between_competitions_by_key(competition):
    """Klucz „bez konkursu” jest osobny – żądanie spod nieznanego hosta nie zatruwa cudzego."""
    from apps.cms.announcements import cache_key

    assert cache_key(None) != cache_key(competition)


# --- 2. analityka i nagłówek CSP ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_analytics_cache():
    """Pamięć analityki jest stanem procesu, a nie bazy – wycofanie transakcji jej nie czyści."""
    analytics.reset_cache()
    reset_cache()
    yield
    analytics.reset_cache()
    reset_cache()


def test_the_measurement_id_of_one_site_does_not_enable_analytics_on_the_other(
    competition, other_competition
):
    """Identyfikator GA4 wpisany przez jednego organizatora nie jest ustawieniem instalacji."""
    row = SiteSettings.for_site(other_competition.site)
    row.ga_measurement_id = "G-TESTTEST1"
    row.save()

    assert analytics.analytics_enabled(other_competition.site_id) is True
    assert analytics.analytics_enabled(competition.site_id) is False


def test_without_a_site_the_question_stays_the_installation_wide_one(other_competition):
    """Odwrót ``site_id=None`` odpowiada tak, jak przed zakresowaniem – i ma tak zostać.

    Warstwa CSP dokleja nagłówek także do odpowiedzi, dla których witryny nie da się rozstrzygnąć
    (plik statyczny, nieznany host). Nagłówek **węższy** od potrzeby zablokowałby tam analitykę,
    która dotąd działała.
    """
    row = SiteSettings.for_site(other_competition.site)
    row.ga_measurement_id = "G-TESTTEST1"
    row.save()

    assert analytics.analytics_enabled() is True


def test_the_request_helper_asks_about_the_site_of_that_request(rf, competition, other_competition, settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, ".invalid", ".test"]
    row = SiteSettings.for_site(other_competition.site)
    row.ga_measurement_id = "G-TESTTEST1"
    row.save()

    own = rf.get("/", HTTP_HOST=competition.primary_domain)
    other = rf.get("/", HTTP_HOST=other_competition.primary_domain)

    assert analytics.analytics_enabled_for_request(other) is True
    assert analytics.analytics_enabled_for_request(own) is False


# --- 3. menu, stopka i nazwa serwisu -----------------------------------------------------------


def test_the_menu_is_built_from_the_tree_of_the_site_of_the_request(
    client_for, competition, other_competition, other_home
):
    """Menu każdej witryny wciąż wynika z jej własnego drzewa – domek i ``MENU_ORDER`` (uwaga
    organizatora z 21.09.2026) tego nie zmieniają, tylko dokładają się do wyniku.

    ``MENU_ORDER`` dobiera pozycje po **slugu**, tak samo jak ``PRIMARY_MENU_SLUGS`` wcześniej –
    dlatego „Zadania drugiej” i „Wyniki drugiej” (slugi ``zadania``/``wyniki``) wyprzedzają
    w pasku drugiego konkursu „Ogłoszenia” (slug ``ogloszenia``, nieznany liście), mimo że
    w drzewie stoją za nim. To nie jest wyciek danych Konkursu #1 – to ta sama, jawna reguła
    zastosowana do slugów, które akurat się powtarzają.
    """
    own = client_for(competition).get("/").context["cms_menu"]
    other = client_for(other_competition).get("/").context["cms_menu"]

    assert [item["title"] for item in other] == [
        "Strona główna",
        "Zadania drugiej",
        "Wyniki drugiej",
        "Ogłoszenia",
    ]
    assert "Zadania drugiej" not in [item["title"] for item in own]
    # Menu Konkursu #1 zostaje dokładnie takie, jakie było w drzewie – to jest warunek § 0.
    # W nagłówku dziś stoi krócej: domek zamiast „Aktualności”, „Archiwum” organizator zdjął
    # z paska (``HIDDEN_MENU_SLUGS``); oba adresy działają dalej (patrz test niżej).
    assert [item["title"] for item in own] == ["Strona główna", "Zadania", "Wyniki"]


def test_a_site_without_a_page_tree_gets_an_empty_menu_not_the_one_of_competition_one(
    client_for, other_competition
):
    """Lista zapasowa opisuje drzewo Konkursu #1, więc pod cudzą domeną byłaby czterema martwymi
    odnośnikami – i to prowadzącymi do serwisu, o którym czytelnik nigdy nie słyszał."""
    response = client_for(other_competition).get("/nie-ma-takiej-strony/")

    assert response.context["cms_menu"] == []
    assert response.context["cms_menu_primary"] == []


def test_the_fallback_menu_still_stands_for_the_default_site(rf):
    """Świeża baza Konkursu #1 (drzewo jeszcze nie powstało) ma dostać menu zapasowe."""
    from apps.cms.context_processors import FALLBACK_MENU

    Page.objects.live().in_menu().update(show_in_menus=False)

    menu = cms_menu(rf.get("/"))["cms_menu"]

    assert [item["title"] for item in menu] == [item["title"] for item in FALLBACK_MENU]


def test_the_site_name_and_social_links_come_from_the_settings_of_that_site(
    client_for, competition, other_competition, other_home
):
    """``SiteSettings`` jest per witryna od ``cms.0005`` – ten test pilnuje, że tak zostaje."""
    row = SiteSettings.for_site(other_competition.site)
    row.site_name = "Druga Olimpiada Testowa"
    row.facebook_url = "https://example.invalid/druga"
    row.save()

    other = client_for(other_competition).get("/").content.decode()
    own = client_for(competition).get("/").content.decode()

    assert "Druga Olimpiada Testowa" in other
    assert "https://example.invalid/druga" in other
    assert "Druga Olimpiada Testowa" not in own
    assert "https://example.invalid/druga" not in own


# --- 4. strony czytające bieżącą edycję ---------------------------------------------------------


def test_the_home_page_of_one_competition_does_not_announce_the_edition_of_another(
    client_for, competition, other_competition, other_home, other_edition
):
    """Przed zakresowaniem ``current_edition()`` oddawało „jedyną bieżącą w bazie”, czyli cudzą."""
    own = client_for(competition).get("/")
    other = client_for(other_competition).get("/")

    assert own.context["edition"] is None
    assert own.context["stage_rows"] == []
    assert OTHER_EDITION_LABEL not in own.content.decode()
    assert other.context["edition"] == other_edition
    assert len(other.context["stage_rows"]) == 1


def test_the_problems_page_of_one_competition_does_not_show_the_stage_of_another(
    client_for, competition, other_competition, other_home, other_edition
):
    own = client_for(competition).get("/zadania/")
    other = client_for(other_competition).get("/zadania/")

    assert own.context["edition"] is None
    assert own.context["stage"] is None
    assert other.context["edition"] == other_edition
    assert other.context["stage"] is not None


def test_the_results_page_of_one_competition_does_not_list_the_publication_of_another(
    client_for, competition, other_competition, other_home, other_edition
):
    """Sekcja „Archiwum” wymienia **wszystkie** ogłoszone etapy, więc bez zawężenia wyliczałaby
    także roczniki drugiego organizatora – razem z odnośnikami do jego tabel."""
    stage = other_edition.stages.first()
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    ResultsPublicationFactory(
        competition=other_competition,
        stage=stage,
        snapshot=[{"rank": 1, "display": "OLM-OBCY01", "total": 30, "points": {}}],
    )

    own = client_for(competition).get("/wyniki/")
    other = client_for(other_competition).get("/wyniki/")

    assert own.context["tables"] == []
    assert own.context["archive"] == []
    assert "OLM-OBCY01" not in own.content.decode()
    assert "OLM-OBCY01" in other.content.decode()


# --- 5. oś czasu i kalendarz --------------------------------------------------------------------


def test_stage_rows_read_the_edition_of_the_competition_they_were_asked_about(
    competition, other_competition, other_edition
):
    assert stage_rows(competition=competition) == []
    assert len(stage_rows(competition=other_competition)) == 1


def test_the_header_strip_is_empty_for_a_competition_without_a_current_edition(
    competition, other_competition, other_edition
):
    """Pasek jest ozdobą nagłówka – brak własnej edycji znaczy brak paska, a nie cudzy pasek."""
    assert timeline_strip(competition=competition) is None
    assert timeline_strip(competition=other_competition)["edition"] == OTHER_EDITION_LABEL


def test_the_strip_of_one_competition_is_not_served_from_the_cache_of_another(
    competition, other_competition, other_edition
):
    """Klucz bufora niesie konkurs, więc rozgrzanie go pod jedną domeną nie odpowiada drugiej."""
    assert timeline_strip(competition=other_competition) is not None

    assert timeline_strip(competition=competition) is None


def test_the_strip_reads_the_workshops_of_its_own_page_tree(
    competition, other_competition, other_home, other_edition
):
    """Warsztaty są jedynym źródłem paska spoza domeny zawodów – idą przez drzewo stron."""
    other_home.add_child(
        instance=ContentPage(title="Warsztaty drugiej", slug=WORKSHOPS_SLUG, show_in_menus=True)
    )

    assert workshops_page(other_competition).title == "Warsztaty drugiej"
    # Drzewo Konkursu #1 z migracji ``cms.0002`` nie ma strony warsztatów (dokłada ją dopiero
    # ``seed_legacy_content``), więc poprawną odpowiedzią jest **brak strony**, a nie cudza.
    assert workshops_page(competition) is None


def test_the_timeline_of_one_competition_has_no_event_of_another(
    competition, other_competition, other_edition
):
    assert timeline_events(competition=competition) == []
    assert timeline_events(competition=other_competition) != []


def test_the_participant_calendar_does_not_carry_the_deadlines_of_another_competition(
    participant, competition, other_competition, other_edition
):
    """Plik ``.ics`` uczestnika ma wyliczać terminy tej olimpiady, w której on startuje."""
    assert participant_calendar(participant, competition=competition) == []
    assert participant_calendar(participant) == []
    assert participant_calendar(participant, competition=other_competition) != []


# --- 6. skąd bierze się konkurs strony -----------------------------------------------------------


def test_the_competition_of_a_page_comes_from_the_request_without_a_query(
    rf, competition, django_assert_num_queries
):
    """Zwykłe żądanie zna już konkurs – schodzenie do drzewa stron byłoby drugim zapytaniem."""
    from apps.cms.tenancy import competition_for_page

    request = rf.get("/")
    request.competition = competition
    home = HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).get()

    with django_assert_num_queries(0):
        assert competition_for_page(home, request) == competition


def test_a_preview_reads_the_competition_of_the_page_not_of_the_admin_host(
    rf, competition, other_competition, other_home
):
    """W podglądzie żądanie idzie pod domenę panelu, a strona należy do drzewa swojej witryny."""
    from apps.cms.tenancy import competition_for_page

    request = rf.get("/")
    request.competition = competition
    request.is_preview = True

    assert competition_for_page(other_home, request) == other_competition


def test_a_page_outside_any_site_has_no_competition(competition):
    """Strona poza poddrzewem jakiejkolwiek witryny nie ma właściciela – i to nie jest wyjątek."""
    from apps.cms.tenancy import competition_for_page, competition_for_site

    assert competition_for_site(None) is None
    assert competition_for_page(None) is None


# --- 7. slugi zarezerwowane i prefiksy ścieżki ---------------------------------------------------


def test_a_page_slug_cannot_take_over_the_path_prefix_of_another_competition(other_competition, home_page):
    """Strona o slugu równym prefiksowi cudzego konkursu przechwyciłaby **całą** jego witrynę."""
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "druga"
    other_competition.save(update_fields=["routing_mode", "path_prefix"])

    page = ContentPage(title="Podszywacz", slug="druga")
    page.depth = home_page.depth + 1

    with pytest.raises(ValidationError) as error:
        page.clean()

    assert "slug" in error.value.message_dict


def test_a_slug_that_is_nobodys_prefix_is_still_allowed(other_competition, home_page):
    """Reguła ma odrzucać kolizje, a nie utrudniać redaktorowi nazywanie stron."""
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "druga"
    other_competition.save(update_fields=["routing_mode", "path_prefix"])

    page = ContentPage(title="Zwykła strona", slug="o-nas")
    page.depth = home_page.depth + 1

    page.clean()  # nie rzuca


def test_a_competition_addressed_by_domain_does_not_reserve_anything(other_competition, home_page):
    """Prefiks liczy się wyłącznie w trybie ``PATH`` – w trybie domeny nie ma czego przechwycić."""
    other_competition.path_prefix = "druga"
    other_competition.save(update_fields=["path_prefix"])

    page = ContentPage(title="Zwykła strona", slug="druga")
    page.depth = home_page.depth + 1

    page.clean()  # nie rzuca


def test_the_taken_first_segments_name_both_the_application_and_the_page_tree(competition):
    """Druga połowa reguły z § 2.3: prefiks konkursu nie może stanąć na zajętym segmencie.

    Sprawdzenie „czy slug strony nie przechwyci konkursu” robi ``CMSPage.clean`` wyżej; ta funkcja
    jest dla sprawdzenia odwrotnego, które należy do ``Competition.clean`` (``apps/tenancy``).
    Lista ma wymieniać **jedno i drugie** – adresy aplikacji i slugi z drzewa – bo prefiks
    kolidujący z którymkolwiek z nich daje ten sam skutek: konkurs pod adresem, który należy
    do kogoś innego.
    """
    from apps.cms.models import RESERVED_SLUGS, taken_first_segments

    taken = taken_first_segments()

    assert RESERVED_SLUGS <= taken
    assert {"aktualnosci", "zadania", "archiwum", "wyniki"} <= taken
    assert "fizyczna" not in taken


def test_the_taken_segments_of_another_site_are_that_sites_own(other_competition, other_home):
    """Każda witryna ma własne drzewo, więc własną listę zajętych segmentów."""
    from apps.cms.models import taken_first_segments

    taken = taken_first_segments(other_competition.site)

    assert {"ogloszenia", "zadania", "wyniki"} <= taken
    assert "aktualnosci" not in taken


# --- 8. panel redakcyjny ------------------------------------------------------------------------


def test_a_superuser_sees_the_trees_of_every_competition(
    client_for, competition, other_competition, other_home
):
    """Operator platformy jest globalny (§ 3.8) – ``/cms/`` jest jego narzędziem."""
    operator = UserFactory(is_staff=True, is_superuser=True)
    client = client_for(competition)
    assert client.login(email=operator.email, password=DEFAULT_PASSWORD)
    tree_root = Page.objects.filter(depth=1).order_by("path").first()

    content = client.get(f"/cms/pages/{tree_root.pk}/").content.decode()

    assert other_home.title in content
    assert HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).first()


def test_the_coordinator_page_permissions_are_the_ones_from_cms_0003(competition):
    """Zadanie T4 **nie zmienia** uprawnień grupy ``coordinator`` – i ten test tego pilnuje.

    Prawa do stron grupa dostaje w migracji ``cms.0003`` jako kopię grup ``Editors`` i
    ``Moderators`` Wagtaila. Zawężenie ich do poddrzewa jednego konkursu jest osobną decyzją
    (i osobnym etapem), a zrobione przy okazji odebrałoby koordynatorowi Olimpiady Kwantowej
    dostęp do ``/cms/`` bez ani jednego polecenia organizatora.
    """
    coordinator = Group.objects.get(name=GROUP_COORDINATOR)
    expected = {
        (row.page_id, row.permission_id)
        for row in GroupPagePermission.objects.filter(group__name__in=("Editors", "Moderators"))
    }

    actual = {
        (row.page_id, row.permission_id) for row in GroupPagePermission.objects.filter(group=coordinator)
    }

    assert actual == expected
    assert expected  # sam test byłby pusty, gdyby grup wzorcowych nie było


def test_every_competition_keeps_its_own_site_settings_row(competition, other_competition):
    """``SiteSettings`` jest per witryna – schemat się nie zmienia, zmienia się tylko odczyt."""
    own = SiteSettings.for_site(competition.site)
    other = SiteSettings.for_site(other_competition.site)

    assert own.pk != other.pk
    assert Site.objects.filter(is_default_site=True).get() == competition.site


# --- 9. ciągłość Konkursu #1 ---------------------------------------------------------------------


def test_the_page_tree_of_competition_one_is_untouched(competition):
    """§ 0: drzewo, slugi i kolejność Konkursu #1 mają zostać takie, jakie zastała je ta zmiana."""
    home = HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).get()
    slugs = list(Page.objects.live().child_of(home).order_by("path").values_list("slug", flat=True))

    assert slugs[:4] == ["aktualnosci", "zadania", "archiwum", "wyniki"]


def test_a_competition_without_announcements_renders_the_page_without_a_banner(
    client_for, other_competition, other_home
):
    """Pustka jest tu odpowiedzią poprawną: strona ma się otworzyć, tylko bez paska."""
    response = client_for(other_competition).get("/")

    assert response.status_code == 200
    assert response.context["announcements"] == []


def test_an_old_announcement_of_competition_one_stays_visible_after_the_backfill(client_for, competition):
    """Migracja ``cms.0022`` przypisuje zastane komunikaty Konkursowi #1 – bez niej zniknęłyby.

    Wiersz „sprzed wdrożenia” zakładamy przez ``update``, bo przez ``save()`` już się nie da –
    model wypełnia właściciela z kontekstu. To jest zresztą druga połowa tej samej gwarancji:
    nowe komunikaty właściciela **mają**, a stare dostają go od migracji.
    """
    row = AnnouncementFactory(competition=competition, text=OWN_ANNOUNCEMENT)
    Announcement.objects.filter(pk=row.pk).update(competition=None)
    reset_cache()

    assert OWN_ANNOUNCEMENT not in client_for(competition).get("/").content.decode()

    Announcement.objects.filter(pk=row.pk).update(competition=competition)
    reset_cache()

    assert OWN_ANNOUNCEMENT in client_for(competition).get("/").content.decode()


def test_the_window_of_an_announcement_still_decides_its_visibility(competition):
    """Zakresowanie dokłada warunek, a nie zastępuje tych, które już były."""
    now = timezone.now()
    AnnouncementFactory(
        competition=competition,
        text="Komunikat po terminie",
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=1),
    )

    assert active_announcements(competition) == []
