"""Ścieżka oceniania jednej pracy: „oddane → w ocenie → oceniona → wyniki”.

Dlaczego osobny moduł, a nie kilka ``{% if %}`` w karcie zadania: mapowanie
``Submission.status`` na krok ścieżki jest **regułą domenową**, a nie układem strony. Rozpisane
w szablonie rozjechałoby się przy pierwszym dołożeniu stanu do cyklu życia zgłoszenia
(``SubmissionStatus``) – karta pokazywałaby wtedy pracę „przed oddaniem”, choć w bazie stoi ona
w ocenie. Tutaj reguła ma jedno miejsce i jeden test.

Czego ten moduł **nie** robi: nie zna punktów i nie umie ich pokazać. Ścieżka mówi wyłącznie,
na jakim etapie procedury stoi praca; liczba punktów należy do uczestnika dopiero po publikacji
wyników etapu i idzie osobną drogą (``apps.results.feedback``). Gdyby ścieżka niosła punkty,
każdy ekran, który ją renderuje, musiałby powtarzać bramę „czy już opublikowano” – a wystarczy,
żeby jeden jej nie powtórzył.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import SubmissionStatus

#: Stan kroku na ścieżce. ``failed`` dotyczy wyłącznie pracy odrzuconej przez antywirusa: ta nie
#: jest ani „w ocenie”, ani „przed oceną” – jej droga skończyła się na pierwszym kroku.
STATE_DONE = "done"
STATE_CURRENT = "current"
STATE_TODO = "todo"
STATE_FAILED = "failed"

#: Stany, w których praca jest oddana, ale ocenianie jeszcze się nią nie zajęło.
SUBMITTED_STATUSES = frozenset({SubmissionStatus.SUBMITTED, SubmissionStatus.SCANNING})

#: Stany oceniania. ``LOCKED`` jest tu, a nie w kroku poprzednim, bo blokada (zamknięcie etapu albo
#: ``lock_for_review``) jest momentem, w którym praca przechodzi z rąk uczestnika do komitetu.
REVIEW_STATUSES = frozenset(
    {SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW, SubmissionStatus.MODERATION}
)

#: Stany, w których ocena pracy jest już ustalona. ``APPEALED`` jest na tej liście, choć procedura
#: jeszcze trwa: reklamację składa się **na wystawioną ocenę**, więc krok „oceniona” jest za nami.
#: Bez tego praca po złożeniu reklamacji spadałaby na ścieżce o krok wstecz – wyglądałoby to jak
#: cofnięcie oceny, a uczestnik zobaczyłby to dokładnie w chwili, w której jest najbardziej czujny.
GRADED_STATUSES = frozenset(
    {SubmissionStatus.GRADED_PROVISIONAL, SubmissionStatus.APPEALED, SubmissionStatus.FINAL}
)

#: Podpisy kroków – w kolejności, w jakiej stoją na ścieżce. Klucz jest maszynowy (klasa CSS,
#: asercja w teście), etykieta jest tym, co czyta uczestnik.
STEPS: tuple[tuple[str, str], ...] = (
    ("submitted", "oddane"),
    ("review", "w ocenie"),
    ("graded", "oceniona"),
    ("results", "wyniki"),
)

#: Numer kroku „wyniki”. Osiąga go wyłącznie publikacja wyników etapu, nigdy status pracy.
RESULTS_INDEX = len(STEPS) - 1


@dataclass(frozen=True)
class TrackStep:
    """Jeden krok ścieżki: klucz maszynowy, podpis i stan względem miejsca, w którym stoi praca."""

    key: str
    label: str
    state: str

    @property
    def css_class(self) -> str:
        return f"status-track__step status-track__step--{self.state}"


@dataclass(frozen=True)
class StatusTrack:
    """Cała ścieżka jednej pracy wraz z terminem ogłoszenia wyników etapu."""

    steps: tuple[TrackStep, ...]
    #: Kiedy wyniki zostały (albo mają zostać) ogłoszone. ``None`` dla etapu bez terminu – dziś
    #: jest nim trening, którego oś czasu to data-wartownik z roku 2099.
    results_at: datetime | None
    #: Czy ``results_at`` jest faktem (wyniki ogłoszone), czy zapowiedzią (planowany termin).
    results_published: bool
    #: Czy praca została odrzucona przez antywirusa – wtedy ścieżka kończy się na pierwszym kroku.
    rejected: bool


def _reached_index(status: str | None, *, results_published: bool) -> int:
    """Numer ostatniego **osiągniętego** kroku; ``-1`` znaczy „nic jeszcze nie oddano”.

    Publikacja wyników wygrywa z każdym statusem pracy i to jest świadome: praca, której nikt
    nie zdążył przestawić w ``FINAL``, a której etap ma już ogłoszoną tabelę, stoi dla uczestnika
    na końcu ścieżki – bo dla niego procedura tego etapu jest zamknięta.
    """
    if status is None:
        return -1
    if results_published:
        return RESULTS_INDEX
    if status in GRADED_STATUSES:
        return 2
    if status in REVIEW_STATUSES:
        return 1
    if status in SUBMITTED_STATUSES:
        return 0
    # Nieznany stan (dołożony do ``SubmissionStatus`` bez zmiany tego modułu) traktujemy jak pracę
    # świeżo oddaną: lepiej pokazać krok za mało niż obiecać ocenę, której nie ma.
    return 0


def results_announcement(stage, publication) -> tuple[datetime | None, bool]:
    """Termin ogłoszenia wyników etapu: fakt albo zapowiedź.

    ``Stage`` nie ma osobnego pola „planowane ogłoszenie wyników” – ma ``results_published_at``,
    które **nakłada publikacja**, czyli jest śladem zdarzenia, a nie planem. Zapowiedzią jest
    więc otwarcie okna reklamacji (``appeal_window_opens_at``): to pierwszy moment osi czasu
    etapu, w którym uczestnik zna już swoją ocenę i ma co reklamować.

    Etap bez terminu (trening) nie dostaje żadnej daty: w bazie stoi tam wartownik z roku 2099
    i ogłoszenie „wyniki 31.12.2099” byłoby informacją fałszywą.
    """
    if publication is not None:
        return (publication.published_at, True)
    if not stage.has_deadline:
        return (None, False)
    return (stage.appeal_window_opens_at, False)


def status_track(*, submission, stage, publication) -> StatusTrack:
    """Ścieżka oceniania **najnowszej** wersji pracy (albo pusta, gdy nic jeszcze nie oddano).

    ``publication`` to ``results.ResultsPublication`` etapu albo ``None``. Przychodzi z zewnątrz,
    a nie jest tu doczytywane, bo karta zadania renderuje się w pętli po zadaniach etapu –
    zapytanie w środku tej funkcji byłoby N+1 na każdym pulpicie uczestnika.
    """
    status = submission.status if submission is not None else None
    rejected = status == SubmissionStatus.REJECTED_INFECTED
    results_published = publication is not None
    # Odrzucona wersja kończy drogę na pierwszym kroku – także wtedy, gdy etap ma już ogłoszone
    # wyniki. Ta praca nigdy nie weszła do oceniania, więc „wyniki” nie są jej krokiem.
    reached = 0 if rejected else _reached_index(status, results_published=results_published)
    steps = []
    for index, (key, label) in enumerate(STEPS):
        if rejected:
            state = STATE_FAILED if index == 0 else STATE_TODO
        elif index <= reached:
            state = STATE_DONE
        elif index == reached + 1:
            state = STATE_CURRENT
        else:
            state = STATE_TODO
        steps.append(TrackStep(key=key, label=label, state=state))
    results_at, announced = results_announcement(stage, publication)
    return StatusTrack(
        steps=tuple(steps),
        results_at=results_at,
        results_published=announced,
        rejected=rejected,
    )
