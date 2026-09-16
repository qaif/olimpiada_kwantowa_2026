"""„Co teraz” – jedna odpowiedź na pytanie, po które uczestnik wchodzi do panelu.

Panel uczestnika odpowiada na kilkanaście pytań naraz (etap, zadania, wyniki, reklamacje, zgody),
ale **pytanie, z którym się na niego wchodzi, jest jedno**: co mam teraz zrobić i ile mam na to
czasu. Dotąd trzeba było złożyć tę odpowiedź samodzielnie z pięciu sekcji długiej strony – w tym
z tych, które akurat nic nie znaczą. Ten moduł składa ją raz, w jednym miejscu, i robi to
**funkcją czystą**: dostaje fakty, zwraca strukturę, nie dotyka bazy ani żądania.

Czysta, bo to jedyny sposób, żeby tabelę decyzyjną („kiedy co jest najważniejsze”) dało się
przeczytać w całości i przetestować bez stawiania edycji, etapu i zgłoszenia. Reguły domenowe
zostają tam, gdzie były: to nie ten moduł rozstrzyga, czy upload jest otwarty (``Stage``), czy
zgoda opiekuna jest potrzebna (``accounts.guardian``) ani co podlega reklamacji
(``appeals.services``). Tutaj zapada wyłącznie **kolejność ważności** i brzmienie podpisów.

Dlaczego kolejność jest taka, a nie inna (``next_action``):

1. **brak adresu opiekuna** – jedyna rzecz na tej liście, której uczestnik nie załatwi sam:
   po drugiej stronie jest dorosły, który czyta pocztę raz na kilka dni. Ma najdłuższy czas
   realizacji, więc musi paść najwcześniej, nawet jeśli w tej chwili nie blokuje niczego innego,
2. **zapis do etapu** – bez wpisu nie ma ani zadań, ani uploadu; okno zapisów zamyka się razem
   z terminem oddania,
3. **wysłanie rozwiązania** – czynność, dla której ten panel istnieje, ograniczona terminem,
4. **zapis na rozmowę** – to samo, tylko w etapie prowadzonym rozmową,
5. **reklamacja** – okno odwoławcze też się zamyka, ale dotyczy rzeczy już zrobionej,
6. **wyniki** – do przeczytania, nie do zrobienia; nic nie przepada, jeśli uczestnik zajrzy jutro.

Odliczanie jest liczone z czasu **serwera** i renderowane po stronie serwera: strona ma nieść
prawdziwą liczbę także wtedy, gdy JavaScript nie wystartuje. ``static/js/participant.js``
odświeża ją co minutę, składając tekst **z tych samych słów** – dlatego moduł wystawia je osobno
(``countdown_words``): przeglądarka nie ma katalogu tłumaczeń, więc słowa muszą do niej pojechać
w atrybutach ``data-*``, a nie w kodzie skryptu.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.urls import reverse
from django.utils.translation import gettext as _

#: Stan etapu widziany przez uczestnika. Cztery, bo tyle jest różnych odpowiedzi na pytanie
#: „czy mam coś do zrobienia w tym etapie”: jeszcze nie / tak / już nie, trwa ocenianie / koniec.
STATE_NONE = "none"
STATE_BEFORE = "before"
STATE_OPEN = "open"
STATE_GRADING = "grading"
STATE_RESULTS = "results"

#: Klucze czynności. Wartość jest maszynowa (test, klasa CSS), podpis składa ``next_action``.
ACTION_GUARDIAN = "guardian"
ACTION_REGISTER = "register"
ACTION_UPLOAD = "upload"
ACTION_INTERVIEW = "interview"
ACTION_APPEAL = "appeal"
ACTION_RESULTS = "results"

#: Stany zgody opiekuna – te same napisy, co w ``apps.accounts.guardian``. Powtórzone jako stałe
#: tego modułu, żeby funkcja czysta nie ciągnęła za sobą aplikacji kont przy imporcie w teście.
GUARDIAN_NOT_REQUIRED = "not_required"
GUARDIAN_MISSING = "missing"
GUARDIAN_PENDING = "pending"
GUARDIAN_CONFIRMED = "confirmed"


@dataclass(frozen=True)
class Action:
    """Jedna czynność: klucz maszynowy, podpis dla człowieka i adres, pod którym się ją robi."""

    key: str
    label: str
    url: str


@dataclass(frozen=True)
class Chip:
    """Znacznik stanu konta („konto aktywne”, „opiekun potwierdzony”).

    ``tone`` jest nazwą wariantu odznaki z arkusza (``badge--ok`` itd.), a nie kolorem: kolor nie
    jest tu jedynym nośnikiem znaczenia, bo podpis mówi to samo słowami (WCAG 1.4.1).
    """

    key: str
    label: str
    tone: str


@dataclass(frozen=True)
class Deadline:
    """Najbliższy termin: kiedy, czego dotyczy i ile zostało (tekst liczony po stronie serwera)."""

    at: datetime
    label: str
    remaining: str
    passed: bool


@dataclass(frozen=True)
class NowPanel:
    """Kompletna zawartość nagłówka „Co teraz”."""

    state: str
    state_label: str
    state_tone: str
    stage_name: str
    action: Action | None
    deadline: Deadline | None
    chips: tuple[Chip, ...]


def countdown_words() -> dict[str, str]:
    """Słowa, z których składa się odliczanie – dla serwera i dla przeglądarki.

    Jedno źródło dla obu stron: gdyby skrypt miał własne napisy, angielski interfejs pokazywałby
    po minucie polskie „godz.”, a każda zmiana brzmienia wymagałaby poprawki w dwóch plikach.
    """
    return {
        "prefix": _("za"),
        "day": _("dzień"),
        "days": _("dni"),
        "hours": _("godz."),
        "minutes": _("min."),
        "passed": _("termin minął"),
        "soon": _("za chwilę"),
    }


def remaining_text(delta: timedelta, words: dict[str, str] | None = None) -> str:
    """„za 3 dni, 14 godz.” – pozostały czas w dwóch najważniejszych jednostkach.

    Dokładność rośnie w miarę zbliżania się terminu: przy odległym terminie minuty są szumem,
    a przy terminie za kwadrans to one są całą informacją. Sekund nie ma w ogóle – odliczanie
    odświeża się co minutę, a sekundy kazałyby odświeżać je co sekundę po to, żeby nikt na nie
    nie patrzył.

    Skład (kolejność części i przecinek) jest **taki sam** jak w ``static/js/participant.js``;
    test porównuje oba brzmienia na tym samym module słów.
    """
    words = words or countdown_words()
    total = int(delta.total_seconds())
    if total <= 0:
        return words["passed"]
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        day_word = words["day"] if days == 1 else words["days"]
        parts = [f"{days} {day_word}", f"{hours} {words['hours']}"]
    elif hours:
        parts = [f"{hours} {words['hours']}", f"{minutes} {words['minutes']}"]
    elif minutes:
        parts = [f"{minutes} {words['minutes']}"]
    else:
        return words["soon"]
    return f"{words['prefix']} {', '.join(parts)}"


def stage_state(stage, now: datetime) -> str:
    """Stan etapu z punktu widzenia uczestnika – jedna z pięciu odpowiedzi.

    Pytamy o terminy i o publikację, a nie o rodzaj etapu: etap bez terminu (trening) też przez
    to przechodzi i wychodzi z niego jako „trwa”, bo dokładnie tym jest.
    """
    if stage is None:
        return STATE_NONE
    if stage.results_published_at is not None:
        return STATE_RESULTS
    if not stage.has_opened(now):
        return STATE_BEFORE
    if stage.is_open_for_submissions(now) and stage.closed_at is None:
        return STATE_OPEN
    return STATE_GRADING


def _state_caption(state: str) -> tuple[str, str]:
    """Podpis i wariant odznaki dla stanu etapu."""
    captions = {
        STATE_BEFORE: (_("jeszcze się nie zaczął"), "badge badge--neutral"),
        STATE_OPEN: (_("trwa"), "badge badge--ok"),
        STATE_GRADING: (_("zamknięty – trwa ocenianie"), "badge badge--warn"),
        STATE_RESULTS: (_("wyniki ogłoszone"), "badge badge--info"),
    }
    return captions.get(state, (_("brak bieżącego etapu"), "badge badge--neutral"))


def _me_url(*, tab: str = "", anchor: str = "") -> str:
    """Adres wewnątrz panelu: zakładka w zapytaniu, sekcja w kotwicy.

    ``reverse`` zamiast literału ``/me/``: adres panelu jest w jednym miejscu (``apps.web.urls``)
    i ma tam zostać. Funkcja pozostaje czysta – ``reverse`` czyta konfigurację adresów, a nie bazę.
    """
    url = reverse("web:me")
    if tab:
        url = f"{url}?tab={tab}"
    return f"{url}#{anchor}" if anchor else url


def next_action(
    *,
    guardian_state: str,
    can_register: bool,
    is_training_stage: bool,
    upload_open: bool,
    missing_numbers: tuple[int, ...],
    is_interview: bool,
    interview_booked: bool,
    interview_bookable: bool,
    appeal_window_open: bool,
    results_ready: bool,
) -> Action | None:
    """Jedna czynność – ta, którą warto zrobić najpierw. Uzasadnienie kolejności: docstring modułu.

    ``None`` znaczy „na teraz nic” i jest **normalnym** stanem przez większą część roku. Panel
    mówi to wprost, bo pusty nagłówek czyta się jak awaria, a nie jak spokój.
    """
    if guardian_state == GUARDIAN_MISSING:
        return Action(
            key=ACTION_GUARDIAN,
            label=_("Podaj adres e-mail opiekuna – bez jego zgody udział jest niekompletny"),
            url=_me_url(tab="zgody", anchor="zgoda-opiekuna"),
        )
    if can_register:
        label = _("Zgłoś się do treningu") if is_training_stage else _("Zgłoś się do etapu")
        return Action(key=ACTION_REGISTER, label=label, url=_me_url(anchor="etap"))
    if upload_open and missing_numbers:
        return Action(
            key=ACTION_UPLOAD,
            label=_("Wyślij rozwiązanie zadania %(number)s") % {"number": missing_numbers[0]},
            url=_me_url(anchor="zadania"),
        )
    if is_interview and not interview_booked and interview_bookable:
        return Action(
            key=ACTION_INTERVIEW,
            label=_("Zapisz się na termin rozmowy"),
            url=_me_url(anchor="rozmowa"),
        )
    if appeal_window_open:
        return Action(
            key=ACTION_APPEAL,
            label=_("Sprawdź ocenę – okno reklamacji jest otwarte"),
            url=_me_url(tab="reklamacje"),
        )
    if results_ready:
        return Action(key=ACTION_RESULTS, label=_("Sprawdź wyniki"), url=_me_url(tab="wyniki"))
    return None


def next_deadline(*, stage, now: datetime, state: str, booked_slot_at: datetime | None) -> Deadline | None:
    """Najbliższy termin, który uczestnika dotyczy – po jednym na stan etapu.

    Dlaczego jeden, a nie lista wszystkich dat etapu: lista terminów jest już na kalendarzu
    (``/me/calendar/``) i na publicznej osi czasu. Nagłówek ma powiedzieć, **na kiedy** jest
    najbliższa rzecz do zrobienia – druga data w tym miejscu jest szumem, bo nie zmienia decyzji.

    Etap bez terminu (trening, ``has_deadline`` = False) nie ma tu żadnej daty: w bazie stoi
    wartownik z roku 2099 i ogłoszenie go byłoby nieprawdą.
    """
    if stage is None:
        return None
    words = countdown_words()
    if state == STATE_BEFORE:
        return _deadline(stage.opens_at, _("Start etapu"), now, words)
    if state == STATE_OPEN:
        if booked_slot_at is not None:
            return _deadline(booked_slot_at, _("Twoja rozmowa"), now, words)
        if stage.is_interview:
            return _deadline(stage.deadline_at, _("Koniec rozmów"), now, words)
        if not stage.has_deadline:
            return None
        return _deadline(stage.submission_deadline, _("Termin oddania rozwiązań"), now, words)
    if state == STATE_GRADING:
        if stage.is_appeal_window_open(now):
            return _deadline(stage.appeal_window_closes_at, _("Koniec okna reklamacji"), now, words)
        if not stage.has_deadline:
            return None
        return _deadline(stage.appeal_window_opens_at, _("Ogłoszenie wyników (planowane)"), now, words)
    if state == STATE_RESULTS and stage.is_appeal_window_open(now):
        return _deadline(stage.appeal_window_closes_at, _("Koniec okna reklamacji"), now, words)
    return None


def _deadline(moment: datetime, label: str, now: datetime, words: dict[str, str]) -> Deadline:
    return Deadline(
        at=moment,
        label=label,
        remaining=remaining_text(moment - now, words),
        passed=moment <= now,
    )


def status_chips(*, account_active: bool, consents_complete: bool, guardian_state: str) -> tuple[Chip, ...]:
    """Trzy fakty o koncie, które uczestnik ma widzieć bez wchodzenia w zakładkę zgód.

    Znacznik zgody opiekuna nie powstaje dla osoby pełnoletniej: „nie dotyczy” jest informacją
    o niczym, a w rzędzie znaczników wygląda jak brak czegoś, czego brakować nie może.
    """
    chips = [
        Chip(
            key="account",
            label=_("konto aktywne") if account_active else _("konto nieaktywne"),
            tone="badge badge--ok" if account_active else "badge badge--warn",
        ),
        Chip(
            key="consents",
            label=_("zgody kompletne") if consents_complete else _("brakuje zgody"),
            tone="badge badge--ok" if consents_complete else "badge badge--warn",
        ),
    ]
    guardian_chips = {
        GUARDIAN_CONFIRMED: (_("opiekun potwierdził"), "badge badge--ok"),
        GUARDIAN_PENDING: (_("czekamy na opiekuna"), "badge badge--warn"),
        GUARDIAN_MISSING: (_("brak zgody opiekuna"), "badge badge--warn"),
    }
    if guardian_state in guardian_chips:
        label, tone = guardian_chips[guardian_state]
        chips.append(Chip(key="guardian", label=label, tone=tone))
    return tuple(chips)


def now_panel(
    *,
    now: datetime,
    stage,
    entry,
    can_register: bool,
    upload_open: bool,
    missing_numbers: tuple[int, ...] = (),
    interview_booked: bool = False,
    interview_bookable: bool = False,
    booked_slot_at: datetime | None = None,
    guardian_state: str = GUARDIAN_NOT_REQUIRED,
    consents_complete: bool = True,
    account_active: bool = True,
    results_ready: bool = False,
) -> NowPanel:
    """Nagłówek „Co teraz” złożony z faktów, które i tak są w kontekście pulpitu.

    Wszystkie argumenty są nazwane i mają wartości domyślne odpowiadające „nic się nie dzieje”:
    wywołanie z jednym faktem ma dać czytelny wynik, bo ten sam nagłówek stoi na każdej zakładce
    panelu, a nie tylko na tej, która policzyła komplet danych.
    """
    state = stage_state(stage, now)
    label, tone = _state_caption(state)
    appeal_window_open = entry is not None and stage is not None and stage.is_appeal_window_open(now)
    action = next_action(
        guardian_state=guardian_state,
        can_register=can_register,
        is_training_stage=bool(stage is not None and stage.is_training),
        upload_open=upload_open,
        missing_numbers=tuple(missing_numbers),
        is_interview=bool(stage is not None and stage.is_interview and entry is not None),
        interview_booked=interview_booked,
        interview_bookable=interview_bookable,
        appeal_window_open=appeal_window_open,
        results_ready=results_ready,
    )
    return NowPanel(
        state=state,
        state_label=label,
        state_tone=tone,
        stage_name=stage.display_name if stage is not None else "",
        action=action,
        deadline=next_deadline(stage=stage, now=now, state=state, booked_slot_at=booked_slot_at),
        chips=status_chips(
            account_active=account_active,
            consents_complete=consents_complete,
            guardian_state=guardian_state,
        ),
    )
