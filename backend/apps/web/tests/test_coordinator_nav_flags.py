"""Menu panelu koordynatora a przełączniki konkursu (etap 2, montaż końcowy wydań E–K).

Ten plik pilnuje jednej rzeczy i pilnuje jej dosłownie: **konkurs z domyślnymi przełącznikami ma
mieć menu bajt w bajt takie, jak przed etapem 2** (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1 punkt 1),
a każda z piętnastu flag etapu 2 dokłada **dokładnie swoje** pozycje i nic poza nimi.

Dlaczego osobny plik, a nie asercja w ``test_coordinator_shell.py``: tamten plik sprawdza to, co
widzi człowiek na wyrenderowanej stronie (nagłówki sekcji, odnośniki, aktywna pozycja) i celowo
nie zna kształtu struktury w ``coordinator_nav``. Tutaj kształt struktury **jest** przedmiotem:
pytanie brzmi „czy ta lista nie urosła”, a nie „czy panel się rysuje”. Odpowiedź na to pytanie
musi być porównaniem z zapisaną listą, bo inaczej pozycja dołożona bez flagi przeszłaby niezauważona.

Struktura, a nie HTML, i dlatego:

- ``groups()`` jest **jedynym** miejscem, w którym zapisany jest podział panelu na sekcje, więc
  porównanie tutaj łapie każdą zmianę menu, także tę, której akurat nie widać na pulpicie,
- pozycje zależne od etapu (``stage_items``, „Skala punktacji”, „Dyplomy”) wypadają przy pustej
  liście etapów – i o to chodzi: przedmiotem jest to, co dokłada **flaga**, a nie to, co dokłada
  otwarty etap. Część flag etapu 2 ma jednak pozycje **wyłącznie** etapowe (komponenty, remisy,
  role recenzenckie, przyjazdy), więc każdą flagę sprawdzamy **dwa razy**: bez etapów i z jednym
  otwartym etapem.

Drugi przedmiot dołożony przez montaż końcowy: **wzorce adresów naprawdę stoją w mapie
produkcyjnej**. Do czasu montażu ekrany wydań G–K miały własne moduły wzorców i testowe
urlconfy; gdyby rozwinięcia zabrakło, pozycja menu po prostu znikałaby (``resolve`` oddaje
``None`` przy ``NoReverseMatch``), a menu bez pozycji wygląda dokładnie tak samo, jak menu za
wyłączoną flagą. Dlatego test przechodzi **każdy** wzorzec każdego modułu i porównuje adres
złożony przez ``reverse`` ze ścieżką zapisaną w module – znak w znak.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from apps.tenancy.models import FEATURE_DEFAULTS
from apps.web.coordinator_nav import groups
from apps.web.urls_fees import urlpatterns as fee_urlpatterns
from apps.web.urls_institutions import urlpatterns as institution_urlpatterns
from apps.web.urls_pipeline import urlpatterns as pipeline_urlpatterns
from apps.web.urls_regions import urlpatterns as region_urlpatterns
from apps.web.urls_scoring import urlpatterns as scoring_urlpatterns

pytestmark = pytest.mark.django_db

#: Menu konkursu **bez ani jednej flagi** – stan sprzed etapu 2, przepisany z ``coordinator_nav``.
#: Pary „sekcja → pozycje” w kolejności wyświetlania; sekcje puste zostają na liście, bo puste
#: zostają i w funkcji (nie renderuje ich dopiero szablon).
#:
#: **Jak zmienić:** wyłącznie razem z poleceniem organizatora dotyczącym menu. Pozycja dopisana do
#: tej stałej „żeby test przechodził” jest pozycją, która pojawiła się koordynatorowi Olimpiady
#: Kwantowej bez zamówienia — czyli dokładnie tym, czego zabrania § 0.1.
EXPECTED_MENU_WITHOUT_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Pulpit", ("Co wymaga uwagi",)),
    ("Etapy", ()),
    ("Ocenianie", ("Moderacja", "Zgłoszone problemy")),
    (
        "Uczestnicy i konta",
        ("Uczestnicy", "Wszystkie konta", "Opiekunowie szkolni", "Aktywacje"),
    ),
    ("Komitet", ("Członkowie", "Zatwierdzenia", "Zaproszenia", "Województwa")),
    ("Komunikacja", ("Komunikaty", "Zgłoszenia", "Ogłoszenia")),
    (
        "Raporty",
        (
            "Eksport danych",
            "Audyt",
            "Dyplomy: szablony",
            "Obecność na warsztatach",
            "Zaświadczenia opiekunów",
            "Retencja danych",
            "Rejestr czynności",
        ),
    ),
    (
        "Ustawienia",
        (
            "Rejestracja uczestników",
            "Wydarzenia linii czasu",
            "Integracje",
            # Prośba organizatora z 20.09.2026 – pozycja bez flagi, bo ekran jest zamówiony
            # dla Konkursu #1, a nie jest zdolnością systemu wielokonkursowego. Samo dopisanie
            # pozycji nie zmienia ani jednego listu: bramką jest puste pole adresów.
            "Przekazywanie rozwiązań",
        ),
    ),
)


#: Flagi, które weszły do katalogu **przed** etapem 2 – z nich żadna nie jest przedmiotem tego
#: pliku. ``path_prefix_routing`` i ``memberships_enforced`` nie dotykają menu w ogóle,
#: ``competition_settings_page`` ma własny test w T5, a trzy ostatnie są domyślnie **włączone**
#: (rola opiekuna, odwołania, dyplomy), czyli opisują dzisiejszy stan, a nie nową zdolność.
STAGE_ONE_FLAGS = frozenset(
    {
        "path_prefix_routing",
        "memberships_enforced",
        "competition_settings_page",
        "supervisor_role",
        "appeals",
        "certificates",
    }
)

#: Co dokłada każda flaga etapu 2: (bez etapów, z jednym otwartym etapem). Zbiór, a nie krotka,
#: bo pozycję w kolejności sprawdzają osobne testy sekcji – tutaj przedmiotem jest **co** przybyło
#: i czy nic poza tym.
#:
#: **Jak zmienić:** wyłącznie razem z poleceniem organizatora dotyczącym menu. Pusty zbiór przy
#: fladze nie jest przeoczeniem – ``competition_branding_in_mail``, ``scoped_cms_permissions``
#: i ``content_translations`` z założenia **nie mają własnego ekranu** (§ 2.2), a zmieniają
#: zachowanie poczty, uprawnień ``/cms/`` i drzewa stron.
FLAG_ITEMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # --- wydanie E -----------------------------------------------------------------------------
    "per_competition_consents": (frozenset({"Zgody konkursu"}), frozenset({"Zgody konkursu"})),
    "competition_branding_in_mail": (frozenset(), frozenset()),
    "document_templates": (frozenset({"Szablony dokumentów"}), frozenset({"Szablony dokumentów"})),
    "scoped_cms_permissions": (frozenset(), frozenset()),
    # --- wydanie G -----------------------------------------------------------------------------
    "custom_regions": (frozenset({"Regiony"}), frozenset({"Regiony"})),
    # --- wydanie H -----------------------------------------------------------------------------
    "institution_types": (frozenset({"Profil rejestracji"}), frozenset({"Profil rejestracji"})),
    "custom_school_directory": (frozenset({"Słownik placówek"}), frozenset({"Słownik placówek"})),
    # --- wydanie I -----------------------------------------------------------------------------
    "process_editor": (
        frozenset({"Przebieg edycji"}),
        frozenset({"Przebieg edycji", "Komponenty etapu", "Punkty z rozmowy"}),
    ),
    "categories": (frozenset({"Kategorie"}), frozenset({"Kategorie"})),
    # --- wydanie J -----------------------------------------------------------------------------
    "team_entries": (frozenset({"Drużyny"}), frozenset({"Drużyny"})),
    "weighted_scoring": (frozenset(), frozenset({"Rozstrzyganie remisów"})),
    "reviewer_roles": (frozenset(), frozenset({"Role recenzenckie"})),
    # --- wydanie K -----------------------------------------------------------------------------
    "fees": (frozenset({"Wpisowe"}), frozenset({"Wpisowe"})),
    "onsite_logistics": (
        frozenset({"Miejsca zawodów"}),
        frozenset({"Miejsca zawodów", "Przyjazdy i potrzeby", "Obecność"}),
    ),
    "content_translations": (frozenset(), frozenset()),
    # --- konkursy w subdomenach platformy ---------------------------------------------------------
    "competition_creation": (frozenset({"Nowy konkurs"}), frozenset({"Nowy konkurs"})),
    # --- forum uczestników (prośba organizatora z 21.09.2026) ------------------------------------
    "participant_forum": (frozenset({"Forum uczestników"}), frozenset({"Forum uczestników"})),
}

#: Flagi spoza etapu 2, które mimo to dokładają pozycję menu i dlatego stoją w tabeli wyżej.
#: Dziś są dwie: ``competition_creation`` (ekran „Nowy konkurs”, subdomeny platformy) i
#: ``participant_forum`` (moderacja forum, prośba organizatora z 21.09.2026). Stała istnieje po to,
#: żeby licznik niżej nadal mówił o **etapie 2** – inaczej trzeba by przy każdym kolejnym ekranie
#: poprawiać liczbę, o której dokument mówi, że jest ceną świadomie zapłaconą.
LATER_FLAGS = frozenset({"competition_creation", "participant_forum"})

#: Klucze odznak w menu. Piąta i szósta pozycja tej listy to dwa różne rodzaje wyjątku, więc obie
#: mają tu własne zdanie:
#:
#: - ``tickets`` i cztery przed nim są dzisiejszym menu i nie zależą od żadnej flagi,
#: - ``forum`` jest **świadomym odstępstwem** od reguły „nowy ekran nie dostaje odznaki” (§ 2.2).
#:   Reguła broni kosztu pulpitu, a tutaj koszt jest zerowy dla konkursu z domyślnymi
#:   przełącznikami: ``apps.forum.services.moderation_count`` oddaje zero **bez ani jednego
#:   zapytania**, dopóki flaga jest wyłączona. Płaci wyłącznie konkurs, który forum włączył – i on
#:   płaci za coś, bez czego moderacja by nie działała: forum w wersji pierwszej **nie wysyła
#:   listów** (decyzja opisana w ``docs/PODRECZNIK-ORGANIZATORA.md``), więc odznaka jest jedynym
#:   sygnałem, że pod adresem czekają wpisy, których nikt jeszcze nie widział. Ekran bez odznaki
#:   znaczyłby kolejkę moderacyjną, do której trzeba pamiętać, żeby zaglądać – a przy domyślnej
#:   moderacji wstępnej „zapomniałem zajrzeć” równa się „forum milczy”.
EXPECTED_BADGES = frozenset({"moderation", "issues", "activations", "committee", "tickets", "forum"})

#: Wzorce wydań G–K, po jednej liście na wydanie – tak, jak stoją w ``apps/web/urls.py``.
RELEASE_PATTERNS = {
    "G": region_urlpatterns,
    "H": institution_urlpatterns,
    "I": pipeline_urlpatterns,
    "J": scoring_urlpatterns,
    "K": fee_urlpatterns,
}

#: Przykładowa wartość dla każdego konwertera adresu. Wystarczą trzy – więcej w tych wzorcach
#: nie ma, a nieznany konwerter ma podnieść ``KeyError``, a nie po cichu wypaść z porównania.
SAMPLE_ARGS = {"IntConverter": 1, "StringConverter": "x", "SlugConverter": "x"}


def menu(competition, stages: list | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Menu jako pary „sekcja → etykiety pozycji”. Bez etapów, o ile test nie poda inaczej."""
    return tuple(
        (group.label, tuple(item.label for item in group.items))
        for group in groups(stages or [], competition)
    )


def all_labels(competition, stages: list | None = None) -> set[str]:
    """Wszystkie etykiety menu **razem z dziećmi** – pozycje etapowe są dziećmi, nie sekcjami."""
    labels: set[str] = set()
    for group in groups(stages or [], competition):
        for item in group.items:
            labels.add(item.label)
            labels.update(child.label for child in item.children)
    return labels


def with_flags(competition, **flags: bool):
    """Konkurs z ustawionymi przełącznikami – w pamięci, bo ``has_feature`` czyta pole wiersza."""
    competition.feature_flags = {**(competition.feature_flags or {}), **flags}
    return competition


def labels_of(competition, section: str) -> tuple[str, ...]:
    return dict(menu(competition))[section]


def badges(competition) -> set[str]:
    return {item.badge for group in groups([], competition) for item in group.items if item.badge}


def test_menu_without_flags_is_the_menu_from_before_stage_two(competition):
    """Domyślne przełączniki = dzisiejsze menu, sekcja po sekcji i pozycja po pozycji."""
    assert menu(with_flags(competition, **{})) == EXPECTED_MENU_WITHOUT_FLAGS


def test_menu_without_a_competition_is_the_same(competition):  # noqa: ARG001 - fikstura bazy
    """Host bez rozstrzygniętego konkursu dostaje to samo menu – flaga bez konkursu nie istnieje."""
    assert menu(None) == EXPECTED_MENU_WITHOUT_FLAGS


def test_consents_flag_adds_exactly_one_item_in_settings(competition):
    with_flags(competition, per_competition_consents=True)

    assert labels_of(competition, "Ustawienia") == (
        "Zgody konkursu",
        "Rejestracja uczestników",
        "Wydarzenia linii czasu",
        "Integracje",
        "Przekazywanie rozwiązań",
    )
    # Poza „Ustawieniami” nie zmienia się nic: flaga dokłada ekran, a nie przebudowuje panelu.
    assert labels_of(competition, "Raporty") == dict(EXPECTED_MENU_WITHOUT_FLAGS)["Raporty"]


def test_document_templates_flag_adds_exactly_one_item_in_reports(competition):
    with_flags(competition, document_templates=True)

    assert labels_of(competition, "Raporty") == (
        "Eksport danych",
        "Audyt",
        "Dyplomy: szablony",
        # § 2.2: zaraz obok „Dyplomów: szablonów” – obie pozycje dotyczą tego samego papieru.
        "Szablony dokumentów",
        "Obecność na warsztatach",
        "Zaświadczenia opiekunów",
        "Retencja danych",
        "Rejestr czynności",
    )
    assert labels_of(competition, "Ustawienia") == dict(EXPECTED_MENU_WITHOUT_FLAGS)["Ustawienia"]


def test_both_flags_add_both_items_and_nothing_else(competition):
    with_flags(competition, per_competition_consents=True, document_templates=True)

    before = {label for _, items in EXPECTED_MENU_WITHOUT_FLAGS for label in items}
    after = {label for _, items in menu(competition) for label in items}

    assert after - before == {"Zgody konkursu", "Szablony dokumentów"}
    assert before - after == set()


def test_new_items_have_no_badges(competition):
    """Nowy ekran **nie dostaje odznaki** (§ 2.2): każda odznaka to szóste zapytanie na pulpicie."""
    with_flags(competition, per_competition_consents=True, document_templates=True)

    assert badges(competition) == EXPECTED_BADGES - {"forum"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("web:coordinator-consents", "/coordinator/consents/"),
        ("web:coordinator-documents", "/coordinator/documents/"),
        ("web:coordinator-competitions", "/coordinator/competitions/"),
        ("web:coordinator-competition-new", "/coordinator/competitions/new/"),
    ],
)
def test_menu_targets_resolve_in_the_production_urlconf(name, expected):
    """Pozycja menu ma dokąd prowadzić – czyli montaż wzorców w ``apps/web/urls.py`` się odbył.

    Bez tego testu obie pozycje po prostu znikałyby z menu (``resolve`` oddaje ``None`` przy
    ``NoReverseMatch``), a menu bez pozycji wygląda dokładnie tak samo jak menu za wyłączoną flagą.
    """
    assert reverse(name) == expected


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("web:coordinator-consent-edit", (7,), "/coordinator/consents/7/"),
        ("web:coordinator-consent-version", (7,), "/coordinator/consents/7/version/"),
        ("web:coordinator-document", ("LAUREAT",), "/coordinator/documents/LAUREAT/"),
    ],
)
def test_screen_subpages_resolve_in_the_production_urlconf(name, args, expected):
    assert reverse(name, args=args) == expected


# --- montaż końcowy: piętnaście flag etapu 2 -----------------------------------------------------


def test_the_table_of_flags_covers_the_whole_stage_two_catalogue():
    """Tabela wyżej opisuje **każdą** flagę etapu 2 – i żadnej spoza katalogu.

    Bez tego testu flaga dopisana do ``FEATURE_DEFAULTS`` przez zadanie modelu przeszłaby tu
    niezauważona: parametryzacja chodzi po tabeli, więc brak wiersza znaczyłby brak testu, a nie
    czerwony przebieg. Katalog jest źródłem prawdy, tabela – jego odbiciem.
    """
    catalogue = set(FEATURE_DEFAULTS) - STAGE_ONE_FLAGS

    assert set(FLAG_ITEMS) == catalogue
    # § 0.6: „Piętnaście flag to dużo i to jest świadoma cena”. Liczymy **etap 2**, więc flagi
    # dołożone później (``LATER_FLAGS``) odejmujemy – mają własne wiersze w tabeli wyżej, ale nie
    # zmieniają zdania, które dokument postawił o etapie 2.
    assert len(catalogue - LATER_FLAGS) == 15


@pytest.mark.parametrize("flag", sorted(FLAG_ITEMS))
def test_each_flag_adds_exactly_its_own_items_without_stages(competition, flag):
    """Jedna flaga włączona = dokładnie jej pozycje przybyły i **ani jedna nie zniknęła**."""
    before = all_labels(competition)
    with_flags(competition, **{flag: True})
    after = all_labels(competition)

    assert after - before == set(FLAG_ITEMS[flag][0])
    assert before - after == set()


@pytest.mark.parametrize("flag", sorted(FLAG_ITEMS))
def test_each_flag_adds_exactly_its_own_items_with_an_open_stage(competition, flag, elim_stage):
    """To samo przy otwartym etapie – wtedy dochodzą pozycje etapowe (komponenty, remisy, role)."""
    stages = [elim_stage]
    before = all_labels(competition, stages)
    with_flags(competition, **{flag: True})
    after = all_labels(competition, stages)

    assert after - before == set(FLAG_ITEMS[flag][1])
    assert before - after == set()


def test_all_stage_two_flags_at_once_add_the_union_and_nothing_more(competition, elim_stage):
    """Wszystkie piętnaście naraz: suma tabeli, co do etykiety. Kontrola na sumę, nie na sumę po jednej."""
    stages = [elim_stage]
    before = all_labels(competition, stages)
    with_flags(competition, **dict.fromkeys(FLAG_ITEMS, True))
    after = all_labels(competition, stages)

    expected = set().union(*(items for _, items in FLAG_ITEMS.values()))
    assert after - before == expected
    assert before - after == set()


def test_no_stage_two_flag_adds_a_badge(competition, elim_stage):
    """Nowy ekran **nie dostaje odznaki** (§ 2.2): każda odznaka to szóste zapytanie na pulpicie.

    Wyjątek ``forum`` jest jeden, jest nazwany i ma uzasadnienie przy :data:`EXPECTED_BADGES`.
    Asercja niżej pilnuje, żeby **został jeden**: kolejna flaga etapu 2 z odznaką wywróci ten test
    i będzie musiała napisać swoje zdanie tak samo, jak forum napisało swoje.
    """
    with_flags(competition, **dict.fromkeys(FLAG_ITEMS, True))

    marked = {item.badge for group in groups([elim_stage], competition) for item in group.items if item.badge}
    assert marked == set(EXPECTED_BADGES)


def test_stage_children_of_the_logistics_flag_hang_under_the_stage(competition, elim_stage):
    """„Przyjazdy i potrzeby” i „Obecność” są **dziećmi etapu**, a nie pozycjami sekcji.

    Rozróżnienie jest widoczne dla koordynatora: dziecko zwija się razem z etapem i niesie jego
    identyfikator w adresie, a pozycja sekcji stoi zawsze i dotyczyłaby „jakiegoś” etapu. Przy
    trzech etapach w edycji to jest różnica między menu, które da się przeczytać, a listą
    powtórzonych nazw.
    """
    with_flags(competition, onsite_logistics=True)

    stage_group = next(group for group in groups([elim_stage], competition) if group.label == "Etapy")
    stage_item = next(item for item in stage_group.items if item.label == elim_stage.display_name)
    children = {child.label: child for child in stage_item.children}

    assert [item.label for item in stage_group.items] == [elim_stage.display_name]
    assert set(children) == {
        "Zadania",
        "Przydziały i oceny",
        "Postęp",
        "Wyniki",
        "Przyjazdy i potrzeby",
        "Obecność",
    }
    # Dziecko niesie identyfikator **tego** etapu – bez tego menu świeciłoby się na cudzym ekranie.
    assert children["Przyjazdy i potrzeby"].args == (elim_stage.pk,)
    assert children["Obecność"].args == (elim_stage.pk,)


def test_pipeline_flag_puts_the_editor_above_the_stages_and_components_at_the_end(competition, elim_stage):
    """Sekcja „Etapy” za flagą ``process_editor``: tor nad listą, komponenty na końcu (§ 2.2)."""
    with_flags(competition, process_editor=True)

    stage_group = next(group for group in groups([elim_stage], competition) if group.label == "Etapy")

    assert [item.label for item in stage_group.items] == [
        "Przebieg edycji",
        elim_stage.display_name,
        "Komponenty etapu",
    ]


def test_stage_children_do_not_grow_without_the_flag(competition, elim_stage):
    """Konkurs #1 (domyślne flagi) ma pod etapem **te same cztery** pozycje, co przed etapem 2."""
    stage_group = next(group for group in groups([elim_stage], competition) if group.label == "Etapy")
    stage_item = stage_group.items[0]

    assert [child.label for child in stage_item.children] == [
        "Zadania",
        "Przydziały i oceny",
        "Postęp",
        "Wyniki",
    ]


# --- montaż końcowy: wzorce wydań G–K w mapie produkcyjnej ---------------------------------------


def sample_kwargs(pattern) -> dict:
    """Argumenty adresu dobrane po konwerterze – po jednej przykładowej wartości na rodzaj."""
    return {
        name: SAMPLE_ARGS[type(converter).__name__] for name, converter in pattern.pattern.converters.items()
    }


def expected_path(pattern, kwargs: dict) -> str:
    """Ścieżka **z modułu wzorców** z podstawionymi argumentami – wzorzec jest tu źródłem prawdy."""
    route = str(pattern.pattern)
    return "/" + re.sub(r"<\w+:(\w+)>", lambda match: str(kwargs[match.group(1)]), route)


@pytest.mark.parametrize("release", sorted(RELEASE_PATTERNS))
def test_every_release_pattern_reverses_in_the_production_urlconf(release):
    """Każdy wzorzec wydania stoi w mapie produkcyjnej pod **tą samą** ścieżką, co w module.

    To jest test montażu, a nie widoków: dopóki wzorce czekały w ``apps/web/urls_*.py``, testy
    ekranów chodziły po własnych urlconfach, więc pominięte rozwinięcie w ``apps/web/urls.py``
    nie zapaliłoby ani jednej lampki – pozycje menu po prostu znikałyby (``resolve`` oddaje
    ``None`` przy ``NoReverseMatch``), a menu bez pozycji wygląda jak menu za wyłączoną flagą.
    """
    for pattern in RELEASE_PATTERNS[release]:
        kwargs = sample_kwargs(pattern)
        assert reverse(f"web:{pattern.name}", kwargs=kwargs) == expected_path(pattern, kwargs)


def test_release_pattern_names_are_unique_in_the_web_namespace():
    """Żadna nazwa wydań G–K nie powtarza nazwy już zamontowanej – inaczej ``reverse`` kłamie.

    Django nie ostrzega o powtórzonej nazwie: ``reverse`` oddaje wtedy adres **ostatniego**
    wzorca, a wszystkie pięć list stoi na końcu ``urlpatterns``, więc kolizja przykryłaby ekran
    zamontowany wcześniej i nie byłoby tego widać po niczym poza 404 w przeglądarce.
    """
    from apps.web.urls import urlpatterns as web_urlpatterns

    names = [pattern.name for pattern in web_urlpatterns if getattr(pattern, "name", None)]
    duplicates = {name for name in names if names.count(name) > 1}

    assert duplicates == set()
