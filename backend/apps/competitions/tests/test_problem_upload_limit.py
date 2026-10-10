"""``Problem.max_file_mb`` nie obiecuje więcej, niż przepuści proxy (audyt 10.10.2026, niskie).

Caddy odcina żądania powyżej ``MAX_UPLOAD_MB`` (domyślnie 25 MB) kodem 413, zanim aplikacja zdąży
powiedzieć cokolwiek po ludzku. Zadanie z limitem 100 MB zapraszało więc uczestnika do wysłania
pliku, którego serwis nie mógł przyjąć.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.competitions.models import MAX_FILE_MB_LIMIT, max_file_mb_limit
from apps.competitions.tests.factories import ProblemFactory, StageFactory

pytestmark = pytest.mark.django_db


def test_the_limit_follows_the_proxy_with_a_megabyte_of_headroom(settings):
    settings.MAX_UPLOAD_MB = 25

    assert max_file_mb_limit() == 24


def test_the_clamav_limit_still_caps_a_generous_proxy(settings):
    settings.MAX_UPLOAD_MB = 500

    assert max_file_mb_limit() == MAX_FILE_MB_LIMIT


def test_a_problem_above_the_proxy_limit_is_invalid_with_the_real_number(settings):
    settings.MAX_UPLOAD_MB = 25
    problem = ProblemFactory.build(stage=StageFactory(), max_file_mb=30)

    with pytest.raises(ValidationError) as caught:
        problem.clean()

    assert "1–24 MB" in caught.value.message_dict["max_file_mb"][0]


def test_a_problem_within_the_proxy_limit_is_valid(settings):
    settings.MAX_UPLOAD_MB = 25

    ProblemFactory.build(stage=StageFactory(), max_file_mb=24).clean()


def test_the_coordinator_form_shows_the_error_instead_of_saving(settings):
    """Formularz zadania jest ``ModelForm`` – błąd z ``clean()`` trafia pod pole, a nie do 500."""
    from apps.web.forms import ProblemForm

    settings.MAX_UPLOAD_MB = 25
    stage = StageFactory()
    form = ProblemForm(
        data={"number": 1, "title": "Zadanie", "allowed_formats": ["pdf"], "max_file_mb": 50},
        instance=ProblemFactory.build(stage=stage),
        stage=stage,
    )

    assert not form.is_valid()
    assert "max_file_mb" in form.errors
