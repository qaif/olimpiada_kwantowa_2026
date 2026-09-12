"""Sekcje strony głównej, które powstały z trzech uwag organizatora po pierwszym przeglądzie.

Wszystkie trzy dotyczą tego samego: **treść, która odpowiada na pierwsze pytania czytelnika, stała
o jedno kliknięcie za daleko.**

- „O Olimpiadzie” było osobną podstroną, mimo że odpowiada na pytanie zadawane w sekundzie, w której
  czytelnik przeczytał hasło. Jest teraz sekcją ``#o-olimpiadzie`` (``HomePage.about_body``),
- harmonogram warsztatów był tabelą w środku ``/harmonogram/``: warsztatów nie widział nikt, kto
  nie przewinął tej podstrony do końca, choć są bezpłatne i otwarte. Strona główna pokazuje trzy
  najbliższe terminy, czytane z **tej samej** tabeli (``apps.cms.workshops``),
- „Jak zacząć?” dublowało sekcję kroków, która stoi na stronie głównej od początku – zostało
  skasowane (testy tego są w ``test_legacy_content.py``).

Reguła „które warsztaty są najbliższe” jest testowana osobno od szablonu: zależy od zegara, więc
testy szablonu zamrażają czas (``freezegun``), a testy samej reguły podają ``now`` wprost.
"""

from datetime import date, datetime

import pytest
from django.core.management import call_command
from freezegun import freeze_time
from wagtail.rich_text import RichText

from apps.cms.models import ContentPage, HomePage
from apps.cms.workshops import WORKSHOPS_SLUG, upcoming_workshops

pytestmark = pytest.mark.django_db

#: Dzień w środku cyklu warsztatów: dwa pierwsze terminy są już za nami, trzeci jest najbliższy.
DURING_WORKSHOPS = "2026-10-20 09:00:00+02:00"
#: Dzień po ostatnim warsztacie (13 lutego 2027) – nie ma już czego zapowiadać.
AFTER_WORKSHOPS = "2027-03-01 09:00:00+01:00"


@pytest.fixture
def legacy_content():
    call_command("seed_legacy_content", verbosity=0)


def schedule_page(topic_dates: list[tuple[str, date | None]], home: HomePage) -> ContentPage:
    """Strona z jednym blokiem ``schedule`` – do testów reguły bez pliku źródłowego."""
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "heading",
                {"text": "Harmonogram", "level": "2", "anchor": "harmonogram", "in_toc": True},
            ),
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "Godziny",
                    "rows": [
                        {
                            "topic": topic,
                            "date": "termin podany słownie",
                            "date_value": value,
                            "time": "11:00–13:00",
                        }
                        for topic, value in topic_dates
                    ],
                },
            ),
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


# --- reguła „najbliższe warsztaty” --------------------------------------------------------------


def test_upcoming_workshops_skips_past_rows_and_keeps_the_order(home_page):
    page = schedule_page(
        [
            ("Późniejszy", date(2026, 12, 1)),
            ("Wczorajszy", date(2026, 10, 19)),
            ("Najbliższy", date(2026, 10, 24)),
            ("Środkowy", date(2026, 11, 14)),
            ("Czwarty w kolejce", date(2026, 12, 20)),
        ],
        home_page,
    )

    rows = upcoming_workshops(page, now=datetime(2026, 10, 20, 9, 0))

    # Trzy najbliższe, od najwcześniejszego – kolejność wiersza w tabeli nie ma znaczenia.
    assert [row["topic"] for row in rows] == ["Najbliższy", "Środkowy", "Późniejszy"]


def test_workshop_today_is_still_upcoming(home_page):
    """Granicą jest dzień, nie godzina: w tabeli stoi data, a godziny bywają „do potwierdzenia”."""
    page = schedule_page([("Dzisiejszy", date(2026, 10, 20))], home_page)

    assert len(upcoming_workshops(page, now=datetime(2026, 10, 20, 23, 0))) == 1


def test_rows_without_a_readable_date_are_skipped(home_page):
    """Termin nieostry zostaje w tabeli, ale nie ma jak go uszeregować względem zegara."""
    page = schedule_page([("Bez daty", None), ("Z datą", date(2026, 11, 14))], home_page)

    rows = upcoming_workshops(page, now=datetime(2026, 10, 20, 9, 0))

    assert [row["topic"] for row in rows] == ["Z datą"]


def test_missing_page_gives_an_empty_list():
    """Strony „Warsztaty” może nie być (świeża baza, szkic) – sekcja ma zniknąć, nie wybuchnąć."""
    assert upcoming_workshops(None) == []


# --- sekcja „Warsztaty online” na stronie głównej -----------------------------------------------


@freeze_time(DURING_WORKSHOPS)
def test_home_page_announces_the_next_three_workshops(web_client, legacy_content):
    response = web_client.get("/")
    content = response.content.decode()

    # Temat niesie też prowadzącego – tak wpisał go organizator, więc porównujemy sam początek.
    topics = [row["topic"] for row in response.context["workshops"]]
    assert [topic.split(" (prowadzący")[0] for topic in topics] == [
        "Rachunek prawdopodobieństwa i statystyka",
        "Elementy analizy matematycznej",
        "Podstawy mechaniki kwantowej i podstawowe układy kwantowe",
    ]
    assert "Warsztaty online" in content
    # Temat, termin i godziny – czytelnik planuje udział bez wchodzenia na podstronę.
    assert "24 października 2026" in content
    assert "11:00–13:00" in content
    # Warsztat, który już się odbył, nie stoi w zapowiedzi.
    assert "Liczby zespolone" not in content
    # Po komplet prowadzi jeden przycisk, a nie szesnaście wierszy tabeli na stronie głównej.
    assert 'href="/warsztaty/"' in content
    assert "Zobacz wszystkie warsztaty" in content
    assert "13 lutego 2027" not in content


@freeze_time(AFTER_WORKSHOPS)
def test_home_page_hides_the_section_after_the_last_workshop(web_client, legacy_content):
    """Nagłówek nad pustą listą czytałby się jak awaria szablonu, więc sekcja znika w całości."""
    response = web_client.get("/")
    content = response.content.decode()

    assert response.context["workshops"] == []
    assert "Warsztaty online" not in content
    assert "Zobacz wszystkie warsztaty" not in content


@freeze_time(DURING_WORKSHOPS)
def test_home_page_hides_the_section_when_the_workshops_page_is_a_draft(web_client, legacy_content):
    """Zapowiedź czyta stronę opublikowaną – szkic redakcji nie wycieka na stronę główną."""
    page = ContentPage.objects.get(slug=WORKSHOPS_SLUG)
    page.live = False
    page.save()

    response = web_client.get("/")

    assert response.context["workshops_page"] is None
    assert "Warsztaty online" not in response.content.decode()


# --- sekcja „O Olimpiadzie” ---------------------------------------------------------------------


@freeze_time(DURING_WORKSHOPS)
def test_sections_stand_in_the_order_of_the_readers_questions(web_client, legacy_content, edition):
    """Kolejność sekcji jest kolejnością pytań czytelnika, który trafia tu pierwszy raz.

    Co to jest (sekcja „O Olimpiadzie”) → co mam zrobić (kroki) → kiedy (terminy) → z czego się
    przygotować (warsztaty) → co się dzieje (aktualności). Fragmenty są brane z nagłówków sekcji,
    a nie z samych słów: te same słowa stoją w pasku nawigacji, czyli nad całą stroną.
    """
    content = web_client.get("/").content.decode()
    order = [
        "Przyszłość ma naturę kwantową.",  # hero
        'id="o-olimpiadzie"',
        '<h2 class="mt-0">Jak zacząć w 3 krokach</h2>',
        '<h2 class="mt-0">Przebieg zawodów',
        '<h2 class="mt-0">Warsztaty online</h2>',
        '<h2 class="mt-0">Aktualności</h2>',
    ]

    positions = [content.index(fragment) for fragment in order]
    assert positions == sorted(positions)


def test_about_section_disappears_without_content(web_client, legacy_content):
    """Sekcja jest treścią redakcyjną, więc redakcja może ją zdjąć – bez wydania aplikacji."""
    home = HomePage.objects.get()
    home.about_body = []
    home.save()
    home.save_revision().publish()

    content = web_client.get("/").content.decode()

    assert 'id="o-olimpiadzie"' not in content
    assert "Fundacja Quantum AI" in content  # stopka zostaje – to inne źródło (SiteSettings)


def test_about_section_renders_blocks_not_raw_markup(web_client, legacy_content):
    """Treść idzie przez szablony bloków: śródtytuły mają kotwice, ramka jest ``<aside>``."""
    home = HomePage.objects.get()
    home.about_body = [
        ("heading", {"text": "Po co", "level": "2", "anchor": "po-co", "in_toc": True}),
        ("notice", {"tone": "info", "text": RichText("<p>Udział jest bezpłatny.</p>")}),
    ]
    home.save()
    home.save_revision().publish()

    content = web_client.get("/").content.decode()

    assert 'id="po-co"' in content
    assert 'class="notice notice--info"' in content


# --- najważniejsze pozycje menu w przyklejonym pasku -------------------------------------------


def test_primary_menu_items_are_ready_in_the_sticky_bar_and_stay_in_the_service_menu(
    web_client, legacy_content
):
    """Zadania, Harmonogram i Warsztaty są w pasku (ukryte do przyklejenia) **i** w dolnym menu.

    Pasek pokazuje je dopiero po przewinięciu (klasa ``is-stuck`` ze skryptu), więc na górze
    strony nie ma dwóch takich samych rzędów odnośników jeden pod drugim.
    """
    content = web_client.get("/").content.decode()
    sticky_nav = content.split('class="nav nav--primary"', 1)[1].split("</nav>", 1)[0]
    service_nav = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    for path in ("/zadania/", "/harmonogram/", "/warsztaty/"):
        assert f'href="{path}"' in sticky_nav, path
        assert f'href="{path}"' in service_nav, path
    assert 'href="/aktualnosci/"' not in sticky_nav
    assert "data-sticky-nav" in content
    assert 'src="/static/js/sticky-bar.js"' in content
    # Kolejność w pasku to kolejność drzewa stron, tak jak w dolnym menu.
    assert (
        sticky_nav.index('href="/zadania/"')
        < sticky_nav.index('href="/harmonogram/"')
        < sticky_nav.index('href="/warsztaty/"')
    )


def test_primary_item_is_highlighted_on_its_own_page(web_client, legacy_content):
    content = web_client.get("/harmonogram/").content.decode()
    sticky_nav = content.split('class="nav nav--primary"', 1)[1].split("</nav>", 1)[0]
    harmonogram_tag = sticky_nav.split('href="/harmonogram/"', 1)[1].split(">", 1)[0]

    assert 'aria-current="page"' in harmonogram_tag
