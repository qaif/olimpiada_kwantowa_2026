"""Panel uczestnika ``/me/``.

Zakres (T-08): bieżący etap i odliczanie do deadline'u liczone z czasu **serwera**, rejestracja do
eliminacji, zadania z treścią PDF, upload per zadanie przez HTMX z historią wersji i statusem
antywirusa, własne wyniki po publikacji oraz reklamacja w oknie odwoławczym.

Wszystkie reguły (deadline, okno reklamacji, widoczność wyników) są egzekwowane w serwisach –
tutaj są wyłącznie po to, żeby nie pokazywać formularza, którego serwis i tak by nie przyjął.

**Zakładki zamiast jednej długiej strony.** Pulpit odpowiadał dotąd na wszystkie pytania naraz
(etap, zadania, wyniki, reklamacje, zgody) w jednym przewijanym dokumencie – i liczył je wszystkie
przy każdym wejściu, także te, których nikt w danej chwili nie czyta. Od tej zmiany sekcje są
zakładkami rozstrzyganymi **po stronie serwera** (``/me/?tab=…``), a widok liczy wyłącznie to,
co renderuje: zakładka „Zadania” nie dotyka ani wyników, ani reklamacji. Zakładki są zwykłymi
odnośnikami (``aria-current``), więc działają bez JavaScriptu, dają się otworzyć w nowej karcie
i zapisać w zakładkach przeglądarki – czego panel z przełącznikiem w JS by nie dał.

Nagłówek „Co teraz” stoi **nad** zakładkami i jest na każdej z nich, bo odpowiada na pytanie,
z którym uczestnik wchodzi do panelu, a nie na pytanie o wybraną sekcję. Tabela decyzyjna („co
jest teraz najważniejsze”) mieszka w ``apps.web.participant_now`` – funkcji czystej, testowanej
bez stawiania edycji i etapu.
"""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import TemplateView, View
from rest_framework.status import HTTP_400_BAD_REQUEST

from apps.accounts.consents import CONSENTS, ConsentKind, ConsentSource, labels, required_kinds
from apps.accounts.guardian import guardian_status
from apps.accounts.services import consents_for_participant, set_publish_name_consent
from apps.appeals.services import appealable_submissions, appeals_for_participant, file_appeal
from apps.competitions.interviews import (
    book_slot,
    booking_for_participant,
    cancel_booking,
    slots_for_participant,
)
from apps.competitions.models import InterviewSlot, Problem, Stage, StageEntry
from apps.competitions.services import (
    SELF_REGISTRATION_KINDS,
    current_edition,
    current_stage,
    register_for_stage,
    training_stage,
)
from apps.competitions.video import PRECHECK_TEXT
from apps.core.api import DomainError
from apps.quiz.services import quiz_for_stage
from apps.results.services import published_results, results_for_participant
from apps.submissions.preview import preview_for
from apps.submissions.services import (
    UNDER_REVIEW_STATUSES,
    create_submission,
    submissions_for_user,
)
from apps.submissions.status_track import STATE_CURRENT, STATE_FAILED, status_track
from apps.web.forms import AppealForm, SubmissionUploadForm
from apps.web.mixins import ActionViewMixin, ParticipantRequiredMixin
from apps.web.participant_now import countdown_words, now_panel
from apps.web.throttle import ThrottledFormMixin

#: Zakładki pulpitu. Klucz jest w adresie (``/me/?tab=wyniki``), więc jest po polsku i bez odmiany –
#: adres panelu bywa przesyłany dalej i ma być czytelny. Pierwsza jest domyślna: wejście na ``/me/``
#: bez parametru ma pokazać to, po co uczestnik przychodzi najczęściej, czyli zadania.
TAB_TASKS = "zadania"
TAB_RESULTS = "wyniki"
TAB_APPEALS = "reklamacje"
TAB_CONSENTS = "zgody"

#: Kolejność zakładek i ich podpisy. Podpisy są leniwe – moduł ładuje się przy starcie procesu.
PANEL_TABS: tuple[tuple[str, object], ...] = (
    (TAB_TASKS, gettext_lazy("Zadania")),
    (TAB_RESULTS, gettext_lazy("Wyniki")),
    (TAB_APPEALS, gettext_lazy("Reklamacje")),
    (TAB_CONSENTS, gettext_lazy("Zgody")),
)

#: Ekrany panelu, które są osobnymi adresami, a nie zakładkami pulpitu: mają własną treść i własne
#: zapytania, a odwiedza się je rzadziej. Stoją w tym samym pasku, bo z punktu widzenia uczestnika
#: to dalszy ciąg tej samej nawigacji. ``participant-certificates`` przechodzi przez ``reverse``
#: w bloku ``try``: dyplomy są młodszą częścią panelu i pasek nie może się wywrócić tam, gdzie
#: tego adresu (jeszcze) nie ma.
PANEL_LINKS: tuple[tuple[str, object], ...] = (
    ("web:participant-calendar", gettext_lazy("Kalendarz")),
    ("web:participant-archive", gettext_lazy("Archiwum")),
    ("web:participant-certificates", gettext_lazy("Dyplomy")),
    ("web:profile", gettext_lazy("Profil")),
)


def _entry_for(participant, stage: Stage | None) -> StageEntry | None:
    if stage is None:
        return None
    return StageEntry.objects.filter(participant=participant, stage=stage).first()


def _under_review(versions: list) -> bool:
    """Czy najnowsza wersja pracy jest już w ocenie – podstawa ostrzeżenia przy uploadzie.

    Odkąd koordynator może wciągnąć prace do oceniania przed zamknięciem etapu
    (``submissions.services.lock_for_review``), uczestnik z otwartym oknem uploadu może mieć pracę
    już czytaną przez komitet. Wysłanie nowej wersji jest wtedy nadal dozwolone, ale kasuje
    dotychczasową ocenę – i to musi być widoczne **przed** kliknięciem, a nie dopiero w historii.

    Lista wersji przychodzi posortowana malejąco (``submissions_for_user``), więc pierwsza jest
    najnowsza. Stany bierzemy z jednej listy w serwisie, żeby panel i reguła unieważniania
    (``grading.services.supersede_earlier_versions``) nie mogły się rozjechać.
    """
    return bool(versions) and versions[0].status in UNDER_REVIEW_STATUSES


def _problem_rows(user, entry: StageEntry | None, competition=None) -> list[dict]:
    """Zadania etapu wraz z własnymi wersjami rozwiązań (najnowsza pierwsza).

    Rozwiązania biorą się z ``Submission.objects.for_user`` – filtr roli siedzi w queryseckie,
    a nie w tym widoku (PROJEKT.md 2.3).
    """
    if entry is None:
        return []
    problems = list(Problem.objects.filter(stage=entry.stage).order_by("number", "id"))
    versions: dict[int, list] = defaultdict(list)
    for submission in submissions_for_user(user, competition).filter(entry=entry):
        versions[submission.problem_id].append(submission)
    # Publikacja etapu jest jedna na całą listę zadań, więc czytamy ją **raz**: w środku pętli
    # byłaby jednym zapytaniem na zadanie, czyli N+1 na każdym wejściu do panelu.
    publication = published_results(entry.stage_id)
    return [_row(problem, versions.get(problem.pk, []), entry.stage, publication) for problem in problems]


def _row(problem: Problem, versions: list, stage: Stage, publication) -> dict:
    """Jedna karta zadania. Kształt jest wspólny dla pulpitu i dla odpowiedzi HTMX po uploadzie."""
    track = _track_for(versions, stage, publication)
    return {
        "problem": problem,
        "versions": versions,
        "under_review": _under_review(versions),
        "track": track,
        "state": _card_state(versions, track),
        "preview": _preview_of(versions),
    }


def _card_state(versions: list, track) -> dict:
    """Stan pracy **jednym słowem** – odznaka w nagłówku karty zadania.

    Ścieżka oceniania poniżej mówi to samo dokładniej, ale czyta się ją dopiero wtedy, gdy się na
    nią spojrzy; odznaka ma odpowiedzieć z odległości metra, zanim uczestnik zacznie czytać kartę.
    Bierze etykietę z tej samej ścieżki (``apps.submissions.status_track``), więc jedno i drugie
    nie może powiedzieć dwóch różnych rzeczy.

    Brak wersji jest tu osobnym przypadkiem, a nie pierwszym krokiem ścieżki: krok „oddane” jako
    bieżący znaczy „to teraz”, ale w nagłówku karty wyglądałby jak „oddane” – czyli dokładnie
    odwrotnie niż jest.
    """
    if not versions:
        return {"label": _("brak rozwiązania"), "tone": "badge badge--warn"}
    tones = {
        STATE_FAILED: "badge badge--danger",
        STATE_CURRENT: "badge badge--info",
    }
    for step in track.steps:
        if step.state in tones:
            return {"label": step.label, "tone": tones[step.state]}
    # Wszystkie kroki zaliczone – praca przeszła całą drogę aż do ogłoszonych wyników.
    return {"label": track.steps[-1].label, "tone": "badge badge--ok"}


def _visible_slots(rows: list[dict], now) -> list[dict]:
    """Terminy rozmów, które warto jeszcze pokazać.

    Termin, który się skończył, znika z listy: nie ma po co proponować rozmowy, której nie da się
    już odbyć. Trwający zostaje (z podpisem „Termin już trwa”) – zniknięcie wiersza w trakcie
    godziny wyglądałoby jak awaria. Własny termin zostaje zawsze, także po rozmowie.

    Odsiew jest tutaj, a nie w szablonie, bo lista jest grupowana po dniach
    (``{% templatetag openblock %} regroup {% templatetag closeblock %}``), a grupowanie nie umie
    pomijać wierszy: dzień złożony z samych minionych terminów zostałby pustym nagłówkiem.
    """
    return [row for row in rows if row["slot"].ends_at > now or row["is_mine"]]


def _preview_of(versions: list) -> dict | None:
    """Podgląd **najnowszej** wersji pracy albo ``None``.

    Tylko najnowsza, bo to ona pójdzie do oceny – miniatury wszystkich wersji naraz kosztowałyby
    tyle samo pobrań ze storage, ile jest wierszy w historii, a odpowiadałyby na pytanie, którego
    nikt nie zadaje. Regułę „co da się pokazać” trzyma ``apps.submissions.preview``.
    """
    if not versions:
        return None
    return preview_for(versions[0].latest_file)


def _track_for(versions: list, stage: Stage, publication) -> object:
    """Ścieżka „oddane → w ocenie → oceniona → wyniki” dla **najnowszej** wersji pracy.

    Lista wersji przychodzi posortowana malejąco (``submissions_for_user``), więc pierwsza jest
    najnowsza – i to ona wyznacza status, bo tylko ona idzie do oceniania. Regułę mapowania
    trzyma ``apps.submissions.status_track``; tutaj zostaje samo wybranie wersji.
    """
    return status_track(submission=versions[0] if versions else None, stage=stage, publication=publication)


def _consent_rows(records) -> list[dict]:
    """Zgody uczestnika do pokazania w panelu: po jednym wierszu na rodzaj, stan najświeższy.

    Historia w bazie bywa dłuższa niż jeden wpis na rodzaj (zgoda wycofana i wyrażona ponownie),
    ale panel odpowiada na pytanie „co obowiązuje teraz i od kiedy”. Wpisy przychodzą posortowane
    malejąco po dacie (``ConsentRecord.Meta.ordering``), więc pierwszy napotkany jest najnowszy.

    Rodzaje bez ani jednego wpisu też są na liście: profil sprzed wprowadzenia zestawu zgód ma
    tylko projekcje na ``Participant`` i uczestnik ma prawo zobaczyć, że dowodu nie ma, zamiast
    domyślać się z pustej listy.

    Argumentem są **wpisy**, a nie uczestnik: tę samą listę czyta znacznik „zgody kompletne”
    w nagłówku panelu i drugi odczyt tej samej tabeli w jednym żądaniu byłby zapytaniem po nic.
    """
    latest: dict[str, object] = {}
    for record in records:
        latest.setdefault(record.kind, record)
    texts = labels()
    return [
        {
            "kind": consent.kind,
            "name": ConsentKind(consent.kind).label,
            "label": texts[consent.kind],
            "version": consent.version,
            "optional": consent.is_optional,
            "record": latest.get(consent.kind),
        }
        for consent in CONSENTS
    ]


def _problem_row(user, entry: StageEntry, problem: Problem, competition=None) -> dict:
    """Jedna karta zadania – odpowiedź HTMX po uploadzie. Kształt musi być ten sam, co w pulpicie.

    Ścieżka oceniania jest tu liczona tak samo jak w ``_problem_rows``: karta wracająca po
    wysyłce ma pokazać krok „oddane” od razu, a nie dopiero po przeładowaniu całej strony.
    """
    versions = list(submissions_for_user(user, competition).filter(entry=entry, problem=problem))
    return _row(problem, versions, entry.stage, published_results(entry.stage_id))


def _consents_complete(participant, records=None) -> bool:
    """Czy uczestnik ma komplet **wymaganych od niego** zgód – znacznik w nagłówku „Co teraz”.

    Regułę „które zgody są wymagane” trzyma ``accounts.consents.required_kinds`` (zależy od
    rocznika), a nie ten widok: inaczej znacznik mówiłby „brakuje zgody” pełnoletniemu, od którego
    zgody opiekuna nie wymagamy wcale. Wpis wycofany nie liczy się jako zgoda obowiązująca.

    Zgoda opiekuna jest z tego rachunku **wyjęta**, choć bywa wymagana. Ma własny znacznik i własny,
    surowszy stan (``accounts.guardian``: liczy się wyłącznie potwierdzenie przysłane z adresu
    opiekuna, a nie oświadczenie dziecka złożone przy rejestracji). Liczona tu podwójnie dawałaby
    w jednym rzędzie „zgody kompletne” obok „brak zgody opiekuna” – dwa znaczniki mówiące
    o tej samej rzeczy dwie różne rzeczy.

    ``records`` przyjmujemy z zewnątrz, bo zakładka zgód i tak je wczytuje – bez tego ten sam
    odczyt szedłby do bazy dwa razy na jedno żądanie.
    """
    required = set(required_kinds(participant.birth_year)) - {ConsentKind.GUARDIAN}
    if not required:
        return True
    if records is None:
        records = consents_for_participant(participant)
    active = {record.kind for record in records if record.withdrawn_at is None}
    return required <= active


def _missing_numbers(user, entry, rows=None, competition=None) -> tuple[int, ...]:
    """Numery zadań bez ani jednej wysłanej wersji – podstawa podpowiedzi „wyślij zadanie N”.

    Na zakładce zadań karty są już policzone, więc bierzemy je stamtąd; na pozostałych zakładkach
    idzie jedno zapytanie zamiast całego kompletu kart z podglądami i historią wersji.
    """
    if entry is None:
        return ()
    if rows is not None:
        return tuple(row["problem"].number for row in rows if not row["versions"])
    sent = set(
        submissions_for_user(user, competition).filter(entry=entry).values_list("problem_id", flat=True)
    )
    return tuple(
        problem.number
        for problem in Problem.objects.filter(stage=entry.stage).order_by("number", "id")
        if problem.pk not in sent
    )


def _panel_links() -> list[dict]:
    """Ekrany panelu wystawione w pasku zakładek jako zwykłe odnośniki."""
    links = []
    for name, label in PANEL_LINKS:
        try:
            url = reverse(name)
        except NoReverseMatch:  # pragma: no cover - adres dołożony później albo wyłączony
            continue
        links.append({"url": url, "label": label})
    return links


class MeView(ParticipantRequiredMixin, TemplateView):
    """Pulpit uczestnika: nagłówek „Co teraz” i jedna z czterech zakładek.

    Widok liczy **wyłącznie** to, co renderuje wybrana zakładka. Wspólny jest sam nagłówek, więc
    jego fakty są tanie z założenia (stan zgód, stan zgody opiekuna, numer pierwszego zadania bez
    rozwiązania) – nigdy komplet wyników ani kolejka reklamacji.
    """

    template_name = "web/participant/dashboard.html"

    def active_tab(self) -> str:
        """Zakładka z adresu. Nieznana wartość to zakładka domyślna, a nie 404.

        Parametr w adresie jest danymi od nadawcy żądania i bywa uszkodzony przez skrócenie linku
        albo autokorektę w komunikatorze. Panel ma się wtedy otworzyć, a nie odmówić.
        """
        requested = self.request.GET.get("tab") or TAB_TASKS
        return requested if requested in dict(PANEL_TABS) else TAB_TASKS

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()
        edition = current_edition(self.competition)
        stage = current_stage(edition, now) if edition else None
        entry = _entry_for(self.participant, stage)
        tab = self.active_tab()
        # Ta sama lista rodzajów, na której stoi ``register_for_stage`` – widok tylko ukrywa
        # przycisk, którego serwis i tak by nie przyjął. Gdyby powtarzał tu regułę własnym
        # warunkiem (``kind == ELIM``), dołożenie treningu do zapisów otwartych zmieniłoby serwis,
        # a przycisk zostałby ukryty.
        can_register = (
            stage is not None
            and entry is None
            and stage.kind in SELF_REGISTRATION_KINDS
            and stage.is_open_for_submissions(now)
        )
        # Etap w formie rozmowy ani w formie testu online nie ma uploadu w ogóle – nie
        # „zamkniętego”, tylko żadnego (``submissions.create_submission`` odmawia
        # z ``STAGE_NOT_ACCEPTING_FILES``). Warunek jest tu po to, żeby panel nie pokazywał
        # kart zadań, których w takim etapie i tak nie ma.
        upload_open = (
            entry is not None
            and not stage.is_interview
            and not stage.is_quiz
            and stage.is_open_for_submissions(now)
            and stage.closed_at is None
        )
        context.update(
            {
                "now": now,
                # Profil **tego** konkursu, wprost w kontekście: po § 3.3 jedna osoba ma tyle
                # profili, w ilu olimpiadach startuje, więc szablon nie ma jak dojść do
                # właściwego z samego ``user`` – a droga, którą chodził dotąd
                # (``user.participant``), po prostu nie istnieje.
                "participant": self.participant,
                "edition": edition,
                "stage": stage,
                "entry": entry,
                "tab": tab,
                "tabs": [
                    {
                        "key": key,
                        "label": label,
                        "url": reverse("web:me") if key == TAB_TASKS else f"{reverse('web:me')}?tab={key}",
                        "current": key == tab,
                    }
                    for key, label in PANEL_TABS
                ],
                "panel_links": _panel_links(),
                "can_register": can_register,
                "stage_opened": stage is not None and stage.has_opened(now),
                "upload_open": upload_open,
                # Terminy rozmów liczymy tylko dla etapu w formie rozmowy: w pozostałych obie
                # wartości byłyby pustą listą i ``None``, a zapytania i tak by poszły. Liczymy je
                # na każdej zakładce, bo o „zapisz się na rozmowę” pyta też nagłówek „Co teraz”.
                "interview_rows": (
                    _visible_slots(slots_for_participant(stage, self.participant, now), now)
                    if stage is not None and stage.is_interview
                    else []
                ),
                "interview_booking": (
                    booking_for_participant(stage, self.participant)
                    if stage is not None and stage.is_interview
                    else None
                ),
                # Instrukcja „co zrobić przed rozmową” w jednym brzmieniu dla panelu i dla listu –
                # patrz ``apps.competitions.video.PRECHECK_TEXT``.
                "interview_precheck_text": PRECHECK_TEXT,
                # Test online etapu – wyłącznie dla etapu w tej formie, tak samo jak terminy
                # rozmów wyżej: w pozostałych etapach zapytanie poszłoby po to, żeby oddać
                # ``None``. Zakładka „Zadania” pokazuje na tej podstawie kartę wejścia do testu;
                # o tym, czy wolno go rozpocząć, rozstrzyga i tak ``apps.quiz.services``.
                "stage_quiz": (quiz_for_stage(stage) if stage is not None and stage.is_quiz else None),
                # Słowa odliczania jadą do przeglądarki w atrybutach ``data-*``: skrypt odświeżający
                # licznik nie ma katalogu tłumaczeń i nie może mieć własnych napisów.
                "countdown_words": countdown_words(),
            }
        )
        context.update(self._tab_context(tab, user, edition, now, stage, entry, upload_open))
        context["now_panel"] = self._now_panel(context, now, stage, entry, can_register, upload_open)
        return context

    def _tab_context(self, tab, user, edition, now, stage, entry, upload_open) -> dict:
        """Dane **tylko** wybranej zakładki. Każda gałąź odpowiada jednemu ekranowi."""
        if tab == TAB_RESULTS:
            return {"results": results_for_participant(user, self.competition)}
        if tab == TAB_APPEALS:
            return {
                # Reguła „co podlega reklamacji” mieszka w serwisie reklamacji, nie w widoku –
                # ten sam predykat obowiązuje w API i przy walidacji w ``file_appeal``.
                "appealable": appealable_submissions(user, now, self.competition),
                "appeal_form": AppealForm(),
                "my_appeals": list(appeals_for_participant(user, self.competition)),
            }
        if tab == TAB_CONSENTS:
            records = consents_for_participant(self.participant)
            return {
                "consent_records": records,
                "consent_rows": _consent_rows(records),
                # Stan zgody opiekuna liczy serwis (``apps.accounts.guardian``): wiek uczestnika,
                # wysłana prośba i wpis dowodowy to trzy fakty z trzech miejsc i szablon nie ma
                # ich składać samodzielnie.
                "guardian": guardian_status(self.participant),
                "publish_name_kind": ConsentKind.PUBLISH_NAME,
            }
        context = {
            "upload_form": SubmissionUploadForm(),
            "problem_rows": _problem_rows(user, entry, self.competition),
        }
        context.update(self._training_context(user, edition, now))
        return context

    def _now_panel(self, context, now, stage, entry, can_register, upload_open):
        """Nagłówek „Co teraz” – fakty zbierane tak, żeby nie powtarzać zapytań zakładki."""
        guardian = context.get("guardian") or guardian_status(self.participant)
        booking = context.get("interview_booking")
        return now_panel(
            now=now,
            stage=stage,
            entry=entry,
            can_register=can_register,
            upload_open=upload_open,
            missing_numbers=_missing_numbers(
                self.request.user, entry, context.get("problem_rows"), self.competition
            ),
            interview_booked=booking is not None,
            interview_bookable=any(row["bookable"] for row in context.get("interview_rows", [])),
            booked_slot_at=booking.slot.starts_at if booking is not None else None,
            guardian_state=guardian["state"],
            consents_complete=_consents_complete(self.participant, context.get("consent_records")),
            account_active=self.request.user.is_active,
            results_ready=self._results_ready(context),
        )

    def _results_ready(self, context) -> bool:
        """Czy jakikolwiek etap ma już ogłoszone wyniki tego uczestnika.

        Na zakładce wyników odpowiedź jest darmowa (lista już policzona); poza nią idzie jedno
        zapytanie o istnienie wpisu, a **nie** komplet punktów i komentarzy – nagłówek pyta
        „czy jest co czytać”, a nie „ile jest punktów”.
        """
        results = context.get("results")
        if results is not None:
            return bool(results)
        return StageEntry.objects.filter(
            participant=self.participant, stage__results_published_at__isnull=False
        ).exists()

    def _training_context(self, user, edition, now) -> dict:
        """Etap treningowy jako **druga**, niezależna karta pulpitu.

        Trening nie może przyjść z ``current_stage`` (ta funkcja go pomija – inaczej piaskownica
        bez terminu zostawałaby „etapem bieżącym” w każdej przerwie między zawodami), a jest
        jedynym miejscem, w którym uczestnik przejdzie całą ścieżkę zgłoszenie → upload → wyniki
        poza zawodami. Dlatego pulpit liczy dla niego ten sam komplet wartości, co dla etapu
        zawodów, i renderuje tym samym ``_problem_card.html``: karta uploadu, która zachowuje się
        „prawie jak prawdziwa”, nie nauczyłaby niczego o tej prawdziwej.

        Rozmów tu nie ma: trening jest z definicji etapem oddawania plików
        (``StageFormat.SUBMISSIONS``), więc żadnej gałęzi ``is_interview`` ta karta nie potrzebuje.
        """
        stage = training_stage(edition)
        if stage is None:
            return {"training_stage": None}
        entry = _entry_for(self.participant, stage)
        return {
            "training_stage": stage,
            "training_entry": entry,
            "training_can_register": entry is None and stage.is_open_for_submissions(now),
            "training_upload_open": (
                entry is not None and stage.is_open_for_submissions(now) and stage.closed_at is None
            ),
            "training_problem_rows": _problem_rows(user, entry, self.competition),
        }


class ConsentPublishNameView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Wyrażenie albo wycofanie zgody na publikację imienia i nazwiska w tabelach wyników.

    Jedyna zgoda odwracalna z poziomu portalu – uzasadnienie w
    ``accounts.services.set_publish_name_consent``. Wartość przychodzi **jawnie** w polu ``given``
    (``1``/``0``), a nie jako „odwróć bieżący stan”: dwa kliknięcia w tę samą stronę (podwójne
    wysłanie formularza, powrót „wstecz”) muszą dawać ten sam wynik, a nie przełączać zgodę tam
    i z powrotem.
    """

    success_url = reverse_lazy("web:me")

    def get_success_url(self, *args, **kwargs) -> str:
        """Powrót na **tę samą zakładkę**, z której poszło kliknięcie.

        Odesłanie na domyślną zakładkę („Zadania”) po przestawieniu zgody kazałoby uczestnikowi
        szukać wiersza, który właśnie zmienił – a komunikat o zapisaniu zgody wisiałby nad
        zupełnie inną treścią.
        """
        return f"{reverse('web:me')}?tab={TAB_CONSENTS}"

    def perform(self, request) -> str:
        given = request.POST.get("given") == "1"
        set_publish_name_consent(self.participant, given=given, source=ConsentSource.PANEL, request=request)
        return (
            "Zgoda na publikację imienia i nazwiska została zapisana."
            if given
            else "Zgoda na publikację imienia i nazwiska została wycofana."
        )


class StageRegisterView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Rejestracja do etapu eliminacyjnego (``competitions.services.register_for_stage``)."""

    success_url = reverse_lazy("web:me")

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"),
            pk=stage_id,
        )
        register_for_stage(self.participant, stage)
        return "Zgłoszenie do etapu zostało przyjęte."


class InterviewBookView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Zapis na termin rozmowy albo zmiana już wybranego (``competitions.interviews.book_slot``).

    Jedna akcja na oba przypadki, bo z punktu widzenia uczestnika to jedno kliknięcie: „chcę ten
    termin”. Rozstrzygnięcie, czy to nowy zapis, czy przeniesienie, należy do serwisu – razem
    z blokadą wiersza terminu, bez której dwa równoległe kliknięcia zajęłyby jedno miejsce.
    """

    success_url = reverse_lazy("web:me")

    def perform(self, request, slot_id: int) -> str:
        slot = get_object_or_404(
            InterviewSlot.objects.for_competition(request.competition).select_related(
                "stage", "stage__edition"
            ),
            pk=slot_id,
        )
        book_slot(self.participant, slot, request=request)
        return "Termin rozmowy został zapisany. Potwierdzenie wysyłamy e-mailem."


class InterviewChooseView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Zapis na termin wybrany z **listy wyboru** (jeden formularz, pole ``slot_id``).

    Osobny adres od ``interview-book``, choć czynność jest ta sama. Powód jest w interfejsie:
    lista terminów jest listą pól wyboru z jednym przyciskiem „Zapisz się na wybrany termin”,
    a formularz HTML ma jeden adres docelowy – identyfikator terminu przychodzi więc w polu,
    a nie w ścieżce. Adres z identyfikatorem w ścieżce zostaje nietknięty: jest w API panelu
    i w linkach wysyłanych z listu, a zmiana kształtu ekranu nie może ich unieważnić.

    Reguły (wolne miejsca, przeniesienie zapisu, blokada wiersza) zostają w ``book_slot`` –
    tutaj jest wyłącznie odczytanie pola i ten sam komunikat, co przy zapisie ze ścieżki.
    """

    success_url = reverse_lazy("web:me")

    def perform(self, request) -> str:
        slot_id = (request.POST.get("slot_id") or "").strip()
        if not slot_id.isdigit():
            raise DomainError("Wybierz termin rozmowy z listy.", "INVALID_INPUT", HTTP_400_BAD_REQUEST)
        slot = get_object_or_404(
            InterviewSlot.objects.for_competition(request.competition).select_related(
                "stage", "stage__edition"
            ),
            pk=int(slot_id),
        )
        book_slot(self.participant, slot, request=request)
        return "Termin rozmowy został zapisany. Potwierdzenie wysyłamy e-mailem."


class InterviewCancelView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Rezygnacja z zapisanego terminu rozmowy w etapie."""

    success_url = reverse_lazy("web:me")

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"),
            pk=stage_id,
        )
        cancel_booking(self.participant, stage=stage, request=request)
        return "Termin rozmowy został odwołany. Możesz wybrać inny."


class ProblemUploadView(ParticipantRequiredMixin, ThrottledFormMixin, View):
    """Upload rozwiązania jednego zadania. Odpowiedź HTMX to odświeżona karta zadania.

    Deadline i walidację pliku (rozmiar, magic bytes, formaty) egzekwuje
    ``submissions.services.create_submission`` – widok nie powtarza ani jednej z tych reguł.

    Kolejność mixinów: najpierw rola (anonim dostaje 302, obcy 403), dopiero potem limit –
    licznik uploadów nie ma się zapełniać żądaniami, które i tak nie wchodzą do widoku.
    Scope ``upload`` jest ten sam, co w ``POST /api/submissions/``.
    """

    template_name = "web/participant/_problem_card.html"
    throttle_scope = "upload"

    def post(self, request, stage_id: int, number: int):
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"),
            pk=stage_id,
        )
        entry = get_object_or_404(StageEntry, participant=self.participant, stage=stage)
        problem = get_object_or_404(
            Problem.objects.for_competition(request.competition), stage=stage, number=number
        )
        form = SubmissionUploadForm(request.POST, request.FILES)
        error = None
        if form.is_valid():
            try:
                create_submission(
                    user=request.user,
                    stage=stage,
                    problem_number=number,
                    upload=form.cleaned_data["file"],
                    request=request,
                )
            except DomainError as exc:
                error = str(exc.detail)
        now = timezone.now()
        context = {
            "row": _problem_row(request.user, entry, problem, request.competition),
            "stage": stage,
            "entry": entry,
            "now": now,
            "upload_open": stage.is_open_for_submissions(now) and stage.closed_at is None,
            # Formularz wraca **związany**, gdy odmowa dotyczy pola: karta ma wtedy pokazać błąd
            # przy tym polu, którego dotyczy („zaznacz potwierdzenie” stoi przy polu wyboru,
            # a nie w komunikacie nad całą kartą, gdzie nie widać, co poprawić). Po udanej wysyłce
            # i po odmowie serwisu (deadline, format, rozmiar) formularz jest czysty – tam błąd
            # dotyczy całej czynności, a nie jednego pola.
            "upload_form": form if form.errors else SubmissionUploadForm(),
            "error": error,
        }
        return TemplateResponse(request, self.template_name, context)


class AppealCreateView(ParticipantRequiredMixin, View):
    """Złożenie reklamacji na własne rozwiązanie (``appeals.services.file_appeal``).

    Powrót idzie na zakładkę reklamacji, a nie na domyślną: to tam stoi formularz, z którego
    przyszło żądanie, i tam jest lista, na której zaraz widać nowy wiersz.
    """

    def post(self, request, submission_id: int):
        submission = get_object_or_404(
            submissions_for_user(request.user, request.competition), pk=submission_id
        )
        target = f"{reverse('web:me')}?tab={TAB_APPEALS}"
        form = AppealForm(request.POST)
        if not form.is_valid():
            messages.error(request, " ".join(form.errors.get("argument", ["Nieprawidłowe uzasadnienie."])))
            return redirect(target)
        try:
            file_appeal(request.user, submission, form.cleaned_data["argument"], request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Reklamacja została złożona.")
        return redirect(target)
