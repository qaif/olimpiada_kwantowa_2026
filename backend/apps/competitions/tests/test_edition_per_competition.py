"""Wydanie D: rocznik jest rocznikiem **konkursu** – i tylko tam jest jedyny.

Dwie więzi z ``docs/UNIWERSALNY-ETAP-1.md`` § 1.4 zmieniły zakres z instalacji na konkurs:

- ``competitions_edition_single_current`` – jedna edycja bieżąca **na konkurs**,
- ``competitions_edition_unique_year_label`` – oznaczenie rocznika unikalne **u organizatora**
  (zamiast ``unique=True`` na kolumnie).

Test pilnuje obu kierunków naraz, bo tylko razem mają sens. Sama zmiana zakresu w dół („teraz
wolno więcej”) jest łatwa do zrobienia przez pomyłkę i wygląda jak działający serwis do dnia,
w którym dwie edycje bieżące jednego konkursu rozjadą harmonogram; sama zmiana w górę zabroniłaby
drugiemu organizatorowi nazwać swój rocznik tak, jak nazwał go pierwszy.

Ostatnia grupa (``clean()``) strzeże § 0: komunikat, który koordynator Olimpiady Kwantowej widzi
pod polem „edycja bieżąca”, ma zostać co do znaku tym samym, co przed wielokonkursowością.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.competitions.models import Edition

from .factories import CurrentEditionFactory, EditionFactory

pytestmark = pytest.mark.django_db

#: Oznaczenie rocznika w obu konkursach naraz – dokładnie ten przypadek, którego zabraniało
#: globalne ``unique=True``.
SHARED_LABEL = "I edycja 2026/2027"

#: Komunikat spod pola „edycja bieżąca”. Wpisany wprost, a nie zaimportowany z modelu: test ma
#: wywrócić się także wtedy, gdy ktoś zmieni samo zdanie, bo to ono jest tu umową z użytkownikiem.
SINGLE_CURRENT_MESSAGE = "Bieżąca może być tylko jedna edycja. Odznacz poprzednią."


# --- oznaczenie rocznika -------------------------------------------------------------------------


def test_the_same_year_label_may_exist_in_two_competitions(competition, other_competition):
    """„I edycja 2026/2027” należy się każdemu organizatorowi, a nie temu, kto pierwszy zapisał."""
    ours = EditionFactory(competition=competition, year_label=SHARED_LABEL)
    theirs = EditionFactory(competition=other_competition, year_label=SHARED_LABEL)

    assert ours.pk != theirs.pk
    assert Edition.objects.filter(year_label=SHARED_LABEL).count() == 2
    assert list(Edition.objects.for_competition(competition).filter(year_label=SHARED_LABEL)) == [ours]


def test_the_same_year_label_twice_in_one_competition_is_refused(competition):
    """Unikalność nie zniknęła, tylko zmieniła zakres – dwa razy ten sam rocznik u jednego organizatora
    dalej jest błędem, bo archiwum i lista edycji nie miałyby jak ich rozróżnić."""
    EditionFactory(competition=competition, year_label=SHARED_LABEL)

    with pytest.raises(IntegrityError), transaction.atomic():
        Edition.objects.create(competition=competition, year_label=SHARED_LABEL)


# --- edycja bieżąca ------------------------------------------------------------------------------


def test_each_competition_has_its_own_current_edition(competition, other_competition):
    """Edycja bieżąca konkursu B nie zajmuje miejsca edycji bieżącej konkursu A.

    Przed wydaniem D częściowy indeks unikalny stał na samym ``is_current``, więc druga olimpiada
    nie mogła w ogóle ustawić swojego rocznika jako bieżącego – dopóki pierwsza go nie odznaczyła.
    """
    ours = CurrentEditionFactory(competition=competition)
    theirs = CurrentEditionFactory(competition=other_competition)

    assert Edition.objects.filter(is_current=True).count() == 2
    assert list(Edition.objects.for_competition(competition).filter(is_current=True)) == [ours]
    assert list(Edition.objects.for_competition(other_competition).filter(is_current=True)) == [theirs]


def test_two_current_editions_in_one_competition_break_the_constraint(competition):
    """Reguła „jedna bieżąca” zostaje – i pilnuje jej dalej baza, a nie tylko formularz."""
    CurrentEditionFactory(competition=competition)

    with pytest.raises(IntegrityError), transaction.atomic():
        Edition.objects.create(competition=competition, year_label="Edycja druga", is_current=True)


# --- walidacja formularza (§ 0: komunikat bez zmian) ----------------------------------------------


def test_clean_still_refuses_a_second_current_edition_with_the_same_message(competition):
    """Komunikat pod polem jest ten sam, co przed wielokonkursowością – to widzi koordynator."""
    CurrentEditionFactory(competition=competition)

    with pytest.raises(ValidationError) as exc:
        Edition(competition=competition, year_label="Edycja druga", is_current=True).full_clean()

    assert exc.value.message_dict["is_current"] == [SINGLE_CURRENT_MESSAGE]


def test_clean_without_an_explicit_competition_checks_the_one_from_the_context(competition):
    """Formularz waliduje się **przed** zapisem, więc konkursu w polu jeszcze nie ma.

    Walidacja bierze wtedy ten sam konkurs, który za chwilę wpisze ``save()`` – inaczej edycja
    zakładana z panelu ``/admin/`` przechodziłaby walidację i wywracała się na ``IntegrityError``
    w środku zapisu, czyli komunikatem, którego nie da się pokazać pod polem.
    """
    CurrentEditionFactory(competition=competition)

    with pytest.raises(ValidationError) as exc:
        Edition(year_label="Edycja druga", is_current=True).full_clean(exclude=["competition"])

    assert exc.value.message_dict["is_current"] == [SINGLE_CURRENT_MESSAGE]


def test_clean_lets_another_competition_have_its_own_current_edition(competition, other_competition):
    """Walidacja ma być tak samo szeroka, jak więź w bazie – ani szersza, ani węższa.

    Szersza (globalna) zabraniałaby organizatorowi B ustawienia własnej edycji bieżącej dlatego,
    że ma ją organizator A – i to komunikatem, którego nie da się zrozumieć z jego strony ekranu.
    """
    CurrentEditionFactory(competition=competition)

    Edition(competition=other_competition, year_label="Edycja sąsiada", is_current=True).full_clean()
