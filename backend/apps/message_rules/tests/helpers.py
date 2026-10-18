"""Strona „Warsztaty” z harmonogramem i reguła z domyślnymi polami – wspólne dla testów z bazą."""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from apps.cms.models import ContentPage
from apps.cms.site_tree import home_page
from apps.cms.workshops import WORKSHOPS_SLUG
from apps.message_rules import services
from apps.message_rules.models import RuleKind, RuleTiming

MEET = "https://meet.google.com/izu-qrvp-kuw"


def today() -> dt.date:
    return timezone.localdate()


def row(topic: str, day: dt.date | None, time: str = "11:00–14:00", lecturer: str = "") -> dict:
    return {
        "topic": topic,
        "date": day.strftime("%d.%m.%Y") if day else "do potwierdzenia",
        "date_value": day,
        "time": time,
        "lecturer": lecturer,
    }


def _block(rows: list[dict]):
    return (
        "schedule",
        {
            "caption": "",
            "topic_label": "Temat",
            "date_label": "Termin",
            "time_label": "Godziny",
            "rows": rows,
        },
    )


def workshops_page_for(competition, rows: list[dict]) -> ContentPage:
    """Strona „Warsztaty” **tego** konkursu z jednym blokiem ``schedule`` (jak w ``workshop_materials``)."""
    home = home_page(competition.site)
    page = ContentPage(title="Warsztaty", slug=WORKSHOPS_SLUG, live=True, body=[_block(rows)])
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def rewrite_rows(page: ContentPage, rows: list[dict]) -> None:
    page.body = [_block(rows)]
    page.save()
    page.save_revision().publish()


def workshop_rule(competition, actor, **fields):
    """Reguła „w dniu warsztatu o 06:00 do wszystkich uczestników i nauczycieli” – przykład z podręcznika."""
    defaults = {
        "name": "Warsztaty – rano w dniu zajęć",
        "enabled": True,
        "group": "ALL_PARTICIPANTS_AND_TEACHERS",
        "parameters": {"include_past_editions": False},
        "target": {"past_editions": False, "label": "bieżąca edycja"},
        "subject_template": "Dziś warsztat: {temat}",
        "body_template": "{temat}, {data}, {godziny}. Prowadzi {prowadzacy}. Link: {link}",
        "link": MEET,
        "timing": RuleTiming.DAY_AT,
        "days_before": [0],
        "send_time": dt.time(6, 0),
    }
    return services.create_rule(
        competition=competition, actor=actor, kind=RuleKind.WORKSHOP, **{**defaults, **fields}
    )
