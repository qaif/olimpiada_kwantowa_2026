"""Deklaracja dostępności (A11Y-01 § 5): komenda zakładająca projekt strony i odnośnik w stopce."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.accessibility.management.commands.seed_accessibility_statement import PAGE_SLUG
from apps.cms.models import DocumentPage

pytestmark = pytest.mark.django_db

PATH = f"/dokumenty/{PAGE_SLUG}/"


def statement():
    return DocumentPage.objects.get(slug=PAGE_SLUG)


def test_default_is_an_unpublished_draft_for_the_organiser(competition, client_for):
    call_command("seed_accessibility_statement", competition.slug, verbosity=0)

    page = statement()
    assert page.live is False and page.has_unpublished_changes
    assert page.get_parent().slug == "dokumenty"
    # Projekt nie jest jeszcze stroną serwisu – organizator zatwierdza go w /cms/.
    assert client_for(competition).get(PATH).status_code == 404


def test_publish_renders_polish_statement_with_site_contact(competition, client_for):
    call_command("seed_accessibility_statement", competition.slug, publish=True, verbosity=0)

    response = client_for(competition).get(PATH)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Deklaracja dostępności" in html
    assert "ustawy z dnia 4 kwietnia 2019" in html and "WCAG 2.1" in html
    assert "Procedura wnioskowo-skargowa" in html and "Rzecznika Praw Obywatelskich" in html
    assert "Projekt – do zatwierdzenia przez organizatora" in html
    from apps.cms.models import SiteSettings

    site = SiteSettings.for_site(competition.site)
    assert site.contact_email in html


def test_published_statement_is_not_overwritten_without_force(competition):
    call_command("seed_accessibility_statement", competition.slug, publish=True, verbosity=0)
    page = statement()
    page.title = "Deklaracja dostępności – zatwierdzona"
    page.save_revision().publish()

    with pytest.raises(CommandError, match="--force"):
        call_command("seed_accessibility_statement", competition.slug, verbosity=0)
    assert statement().title == "Deklaracja dostępności – zatwierdzona"

    call_command("seed_accessibility_statement", competition.slug, force=True, verbosity=0)
    assert DocumentPage.objects.filter(slug=PAGE_SLUG).count() == 1
    # --force bez --publish zapisuje projekt jako wersję roboczą – opublikowana zostaje do decyzji.
    assert statement().title == "Deklaracja dostępności – zatwierdzona"
    assert statement().get_latest_revision_as_object().title == "Deklaracja dostępności"


def test_international_competition_gets_english_statement(competition):
    competition.default_language = "en"
    competition.save(update_fields=["default_language"])

    call_command("seed_accessibility_statement", competition.slug, publish=True, verbosity=0)

    page = statement()
    assert page.title == "Accessibility statement"
    assert "Enforcement procedure" in str(page.body.render_as_block())


def test_unknown_competition_is_an_error():
    with pytest.raises(CommandError, match="Nie ma konkursu"):
        call_command("seed_accessibility_statement", "nie-ma-takiego", verbosity=0)


def test_footer_links_to_statement_on_every_page(competition, client_for):
    html = client_for(competition).get("/login/").content.decode()
    assert f'href="/dokumenty/{PAGE_SLUG}/"' in html and "Deklaracja dostępności" in html
