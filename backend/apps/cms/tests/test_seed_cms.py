"""``manage.py seed_cms`` – treści demonstracyjne muszą być idempotentne.

Komenda bywa uruchamiana po każdym deployu dev-a; drugie wywołanie nie może zduplikować stron
ani nadpisać tekstu, który w międzyczasie napisał redaktor.
"""

import pytest
from django.core.management import call_command

from apps.cms.models import ArchiveEditionPage, HomePage, NewsPage
from apps.competitions.tests.factories import EditionFactory

pytestmark = pytest.mark.django_db


def test_seed_cms_is_idempotent():
    archived = EditionFactory(is_current=False)

    call_command("seed_cms", verbosity=0)
    counts = (NewsPage.objects.count(), ArchiveEditionPage.objects.count())
    call_command("seed_cms", verbosity=0)

    assert counts[0] > 0
    assert ArchiveEditionPage.objects.filter(edition=archived).count() == 1
    assert (NewsPage.objects.count(), ArchiveEditionPage.objects.count()) == counts


def test_seed_cms_does_not_overwrite_editor_content():
    home = HomePage.objects.get()
    home.hero_text = "<p>Tekst redaktora.</p>"
    home.save()

    call_command("seed_cms", verbosity=0)

    home.refresh_from_db()
    assert home.hero_text == "<p>Tekst redaktora.</p>"
