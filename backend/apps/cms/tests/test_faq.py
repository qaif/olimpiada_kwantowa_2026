"""Najczęstsze pytania (``/faq/``): treść, struktura strony i reguły importu.

Trzy rzeczy, których pilnują te testy:

- **strona jest kompletna i publiczna.** Seed zakłada ją opublikowaną, z kompletem pytań
  z ``FAQ_ENTRIES``, i wypisuje je pogrupowane w sekcje w kolejności pierwszego wystąpienia
  sekcji (a nie w kolejności, w jakiej redaktor dopisał wiersze),
- **każde pytanie ma trwałą kotwicę.** Odpowiedź na zgłoszenie odsyła do konkretnego pytania,
  więc odnośnik musi przeżyć poprawkę sformułowania – identyfikator pochodzi z wiersza w bazie,
  a nie z treści,
- **reguły redakcyjne importu.** Strona zredagowana albo skasowana w ``/cms/`` zostaje nietknięta.
  To ta sama ochrona, co przy stronach treści, i bez niej dopisane przez organizatora pytanie
  znikałoby przy najbliższym wdrożeniu.

Do tego: FAQ jest w pełnym menu serwisu, ale **nie** w przyklejonym pasku (``PRIMARY_MENU_SLUGS``)
– pasek trzyma zadania, terminy i warsztaty.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.cms.context_processors import PRIMARY_MENU_SLUGS
from apps.cms.management.commands.seed_legacy_content import FAQ_ENTRIES, FAQ_SLUG, FAQ_TITLE
from apps.cms.models import FAQPage

pytestmark = pytest.mark.django_db

FAQ_URL = "/faq/"


@pytest.fixture
def faq():
    call_command("seed_legacy_content", verbosity=0)
    return FAQPage.objects.get(slug=FAQ_SLUG)


# --- treść ----------------------------------------------------------------------------------


def test_the_seed_publishes_the_page_with_every_question(faq):
    assert faq.live
    assert faq.title == FAQ_TITLE
    assert faq.entries.count() == len(FAQ_ENTRIES)


def test_the_page_answers_the_questions_people_actually_ask(web_client, faq):
    """Każde pytanie odpowiada miejscu, w którym uczestnik utyka – nie jest wymyślone."""
    body = web_client.get(FAQ_URL).content.decode()

    assert "aktywacyjnego" in body
    # Zdjęcie rozwiązania pisanego ręcznie jest osobną prośbą organizatora i musi być opisane.
    assert "JPEG" in body
    assert "najnowsza" in body
    assert "reklamacj" in body


def test_every_question_is_a_native_disclosure_element(web_client, faq):
    """``<details>`` działa klawiaturą i bez JavaScriptu – przy strict CSP to jedyne rozwiązanie."""
    body = web_client.get(FAQ_URL).content.decode()

    # Po klasie, a nie po samym ``<summary>``: rozwijana pozycja menu w szablonie bazowym też
    # jest ``<details>``, więc gołe zliczanie znacznika mierzyłoby nawigację razem z treścią.
    assert body.count('class="faq__question"') == len(FAQ_ENTRIES)
    assert body.count("faq__item") == len(FAQ_ENTRIES)


def test_every_question_has_a_stable_anchor(web_client, faq):
    """Odnośnik wysłany w odpowiedzi na zgłoszenie ma działać także po poprawce sformułowania."""
    entry = faq.entries.first()
    before = entry.anchor

    entry.question = "Zupełnie inaczej sformułowane pytanie?"
    entry.save()

    assert entry.anchor == before
    assert f'id="{before}"' in web_client.get(FAQ_URL).content.decode()


def test_questions_are_grouped_by_section_in_order_of_first_appearance(faq):
    """``{% regroup %}`` grupuje po kolejności wystąpienia – wpis dopisany na końcu zakładałby
    drugą sekcję o tej samej nazwie. Tutaj sekcja powstaje raz."""
    sections = [group["section"] for group in faq.sections()]

    assert sections == list(dict.fromkeys(section for section, _, _ in FAQ_ENTRIES))
    assert len(sections) == len(set(sections))


def test_the_page_points_at_the_support_form(web_client, faq):
    """Kto przewinął całą stronę i nie znalazł odpowiedzi, jest tą osobą, która ma napisać."""
    body = web_client.get(FAQ_URL).content.decode()

    assert "/support/new/" in body


# --- menu -----------------------------------------------------------------------------------


def test_the_faq_is_in_the_full_site_menu(web_client, faq):
    body = web_client.get(FAQ_URL).content.decode()

    assert f'href="{FAQ_URL}"' in body


def test_the_faq_is_not_in_the_sticky_primary_bar():
    """Pasek jest krótki i trzyma to, czego szuka się w trakcie zawodów, a nie po nich."""
    assert FAQ_SLUG not in PRIMARY_MENU_SLUGS


def test_the_footer_links_to_the_faq_on_every_page(web_client, faq):
    """Stopka jest miejscem, w którym szuka się pomocy – obok kontaktu i dokumentów."""
    body = web_client.get("/login/").content.decode()

    assert f'href="{FAQ_URL}"' in body


# --- reguły importu --------------------------------------------------------------------------


def test_a_page_edited_in_the_cms_is_left_alone(faq):
    """Rewizja z autorem znaczy „treść redakcji” – powtórny import nie może jej cofnąć."""
    from apps.accounts.tests.factories import CoordinatorFactory

    faq.intro = "<p>Wstęp napisany przez organizatora.</p>"
    faq.save()
    faq.save_revision(user=CoordinatorFactory()).publish()

    call_command("seed_legacy_content", verbosity=0)

    assert FAQPage.objects.get(slug=FAQ_SLUG).intro == "<p>Wstęp napisany przez organizatora.</p>"


def test_a_page_deleted_in_the_cms_is_not_recreated(faq):
    """Organizator, który usunął stronę, ma ją zobaczyć usuniętą także po następnym wdrożeniu."""
    from wagtail.models import Page

    from apps.accounts.tests.factories import CoordinatorFactory

    Page.objects.get(pk=faq.pk).delete(user=CoordinatorFactory())

    call_command("seed_legacy_content", verbosity=0)

    assert not FAQPage.objects.filter(slug=FAQ_SLUG).exists()


def test_a_repeated_import_does_not_duplicate_the_questions(faq):
    call_command("seed_legacy_content", verbosity=0)

    assert FAQPage.objects.get(slug=FAQ_SLUG).entries.count() == len(FAQ_ENTRIES)


def test_the_page_slug_cannot_shadow_an_application_address():
    """``/support/`` i ``/status/`` należą do aplikacji – strona CMS o takim slugu byłaby martwa."""
    from apps.cms.models import RESERVED_SLUGS

    assert {"support", "status"} <= RESERVED_SLUGS
