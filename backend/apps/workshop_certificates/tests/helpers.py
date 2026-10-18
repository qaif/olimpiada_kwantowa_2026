"""Świat testów reguły: strona „Warsztaty” konkursu, edycja bieżąca z etapem i uczestnicy z wpisem."""

from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.cms.models import ContentPage, WorkshopAttendance
from apps.cms.site_tree import home_page
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_key
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.workshop_certificates.models import AutoIssuePolicy

#: Cztery warsztaty – tyle, żeby próg 3 dało się spełnić, nie spełnić i przekroczyć.
ROWS = [
    ("Kubity i bramki kwantowe", date(2026, 11, 12), "dr Anna Kowalska"),
    ("Splątanie i nierówności Bella", date(2026, 12, 10), "prof. Jan Nowak"),
    ("Algorytm Grovera", date(2027, 1, 14), "dr Ewa Lis"),
    ("Kryptografia kwantowa", date(2027, 2, 11), "dr Piotr Wrona"),
]


def schedule_block(rows=ROWS) -> tuple:
    return (
        "schedule",
        {
            "caption": "",
            "topic_label": "Temat",
            "date_label": "Termin",
            "time_label": "",
            "lecturer_label": "Prowadzący",
            "rows": [
                {
                    "topic": topic,
                    "date": when.strftime("%d.%m.%Y"),
                    "date_value": when,
                    "time": "",
                    "lecturer": who,
                }
                for topic, when, who in rows
            ],
        },
    )


def workshops_page_for(competition, rows=ROWS) -> ContentPage:
    """Strona „Warsztaty” **tego** konkursu z jednym blokiem ``schedule``.

    Konkurs drugi (fikstura ``other_competition``) ma za korzeń witryny gołą ``Page`` bez strony
    głównej – wtedy strona staje wprost pod korzeniem, czyli nadal w poddrzewie **tej** witryny.
    """
    home = home_page(competition.site) or competition.site.root_page
    page = ContentPage(title="Warsztaty", slug=WORKSHOPS_SLUG, live=True, body=[schedule_block(rows)])
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def keys(rows=ROWS) -> list[str]:
    return [workshop_key(topic, when) for topic, when, _ in rows]


def current_stage(competition):
    """Edycja bieżąca konkursu z jednym etapem – do niej reguła przypina dokumenty."""
    edition = CurrentEditionFactory(competition=competition)
    return StageFactory(
        competition=competition, edition=edition, opens_at=timezone.now() - timedelta(days=60)
    )


def participant_in(stage, first_name="Łucja", last_name="Śniadecka"):
    participant = ParticipantFactory(
        competition=stage.edition.competition, user=UserFactory(first_name=first_name, last_name=last_name)
    )
    StageEntryFactory(competition=stage.edition.competition, stage=stage, participant=participant)
    return participant


def attend(participant, count: int) -> None:
    """Obecność na ``count`` pierwszych warsztatach – pojedynczymi ``create()`` (droga ``post_save``)."""
    for key in keys()[:count]:
        WorkshopAttendance.objects.create(participant=participant, workshop_key=key)


def enable(edition, *, threshold: int = 3, notify: bool = False, template=None) -> AutoIssuePolicy:
    policy, _ = AutoIssuePolicy.objects.update_or_create(
        edition=edition,
        defaults={
            "enabled": True,
            "min_workshops": threshold,
            "notify_participants": notify,
            "template": template,
        },
    )
    return policy
