"""Fixture'y testów stron i wtyczek żywych (DJ-01f).

Odpowiedzi API mają kształt z **implementacji** (``backend/apps/cms/djcms_api/serializers.py``),
a nie tylko ze speca: ``X``/``X_local_time``/``X_local_datetime`` przy każdej dacie, ``results_url``
tylko przy ogłoszonej publikacji, ``district``/``category`` w wierszu tylko, gdy są w snapshotcie.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

#: Szablony stron DJ-01f. ``CMS_TEMPLATES`` należy do ustawień (DJ-01e); dopóki ich tam nie ma,
#: testy dopisują je same – ``create_page`` odmawia szablonu spoza listy.
LIVE_TEMPLATES = [
    ("dj/pages/home.html", "Strona główna"),
    ("dj/pages/problems.html", "Zadania"),
    ("dj/pages/results.html", "Wyniki"),
    ("dj/pages/archive_edition.html", "Edycja w archiwum"),
]


@pytest.fixture(autouse=True)
def _live_templates(settings):
    known = {name for name, _ in settings.CMS_TEMPLATES}
    settings.CMS_TEMPLATES = [*settings.CMS_TEMPLATES, *(t for t in LIVE_TEMPLATES if t[0] not in known)]


@pytest.fixture
def api_up(main_api, chrome_payload):
    """API działa: rama z pełną odpowiedzią ``chrome``; endpointy danych ustawia test.

    Bez ``chrome`` rama zapytałaby pierwsza, dostała błąd i otworzyła bezpiecznik – a wtedy także
    endpoint ustawiony przez test nie zostałby zapytany (bezpiecznik jest wspólny dla API).
    """
    main_api.set("chrome", chrome_payload())
    return main_api


def iso(delta: timedelta) -> str:
    return timezone.localtime(timezone.now() + delta).isoformat()


def stage_dto(**overrides) -> dict:
    """``StageDTO`` – etap otwarty dzień temu, termin za dwa tygodnie."""
    stage = {
        "id": 12,
        "display_name": "Etap I",
        "kind": "ELIM",
        "is_interview": False,
        "is_training": False,
        "location": "",
        "edition_year_label": "I edycja 2026/2027",
        "opens_at": iso(timedelta(days=-1)),
        "opens_at_local_time": "25 września 2026 00:00 (czas polski)",
        "opens_at_local_datetime": "25 września 2026 00:00",
        "deadline_at": iso(timedelta(days=14)),
        "deadline_at_local_time": "10 października 2026 23:59 (czas polski)",
        "deadline_at_local_datetime": "10 października 2026 23:59",
        "submission_deadline": iso(timedelta(days=14)),
        "submission_deadline_local_time": "10 października 2026 23:59 (czas polski)",
        "submission_deadline_local_datetime": "10 października 2026 23:59",
        "review_deadline_at": iso(timedelta(days=28)),
        "review_deadline_at_local_time": "24 października 2026 23:59 (czas polski)",
        "review_deadline_at_local_datetime": "24 października 2026 23:59",
        "grace_seconds": 0,
        "event_range": None,
        "event_dates": "",
        "results_url": None,
    }
    stage.update(overrides)
    return stage


def edition_dto(**overrides) -> dict:
    edition = {
        "id": 3,
        "year_label": "I 2026/2027",
        "title": "I edycja 2026/2027",
        "title_cap": "I Edycja 2026/2027",
    }
    edition.update(overrides)
    return edition


def problem_dto(number: int, title: str, **overrides) -> dict:
    problem = {
        "id": 50 + number,
        "number": number,
        "title": title,
        "allowed_formats": ["pdf", "jpg"],
        "statement_url": f"https://olimpiada.example/competitions/problems/{50 + number}/statement/",
    }
    problem.update(overrides)
    return problem


def stage_row(stage: dict, **overrides) -> dict:
    row = {
        "stage": stage,
        "is_open": True,
        "has_opened": True,
        "has_results": False,
        "status": "open",
        "status_label": "otwarty",
        "badge_class": "badge badge--accent",
        "is_onsite_event": False,
        "date_range": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def live_page(make_page, superuser):
    """Strona z wtyczkami w slotach, opublikowana: ``live_page("Zadania", "zadania", "dj/pages/problems.html",
    problems=[("ProblemsPlugin", {"closed_notice": "…"})])``."""
    from cms.api import add_plugin
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    def _make(title: str, slug: str, template: str, *, home: bool = False, **slots):
        page = make_page(title, slug, template=template, publish=False)
        content = PageContent.admin_manager.get(page=page, language="pl")
        placeholders = content.rescan_placeholders()
        for slot, plugins in slots.items():
            for plugin_type, fields in plugins:
                add_plugin(placeholders[slot], plugin_type, "pl", **fields)
        Version.objects.get_for_content(content).publish(superuser)
        if home:
            page.set_as_homepage(superuser)
        return page

    return _make
