"""Podpowiedzi i błędy pól są powiązane z polem (A11Y-01 § 4.3, WCAG 1.3.1 / 3.3.1).

Django dokleja polu ``aria-describedby="<id>_helptext"``, ale szablon pisany ręcznie musi nadać
podpowiedzi ten identyfikator – bez niego odwołanie wisi w próżni i czytnik ekranu nie czyta
podpowiedzi wcale. Audyt znalazł 25 takich miejsc; ten test pilnuje formularza rejestracji (zgody,
telefon, data urodzenia, CAPTCHA) i reguły ogólnej dla szablonów.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def edition():
    """Bieżąca edycja z otwartą rejestracją – bez niej ``/register/`` nie pokazuje formularza."""
    from apps.competitions.tests.factories import CurrentEditionFactory

    return CurrentEditionFactory()

DESCRIBEDBY = re.compile(r'aria-describedby="([^"]+)"')


def dangling(html: str) -> set[str]:
    ids = set(re.findall(r'\sid="([^"]+)"', html))
    referenced = {ref for refs in DESCRIBEDBY.findall(html) for ref in refs.split()}
    return referenced - ids


def test_registration_form_has_no_dangling_descriptions(competition, client_for):
    html = client_for(competition).get("/register/").content.decode()
    assert 'aria-describedby="id_captcha_helptext"' in html
    assert 'id="id_captcha_helptext"' in html
    assert dangling(html) == set()


def test_captcha_error_is_announced_with_the_field(competition, client_for):
    response = client_for(competition).post("/register/", {})
    html = response.content.decode()
    assert response.status_code == 200
    assert 'aria-describedby="id_captcha_helptext id_captcha_error"' in html
    assert 'id="id_captcha_error"' in html
    assert dangling(html) == set()


def test_templates_render_help_text_with_the_id_django_points_to():
    """Każde ręczne ``{{ x.help_text }}`` w ``<span>`` ma ``id="{{ x.auto_id }}_helptext"``."""
    roots = [Path(settings.BASE_DIR) / "templates", *Path(settings.BASE_DIR, "apps").glob("*/templates")]
    offenders = []
    pattern = re.compile(r'<span class="[^"]*">\{\{ (\S+)\.help_text \}\}</span>')
    for root in roots:
        for path in root.rglob("*.html"):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(settings.BASE_DIR)}: {match.group(0)[:80]}")
    assert offenders == []
