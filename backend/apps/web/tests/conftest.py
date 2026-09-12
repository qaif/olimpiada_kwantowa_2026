"""Wspólne dane testów interfejsu WWW: bieżąca edycja z otwartym etapem eliminacyjnym.

Układ odpowiada temu, co zakłada ``manage.py seed_demo`` (edycja bieżąca, etap ELIM otwarty,
trzy zadania, uczestnicy zapisani do etapu), ale powstaje z fabryk – test widzi dokładnie to,
co zadeklarował, i nie zależy od ``DEBUG``.
"""

import time

import pytest
from django.test import Client

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageEntryStatus, StageFormat, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)

#: Nazwisko uczestnika użyte w asercjach „recenzent nie widzi danych osobowych”.
PARTICIPANT_LAST_NAME = "Nazwiskowski"

#: Odpowiedź, którą ``django-simple-captcha`` przyjmuje przy ``CAPTCHA_TEST_MODE`` (settings/test.py).
CAPTCHA_TEST_RESPONSE = "PASSED"


def captcha_fields(*, elapsed: int = 10, honeypot: str = "") -> dict:
    """Pola bloku antyspamowego dla POST-a na ``/register/`` i ``/register/committee/``.

    Cztery klucze, bo tyle ich jest w formularzu (patrz apps/web/captcha.py):

    - ``captcha_0`` – klucz wyzwania. W trybie testowym jego treść jest nieistotna, ale **musi**
      być niepusta: ``MultiValueField`` odrzuca niekompletną wartość, zanim dojdzie do trybu
      testowego,
    - ``captcha_1`` – odpowiedź; „PASSED” przechodzi przy ``CAPTCHA_TEST_MODE``,
    - ``website`` – pułapka. Pusta, czyli tak, jak wysyła ją człowiek,
    - ``form_ts`` – podpisany znacznik czasu wystawienia formularza. Domyślnie 10 s w przeszłości,
      czyli powyżej progu ``ANTISPAM_MIN_FILL_SECONDS``. Test „za szybko” podaje ``elapsed=0``.

    Znacznik podpisujemy tą samą funkcją, co formularz – podpisu nie da się tu podrobić literałem,
    a test z zaszytym ciągiem znaków przestałby cokolwiek sprawdzać po zmianie soli.
    """
    from apps.web.captcha import sign_timestamp

    return {
        "captcha_0": "klucz-nieistotny-w-trybie-testowym",
        "captcha_1": CAPTCHA_TEST_RESPONSE,
        "website": honeypot,
        "form_ts": sign_timestamp(time.time() - elapsed),
    }


#: Hasło używane w POST-ach na formularze rejestracji. To samo, co ``factories.DEFAULT_PASSWORD``,
#: powtórzone tutaj, żeby testy warstwy WWW nie zależały od fabryk kont.
WEB_TEST_PASSWORD = "Poprawne-Haslo-2026"

#: Telefon w postaci „jak wpisuje człowiek” – serwis sprowadzi go do ``+48600100200``.
WEB_TEST_PHONE = "600 100 200"


def password_fields(password: str = WEB_TEST_PASSWORD) -> dict:
    """Hasło **i jego powtórzenie**: oba formularze rejestracji mają od tej zmiany dwa pola.

    Osobny helper, a nie literał w każdym teście: gdy pojawi się trzecie pole poświadczeń,
    zmienia się jedno miejsce. Test, którego przedmiotem jest sama niezgodność haseł, podaje
    ``password2`` jawnie i nadpisuje to, co zwraca ten helper.
    """
    return {"password": password, "password2": password}


def participant_extra_fields(*, phone: str = WEB_TEST_PHONE, **captcha_kwargs) -> dict:
    """Komplet pól, których POST na ``/register/`` wymaga, a których dany test nie bada.

    Hasło z powtórzeniem, telefon i blok antyspamowy w jednym miejscu – inaczej każde dołożenie
    pola do formularza rejestracji znaczyłoby obchodzenie kilkunastu testów po kolei.
    """
    return {**password_fields(), "phone": phone, **captcha_fields(**captcha_kwargs)}


@pytest.fixture
def web_client() -> Client:
    return Client()


@pytest.fixture
def edition():
    return CurrentEditionFactory()


@pytest.fixture
def elim_stage(edition):
    """Otwarty etap eliminacyjny ze skalą 0/2/5/6 i progiem kwalifikacji."""
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


@pytest.fixture
def interview_stage(edition):
    """Etap okręgowy w formie rozmowy: otwarty od wczoraj, zapisy do terminu za 14 dni.

    Ta sama oś czasu, co ``elim_stage`` – zmienia się wyłącznie forma. Dzięki temu testy różnicy
    między etapem pisemnym a rozmową nie mieszają się z różnicą terminów.
    """
    stage = StageFactory(edition=edition, kind=StageKind.DISTRICT, format=StageFormat.INTERVIEW)
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


@pytest.fixture
def interview_entry(participant, interview_stage):
    """Uczestnik zakwalifikowany do etapu rozmowy (wpis tworzy kwalifikacja, nie rejestracja)."""
    return StageEntryFactory(
        participant=participant, stage=interview_stage, status=StageEntryStatus.QUALIFIED
    )


@pytest.fixture
def problems(elim_stage):
    return [
        ProblemFactory(stage=elim_stage, number=1, title="Nierówność ze średnimi"),
        ProblemFactory(stage=elim_stage, number=2, title="Kolorowanie grafu turniejowego"),
    ]


@pytest.fixture
def participant():
    return ParticipantFactory(
        user=UserFactory(
            email="uczestnik@example.test",
            first_name="Uczestnik",
            last_name=PARTICIPANT_LAST_NAME,
            groups=["participant"],
        )
    )


@pytest.fixture
def entry(participant, elim_stage):
    return StageEntryFactory(participant=participant, stage=elim_stage)


@pytest.fixture
def reviewer():
    return ActiveReviewerFactory()


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


def shift_stage(stage, *, opens, deadline, review, appeal_opens, appeal_closes) -> None:
    """Przesuwa oś czasu etapu (dni względem „teraz”), z pominięciem ``full_clean``.

    ``update()`` zamiast ``save()``: w teście interesuje nas stan po deadline, a nie droga do
    niego. Constraintów w bazie (kolejność dat) i tak nie da się tak obejść.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.competitions.models import Stage

    now = timezone.now()
    Stage.objects.filter(pk=stage.pk).update(
        opens_at=now + timedelta(days=opens),
        deadline_at=now + timedelta(days=deadline),
        review_deadline_at=now + timedelta(days=review),
        appeal_window_opens_at=now + timedelta(days=appeal_opens),
        appeal_window_closes_at=now + timedelta(days=appeal_closes),
    )
    stage.refresh_from_db()


def close_submissions(stage) -> None:
    """Etap po deadline uploadu, z otwartym oknem reklamacji."""
    shift_stage(stage, opens=-30, deadline=-2, review=-1, appeal_opens=-1, appeal_closes=7)


def close_stage_timeline(stage) -> None:
    """Etap całkowicie zamknięty: po deadline, po recenzjach i po oknie reklamacji."""
    shift_stage(stage, opens=-60, deadline=-50, review=-40, appeal_opens=-30, appeal_closes=-20)
