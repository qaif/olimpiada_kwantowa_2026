"""Wspólne fixture'y testów djcms.

Strony powstają przez publiczne API django CMS (``cms.api.create_page``) i są publikowane przez
djangocms-versioning (``Version.publish``) – tak samo, jak zrobi to importer (DJ-01g). Testy nie
piszą do tabel CMS-a bezpośrednio: zmiana schematu w kolejnym wydaniu django CMS ma wywrócić
test na API, a nie przejść niezauważona przez ręcznie sklejony wiersz.
"""

from __future__ import annotations

import pytest
from django.core.cache import caches


@pytest.fixture(autouse=True)
def _clear_caches():
    """Bufory (także licznik blokady logowania) nie przenoszą stanu między testami."""
    for alias in ("default", "throttle"):
        caches[alias].clear()
    yield
    for alias in ("default", "throttle"):
        caches[alias].clear()


@pytest.fixture
def editor(django_user_model):
    """Konto personelu (``is_staff``) – dostaje luźną politykę CSP i pasek narzędzi."""
    return django_user_model.objects.create_user(
        username="redaktor@example.com",
        email="redaktor@example.com",
        password="haslo-redaktora-123",
        is_staff=True,
    )


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin@example.com", email="admin@example.com", password="haslo-admina-123456"
    )


@pytest.fixture
def make_page(superuser):
    """Tworzy stronę w korzeniu drzewa; ``publish=True`` publikuje jej wersję (versioning)."""
    from cms.api import create_page
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    def _make(title: str, slug: str, *, publish: bool = True, template: str = "dj/pages/content.html"):
        page = create_page(title, template, "pl", slug=slug, created_by=superuser, in_navigation=True)
        if publish:
            content = PageContent.admin_manager.get(page=page, language="pl")
            Version.objects.get_for_content(content).publish(superuser)
        return page

    return _make
