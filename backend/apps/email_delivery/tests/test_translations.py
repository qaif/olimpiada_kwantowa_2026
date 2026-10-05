"""Napisy MAIL-02 czytane przez uczestnika IQO są przetłumaczone we wszystkich 10 językach interfejsu."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.utils import translation

from apps.email_delivery.fields import CheckedEmailField

BANNER = (
    "Nie możemy dostarczyć poczty na adres %(email)s – serwer odbiorcy odpowiada, że taka skrzynka albo "
    "domena nie istnieje. Nie dostaniesz od nas listów, dopóki adres nie zostanie poprawiony."
)
LANGUAGES = [code for code, _label in settings.LANGUAGES if code != "pl"]


def test_ten_languages_are_covered():
    assert len(LANGUAGES) == 10


@pytest.mark.parametrize("language", LANGUAGES)
def test_messages_are_translated(language):
    field = CheckedEmailField()
    with translation.override(language):
        texts = [
            translation.gettext(BANNER),
            translation.gettext("Mój adres jest poprawny"),
            translation.gettext("Czy chodziło Ci o %s?"),
            str(field.error_messages["email_typo"]),
            str(field.error_messages["no_mail"]),
        ]
    assert "Nie możemy" not in texts[0] and "%(email)s" in texts[0]
    assert texts[1] != "Mój adres jest poprawny"
    assert texts[2] != "Czy chodziło Ci o %s?" and "%s" in texts[2]
    assert "%(suggestion)s" in texts[3] and not texts[3].startswith("Sprawdź")
    assert "%(domain)s" in texts[4] and not texts[4].startswith("Domena")
