"""``manage.py seed_forum_categories`` – działy startowe forum: „Ogólne” i po jednym na warsztat.

Ten plik pilnuje **komendy**, nie ekranu forum: co powstaje z tabeli warsztatów, że nazwa działu
gubi dopisek prowadzącego (ale nie samą informację – ta trafia do opisu), że drugi przebieg nic
nie tworzy i nie nadpisuje ręcznej redakcji koordynatora, oraz że działy trafiają wyłącznie do
konkursu, którego dotyczyło wywołanie.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cms.models import ContentPage
from apps.cms.site_tree import home_page
from apps.cms.workshops import WORKSHOPS_SLUG
from apps.core.models import AuditLog
from apps.forum.management.commands.seed_forum_categories import (
    GENERAL_DESCRIPTION,
    GENERAL_NAME,
    split_topic,
    workshop_description,
)
from apps.forum.models import ForumCategory
from apps.forum.services import AUDIT_CATEGORY_SAVED

pytestmark = pytest.mark.django_db


def workshops_page_with(competition, rows: list[dict]) -> ContentPage:
    """Strona „Warsztaty” **tego** konkursu, z jednym blokiem ``schedule``.

    Ta sama konstrukcja co w ``apps.cms.tests.test_timeline_strip`` i ``test_home_sections``
    (``ContentPage`` z gotowym blokiem, dołożona przez ``add_child`` i opublikowana), tylko pod
    stroną głównej **konkretnego** konkursu – bo test izolacji potrzebuje dwóch konkursów, a nie
    tylko jedynej instalacji jednokonkursowej.
    """
    home = home_page(competition.site)
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
                    "rows": rows,
                },
            )
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def row(topic: str, date_value: date, when: str, time: str = "11:00–14:00") -> dict:
    return {"topic": topic, "date": when, "date_value": date_value, "time": time}


#: Trzy wiersze wzięte wprost z ``apps/cms/fixtures/legacy/warsztaty.md`` – łącznie z tematem,
#: który niesie dwóch prowadzących naraz („i PCSS”), żeby dopisek nie zakładał jednego nazwiska.
WORKSHOP_ROWS = [
    row("Liczby zespolone (prowadzący: Tomasz Sowiński)", date(2026, 10, 10), "10 października 2026"),
    row("Algebra liniowa (prowadzący: Grzegorz Czelusta)", date(2026, 10, 17), "17 października 2026"),
    row(
        "Realizacje komputerów kwantowych (prowadzący: Grzegorz Czelusta i PCSS)",
        date(2026, 10, 24),
        "24 października 2026",
    ),
]


def seed(competition=None, **options):
    args = ("--competition", competition.slug) if competition is not None else ()
    call_command("seed_forum_categories", *args, verbosity=0, **options)


# --- podział tematu (temat → nazwa działu i prowadzący) --------------------------------------------


def test_split_topic_strips_the_lecturer_parenthetical():
    name, lecturer = split_topic("Liczby zespolone (prowadzący: Tomasz Sowiński)")

    assert name == "Liczby zespolone"
    assert lecturer == "Tomasz Sowiński"


def test_split_topic_keeps_two_lecturers_named_together():
    name, lecturer = split_topic("Realizacje komputerów kwantowych (prowadzący: Grzegorz Czelusta i PCSS)")

    assert name == "Realizacje komputerów kwantowych"
    assert lecturer == "Grzegorz Czelusta i PCSS"


def test_split_topic_without_a_parenthetical_keeps_the_whole_topic():
    """Temat bez dopisku (inne źródło danych niż dzisiejszy import) zostaje w całości."""
    name, lecturer = split_topic("Warsztat wprowadzający")

    assert name == "Warsztat wprowadzający"
    assert lecturer == ""


def test_workshop_description_matches_the_brief_example():
    example = row(
        "Liczby zespolone (prowadzący: Tomasz Sowiński)", date(2026, 10, 10), "10 października 2026"
    )

    description = workshop_description(example, "Tomasz Sowiński")

    assert description == (
        "Warsztat 10 października 2026, 11:00–14:00 · prowadzący: Tomasz Sowiński. "
        "Pytania i dyskusja do tego spotkania."
    )


def test_workshop_description_without_a_lecturer_omits_the_dash():
    example = row("Warsztat wprowadzający", date(2026, 9, 1), "1 września 2026")

    description = workshop_description(example, "")

    assert description == "Warsztat 1 września 2026, 11:00–14:00. Pytania i dyskusja do tego spotkania."


# --- utworzenie ------------------------------------------------------------------------------------


def test_creates_general_plus_one_category_per_workshop_in_table_order(competition):
    workshops_page_with(competition, WORKSHOP_ROWS)

    seed(competition)

    categories = list(ForumCategory.objects.for_competition(competition).order_by("ordering"))
    assert [item.name for item in categories] == [
        GENERAL_NAME,
        "Liczby zespolone",
        "Algebra liniowa",
        "Realizacje komputerów kwantowych",
    ]
    assert [item.ordering for item in categories] == [0, 10, 20, 30]
    general = categories[0]
    assert general.description == GENERAL_DESCRIPTION
    assert general.is_open is True


def test_category_names_never_carry_the_lecturer_parenthetical(competition):
    workshops_page_with(competition, WORKSHOP_ROWS)

    seed(competition)

    names = ForumCategory.objects.for_competition(competition).values_list("name", flat=True)
    assert not any("prowadzący" in name for name in names)
    assert not any("(" in name for name in names)


def test_workshop_category_description_carries_the_date_and_the_lecturer(competition):
    workshops_page_with(competition, WORKSHOP_ROWS[:1])

    seed(competition)

    category = ForumCategory.objects.for_competition(competition).get(name="Liczby zespolone")
    assert category.description == (
        "Warsztat 10 października 2026, 11:00–14:00 · prowadzący: Tomasz Sowiński. "
        "Pytania i dyskusja do tego spotkania."
    )


def test_without_a_workshops_page_only_general_is_created(competition):
    """Brak strony „Warsztaty” (dopóki nikt nie uruchomił ``seed_legacy_content``) nie jest błędem –
    forum ma dostać przynajmniej dział „Ogólne”, zanim jeszcze ktokolwiek napisze harmonogram."""
    seed(competition)

    names = ForumCategory.objects.for_competition(competition).values_list("name", flat=True)
    assert list(names) == [GENERAL_NAME]


def test_each_created_category_leaves_an_audit_entry_with_no_actor(competition):
    """Komenda chodzi poza żądaniem – nikt nie kliknął „zapisz” – więc aktorem jest system."""
    workshops_page_with(competition, WORKSHOP_ROWS[:1])

    seed(competition)

    entries = AuditLog.objects.filter(action=AUDIT_CATEGORY_SAVED)
    assert entries.count() == 2  # „Ogólne” + jeden warsztat
    assert all(entry.actor_id is None for entry in entries)


# --- idempotencja ------------------------------------------------------------------------------------


def test_a_second_run_creates_nothing_and_keeps_a_renamed_category_untouched(competition):
    """Rozpoznanie po slugu, a nie po nazwie: koordynator zdążył przeredagować dział, zanim
    ktoś dopisał kolejny warsztat, a drugi przebieg komendy nie ma prawa cofnąć tej redakcji."""
    workshops_page_with(competition, WORKSHOP_ROWS[:1])
    seed(competition)
    renamed = ForumCategory.objects.for_competition(competition).get(name="Liczby zespolone")
    renamed.name = "Fizyka liczb zespolonych (nazwa poprawiona przez koordynatora)"
    renamed.description = "Opis poprawiony przez koordynatora."
    renamed.save(update_fields=["name", "description"])
    before = list(
        ForumCategory.objects.for_competition(competition)
        .order_by("pk")
        .values("pk", "slug", "name", "description", "ordering")
    )

    seed(competition)

    after = list(
        ForumCategory.objects.for_competition(competition)
        .order_by("pk")
        .values("pk", "slug", "name", "description", "ordering")
    )
    assert after == before
    assert AuditLog.objects.filter(action=AUDIT_CATEGORY_SAVED).count() == 2  # bez trzeciego wpisu


# --- konkurs -------------------------------------------------------------------------------------


def test_categories_land_only_in_the_requested_competition(competition, other_competition):
    workshops_page_with(competition, WORKSHOP_ROWS[:1])

    seed(competition)

    assert ForumCategory.objects.for_competition(competition).count() == 2
    assert ForumCategory.objects.for_competition(other_competition).count() == 0


def test_an_unknown_competition_slug_is_refused():
    with pytest.raises(CommandError):
        call_command("seed_forum_categories", "--competition", "nie-ma-takiego-konkursu", verbosity=0)


def test_more_than_one_competition_requires_an_explicit_slug(competition, other_competition):
    with pytest.raises(CommandError):
        seed()


def test_a_single_competition_installation_needs_no_flag(competition):
    seed()

    names = ForumCategory.objects.for_competition(competition).values_list("name", flat=True)
    assert GENERAL_NAME in names
