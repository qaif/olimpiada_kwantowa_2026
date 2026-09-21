"""Pasek linii czasu w nagłówku: scalanie kalendarza, oś, głowica i upakowanie podpisów.

Pasek jest jedynym miejscem w serwisie, w którym **cztery** źródła terminów stają się jedną
listą: etapy zawodów, wydarzenia dopisane przez koordynatora, warsztaty z treści redakcyjnej
i okno rejestracji uczestników. Testy pilnują tego, czego nie widać w szablonie:

- że żadne z czterech źródeł nie wypada ze scalenia i że kolejność jest kolejnością dat,
- że stan („minione / teraz / przed nami”) liczy się **dniami**, z oboma końcami włącznie,
- że oś i głowica odpowiadają dzisiejszej dacie, a edycja bez terminów dostaje rok szkolny,
- że każde wydarzenie ma na linii swój znacznik z pełnym podpisem dla czytnika ekranu,
- że wynik jest buforowany i że zapis koordynatora ten bufor zdejmuje.

Animacji (rysowanej w przeglądarce) tu nie ma i być nie może: sprawdza ją e2e/check_timeline_strip.py.
"""

from datetime import date, datetime, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.cms.models import ContentPage
from apps.cms.timeline import (
    BAR_SIZE,
    cell_char,
    format_compact_range,
    invalidate_timeline_cache,
    timeline_events,
    timeline_strip,
)
from apps.cms.workshops import WORKSHOPS_SLUG
from apps.competitions.models import EditionEvent, StageFormat, StageKind
from apps.competitions.tests.factories import StageFactory

pytestmark = pytest.mark.django_db

#: Dzień, względem którego liczone są wszystkie asercje. Stoi w środku edycji 2026/2027, żeby
#: pasek miał i przeszłość, i przyszłość – inaczej połowa reguł nie miałaby czego rozstrzygać.
TODAY = datetime(2026, 12, 1, 12, 0)


def day(year: int, month: int, number: int):
    """Aware ``datetime`` o 9:00 czasu polskiego – terminy etapów są momentami, nie dniami."""
    return timezone.make_aware(datetime(year, month, number, 9, 0))


def stage(edition, kind, start: tuple, end: tuple, **extra):
    """Etap o podanym oknie oddawania prac. Terminy recenzji liczą się od deadline'u."""
    opens_at = day(*start)
    # Okno etapu musi mieć dodatnią długość (constraint w bazie). Etap jednodniowy – finał,
    # na którym sesja trwa kilka godzin – dostaje więc te godziny, a nie zerowe okno.
    deadline_at = max(day(*end), opens_at + timedelta(hours=8))
    return StageFactory(
        edition=edition,
        kind=kind,
        opens_at=opens_at,
        deadline_at=deadline_at,
        review_deadline_at=deadline_at + timedelta(days=14),
        appeal_window_opens_at=deadline_at + timedelta(days=16),
        appeal_window_closes_at=deadline_at + timedelta(days=23),
        **extra,
    )


def workshops_page(home, topic_dates: list[tuple[str, date | None]]) -> ContentPage:
    """Strona „Warsztaty” z jednym blokiem ``schedule`` – tabela, z której pasek czyta terminy."""
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "Godziny",
                    "rows": [
                        {"topic": topic, "date": "słownie", "date_value": value, "time": ""}
                        for topic, value in topic_dates
                    ],
                },
            )
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def titles(items) -> list[str]:
    return [item["title"] for item in items]


# --- scalanie czterech źródeł -------------------------------------------------------------------


def test_merges_stages_events_registration_and_workshops(edition, home_page):
    """Cztery źródła, jedna lista, kolejność po dacie początku."""
    edition.registration_opens_at = day(2026, 9, 1)
    edition.registration_closes_at = day(2026, 10, 15)
    edition.save()
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(
        edition=edition, title="Gala finałowa", starts_on=date(2027, 6, 12), note="Kraków"
    )
    workshops_page(home_page, [("Splątanie", date(2026, 11, 7))])

    items = timeline_events(edition, TODAY)

    assert titles(items) == [
        "Rejestracja uczestników",
        "Eliminacje",
        "Warsztaty: Splątanie",
        "Gala finałowa",
    ]
    assert [item["kind"] for item in items] == ["registration", "stage", "workshop", "event"]


def test_training_stage_never_reaches_the_strip(edition):
    """Etap treningowy jest piaskownicą bez terminu – na osi stałby jako „najdłużej oczekiwany”."""
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    StageFactory(edition=edition, kind=StageKind.TRAINING)

    assert titles(timeline_events(edition, TODAY)) == ["Eliminacje"]


def test_hidden_event_is_not_on_the_strip(edition):
    """``show_on_timeline`` jest wyłącznikiem jednego wiersza, a nie powodem do jego kasowania."""
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(
        edition=edition, title="Jeszcze nieogłoszone", starts_on=date(2027, 2, 1), show_on_timeline=False
    )

    assert titles(timeline_events(edition, TODAY)) == ["Eliminacje"]


def test_disabled_registration_is_not_announced(edition):
    """Pasek nie ogłasza terminu, pod którym stoi formularz odmawiający założenia konta."""
    edition.registration_enabled = False
    edition.registration_opens_at = day(2026, 9, 1)
    edition.registration_closes_at = day(2026, 10, 15)
    edition.save()
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))

    assert titles(timeline_events(edition, TODAY)) == ["Eliminacje"]


def test_stage_event_days_win_over_the_upload_window(edition):
    """Finał ogłasza dni zjazdu, a nie kilkugodzinną sesję, w której serwer przyjmuje pliki."""
    stage(
        edition,
        StageKind.FINAL,
        (2027, 6, 4),
        (2027, 6, 4),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
        location="Kraków",
    )
    # Okno uploadu trwa jeden dzień; termin ogłoszony to cztery dni zjazdu.
    item = timeline_events(edition, TODAY)[0]

    assert (item["start"], item["end"]) == (date(2027, 6, 4), date(2027, 6, 7))
    assert item["dates"] == "04.06 – 07.06.2027"


def test_results_link_appears_only_after_publication(edition, coordinator):
    """Odnośnik do wyników to obietnica: bez ogłoszonej tabeli nie ma dokąd prowadzić."""
    from apps.results.models import ResultsPublication

    elim = stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))

    assert timeline_events(edition, TODAY)[0]["url"] == ""

    ResultsPublication.objects.create(stage=elim, published_by=coordinator)

    assert timeline_events(edition, TODAY)[0]["url"] == f"/results/{elim.pk}/"


# --- warsztaty ------------------------------------------------------------------------------------


def test_every_workshop_is_its_own_item(edition, home_page):
    """Warsztat jest osobnym wydarzeniem, na które zapisuje się osobno – i tak ma stać na osi.

    Grupowanie po miesiącu („Warsztaty (3)”) było tu wcześniej i zostało wycofane: zbiorczy
    podpis odbierał warsztatowi temat, czyli jedyną informację, po której czytelnik go rozpoznaje.
    """
    workshops_page(
        home_page,
        [
            ("Kubity", date(2026, 11, 7)),
            ("Splątanie", date(2026, 11, 21)),
            ("Dekoherencja", date(2026, 11, 28)),
            ("Algorytm Shora", date(2026, 12, 12)),
        ],
    )

    items = timeline_events(edition, TODAY)

    assert titles(items) == [
        "Warsztaty: Kubity",
        "Warsztaty: Splątanie",
        "Warsztaty: Dekoherencja",
        "Warsztaty: Algorytm Shora",
    ]
    # Każdy jest terminem jednodniowym i prowadzi na wspólną stronę warsztatów.
    assert all(item["start"] == item["end"] for item in items)
    assert {item["url"] for item in items} == {"/warsztaty/"}


def test_workshop_rows_without_a_date_are_skipped(edition, home_page):
    """Termin nieostry („do potwierdzenia”) zostaje w tabeli, ale nie ma jak go ustawić na osi."""
    workshops_page(home_page, [("Bez terminu", None), ("Z terminem", date(2026, 11, 7))])

    assert titles(timeline_events(edition, TODAY)) == ["Warsztaty: Z terminem"]


# --- stany ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("starts_on", "ends_on", "expected"),
    [
        (date(2026, 10, 1), date(2026, 11, 30), "past"),
        (date(2026, 11, 20), date(2026, 12, 1), "current"),
        (date(2026, 12, 1), date(2026, 12, 1), "current"),
        (date(2026, 12, 1), date(2026, 12, 20), "current"),
        (date(2026, 12, 2), date(2026, 12, 20), "upcoming"),
    ],
)
def test_status_counts_days_with_both_ends_included(edition, starts_on, ends_on, expected):
    """Granicą jest dzień: wydarzenie kończące się dziś jest „teraz”, a nie „minione”."""
    EditionEvent.objects.create(edition=edition, title="Wydarzenie", starts_on=starts_on, ends_on=ends_on)

    assert timeline_events(edition, TODAY)[0]["status"] == expected


def test_single_day_event_has_both_ends(edition):
    """Puste ``ends_on`` znaczy „jeden dzień”, a nie „bez końca”."""
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    item = timeline_events(edition, TODAY)[0]

    assert (item["start"], item["end"]) == (date(2027, 6, 12), date(2027, 6, 12))
    assert item["dates"] == "12.06.2027"


# --- oś, głowica, komórki -------------------------------------------------------------------------


def test_axis_spans_from_the_first_start_to_the_last_end(edition):
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    strip = timeline_strip(edition, TODAY)

    assert strip["axis_start"] == date(2026, 10, 20)
    assert strip["axis_end"] == date(2027, 6, 12)


def test_axis_falls_back_to_the_school_year_when_there_is_too_little_to_measure(edition):
    """Jedno wydarzenie dałoby oś długości tego wydarzenia – czyli wykres bez informacji."""
    edition.year_label = "I edycja 2026/2027"
    edition.save()
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    strip = timeline_strip(edition, TODAY)

    assert strip["axis_start"] == date(2026, 9, 1)
    assert strip["axis_end"] == date(2027, 8, 31)


def test_progress_and_head_follow_today(edition):
    """Głowica stoi na komórce odpowiadającej dzisiejszemu ułamkowi osi (0 – 99)."""
    stage(edition, StageKind.ELIM, (2026, 11, 1), (2026, 11, 1))
    EditionEvent.objects.create(edition=edition, title="Koniec", starts_on=date(2027, 1, 1))

    strip = timeline_strip(edition, TODAY)

    total = (date(2027, 1, 1) - date(2026, 11, 1)).days
    elapsed = (date(2026, 12, 1) - date(2026, 11, 1)).days
    assert strip["progress"] == pytest.approx(elapsed / total)
    assert strip["head"] == round(elapsed / total * BAR_SIZE)


def test_single_day_item_still_gets_a_cell(edition):
    """Gala na rocznej osi to 1/365 paska – bez dolnej granicy byłaby zerem komórek."""
    stage(edition, StageKind.ELIM, (2026, 9, 10), (2027, 6, 10))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    gala = next(item for item in timeline_strip(edition, TODAY)["items"] if item["title"] == "Gala")

    assert gala["cell_to"] - gala["cell_from"] >= 1


def test_bar_is_exactly_one_hundred_cells_with_one_head(edition):
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    strip = timeline_strip(edition, TODAY)
    drawn = "".join(cell["char"] for cell in strip["cells"])

    assert len(drawn) == BAR_SIZE
    assert drawn.count(">") == 1
    assert drawn.index(">") == strip["head"]
    # Za głowicą sama przyszłość, przed nią – sama przeszłość (znaczniki początków wyłącznie
    # po prawej albo po lewej, ale nigdy „=” po „.”).
    assert "=" not in drawn[strip["head"] :]


def test_cell_char_matches_the_rule_mirrored_in_javascript(edition):
    """Ta sama tabelka, co w static/js/timeline-strip.js – stąd ten test jako dokumentacja."""
    marks = {0, 40}

    assert cell_char(0, 10, marks) == "|"
    assert cell_char(5, 10, marks) == "="
    assert cell_char(10, 10, marks) == ">"
    assert cell_char(40, 10, marks) == "|"
    assert cell_char(41, 10, marks) == "."


# --- znaczniki wydarzeń i dymki -----------------------------------------------------------------


def marks(strip) -> list[dict]:
    return [cell for cell in strip["cells"] if cell["items"]]


def test_every_event_gets_a_marker_on_the_line(edition, home_page):
    """Wydarzenia nie mają już własnych wierszy podpisów – stoją **na** linii jako kreski."""
    edition.registration_opens_at = day(2026, 9, 1)
    edition.registration_closes_at = day(2026, 10, 15)
    edition.save()
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    stage(edition, StageKind.DISTRICT, (2027, 1, 10), (2027, 2, 20))
    stage(edition, StageKind.FINAL, (2027, 6, 4), (2027, 6, 5))
    workshops_page(home_page, [("Kubity", date(2026, 11, 7)), ("Splątanie", date(2027, 3, 21))])

    strip = timeline_strip(edition, TODAY)
    marked = marks(strip)

    # Każde wydarzenie ma swój znacznik; kilka wydarzeń w jednej komórce dzieli jeden znacznik.
    assert sum(len(cell["items"]) for cell in marked) == len(strip["items"])
    assert all(cell["char"] in "|>" for cell in marked), "znacznik jest kreską (albo głowicą)"
    assert all("tl__mark" in cell["css_class"] for cell in marked)


def test_marker_carries_the_whole_label_for_the_screen_reader(edition):
    """Kreski czytnik ekranu nie przeczyta – nazwę i termin niesie ``aria-label`` znacznika."""
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(
        edition=edition,
        title="Konferencja podsumowująca edycję i rozdanie nagród",
        starts_on=date(2027, 6, 12),
        url="/aktualnosci/",
    )

    gala = next(cell for cell in marks(timeline_strip(edition, TODAY)) if cell["url"])

    # Pełna nazwa, bez skracania: podpis stoi w chipie legendy, więc jego długość nic nie rozpycha.
    assert gala["label"] == "Konferencja podsumowująca edycję i rozdanie nagród, 12.06.2027"
    assert gala["url"] == "/aktualnosci/"


def test_events_sharing_a_cell_share_one_marker(edition):
    """Na rocznej osi jedna komórka to blisko cztery dni – dwie kreski obok siebie rozjechałyby siatkę."""
    stage(edition, StageKind.ELIM, (2026, 9, 10), (2027, 6, 10))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))
    EditionEvent.objects.create(edition=edition, title="Bankiet", starts_on=date(2027, 6, 12))

    together = [cell for cell in marks(timeline_strip(edition, TODAY)) if len(cell["items"]) > 1]

    assert len(together) == 1
    assert [item["title"] for item in together[0]["items"]] == ["Bankiet", "Gala"]
    assert together[0]["label"] == "Bankiet, 12.06.2027; Gala, 12.06.2027"
    # Dwa wydarzenia pod jednym znacznikiem nie mają jednego adresu – znacznik zostaje przyciskiem.
    assert together[0]["url"] == ""


def test_marker_points_at_the_legend_chips_of_its_events(edition):
    """Kreska i chip legendy stoją w różnych gałęziach drzewa – wiąże je numer wydarzenia."""
    stage(edition, StageKind.ELIM, (2026, 9, 10), (2027, 6, 10))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))
    EditionEvent.objects.create(edition=edition, title="Bankiet", starts_on=date(2027, 6, 12))

    strip = timeline_strip(edition, TODAY)
    numbers = {item["title"]: item["index"] for item in strip["items"]}
    together = next(cell for cell in marks(strip) if len(cell["items"]) > 1)

    assert together["indexes"] == f"{numbers['Bankiet']},{numbers['Gala']}"
    # Numery są kolejnymi pozycjami listy, więc chip legendy da się znaleźć bez szukania po tytule
    # (ten sam tytuł bywa na osi dwa razy: „Warsztaty: …” z powtórzonym tematem).
    assert sorted(numbers.values()) == list(range(len(strip["items"])))


def test_header_lines_carry_the_school_year_and_the_edition_number(edition):
    edition.year_label = "I edycja 2026/2027"
    edition.save()
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))

    lines = [line.strip() for line in timeline_strip(edition, TODAY)["header_lines"]]

    assert lines == ["rok_szkolny(2026, 2027);", "edycja(I);"]


def test_header_skips_the_edition_line_when_the_label_has_no_number(edition):
    """``edycja();`` byłoby wywołaniem bez argumentu – czyli widoczną usterką w nagłówku."""
    edition.year_label = "Edycja testowa 7"
    edition.save()
    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))

    lines = [line.strip() for line in timeline_strip(edition, TODAY)["header_lines"]]

    assert len(lines) == 1
    assert lines[0].startswith("rok_szkolny(")


# --- brak edycji i bufor -------------------------------------------------------------------------


def test_no_current_edition_means_no_strip():
    """Pasek bez kalendarza byłby pustą ramką zajmującą wiersz na każdej stronie serwisu."""
    assert timeline_strip() is None
    assert timeline_events() == []


def test_empty_edition_still_draws_a_bar_over_the_school_year(edition):
    """Edycja bez jednego terminu: pasek stoi, oś jest rokiem szkolnym, podpisów nie ma."""
    edition.year_label = "I edycja 2026/2027"
    edition.save()

    strip = timeline_strip(edition, TODAY)

    assert strip["items"] == []
    assert (strip["axis_start"], strip["axis_end"]) == (date(2026, 9, 1), date(2027, 8, 31))
    assert len(strip["cells"]) == BAR_SIZE


def test_result_is_cached_and_the_event_service_drops_the_cache(edition, coordinator):
    """Pasek liczy się przy każdym żądaniu HTML, więc bufor jest tu warunkiem, a nie optymalizacją."""
    from apps.competitions.events import create_event

    stage(edition, StageKind.ELIM, (2026, 10, 20), (2026, 11, 30))
    EditionEvent.objects.create(edition=edition, title="Gala", starts_on=date(2027, 6, 12))

    first = timeline_strip(edition, TODAY)
    # Zmiana z pominięciem serwisu nie czyści bufora – i dokładnie to ma pokazać ten wiersz.
    EditionEvent.objects.create(edition=edition, title="Dopisane obok", starts_on=date(2027, 3, 1))
    assert titles(timeline_strip(edition, TODAY)["items"]) == titles(first["items"])

    invalidate_timeline_cache(edition.pk)
    assert "Dopisane obok" in titles(timeline_strip(edition, TODAY)["items"])

    # Serwis robi to sam: po dopisaniu wydarzenia pasek nie czeka pięciu minut.
    cache.clear()
    timeline_strip(edition, TODAY)
    create_event(
        edition=edition,
        actor=coordinator,
        title="Przez serwis",
        starts_on=date(2027, 4, 1),
        ends_on=None,
        note="",
        url="",
        show_on_timeline=True,
    )
    assert "Przez serwis" in titles(timeline_strip(edition, TODAY)["items"])


# --- format terminu ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 11, 20), date(2026, 11, 20), "20.11.2026"),
        (date(2026, 10, 12), date(2026, 11, 16), "12.10 – 16.11.2026"),
        (date(2026, 12, 28), date(2027, 1, 3), "28.12.2026 – 03.01.2027"),
    ],
)
def test_compact_range(start, end, expected):
    assert format_compact_range(start, end) == expected


# --- pasek w szablonie bazowym -------------------------------------------------------------------


def test_strip_renders_on_the_home_page_and_on_login(web_client, edition):
    """Pasek stoi w nagłówku, czyli na każdej stronie – także na tych bez treści redakcyjnej."""
    StageFactory(edition=edition, kind=StageKind.ELIM, format=StageFormat.SUBMISSIONS)
    EditionEvent.objects.create(
        edition=edition, title="Gala finałowa", starts_on=timezone.localdate() + timedelta(days=120)
    )

    for path in ("/", "/login/"):
        content = web_client.get(path).content.decode()
        assert "data-timeline-strip" in content, path
        assert "rok_szkolny(" in content, path
        # Pasek jest siatką znaków: klamry i głowica muszą być w kodzie strony, a nie dorysowane
        # skryptem – strona bez JavaScriptu ma mieć kompletny kalendarz.
        assert "tl__head" in content, path
        assert "tl__mark" in content, path
        # Podpis jest w kodzie strony **zawsze**: raz jako nazwa dostępna kreski, raz jako chip
        # legendy w panelu, który arkusz trzyma zwinięty. Przeglądarka nie pyta o niego serwera.
        assert 'aria-label="Gala finałowa,' in content, path
        assert "tl-legend__chip" in content, path
        assert "tl-panel" in content, path


def test_strip_is_absent_without_a_current_edition(web_client):
    assert "data-timeline-strip" not in web_client.get("/login/").content.decode()


def test_strip_sits_in_the_dock_after_main_not_inside_the_header(web_client, edition):
    """Pasek stoi w ``.timeline-dock`` tuż nad stopką (uwaga organizatora z 21.09.2026) – wcześniej
    stał w nagłówku, pod menu. ``<header class="topbar">`` ma zostać bez niego."""
    StageFactory(edition=edition, kind=StageKind.ELIM)

    content = web_client.get("/").content.decode()
    header = content.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]

    assert "data-timeline-strip" not in header
    # Kolejność w źródle strony: treść, potem dok z paskiem, potem stopka.
    main_end = content.index("</main>")
    dock_start = content.index('class="timeline-dock"')
    footer_start = content.index('<footer class="footer">')
    assert main_end < dock_start < footer_start
    assert "data-timeline-strip" in content[dock_start:footer_start]


def test_at_rest_the_header_holds_only_the_bar(web_client, edition):
    """W spoczynku pasek jest **jedną linią**: reszta siedzi w panelu zwiniętym do zera wysokości.

    Wysokości nie da się zmierzyć w pytest, ale da się sprawdzić to, co o niej decyduje: że
    wiersze „kodu” i legenda stoją **wewnątrz** ``.tl-panel``, a poza nim jest wyłącznie wykres.
    Gdyby którekolwiek z nich wypadło z panelu, nagłówek serwisu urósłby na każdej stronie –
    i to jest dokładnie ta usterka, którą zgłosił organizator.
    """
    StageFactory(edition=edition, kind=StageKind.ELIM)
    EditionEvent.objects.create(
        edition=edition, title="Gala finałowa", starts_on=timezone.localdate() + timedelta(days=120)
    )

    content = web_client.get("/login/").content.decode()
    bar = content.split('<pre class="tl tl--bar">')[1].split("</pre>")[0]
    panel = content.split('<div class="tl-panel"')[1].split('<details class="tl-list"')[0]

    # Poza panelem: sam wykres, bez nagłówka „kodu” i bez podpisów.
    assert "rok_szkolny(" not in bar
    assert "tl-legend" not in bar
    assert bar.count("[") and bar.count("]")
    # W panelu: nagłówek „kodu” i komplet chipów – obecne w kodzie strony, ale zwinięte arkuszem.
    assert "rok_szkolny(" in panel
    assert "Gala finałowa" in panel
    assert panel.count('class="tl-legend__chip') == len(timeline_strip(edition)["items"])
