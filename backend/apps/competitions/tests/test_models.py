"""T-03, kryteria 1-4: unikalności i walidacje modeli domeny zawodów."""

from datetime import date, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.competitions.models import (
    Edition,
    Problem,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageKind,
    default_scoring_values,
)

from .factories import (
    CurrentEditionFactory,
    EditionFactory,
    ProblemFactory,
    ScoringScaleFactory,
    StageFactory,
)


@pytest.mark.django_db
def test_kryterium_1_druga_edycja_is_current_lamie_constraint_bazy():
    """1. Dwie edycje `is_current=True` → IntegrityError (częściowy indeks unikalny)."""
    CurrentEditionFactory()

    with pytest.raises(IntegrityError), transaction.atomic():
        Edition.objects.create(year_label="Edycja druga", is_current=True)


@pytest.mark.django_db
def test_kryterium_1_druga_edycja_is_current_daje_validation_error_w_full_clean():
    """1. Ta sama reguła w `full_clean()` – czytelny ValidationError zamiast błędu bazy."""
    CurrentEditionFactory()

    with pytest.raises(ValidationError) as exc:
        Edition(year_label="Edycja druga", is_current=True).full_clean()

    assert "is_current" in exc.value.message_dict


@pytest.mark.django_db
def test_kryterium_1_edycja_niebiezaca_moze_byc_wiele():
    """1. Kontrola pozytywna: edycji archiwalnych może być dowolnie wiele."""
    EditionFactory.create_batch(3)
    CurrentEditionFactory()

    assert Edition.objects.filter(is_current=True).count() == 1
    assert Edition.objects.count() == 4


@pytest.mark.django_db
def test_kryterium_2_stage_z_deadline_przed_otwarciem_nie_przechodzi_full_clean():
    """2. `Stage` z `deadline_at < opens_at` → ValidationError przy `full_clean()`."""
    edition = EditionFactory()
    now = timezone.now()
    stage = Stage(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=now + timedelta(days=5),
        deadline_at=now + timedelta(days=1),
        review_deadline_at=now + timedelta(days=20),
        appeal_window_opens_at=now + timedelta(days=21),
        appeal_window_closes_at=now + timedelta(days=28),
    )

    with pytest.raises(ValidationError) as exc:
        stage.full_clean()

    assert "deadline_at" in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    "broken_field",
    ["review_deadline_at", "appeal_window_opens_at", "appeal_window_closes_at"],
)
def test_kryterium_2_kazde_zaburzenie_kolejnosci_terminow_jest_wykrywane(broken_field):
    """2. Pełny łańcuch: opens < deadline <= review <= appeal_opens < appeal_closes."""
    now = timezone.now()
    stage = Stage(
        edition=EditionFactory(),
        kind=StageKind.ELIM,
        opens_at=now,
        deadline_at=now + timedelta(days=10),
        review_deadline_at=now + timedelta(days=20),
        appeal_window_opens_at=now + timedelta(days=25),
        appeal_window_closes_at=now + timedelta(days=30),
    )
    setattr(stage, broken_field, now - timedelta(days=1))

    with pytest.raises(ValidationError) as exc:
        stage.full_clean()

    assert broken_field in exc.value.message_dict


@pytest.mark.django_db
def test_stage_submission_deadline_uwzglednia_grace_i_okno_zgloszen():
    """Property `submission_deadline` = deadline_at + grace_seconds; `is_open_for_submissions`."""
    stage = StageFactory(grace_seconds=300)

    assert stage.submission_deadline == stage.deadline_at + timedelta(seconds=300)
    assert stage.is_open_for_submissions(stage.opens_at) is True
    assert stage.is_open_for_submissions(stage.opens_at - timedelta(seconds=1)) is False
    assert stage.is_open_for_submissions(stage.deadline_at + timedelta(seconds=299)) is True
    assert stage.is_open_for_submissions(stage.submission_deadline) is False


@pytest.mark.django_db
def test_stage_dni_wydarzenia_sa_niezalezne_od_okna_oddawania_prac():
    """Zjazd trwa cztery dni, a sesja egzaminacyjna jest kilkugodzinna – to dwa różne fakty.

    Właśnie dlatego oba zapisują się w jednym etapie bez kolizji: ``event_range`` nie ma wpływu
    na ``is_open_for_submissions``, a okno uploadu nie ma wpływu na termin, który ogłasza strona.
    """
    warsaw = timezone.get_current_timezone()
    stage = StageFactory(
        location="Kraków",
        opens_at=datetime(2027, 6, 5, 9, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 6, 5, 14, 0, tzinfo=warsaw),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    assert stage.event_range == (date(2027, 6, 4), date(2027, 6, 7))
    assert stage.is_open_for_submissions(datetime(2027, 6, 4, 12, 0, tzinfo=warsaw)) is False
    assert stage.is_open_for_submissions(datetime(2027, 6, 5, 12, 0, tzinfo=warsaw)) is True


@pytest.mark.django_db
def test_stage_bez_dni_wydarzenia_nie_ma_zakresu():
    """Etap zdalny zostawia oba pola puste – ``event_range`` jest wtedy ``None``, nie parą pustych."""
    assert StageFactory().event_range is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("starts", "ends", "field"),
    [
        # Sam początek albo sam koniec nie jest terminem, który dałoby się ogłosić.
        (date(2027, 6, 4), None, "event_ends_on"),
        (None, date(2027, 6, 7), "event_starts_on"),
        # Zakres odwrócony: „od 7 do 4 czerwca” nie istnieje.
        (date(2027, 6, 7), date(2027, 6, 4), "event_ends_on"),
    ],
)
def test_stage_polowiczne_i_odwrocone_dni_wydarzenia_nie_przechodza_full_clean(starts, ends, field):
    stage = StageFactory.build(edition=EditionFactory(), event_starts_on=starts, event_ends_on=ends)

    with pytest.raises(ValidationError) as exc:
        stage.full_clean()

    assert field in exc.value.message_dict


@pytest.mark.django_db
def test_stage_odwrocone_dni_wydarzenia_lamie_constraint_bazy():
    """Ta sama reguła w bazie – ostatnia linia obrony przy zapisie z pominięciem ``full_clean()``."""
    with pytest.raises(IntegrityError), transaction.atomic():
        StageFactory(event_starts_on=date(2027, 6, 7), event_ends_on=date(2027, 6, 4))


@pytest.mark.django_db
def test_stage_jednodniowe_wydarzenie_jest_dozwolone():
    """Zjazd na jeden dzień to poprawny zakres (constraint dopuszcza równość)."""
    stage = StageFactory(event_starts_on=date(2027, 6, 4), event_ends_on=date(2027, 6, 4))

    assert stage.event_range == (date(2027, 6, 4), date(2027, 6, 4))


@pytest.mark.django_db
def test_stage_unikalny_rodzaj_w_ramach_edycji():
    """`unique_together(edition, kind)` z PROJEKT.md 2.2."""
    stage = StageFactory(kind=StageKind.ELIM)

    with pytest.raises(IntegrityError), transaction.atomic():
        StageFactory(edition=stage.edition, kind=StageKind.ELIM)


@pytest.mark.django_db
def test_kryterium_3_domyslna_skala_ma_wartosci_0_2_5_6():
    """3. `ScoringScale` domyślna ma `allowed_values() == {0, 2, 5, 6}`."""
    scale = ScoringScaleFactory()

    assert scale.allowed_values() == {0, 2, 5, 6}
    assert scale.max_value == 6
    scale.full_clean()  # domyślna skala musi być poprawna


@pytest.mark.django_db
def test_kryterium_3_skala_niemonotoniczna_daje_validation_error():
    """3. Wartości niemonotoniczne → ValidationError."""
    scale = ScoringScale(
        stage=StageFactory(),
        values=[
            {"value": 0, "label": "brak"},
            {"value": 6, "label": "pełne"},
            {"value": 2, "label": "postęp"},
        ],
        max_value=6,
    )

    with pytest.raises(ValidationError) as exc:
        scale.full_clean()

    assert "values" in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("values", "max_value", "field"),
    [
        ([{"value": 2, "label": "a"}, {"value": 6, "label": "b"}], 6, "values"),  # brak 0
        ([{"value": 0, "label": "a"}, {"value": 0, "label": "b"}], 0, "values"),  # duplikat
        (None, 6, "values"),  # pusta skala
        ([{"value": 0, "label": "a"}, {"value": 6, "label": "b"}], 5, "max_value"),  # zły max
    ],
)
def test_kryterium_3_pozostale_naruszenia_skali(values, max_value, field):
    """3. Skala wymaga 0, wartości unikalnych i `max_value == max(values)`."""
    scale = ScoringScale(
        stage=StageFactory(), values=values if values is not None else [], max_value=max_value
    )

    with pytest.raises(ValidationError) as exc:
        scale.full_clean()

    assert field in exc.value.message_dict


@pytest.mark.django_db
def test_kryterium_4_top_n_bez_top_n_daje_validation_error():
    """4. `QualificationRule(mode=TOP_N)` bez `top_n` → ValidationError."""
    rule = QualificationRule(stage=StageFactory(), mode=QualificationMode.TOP_N, top_n=None)

    with pytest.raises(ValidationError) as exc:
        rule.full_clean()

    assert "top_n" in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mode", "min_points", "top_n", "expected_fields"),
    [
        (QualificationMode.MIN_POINTS, None, None, {"min_points"}),
        (QualificationMode.TOP_N_PER_DISTRICT, None, None, {"top_n"}),
        (QualificationMode.HYBRID, None, None, {"min_points", "top_n"}),
        (QualificationMode.HYBRID, 10, None, {"top_n"}),
    ],
)
def test_kryterium_4_wymagane_pola_zaleza_od_trybu(mode, min_points, top_n, expected_fields):
    """4. MIN_POINTS wymaga min_points; TOP_N* wymaga top_n; HYBRID wymaga obu."""
    rule = QualificationRule(stage=StageFactory(), mode=mode, min_points=min_points, top_n=top_n)

    with pytest.raises(ValidationError) as exc:
        rule.full_clean()

    assert set(exc.value.message_dict) == expected_fields


@pytest.mark.django_db
def test_kryterium_4_poprawne_kombinacje_przechodza():
    """4. Kontrola pozytywna dla każdego trybu."""
    QualificationRule(stage=StageFactory(), mode=QualificationMode.MIN_POINTS, min_points=0).full_clean()
    QualificationRule(stage=StageFactory(), mode=QualificationMode.TOP_N, top_n=50).full_clean()
    QualificationRule(
        stage=StageFactory(), mode=QualificationMode.HYBRID, min_points=12, top_n=30
    ).full_clean()


@pytest.mark.django_db
def test_problem_unikalny_numer_w_etapie_i_dozwolone_formaty():
    """`unique_together(stage, number)` oraz biała lista formatów plików."""
    problem = ProblemFactory(number=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        ProblemFactory(stage=problem.stage, number=1)

    bad = Problem(stage=StageFactory(), number=1, title="X", allowed_formats=["pdf", "exe"])
    with pytest.raises(ValidationError) as exc:
        bad.full_clean()
    assert "allowed_formats" in exc.value.message_dict


@pytest.mark.django_db
def test_domyslne_wartosci_skali_sa_kopiowane_nie_wspoldzielone():
    """`default=` JSONField musi zwracać nowy obiekt – inaczej edycja jednej skali psuje wszystkie."""
    first, second = default_scoring_values(), default_scoring_values()
    first[0]["label"] = "zmienione"

    assert second[0]["label"] == "brak istotnego postępu"
