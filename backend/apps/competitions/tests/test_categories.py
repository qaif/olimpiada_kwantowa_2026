"""Kategorie uczestników (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.4) – model, więzi i reguła klasy.

Test pilnuje dwóch rzeczy naraz i tylko razem mają one sens:

- **Konkurs #1 nie zauważa niczego.** Kategorii nie ma, flaga ``categories`` jest wyłączona,
  ``StageEntry.category`` jest ``NULL`` i nikt go nie ustawia. To jest wymaganie nadrzędne (§ 0.1),
  więc stoi tu jako osobna grupa asercji, a nie jako komentarz.
- **Konkurs drugi dostaje kategorie, których pierwszy nie widzi.** Kategoria ma własną kolumnę
  konkursu, więc jej izolacja jest sprawdzalna na poziomie queryseta (§ 5.7) i nie zależy od tego,
  czy ktoś pamiętał o ``for_competition`` w widoku.

Reguła automatycznego przypisania (``grade_min``/``grade_max``) ma własną grupę, bo jest jedynym
miejscem, w którym system **wylicza** kategorię, zamiast ją przyjąć od człowieka – a błąd w niej
jest niewidoczny do chwili, w której uczestnik wyląduje w cudzym rankingu.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.competitions.models import Category, StageEntry

from .factories import StageEntryFactory

pytestmark = pytest.mark.django_db

#: Kod kategorii w obu konkursach naraz – dokładnie ten przypadek, którego zabraniałaby globalna
#: unikalność kodu. „Podstawowa” należy się każdemu organizatorowi z osobna.
SHARED_CODE = "podstawowa"


def make_category(competition, code=SHARED_CODE, **kwargs):
    """Kategoria bez fabryki: ``apps/competitions/tests/factories.py`` jest plikiem wspólnym.

    Ten test jest jedynym czytelnikiem kategorii do czasu wydania I, więc dokładanie fabryki
    do pliku, który w tej chwili edytuje kilka zadań naraz, kupowałoby konflikt za nic.
    """
    kwargs.setdefault("name", "Szkoła podstawowa")
    return Category.objects.create(competition=competition, code=code, **kwargs)


# --- Konkurs #1: nic się nie zmieniło ------------------------------------------------------------


def test_competition_one_has_no_categories(competition):
    """Tabela kategorii Konkursu #1 jest pusta i migracja nie miała jej czym wypełnić."""
    assert not Category.objects.for_competition(competition).exists()


def test_categories_flag_is_off_for_competition_one(competition):
    """Flaga jest w katalogu (T8) i domyślnie wyłączona – czyli ranking liczy się jak dziś."""
    assert competition.has_feature("categories") is False


def test_stage_entry_has_no_category_by_default(competition):
    """Wpis do etapu powstaje bez kategorii i nikt mu jej nie dopisuje.

    Asercja jest na ``category_id``, a nie na ``category``: odwołanie do obiektu wykonałoby
    zapytanie, a chodzi o to, że go **nie ma** – nowa kolumna nie dokłada joinu do żadnej
    istniejącej ścieżki odczytu (§ 0.2 pkt 7, budżety zapytań § 5.6).
    """
    entry = StageEntryFactory(competition=competition)

    assert entry.category_id is None
    assert StageEntry.objects.for_competition(competition).filter(category__isnull=False).count() == 0


# --- zakresowanie i izolacja ---------------------------------------------------------------------


def test_the_same_code_may_exist_in_two_competitions(competition, other_competition):
    """Kod kategorii jest unikalny **u organizatora**, a nie w instalacji."""
    ours = make_category(competition)
    theirs = make_category(other_competition)

    assert ours.pk != theirs.pk
    assert Category.objects.filter(code=SHARED_CODE).count() == 2


def test_the_same_code_twice_in_one_competition_is_refused(competition):
    """Unikalność nie zniknęła, tylko zmieniła zakres: dwa razy „podstawowa” u jednego organizatora
    znaczyłyby dwa rankingi o tej samej nazwie i nie dałoby się ich rozróżnić w regulaminie."""
    make_category(competition)

    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(competition=competition, code=SHARED_CODE, name="Druga podstawowa")


def test_categories_of_one_competition_are_invisible_to_the_other(competition, other_competition):
    """Dopełnienie reguły izolacji na poziomie queryseta (§ 5.7)."""
    make_category(other_competition)

    assert not Category.objects.for_competition(competition).filter(competition=other_competition).exists()
    assert Category.objects.for_competition(competition).count() == 0
    assert Category.objects.for_competition(other_competition).count() == 1


def test_ordering_is_by_position_then_id(other_competition):
    """Kolejność na ekranie ustala organizator, a nie kolejność wstawiania do tabeli."""
    third = make_category(other_competition, code="c", position=3)
    first = make_category(other_competition, code="a", position=1)
    second = make_category(other_competition, code="b", position=2)

    assert list(Category.objects.for_competition(other_competition)) == [first, second, third]


# --- więzi na zakresie klas ----------------------------------------------------------------------


def test_reversed_grade_range_is_refused_by_the_database(other_competition):
    """„Od 5 do 2” nie jest kategorią pustą, tylko literówką – i ma paść przed zapisem."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(
            competition=other_competition, code="odwrocona", name="Odwrócona", grade_min=5, grade_max=2
        )


def test_reversed_grade_range_is_refused_by_clean(other_competition):
    """Ta sama reguła po stronie formularza – z komunikatem, a nie ze śladem stosu."""
    category = Category(
        competition=other_competition, code="odwrocona", name="Odwrócona", grade_min=5, grade_max=2
    )

    with pytest.raises(ValidationError) as error:
        category.full_clean()

    assert "grade_max" in error.value.message_dict


@pytest.mark.parametrize(
    ("grade_min", "grade_max"),
    [(None, None), (2, None), (None, 4), (3, 3)],
    ids=["bez reguły", "od dołu", "do góry", "jedna klasa"],
)
def test_one_sided_and_empty_grade_ranges_are_allowed(other_competition, grade_min, grade_max):
    """Zakres jednostronny jest regułą, a nie połową reguły: „od pierwszej klasy wzwyż” to zdanie,
    które organizator naprawdę pisze w regulaminie."""
    category = make_category(
        other_competition, code=f"z{grade_min}do{grade_max}", grade_min=grade_min, grade_max=grade_max
    )

    category.full_clean()
    assert category.pk is not None


# --- reguła automatycznego przypisania -----------------------------------------------------------


def test_category_without_a_rule_matches_nobody(other_competition):
    """Brak zakresu znaczy „wskazuje ją człowiek”, a nie „pasuje do wszystkiego”.

    Gdyby znaczył to drugie, pierwsza kategoria wpisana bez klas przechwytywałaby cały konkurs –
    i to bez żadnego śladu, bo wynik wyglądałby jak decyzja organizatora.
    """
    category = make_category(other_competition)

    assert category.has_grade_rule is False
    assert category.matches_grade(3) is False
    assert Category.auto_for_grade(other_competition, 3) is None


@pytest.mark.parametrize(
    ("grade", "expected"),
    [(1, False), (2, True), (3, True), (4, True), (5, False), (None, False)],
)
def test_grade_range_is_inclusive_on_both_ends(other_competition, grade, expected):
    """Granice są domknięte: „klasy 2–4” obejmuje drugą i czwartą."""
    category = make_category(other_competition, grade_min=2, grade_max=4)

    assert category.matches_grade(grade) is expected


def test_auto_for_grade_picks_the_matching_category(other_competition):
    younger = make_category(
        other_competition, code="mlodsi", name="Młodsi", position=1, grade_min=1, grade_max=2
    )
    older = make_category(
        other_competition, code="starsi", name="Starsi", position=2, grade_min=3, grade_max=5
    )

    assert Category.auto_for_grade(other_competition, 2) == younger
    assert Category.auto_for_grade(other_competition, 4) == older
    assert Category.auto_for_grade(other_competition, None) is None


def test_auto_for_grade_skips_inactive_categories(other_competition):
    """Kategoria wycofana nie przyjmuje nowych uczestników, choć zostaje przy wpisach z lat ubiegłych."""
    make_category(other_competition, code="wycofana", grade_min=1, grade_max=5, is_active=False, position=1)
    active = make_category(other_competition, code="czynna", grade_min=1, grade_max=5, position=2)

    assert Category.auto_for_grade(other_competition, 3) == active


def test_auto_for_grade_resolves_overlap_by_position(other_competition):
    """Zakresy mają być rozłączne; gdy nie są, wygrywa pierwsza po ``position`` – czyli ta, którą
    organizator widzi na ekranie wyżej, a nie ta, którą przypadkiem zapisano wcześniej."""
    make_category(other_competition, code="druga", position=2, grade_min=1, grade_max=5)
    first = make_category(other_competition, code="pierwsza", position=1, grade_min=1, grade_max=5)

    assert Category.auto_for_grade(other_competition, 3) == first


def test_auto_for_grade_does_not_cross_competitions(competition, other_competition):
    """Reguła konkursu drugiego nie przypisuje kategorii uczestnikowi Konkursu #1."""
    make_category(other_competition, grade_min=1, grade_max=5)

    assert Category.auto_for_grade(competition, 3) is None


# --- kasowanie -----------------------------------------------------------------------------------


def test_category_in_use_cannot_be_deleted(other_competition):
    """``PROTECT``: skasowanie kategorii razem z wpisami zabrałoby ze sobą wyniki etapu.

    Wycofanie kategorii jest przestawieniem ``is_active``, a nie usunięciem wiersza – inaczej
    tabela wyników sprzed roku przestałaby mówić, w jakiej kategorii ktoś startował.
    """
    category = make_category(other_competition, grade_min=1, grade_max=5)
    entry = StageEntryFactory(competition=other_competition, category=category)

    with pytest.raises(ProtectedError), transaction.atomic():
        category.delete()

    entry.refresh_from_db()
    assert entry.category_id == category.pk


def test_unused_category_may_be_deleted(other_competition):
    """Kategoria założona przez pomyłkę i nikomu nieprzypisana znika bez ceremonii."""
    category = make_category(other_competition)

    category.delete()

    assert not Category.objects.for_competition(other_competition).exists()
