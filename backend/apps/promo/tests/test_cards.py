"""Karty strony ``/plakaty/`` z listy plików (``apps.promo.cards``).

Czego pilnują te testy:

- pliki z identyczną, niepustą grupą stają na **jednej** karcie, pliki bez grupy – każdy na swojej,
- karta stoi tam, gdzie jej **pierwszy** plik na liście koordynatora, a przyciski w karcie idą
  w kolejności plików,
- podgląd karty to pierwszy podgląd w grupie (PDF bez miniatury nie zabiera karcie obrazka), opis –
  pierwszy niepusty,
- napis przycisku bez ``variant_label`` to sam format pliku.
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile

from apps.promo.availability import public_materials
from apps.promo.cards import build_cards
from apps.promo.tests.helpers import image_bytes, make_material

pytestmark = pytest.mark.django_db


def cards_of(competition):
    return build_cards(list(public_materials(competition)))


def test_materials_with_the_same_group_share_one_card(competition):
    make_material(competition, title="A3 (JPG)", fmt="jpg", group="A3 · 297×420 mm", position=0)
    make_material(competition, title="A3 (PDF)", group="A3 · 297×420 mm", position=1)
    make_material(
        competition,
        title="A3 (PDF ze spadem)",
        group="A3 · 297×420 mm",
        variant_label="PDF ze spadem 3 mm",
        position=2,
    )

    [card] = cards_of(competition)

    assert card.is_group is True
    assert card.heading == "A3 · 297×420 mm"
    assert [material.variant_name for material in card.materials] == ["JPG", "PDF", "PDF ze spadem 3 mm"]


def test_card_order_follows_the_first_material_and_buttons_follow_positions(competition):
    make_material(competition, title="A3 PDF", group="A3", position=3)
    make_material(competition, title="A4 JPG", fmt="jpg", group="A4", position=1)
    make_material(competition, title="Ulotka", position=2)
    make_material(competition, title="A3 JPG", fmt="jpg", group="A3", position=0)
    make_material(competition, title="A4 PDF", group="A4", position=4)

    cards = cards_of(competition)

    assert [card.heading for card in cards] == ["A3", "A4", "Ulotka"]
    assert [material.title for material in cards[0].materials] == ["A3 JPG", "A3 PDF"]
    assert [material.title for material in cards[1].materials] == ["A4 JPG", "A4 PDF"]


def test_materials_without_group_keep_their_own_cards(competition):
    make_material(competition, title="Plakat A", position=0)
    make_material(competition, title="Plakat B", position=1)

    cards = cards_of(competition)

    assert [card.heading for card in cards] == ["Plakat A", "Plakat B"]
    assert [card.is_group for card in cards] == [False, False]
    assert [len(card.materials) for card in cards] == [1, 1]


def test_preview_falls_back_to_the_first_material_that_has_one(competition):
    make_material(competition, title="A3 PDF", group="A3", position=0)
    with_preview = make_material(competition, title="A3 PDF 2", group="A3", position=1)
    with_preview.preview.save("podglad.jpg", ContentFile(image_bytes("JPEG")), save=True)
    make_material(competition, title="A2 PDF", group="A2", position=2)

    first, second = cards_of(competition)

    assert first.preview_material == with_preview
    assert second.preview_material is None


def test_description_is_the_first_non_empty_one(competition):
    make_material(competition, title="A3 JPG", group="A3", position=0)
    make_material(competition, title="A3 PDF", group="A3", description="Do gabloty", position=1)

    [card] = cards_of(competition)

    assert card.description == "Do gabloty"


def test_drafts_and_archive_do_not_join_the_card(competition):
    from django.utils import timezone

    make_material(competition, title="A3 JPG", fmt="jpg", group="A3", position=0)
    make_material(competition, title="A3 szkic", group="A3", published=False, position=1)
    make_material(competition, title="A3 archiwum", group="A3", archived_at=timezone.now(), position=2)

    [card] = cards_of(competition)

    assert [material.title for material in card.materials] == ["A3 JPG"]


def test_same_group_in_another_competition_is_a_separate_card(competition, other_competition):
    make_material(competition, title="Nasz A3", group="A3", position=0)
    make_material(other_competition, title="Ich A3", group="A3", position=0)

    [ours] = cards_of(competition)
    [theirs] = cards_of(other_competition)

    assert [material.title for material in ours.materials] == ["Nasz A3"]
    assert [material.title for material in theirs.materials] == ["Ich A3"]


def test_variant_name_defaults_to_the_format(competition):
    pdf = make_material(competition, fmt="pdf")
    png = make_material(competition, fmt="png")
    labelled = make_material(competition, fmt="pdf", variant_label="PDF ze spadem 3 mm")

    assert (pdf.variant_name, png.variant_name, labelled.variant_name) == ("PDF", "PNG", "PDF ze spadem 3 mm")
