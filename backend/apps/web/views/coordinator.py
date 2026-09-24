"""Panel koordynatora ``/coordinator/``.

Koordynator jest jedyną rolą, która widzi dane osobowe (podgląd tabeli wyników przed publikacją)
oraz tożsamość recenzentów w kolejce moderacji – tak stanowi macierz uprawnień (PROJEKT.md 2.3).
Każda akcja to wywołanie istniejącego serwisu; widok nie zna reguł domenowych.

Kod zaproszenia jest pokazywany **dokładnie raz**, przez komunikat sesyjny: w bazie zostaje
wyłącznie sha256, więc odtworzenie go nie jest możliwe (``accounts.services.create_invitation``).
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.accounts.activation import (
    ACTIVATION_MAX_AGE,
    mark_activated,
    resend_activation,
)
from apps.accounts.models import (
    CommitteeMember,
    InvitationCode,
    InvitationGrantsStatus,
)
from apps.accounts.services import (
    approve_committee_member,
    create_invitation,
    resend_invitation,
    revoke_invitation,
    send_invitations,
    verify_committee_district,
)
from apps.competitions.models import Problem, Stage
from apps.competitions.scoring import coordinator_score_widget, safe_score_rule
from apps.competitions.services import current_edition, missing_stage_kinds
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.core.points import format_points
from apps.grading.models import ProblemReviewerRule, Review, ReviewStatus
from apps.grading.services import (
    ASSIGNMENT_STATUS_FILTERS,
    add_problem_reviewer_rule,
    allowed_scores,
    assign_reviewer_to_submission,
    assign_reviewers,
    assign_third_reviewer,
    override_final_grade,
    remove_problem_reviewer_rule,
    resolve_moderation,
    set_review_score,
    stage_assignment_rows,
    stage_problem_rules,
    unassign_reviewer,
)
from apps.results.models import ResultsPublication
from apps.results.services import compute_stage_results, publish_results
from apps.student_status.models import enabled as student_status_enabled
from apps.submissions.services import (
    build_stage_zip,
    close_stage_now,
    lock_for_review,
    lock_submission_for_review,
    review_counters,
    stage_zip_filename,
)
from apps.web.forms import (
    AssignReviewersForm,
    AssignThirdReviewerForm,
    BulkInvitationForm,
    InvitationForm,
    OverrideFinalGradeForm,
    PublishResultsForm,
    ResolveModerationForm,
    ReviewerPickForm,
    SetReviewScoreForm,
    VerifyDistrictForm,
)
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin
from apps.web.points_fields import score_form_error
from apps.web.scoping import reviewer_pool_for
from apps.web.templatetags.web_extras import LOCAL_TIME_LABEL, local_time
from apps.web.views.coordinator_accounts import users_for_competition

DASHBOARD_URL = reverse_lazy("web:coordinator")


def dashboard_context(competition, extra: dict | None = None) -> dict:
    """Kontekst pulpitu: „co wymaga uwagi” plus karty etapów – dla **jednego** konkursu.

    Pulpit odpowiada dziś na **jedno** pytanie – czym trzeba się zająć – i pokazuje kalendarz
    edycji. Kolejki (moderacja, aktywacje, komitet, zaproszenia, podgląd wyników) mają własne
    adresy w ``coordinator_pages.py``; tutaj zostaje z nich sama liczba na kafelku. Dzięki temu
    wejście na pulpit kosztuje kilkanaście zapytań, a nie kilkadziesiąt, i da się z niego
    cokolwiek wyczytać bez przewijania.

    Konkurs jest argumentem **wymaganym i pierwszym**, bo pulpit jest ekranem, na którym błąd
    zakresu widać najpóźniej: same liczby, bez nazwisk i identyfikatorów, po których dałoby się
    poznać, że pochodzą od sąsiada.
    """
    from apps.web.views.coordinator_pages import attention_rows

    edition = current_edition(competition)
    # ``Count`` w zapytaniu, a nie ``stage.problems.count()`` w szablonie: liczniki zadań i terminów
    # rozmów stoją na każdej karcie etapu, więc pętla w szablonie kosztowałaby zapytanie na etap.
    # ``distinct=True`` przy obu, bo dwa ``Count`` na tej samej karcie mnożą wiersze przez siebie.
    stage_qs = Stage.objects.filter(edition=edition).annotate(
        problem_count=Count("problems", distinct=True),
        slot_count=Count("interview_slots", distinct=True),
    )
    stages = list(stage_qs.order_by("opens_at", "id")) if edition else []
    published = set(
        ResultsPublication.objects.for_competition(competition)
        .filter(stage__in=stages)
        .values_list("stage_id", flat=True)
    )
    context = {
        "now": timezone.now(),
        "edition": edition,
        # Liczniki „oddane (niezablokowane)” i „w ocenie” stoją na karcie etapu, bo to jedyne dwie
        # liczby, po których widać, czy „Zablokuj oddane prace do oceny” ma jeszcze co robić.
        # Jedno zapytanie na etap, a etapów w edycji są trzy – stronicowania nie ma czego chronić.
        "stage_rows": [
            {
                "stage": stage,
                "has_results": stage.pk in published,
                "counters": review_counters(stage),
            }
            for stage in stages
        ],
        # Przycisk „Dodaj etap” znika, kiedy edycja ma już wszystkie trzy rodzaje: para
        # (edycja, rodzaj) jest unikalna, więc formularz nie miałby czego zaproponować.
        "missing_kinds": missing_stage_kinds(edition) if edition else [],
        # Kafelki „co wymaga uwagi” – liczby te same, co badge w menu (jedno źródło, jedna minuta
        # pamięci podręcznej). Kolejki, do których prowadzą, mieszkają w ``coordinator_pages.py``.
        "attention": attention_rows(competition),
        "assign_form": AssignReviewersForm(),
        # Wybór zakresu paczki ZIP („wszystkie prace” / „tylko potwierdzony status ucznia”) przy
        # przyciskach pobrania. Flagę czyta widok, a nie szablon (§ 2.1 punkt 3); przy wyłączonej
        # szablon rysuje dzisiejszy odnośnik co do znaku. Odczyt bez zapytania – flaga jest polem
        # wiersza konkursu, który jest już w pamięci.
        "zip_scope_choice": student_status_enabled(competition),
    }
    context.update(extra or {})
    return context


#: Ile wysłanych zaproszeń pokazujemy w panelu. Tabela jest narzędziem do pytania „czy ta osoba
#: dostała kod i co się z nim stało”, a nie archiwum – pełną historię ma audyt (``invitation.*``).
SENT_INVITATIONS_LIMIT = 100


def sent_invitation_rows(competition, limit: int = SENT_INVITATIONS_LIMIT) -> list[InvitationCode]:
    """Zaproszenia **wysłane listem** w tym konkursie, od najnowszego.

    Kody bez adresu (komenda CLI, sekcja „Kod zaproszenia”) do tej tabeli nie wchodzą: nie ma przy
    nich czego ponawiać ani komu unieważniać – kod przekazał człowiek i tylko on wie komu.

    Sortowanie po ``sent_at``, a nie po ``created_at``: ponowienie wystawia nowy kod, a interesuje
    nas kolejność listów, które wyszły.
    """
    return list(
        InvitationCode.objects.for_competition(competition)
        .filter(email__isnull=False)
        .exclude(email="")
        .order_by("-sent_at", "-id")[:limit]
    )


def pending_activation_rows(competition, now=None) -> list[dict]:
    """Konta, które czekają na potwierdzenie adresu e-mail – z czasem do automatycznego skasowania.

    Ta sekcja jest **obejściem operacyjnym z terminem ważności**: dopóki domena nadawcy nie ma
    poprawnych rekordów SPF/DKIM (README § 4.2), część listów aktywacyjnych trafia do spamu albo
    jest odrzucana przez serwer odbiorcy, a uczestnik nie ma jak sam wejść do serwisu. Koordynator
    potwierdza wtedy adres ręcznie – po kontakcie telefonicznym albo ze szkołą.

    Pozostały czas jest tu, bo bez niego przycisk „Aktywuj ręcznie” jest ruletką: konto starsze niż
    okno aktywacji zniknie przy najbliższym przebiegu kosiarki (co 15 minut), więc koordynator musi
    wiedzieć, czy rozmawia o koncie, które jeszcze istnieje. Ujemna wartość znaczy „już po czasie,
    czeka na skasowanie” – i wtedy nie ma sensu do niego wracać.

    Role czytamy z grup jednym zapytaniem (``prefetch_related``): lista bywa długa, a rola jest tu
    jedyną podpowiedzią, czy chodzi o uczestnika, czy o zaproszonego recenzenta.

    Zakres jest ten sam, co na liście kont (``users_for_competition``): konto cudzego konkursu
    tu nie wchodzi, bo ręczna aktywacja cudzego konta nie jest czynnością tego koordynatora.
    Jedna definicja „czyje to konto” dla obu ekranów – dwie rozjechałyby się przy pierwszej zmianie.
    """
    now = now or timezone.now()
    deadline_offset = timedelta(seconds=ACTIVATION_MAX_AGE)
    rows = []
    users = (
        users_for_competition(competition)
        .filter(is_active=False, email_verified_at__isnull=True)
        .prefetch_related("groups")
        .order_by("date_joined", "id")
    )
    for user in users:
        purge_at = user.date_joined + deadline_offset
        rows.append(
            {
                "user": user,
                "purge_at": purge_at,
                # Minuty, nie sekundy: dokładność co do sekundy sugerowałaby, że kosiarka chodzi
                # w tym rytmie – a chodzi co kwadrans.
                "minutes_left": int((purge_at - now).total_seconds() // 60),
                "roles": ", ".join(sorted(group.name for group in user.groups.all())) or "—",
            }
        )
    return rows


class CoordinatorDashboardView(CoordinatorRequiredMixin, TemplateView):
    """Pulpit koordynatora: etapy, moderacja, komitet, zaproszenia, wyniki."""

    template_name = "web/coordinator/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(dashboard_context(self.competition))
        return context


def panel_referer(request) -> str | None:
    """Adres, z którego przyszło żądanie – **tylko** gdy jest ekranem panelu koordynatora.

    Czynności koordynatora mają dziś kilka miejsc wywołania: ta sama „Zatwierdź” stoi na ekranie
    komitetu i na karcie członka komisji, a „Aktywuj ręcznie” – w kolejce aktywacji i na karcie
    konta. Powrót na sztywno wyznaczony pulpit wyrzucałby człowieka z listy, którą właśnie
    przechodzi wiersz po wierszu.

    ``Referer`` pochodzi od nadawcy żądania, więc jest sprawdzany jak każde dane wejściowe:
    musi być adresem **tego** serwera (albo adresem względnym) i zaczynać się od ``/coordinator/``.
    Schemat i host są odcinane – do przekierowania wraca sama ścieżka z parametrami, żeby nawet
    poprawny adres nie mógł przemycić innego portu ani poświadczeń w ``user:hasło@``.
    """
    raw = (request.META.get("HTTP_REFERER") or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc and parts.netloc != request.get_host():
        return None
    if not parts.path.startswith("/coordinator/"):
        return None
    return urlunsplit(("", "", parts.path, parts.query, ""))


class CoordinatorActionView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """Baza akcji koordynatora: POST → serwis → komunikat → powrót na stronę, z której przyszła.

    Powrotem jest ekran wywołujący (``panel_referer``), a pulpit – wariantem zapasowym: żądanie
    bez nagłówka ``Referer`` (formularz wysłany z narzędzia, przeglądarka z wyciętym nagłówkiem)
    ma dokąd wrócić, a nie kończyć się pustą stroną.
    """

    success_url = DASHBOARD_URL

    def get_success_url(self, *args, **kwargs) -> str:
        return panel_referer(self.request) or str(self.success_url)


class CloseStageView(CoordinatorActionView):
    """Zamknięcie etapu: blokada najnowszych wersji plus znacznik ``closed_at``."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.for_competition(request.competition), pk=stage_id)
        locked = close_stage_now(stage, actor=request.user, request=request)
        return f"Etap zamknięty. Zablokowanych rozwiązań: {locked}."


class LockStageForReviewView(CoordinatorActionView):
    """Wciągnięcie oddanych prac do oceniania **bez** zamykania etapu (prośba organizatora).

    Osobny przycisk, a nie wariant „Zamknij etap”: różnica jest widoczna dla uczestnika, bo okno
    uploadu zostaje otwarte, a wysłanie nowej wersji unieważnia rozpoczętą ocenę. Komunikat mówi
    o tym wprost – inaczej koordynator miałby prawo sądzić, że praca jest już nietykalna.
    """

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.for_competition(request.competition), pk=stage_id)
        locked = lock_for_review(stage, actor=request.user, request=request)
        return (
            f"Zablokowano {locked} prac do oceny. Etap pozostaje otwarty – uczestnicy mogą nadal "
            "wysyłać nowe wersje."
        )


class AssignReviewersView(CoordinatorActionView):
    """Przydział recenzentów dla etapu. Pominięte prace (``skipped``) są wypisane z pseudonimem."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
        )
        form = AssignReviewersForm(request.POST)
        if not form.is_valid():
            raise DomainError("Nieprawidłowa liczba recenzentów na pracę.", "INVALID_PER_SUBMISSION")
        result = assign_reviewers(
            stage,
            form.cleaned_data["per_submission"],
            actor=request.user,
            request=request,
        )
        if result["skipped"]:
            codes = ", ".join(item["public_code"] for item in result["skipped"])
            messages.warning(
                request,
                f"Pominięto {len(result['skipped'])} prac (brak recenzentów bez konfliktu): {codes}.",
            )
        # Termin w komunikacie, bo to pierwsze pytanie po przydziale („do kiedy mają czas?”),
        # a odpowiedź na nie zna serwis: liczy ją z ``Stage.review_deadline_days`` i deadline'u
        # recenzji etapu (``apps.grading.deadlines``).
        due_at = result.get("due_at")
        # ``localtime`` jawnie: filtr ``local_time`` dostaje konwersję strefy od Django wyłącznie
        # w szablonie (``expects_localtime``), a wołany z Pythona sformatowałby UTC z etykietą
        # czasu polskiego – czyli godzinę o dwie za wcześnie, bez żadnego sygnału, że coś nie gra.
        deadline = f" Termin recenzji: {local_time(timezone.localtime(due_at))}." if due_at else ""
        assigned = f"Przydzielono {result['assignments']} recenzji"
        return f"{assigned} dla {result['submissions']} rozwiązań.{deadline}"


def _attach_problem_scales(stage: Stage, rows: list[dict], *, fallback: list[int]) -> None:
    """Dokłada do każdego wiersza skalę **jego zadania** – listy wyboru punktów muszą się zgadzać.

    Skala bywa nadpisana per zadanie, więc jedna lista dla całego ekranu pokazywałaby przy części
    prac wartości, których zapis by nie przyjął. Liczymy ją raz na zadanie, a nie raz na wiersz:
    etap finału ma tysiące prac i kilka zadań.

    ``score_widget`` (wydanie 0.35.0) niesie obok listy tryb etapu i granice zakresu – w etapie
    z dowolnymi wartościami formularz ma pole liczbowe, a zadanie z samym maksimum nie ma listy
    wartości wcale, więc samo ``scale_values`` nie odróżniałoby go od zadania bez skali.
    """
    scales: dict[int, list[int]] = {}
    widgets: dict[int, dict | None] = {}
    for problem in stage.problems.order_by("number", "id"):
        rule = safe_score_rule(stage, problem)
        scales[problem.pk] = sorted(rule.values) if rule is not None else []
        widgets[problem.pk] = coordinator_score_widget(rule)
    for row in rows:
        row["scale_values"] = scales.get(row["submission"].problem_id, fallback)
        row["score_widget"] = widgets.get(row["submission"].problem_id)


#: Ile wierszy na stronę ekranu przydziałów. Sto, bo tyle prac komisja przerabia za jednym
#: posiedzeniem, a każdy wiersz niesie kilka formularzy i rozwijaną historię – tysiąc takich
#: wierszy to strona, która długo się składa i w której nic się nie znajduje.
ASSIGNMENTS_PAGE_SIZE = 100

#: Ile wpisów audytu pokazuje „Historia” jednego wiersza. Dwadzieścia wystarcza na całe życie
#: jednej pracy (blokada, przydziały, oceny, korekty); dłuższa lista przestaje być odpowiedzią
#: na pytanie „co się z tym stało”, a staje się drugą przeglądarką audytu.
HISTORY_LIMIT = 20

#: Parametry adresu, które ekran przydziałów uznaje za swoje. Lista jest domknięta, bo to ona
#: decyduje, co wolno przepisać z powrotem do adresu po akcji POST – przepisywanie dowolnego
#: ciągu z żądania byłoby otwartą furtką na doklejanie obcych parametrów do przekierowania.
ASSIGNMENT_FILTER_PARAMS = ("q", "problem", "status", "reviewer", "page")


def _int_param(value: str | None) -> int | None:
    """Parametr adresu jako dodatnia liczba albo ``None`` – literówka w URL-u ma nie wywracać strony."""
    text = (value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _filter_query(params, *, with_page: bool = False) -> str:
    """Filtry ekranu przepisane z powrotem do postaci ``a=1&b=2``, z pominięciem obcych kluczy.

    Służy dwóm rzeczom naraz: odnośnikom stronicowania (filtr musi przeżyć „Następna”, więc numer
    strony jest wtedy doklejany osobno i tu go nie ma) oraz powrotowi po akcji POST, gdzie numer
    strony jest częścią miejsca, do którego się wraca – koordynator poprawiający punkty na trzeciej
    stronie ma po zapisie zobaczyć trzecią stronę, a nie pierwszą.
    """
    wanted = [
        (name, (params.get(name) or "").strip())
        for name in ASSIGNMENT_FILTER_PARAMS
        if (with_page or name != "page") and (params.get(name) or "").strip()
    ]
    return urlencode(wanted)


def _stage_counters(stage: Stage) -> list[dict]:
    """Liczniki kroków obiegu do nagłówka ekranu – jedno zapytanie, bez ładowania wierszy.

    Liczą to samo, co pokazuje tabela: **po jednej, najnowszej wersji** na parę (wpis, zadanie).
    Zwykłe ``GROUP BY status`` dawałoby liczby większe od listy, bo starsze wersje prac zostają
    w bazie ze statusem ``SUBMITTED`` – koordynator zobaczyłby „oddane: 40” nad tabelą czterech
    prac. Wiersze czytamy jako ``values_list``, więc cena to jedno zapytanie po trzech kolumnach.

    Liczniki opisują **cały etap**, a nie wynik filtrów: są punktem odniesienia („z 40 oddanych
    widzę teraz 12”), a nie podsumowaniem tego, co akurat widać.
    """
    from apps.submissions.models import Submission

    tracked = tuple(status for _, statuses in ASSIGNMENT_STATUS_FILTERS.values() for status in statuses)
    latest: dict[tuple[int, int], str] = {}
    for entry_id, problem_id, status in (
        Submission.objects.filter(entry__stage=stage, status__in=tracked)
        .order_by("entry_id", "problem_id", "-version")
        .values_list("entry_id", "problem_id", "status")
    ):
        latest.setdefault((entry_id, problem_id), status)
    counts = Counter(latest.values())
    return [
        {"key": key, "label": label, "count": sum(counts[status] for status in statuses)}
        for key, (label, statuses) in ASSIGNMENT_STATUS_FILTERS.items()
    ]


def _counter_links(counters: list[dict], params, *, active: str) -> list[dict]:
    """Zamienia liczniki w przełączniki filtra statusu – jedno kliknięcie zamiast listy wyboru.

    Licznik i filtr są tą samą informacją widzianą z dwóch stron („jest 12 prac w ocenie” / „pokaż
    prace w ocenie”), więc rozdzielanie ich na odznakę i osobne pole formularza kazałoby czytać
    liczbę w jednym miejscu, a klikać w drugim. Kliknięcie licznika **już włączonego** zdejmuje
    filtr, bo to jedyny naturalny sposób wyjścia z niego bez szukania „Wyczyść”.

    Pozostałe filtry przechodzą do adresu bez zmian: przełączenie statusu nie może po cichu
    kasować wpisanego przed chwilą nazwiska.
    """
    keep = [
        (name, (params.get(name) or "").strip())
        for name in ("q", "problem", "reviewer")
        if (params.get(name) or "").strip()
    ]
    for counter in counters:
        picked = list(keep)
        if counter["key"] != active:
            picked.append(("status", counter["key"]))
        counter["query"] = urlencode(picked)
        counter["active"] = counter["key"] == active
    return counters


def _attach_history(rows: list[dict]) -> None:
    """Dokłada do każdego wiersza ślad audytowy pracy **i jej recenzji** – jednym zapytaniem.

    Historia stoi przy wierszu, a nie w osobnej przeglądarce audytu, bo pytanie „dlaczego ta praca
    ma tyle punktów” pada nad tą właśnie tabelą, a odpowiedź („koordynator odebrał recenzję X,
    potem wpisał korektę”) jest ciągiem zdarzeń z dwóch typów obiektów naraz.

    Zapytanie jest jedno na całą stronę, a nie jedno na wiersz: przy stu wierszach i dwóch
    recenzentach na pracę wariant naiwny kosztowałby trzysta zapytań. Wpisy zbieramy po
    ``(target_type, target_id)`` – klucz jest tekstowy, bo taki jest w ``AuditLog``.

    Audyt z założenia nie zawiera danych osobowych (patrz ``apps.core.models``), więc kto co zrobił,
    czytamy z ``actor`` – jedynej osoby, którą wpis nazywa.
    """
    if not rows:
        return
    owners: dict[tuple[str, str], int] = {}
    for row in rows:
        submission_id = row["submission"].pk
        owners[("submissions.submission", str(submission_id))] = submission_id
        for review in row["reviews"]:
            owners[("grading.review", str(review.pk))] = submission_id
        row["history"] = []
    entries = (
        AuditLog.objects.filter(
            Q(
                target_type="submissions.submission",
                target_id__in=[key[1] for key in owners if key[0] == "submissions.submission"],
            )
            | Q(
                target_type="grading.review",
                target_id__in=[key[1] for key in owners if key[0] == "grading.review"],
            )
        )
        .select_related("actor")
        .order_by("-at", "-id")
    )
    by_submission: dict[int, list[AuditLog]] = {}
    for entry in entries:
        owner = owners.get((entry.target_type, entry.target_id))
        if owner is None:
            continue
        bucket = by_submission.setdefault(owner, [])
        # Ucinamy przy zbieraniu, a nie po: wpisy idą od najnowszego, więc dwudziesty pierwszy
        # i każdy następny jest z definicji starszy niż to, co i tak już mamy.
        if len(bucket) < HISTORY_LIMIT:
            bucket.append(entry)
    for row in rows:
        row["history"] = by_submission.get(row["submission"].pk, [])


class StageDownloadView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/stages/<id>/download/`` – prace etapu w jednym archiwum ZIP.

    Trzy zakresy jednym adresem, bo to jedna czynność w trzech rozmiarach:

    - ``GET`` – wszystkie prace etapu (po jednej, najnowszej czystej wersji na parę wpis-zadanie),
    - ``GET ?problem=<id>`` – jedno zadanie, czyli tyle, ile komisja czyta za jednym posiedzeniem,
    - ``POST`` z ``submission_ids`` – wiersze zaznaczone w tabeli przydziałów.

    ``POST`` przy pobieraniu nie jest zmianą stanu, tylko konsekwencją długości adresu: lista kilkuset
    identyfikatorów nie mieści się w URL-u, a formularz z checkboxami i tak wysyła je ciałem żądania.

    Nazwy plików w paczce są anonimowe (``<kod>_zad<numer>_v<wersja>``) także dla koordynatora,
    choć on jedyny widzi nazwiska: paczka wędruje do komitetu i po drodze nikt jej nie przepakowuje.

    Każdy z trzech zakresów przyjmuje parametr ``students`` (``all`` – domyślnie – albo
    ``verified``): „wszystkie prace” albo „tylko uczniowie z potwierdzonym statusem ucznia” (prośba
    organizatora z 24.09.2026). W ``GET`` jedzie w adresie, w ``POST`` – polem formularza
    zaznaczonych wierszy. Przy wyłączonej fladze ``student_status_certificate`` wartość ``verified``
    daje 404 z powodem, a nie cichą paczkę „wszystkich” (``student_status.services.wants_verified_only``).
    """

    def get(self, request, stage_id: int):
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
        )
        raw = (request.GET.get("problem") or "").strip()
        problem = None
        if raw:
            problem = get_object_or_404(
                Problem.objects.for_competition(request.competition),
                pk=raw if raw.isdigit() else 0,
                stage=stage,
            )
        return self._zip(request, stage, request.GET, problem=problem)

    def post(self, request, stage_id: int):
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
        )
        selected = [value for value in request.POST.getlist("submission_ids") if value.isdigit()]
        if not selected:
            messages.error(request, "Nie zaznaczono żadnej pracy.")
            return redirect(reverse("web:coordinator-stage-assignments", args=[stage.pk]))
        return self._zip(request, stage, request.POST, submission_ids=selected)

    def _zip(self, request, stage: Stage, params, *, problem=None, submission_ids=None):
        from apps.student_status.services import SCOPE_PARAM, wants_verified_only

        try:
            verified_only = wants_verified_only(params.get(SCOPE_PARAM), request.competition)
            package = build_stage_zip(
                stage,
                actor=request.user,
                request=request,
                problem=problem,
                submission_ids=submission_ids,
                verified_only=verified_only,
            )
        except DomainError as exc:
            # 404 ze zdaniem o powodzie, a nie przekierowanie: pobranie, które nie ma czego oddać,
            # musi się odróżniać od pobrania udanego także dla klienta bez przeglądarki.
            return TemplateResponse(request, "404.html", {"reason": str(exc.detail)}, status=404)
        return FileResponse(
            package.stream,
            as_attachment=True,
            filename=stage_zip_filename(
                stage, problem=problem, selected=submission_ids is not None, verified_only=verified_only
            ),
            content_type="application/zip",
        )


class StageAssignmentsView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/assignments/`` – przydziały i oceny etapu.

    Ekran jest osobny od pulpitu, bo odpowiada na inne pytanie: pulpit mówi „przydziel wszystko”,
    ten ekran – „komu konkretnie ma trafić ta praca” i „ile ostatecznie ma dostać punktów”. Tabela
    prac pokazuje **nazwisko obok kodu publicznego**: koordynator jest jedyną rolą, która ma do tego
    prawo (PROJEKT.md 2.3), a bez nazwiska nie da się załatwić telefonu „dzwonię w sprawie pracy
    mojego ucznia”.

    Wartości punktowe w formularzach pochodzą ze skali etapu, a nie z wolnego pola: ocena spoza
    skali i tak zostałaby odrzucona przez serwis, a lista wyboru mówi koordynatorowi wprost, czym
    dysponuje. Etap bez skali nie przewraca ekranu – formularze ocen po prostu na nim nie stoją.

    Strona jest w całości renderowana po stronie serwera, a zmiany idą zwykłymi POST-ami: skrypt
    (``assignments.js``) dokłada wyłącznie skróty (pasek akcji zbiorczych, wysyłka listy wyboru bez
    klikania „Przydziel”), więc ekran działa w całości bez JavaScriptu. Wierszy jest po sto na
    stronę – filtry i strony przenoszą się przez adres, więc przefiltrowaną tabelę da się wysłać
    komuś odnośnikiem.
    """

    template_name = "web/coordinator/assignments.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = get_object_or_404(
            Stage.objects.for_competition(self.request.competition).select_related("edition"),
            pk=self.kwargs["stage_id"],
        )
        params = self.request.GET
        query = params.get("q", "")
        problem_id = _int_param(params.get("problem"))
        # Nieznany klucz statusu traktujemy jak brak filtra: adres z literówką ma pokazać listę,
        # a nie pustą tabelę bez wyjaśnienia.
        status = params.get("status", "") if params.get("status") in ASSIGNMENT_STATUS_FILTERS else ""
        reviewer_id = _int_param(params.get("reviewer"))
        try:
            scores = sorted(allowed_scores(stage))
        except DomainError:
            # Etap bez skali punktacji to stan do naprawienia na ekranie „Skala punktacji”, a nie
            # powód, żeby odciąć koordynatora od przydziałów. Ekran stoi, znika tylko to, czego
            # nie da się wypełnić.
            scores = []
        rows = stage_assignment_rows(
            stage, query, problem_id=problem_id, status=status, reviewer_id=reviewer_id
        )
        paginator = Paginator(rows, ASSIGNMENTS_PAGE_SIZE)
        page = paginator.get_page(params.get("page"))
        page_rows = list(page.object_list)
        # Skale i historia liczą się dla **strony**, a nie dla całego wyniku: to one kosztują,
        # a koordynator i tak czyta tylko to, co widzi.
        _attach_problem_scales(stage, page_rows, fallback=scores)
        _attach_history(page_rows)
        context.update(
            {
                "stage": stage,
                "now": timezone.now(),
                "query": query,
                "problem_id": problem_id,
                "status": status,
                "reviewer_id": reviewer_id,
                "status_filters": ASSIGNMENT_STATUS_FILTERS,
                "counters": _counter_links(_stage_counters(stage), params, active=status),
                "stage_problems": list(stage.problems.order_by("number", "id")),
                "filter_query": _filter_query(params),
                # To samo zapytanie **z numerem strony** – jedzie w ukrytym polu każdego formularza
                # tabeli, żeby akcja wróciła dokładnie tam, gdzie ją kliknięto.
                "return_query": _filter_query(params, with_page=True),
                "page_obj": page,
                "paginator": paginator,
                "problem_rows": stage_problem_rules(stage),
                "submission_rows": page_rows,
                "reviewer_pool": reviewer_pool_for(self.request.competition),
                "assigned_status": ReviewStatus.ASSIGNED,
                "cancelled_status": ReviewStatus.CANCELLED,
                # Odebrać można recenzję w każdym stanie poza anulowaną – także wystawioną.
                # Czy w tej konkretnej sprawie wolno (ogłoszone wyniki, rozstrzygnięta ocena),
                # rozstrzyga dopiero serwis: ekran nie powiela reguły, tylko nie chowa przycisku.
                "withdrawable_statuses": (
                    ReviewStatus.ASSIGNED,
                    ReviewStatus.DRAFT,
                    ReviewStatus.SUBMITTED,
                ),
                "scale_values": scores,
                "results_published": ResultsPublication.objects.filter(stage=stage).exists(),
                # Wybór zakresu paczek ZIP – patrz ``dashboard_context``.
                "zip_scope_choice": student_status_enabled(self.request.competition),
            }
        )
        # Licznik reguł do podsumowania zwiniętego panelu: bez niego koordynator musiałby rozwinąć
        # panel, żeby się dowiedzieć, czy w ogóle jest co rozwijać.
        context["rules_count"] = sum(len(row["rules"]) for row in context["problem_rows"])
        return context


class StageAssignmentActionView(CoordinatorActionView):
    """Akcja wracająca na ekran przydziałów etapu, a nie na pulpit.

    Etap ustala ``perform`` (wynika z zadania, pracy albo recenzji, nie z adresu), więc powrót
    czyta ``self.stage_id``. Gdy akcja odpadła, zanim udało się go ustalić – wracamy na pulpit,
    bo nie wiadomo, na który ekran.

    Filtry wracają razem z ekranem. Każdy formularz w tabeli niesie ukryte pole ``filters``
    z bieżącym zapytaniem; bez tego koordynator, który zawęził listę do jednego zadania i poprawił
    w niej punkty, lądowałby po zapisie na pełnej liście etapu i musiał filtrować od nowa.
    Przepisujemy **tylko znane klucze** (``ASSIGNMENT_FILTER_PARAMS``), więc do przekierowania nie
    da się tędy dokleić niczego z zewnątrz.
    """

    def get_success_url(self, *args, **kwargs) -> str:
        stage_id = getattr(self, "stage_id", None)
        if stage_id is None:
            return str(DASHBOARD_URL)
        url = reverse("web:coordinator-stage-assignments", args=[stage_id])
        filters = _filter_query(QueryDict(self.request.POST.get("filters", "")), with_page=True)
        return f"{url}?{filters}" if filters else url


class AddProblemRuleView(StageAssignmentActionView):
    """Dodanie reguły „to zadanie recenzuje ta osoba” – działa też na prace już zablokowane."""

    def perform(self, request, problem_id: int) -> str:
        problem = get_object_or_404(
            Problem.objects.for_competition(request.competition).select_related("stage"), pk=problem_id
        )
        self.stage_id = problem.stage_id
        form = ReviewerPickForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).select_related("user"),
            pk=form.cleaned_data["reviewer_id"],
        )
        result = add_problem_reviewer_rule(problem, reviewer, actor=request.user, request=request)
        if result["conflicts"]:
            messages.warning(
                request,
                f"Pominięto {result['conflicts']} prac – recenzent ma konflikt interesów z ich autorami.",
            )
        return (
            f"Reguła dodana: zadanie {problem.number} recenzuje {reviewer.user.email}. "
            f"Dopisano recenzji do prac już zablokowanych: {result['assigned']}."
        )


class RemoveProblemRuleView(StageAssignmentActionView):
    """Usunięcie reguły. Recenzje, które z niej powstały, zostają – komunikat mówi to wprost."""

    def perform(self, request, pk: int) -> str:
        rule = get_object_or_404(
            ProblemReviewerRule.objects.for_competition(request.competition).select_related(
                "problem", "reviewer", "reviewer__user"
            ),
            pk=pk,
        )
        self.stage_id = rule.problem.stage_id
        email = rule.reviewer.user.email
        number = rule.problem.number
        remove_problem_reviewer_rule(rule, actor=request.user, request=request)
        return (
            f"Reguła usunięta (zadanie {number}, {email}). Przydziały, które już z niej powstały, "
            "zostają – zdejmij je osobno przyciskiem „Cofnij” albo „Odbierz”."
        )


class AssignSubmissionReviewerView(StageAssignmentActionView):
    """Ręczny przydział jednej pracy jednemu recenzentowi."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.for_competition(request.competition).select_related(
                "entry", "entry__participant"
            ),
            pk=submission_id,
        )
        self.stage_id = submission.entry.stage_id
        form = ReviewerPickForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).select_related("user"),
            pk=form.cleaned_data["reviewer_id"],
        )
        assign_reviewer_to_submission(submission, reviewer, actor=request.user, request=request)
        return (
            f"Przydzielono pracę {submission.entry.participant.public_code} "
            f"recenzentowi {reviewer.user.email}."
        )


class LockSubmissionForReviewView(StageAssignmentActionView):
    """Wciągnięcie do oceniania jednej wskazanej pracy – przed zamknięciem etapu.

    Odpowiednik przycisku z karty etapu, ale dla pojedynczego wiersza: komitet bierze do oceny
    konkretną pracę (np. tę, na którą czeka recenzent gotowy zacząć), a reszta etapu zostaje.
    """

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.for_competition(request.competition).select_related(
                "entry", "entry__participant"
            ),
            pk=submission_id,
        )
        self.stage_id = submission.entry.stage_id
        lock_submission_for_review(submission, actor=request.user, request=request)
        return (
            f"Praca {submission.entry.participant.public_code} została zablokowana do oceny. "
            "Uczestnik może nadal wysłać nową wersję – wtedy ocena zacznie się od nowa."
        )


class UnassignReviewView(StageAssignmentActionView):
    """Odebranie recenzentowi pracy – przydziału nietkniętego, szkicu albo wystawionej oceny."""

    def perform(self, request, pk: int) -> str:
        review = get_object_or_404(
            Review.objects.for_competition(request.competition).select_related(
                "submission",
                "submission__entry",
                "submission__entry__participant",
                "reviewer",
                "reviewer__user",
            ),
            pk=pk,
        )
        self.stage_id = review.submission.entry.stage_id
        # Stan sprzed operacji, bo komunikat ma opisywać to, co się właśnie stało: cofnięcie
        # nietkniętego przydziału to inna wiadomość niż odebranie gotowej oceny.
        was_started = review.status != ReviewStatus.ASSIGNED
        code = review.submission.entry.participant.public_code
        unassign_reviewer(review, actor=request.user, request=request)
        if was_started:
            return (
                f"Praca {code} odebrana recenzentowi {review.reviewer.user.email}. "
                "Jego ocena nie liczy się już do oceny końcowej."
            )
        return f"Przydział pracy {code} dla {review.reviewer.user.email} został cofnięty."


class SetReviewScoreView(StageAssignmentActionView):
    """Wpisanie albo poprawienie punktów jednej recenzji."""

    def perform(self, request, pk: int) -> str:
        review = get_object_or_404(
            Review.objects.for_competition(request.competition).select_related(
                "submission",
                "submission__entry",
                "submission__entry__participant",
                "reviewer",
                "reviewer__user",
            ),
            pk=pk,
        )
        self.stage_id = review.submission.entry.stage_id
        form = SetReviewScoreForm(request.POST)
        if not form.is_valid():
            raise DomainError(score_form_error(form, "Podaj punkty ze skali etapu."), "SCORE_REQUIRED")
        set_review_score(
            review,
            form.cleaned_data["score"],
            actor=request.user,
            request=request,
            rationale=form.cleaned_data["rationale"],
        )
        return (
            f"Praca {review.submission.entry.participant.public_code}: "
            f"{format_points(form.cleaned_data['score'])} pkt w recenzji {review.reviewer.user.email}."
        )


class OverrideFinalGradeView(StageAssignmentActionView):
    """Wpisanie albo korekta oceny końcowej pracy – także pracy, której nikt nie recenzował."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.for_competition(request.competition).select_related(
                "entry", "entry__participant"
            ),
            pk=submission_id,
        )
        self.stage_id = submission.entry.stage_id
        form = OverrideFinalGradeForm(request.POST)
        if not form.is_valid():
            raise DomainError(
                score_form_error(form, "Podaj punkty i uzasadnienie korekty."), "RATIONALE_REQUIRED"
            )
        result = override_final_grade(
            submission,
            form.cleaned_data["score"],
            rationale=form.cleaned_data["rationale"],
            actor=request.user,
            request=request,
        )
        if result["results_stale"]:
            # Ostrzeżenie, a nie odmowa: ogłoszona tabela jest dokumentem z chwili publikacji,
            # więc korekta wchodzi do niej dopiero przez ponowne przeliczenie i ogłoszenie.
            messages.warning(
                request,
                "Wyniki tego etapu są już ogłoszone – zmiana pojawi się dopiero po ponownym "
                "przeliczeniu i publikacji.",
            )
        return (
            f"Ocena końcowa pracy {submission.entry.participant.public_code}: "
            f"{result['grade'].score} pkt (korekta koordynatora)."
        )


class BulkAssignmentActionView(StageAssignmentActionView):
    """``POST /coordinator/stages/<id>/assignments/bulk/`` – jedna czynność na zaznaczonych pracach.

    Po co zbiorczo: komitet dzieli prace pakietami („te trzydzieści bierze Kowalski”), a klikanie
    trzydziestu list wyboru po kolei jest tą samą decyzją rozbitą na trzydzieści okazji do pomyłki.

    Trzy czynności, bo tyle jest sensownych na wielu pracach naraz:

    - ``assign`` – dopisz wskazanego recenzenta do każdej zaznaczonej pracy,
    - ``unassign`` – odbierz mu każdą z nich (wskazanie recenzenta jest wymagane także tutaj:
      „odbierz wszystkim” skasowałoby cudzą pracę jednym kliknięciem, a tego nikt nie chce zrobić
      hurtem),
    - ``lock`` – wciągnij zaznaczone prace do oceniania, bez zamykania etapu.

    Każda praca idzie przez **ten sam serwis**, co pojedynczy wiersz: reguły domenowe (konflikt
    województwa, praca zamknięta, ogłoszone wyniki) obowiązują identycznie, a zbiorcza akcja nie
    ma własnej ścieżki, którą dałoby się je obejść. Odmowa dotycząca jednej pracy nie przerywa
    reszty – ląduje na liście pominiętych z powodem, bo pakiet prac z jedną kolizją ma się
    wykonać w 29/30, a nie w 0/30.

    Formularz jest zwykły: kratki wierszy należą do niego przez atrybut ``form``, więc pasek akcji
    zbiorczych działa bez JavaScriptu. Skrypt dokłada wyłącznie kratkę „zaznacz wszystkie”
    i licznik zaznaczenia.
    """

    #: Etykiety czynności w komunikacie – w formie, w jakiej stoją na przycisku.
    ACTIONS = {"assign": "przydzielono", "unassign": "odebrano", "lock": "zablokowano do oceny"}

    def perform(self, request, stage_id: int) -> str:
        from apps.submissions.models import Submission

        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
        )
        self.stage_id = stage.pk
        action = (request.POST.get("action") or "").strip()
        if action not in self.ACTIONS:
            raise DomainError("Wybierz czynność zbiorczą.", "BULK_ACTION_REQUIRED")
        selected = [value for value in request.POST.getlist("submission_ids") if value.isdigit()]
        if not selected:
            raise DomainError("Nie zaznaczono żadnej pracy.", "NOTHING_SELECTED")
        submissions = list(
            Submission.objects.for_competition(request.competition)
            .filter(pk__in=selected, entry__stage=stage)
            .select_related("entry", "entry__participant")
            .order_by("entry__participant__public_code", "problem__number", "pk")
        )
        reviewer = None
        if action in ("assign", "unassign"):
            form = ReviewerPickForm(request.POST)
            if not form.is_valid():
                raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
            reviewer = get_object_or_404(
                CommitteeMember.objects.for_competition(request.competition).select_related("user"),
                pk=form.cleaned_data["reviewer_id"],
            )
        done = 0
        # Powód → kody prac. Grupowanie po powodzie, a nie lista „praca: powód”, bo pominięcia
        # w pakiecie są zwykle jednego rodzaju („recenzent z tego województwa”) i koordynator ma
        # zobaczyć jedną przyczynę z listą kodów, a nie trzydzieści razy to samo zdanie.
        skipped: dict[str, list[str]] = {}
        for submission in submissions:
            code = submission.entry.participant.public_code
            try:
                self._apply(request, action, submission, reviewer)
            except DomainError as exc:
                skipped.setdefault(str(exc.detail), []).append(code)
            else:
                done += 1
        return self._summary(action, reviewer, done=done, skipped=skipped)

    def _apply(self, request, action: str, submission, reviewer) -> None:
        """Jedna praca, jedna czynność – wyłącznie wołanie serwisu."""
        if action == "assign":
            assign_reviewer_to_submission(submission, reviewer, actor=request.user, request=request)
            return
        if action == "lock":
            lock_submission_for_review(submission, actor=request.user, request=request)
            return
        review = (
            Review.objects.filter(submission=submission, reviewer=reviewer)
            .exclude(status=ReviewStatus.CANCELLED)
            .order_by("round", "id")
            .first()
        )
        if review is None:
            raise DomainError("Ten recenzent nie ma tej pracy w ręku.", "NO_ACTIVE_REVIEW")
        unassign_reviewer(review, actor=request.user, request=request)

    def _summary(self, action: str, reviewer, *, done: int, skipped: dict[str, list[str]]) -> str:
        """Jedno zdanie o tym, co weszło, i jedno o tym, co nie – z kodami prac i powodem."""
        who = f" recenzentowi {reviewer.user.email}" if reviewer else ""
        head = f"Zbiorczo {self.ACTIONS[action]}{who}: {done} prac."
        if not skipped:
            return head
        details = "; ".join(f"{reason} ({', '.join(codes)})" for reason, codes in skipped.items())
        total = sum(len(codes) for codes in skipped.values())
        return f"{head} Pominięto {total}: {details}"


class ResolveModerationView(CoordinatorActionView):
    """Rozstrzygnięcie rozjazdu ocen przez koordynatora (``GradeMethod.MODERATION``)."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.for_competition(request.competition), pk=submission_id
        )
        form = ResolveModerationForm(request.POST)
        if not form.is_valid():
            raise DomainError(score_form_error(form, "Podaj punkty ze skali etapu."), "SCORE_REQUIRED")
        resolve_moderation(
            submission,
            request.user,
            form.cleaned_data["score"],
            None,
            form.cleaned_data["rationale"],
            request=request,
        )
        return "Rozjazd rozstrzygnięty."


class AssignThirdReviewerView(CoordinatorActionView):
    """Wyznaczenie trzeciego recenzenta (runda rozjemcza)."""

    def perform(self, request, submission_id: int) -> str:
        from apps.submissions.models import Submission

        submission = get_object_or_404(
            Submission.objects.for_competition(request.competition), pk=submission_id
        )
        form = AssignThirdReviewerForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wskaż recenzenta.", "REVIEWER_REQUIRED")
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).select_related("user"),
            pk=form.cleaned_data["reviewer_id"],
        )
        assign_third_reviewer(submission, reviewer, actor=request.user, request=request)
        return "Trzeci recenzent został wyznaczony."


class ApproveCommitteeMemberView(CoordinatorActionView):
    """Zatwierdzenie członka komitetu (PENDING → ACTIVE + grupy)."""

    def perform(self, request, pk: int) -> str:
        member = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).select_related("user"), pk=pk
        )
        approve_committee_member(member, actor=request.user)
        return "Członek komitetu został zatwierdzony."


class ActivateAccountView(CoordinatorActionView):
    """Ręczna aktywacja konta przez koordynatora – obejście na czas problemów z dostarczalnością.

    Wpis audytowy ma **własną akcję** (``account.activated_by_coordinator``), a nie tę samą, co
    kliknięcie linku: różnica jest istotna dla każdego, kto później pyta „skąd wiemy, że ten adres
    należy do tej osoby”. Przy aktywacji linkiem dowodem jest dostęp do skrzynki; tutaj – decyzja
    człowieka, który sprawdził to inaczej (telefonicznie, przez szkołę).
    """

    def perform(self, request, pk: int) -> str:
        # Konto **z tego konkursu**: ręczna aktywacja i ponowienie listu są czynnościami
        # organizatora wobec jego własnego uczestnika. Zawężenie jest to samo, co na liście kont
        # (``users_for_competition``), żeby dwa ekrany nie miały dwóch definicji „czyje to konto”.
        user = get_object_or_404(users_for_competition(request.competition), pk=pk)
        if user.email_verified_at is not None:
            raise DomainError("To konto jest już aktywne.", "ALREADY_ACTIVE")
        mark_activated(user, actor=request.user, action="account.activated_by_coordinator", request=request)
        return "Konto zostało aktywowane ręcznie."


class ResendActivationView(CoordinatorActionView):
    """Ponowna wysyłka linku aktywacyjnego z panelu koordynatora.

    Tu, w odróżnieniu od publicznego formularza, komunikat mówi prawdę o stanie konta: koordynator
    i tak widzi całą listę oczekujących, więc ukrywanie przed nim, że konto jest już aktywne,
    nie chroniłoby niczego, a utrudniałoby pracę.
    """

    def perform(self, request, pk: int) -> str:
        # Konto **z tego konkursu**: ręczna aktywacja i ponowienie listu są czynnościami
        # organizatora wobec jego własnego uczestnika. Zawężenie jest to samo, co na liście kont
        # (``users_for_competition``), żeby dwa ekrany nie miały dwóch definicji „czyje to konto”.
        user = get_object_or_404(users_for_competition(request.competition), pk=pk)
        if not resend_activation(user.email, request=request):
            raise DomainError(
                "Tego konta nie da się aktywować linkiem – adres jest już potwierdzony.",
                "NOTHING_TO_SEND",
            )
        return "Link aktywacyjny został wysłany ponownie."


class VerifyDistrictView(CoordinatorActionView):
    """Województwo członka komitetu: ustalenie albo usunięcie (pozycja „— brak —”).

    Województwo jest opcjonalne i wpływa wyłącznie na konflikt interesów na etapie wojewódzkim,
    więc pusta wartość jest tu decyzją koordynatora, a nie niewypełnionym polem.
    """

    def perform(self, request, pk: int) -> str:
        member = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).select_related("user"), pk=pk
        )
        form = VerifyDistrictForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wybierz województwo z listy albo „— brak —”.", "DISTRICT_INVALID")
        member = verify_committee_district(
            member,
            district=form.cleaned_data["district"] or None,
            actor=request.user,
            request=request,
        )
        if not member.district:
            return "Województwo zostało usunięte – ten członek komitetu ocenia prace z całego kraju."
        return f"Województwo zostało zapisane: {member.get_district_display()}."


class CreateInvitationView(CoordinatorActionView):
    """Generowanie kodu zaproszenia. Kod jawny pokazujemy raz i nigdzie go nie zapisujemy."""

    def perform(self, request) -> str:
        form = InvitationForm(request.POST)
        if not form.is_valid():
            raise DomainError("Nieprawidłowe parametry zaproszenia.", "INVALID_INVITATION_PARAMS")
        data = form.cleaned_data
        invitation, plain_code = create_invitation(
            request.user,
            valid_for=timedelta(days=data["valid_days"]),
            max_uses=data["max_uses"],
            grants_status=(
                InvitationGrantsStatus.PENDING if data["requires_approval"] else InvitationGrantsStatus.ACTIVE
            ),
            is_appeals=data["is_appeals"],
            district=data["district"] or None,
        )
        messages.warning(
            request,
            f"Kod zaproszenia (widoczny tylko teraz, nie da się go odtworzyć): {plain_code}",
        )
        # Ważność podajemy w czasie lokalnym – koordynator przepisuje ją do wiadomości dla
        # zapraszanego, a „UTC” w takim komunikacie było zaproszeniem do pomyłki o godzinę lub dwie.
        expires_local = timezone.localtime(invitation.expires_at)
        return (
            f"Kod ważny do {expires_local:%Y-%m-%d %H:%M} ({LOCAL_TIME_LABEL}), "
            f"limit użyć: {invitation.max_uses}."
        )


class SendInvitationsView(CoordinatorActionView):
    """Wysyłka zaproszeń e-mailem: jeden jednorazowy kod na adres, list z kodem i linkiem.

    Kod jawny nie idzie tu do komunikatu (inaczej niż w sekcji „Kod zaproszenia”): wysyłamy ich
    naraz kilkadziesiąt, a jedyny egzemplarz każdego z nich ma być w skrzynce **tej** osoby.
    Wypisanie ich koordynatorowi na ekran zamieniłoby zaproszenia indywidualne we wspólną listę
    poświadczeń – w dodatku widoczną każdemu, kto zajrzy mu przez ramię.
    """

    def perform(self, request) -> str:
        form = BulkInvitationForm(request.POST)
        if not form.is_valid():
            # Błędy formularza (w tym wypisane z nazwy błędne adresy) muszą dojść do człowieka –
            # ogólne „nieprawidłowe parametry” zostawiłoby go z listą stu adresów i bez wskazówki.
            raise DomainError(
                " ".join(message for values in form.errors.values() for message in values),
                "INVALID_INVITATION_PARAMS",
            )
        data = form.cleaned_data
        result = send_invitations(
            request.user,
            data["emails"],
            district=data["district"] or None,
            valid_for=timedelta(days=data["valid_days"]),
            grants_status=(
                InvitationGrantsStatus.PENDING if data["requires_approval"] else InvitationGrantsStatus.ACTIVE
            ),
            is_appeals=data["is_appeals"],
            note=data["note"],
            request=request,
        )
        if result["skipped"]:
            listed = ", ".join(f"{row['email']} ({row['reason']})" for row in result["skipped"])
            messages.warning(request, f"Pominięto {result['skipped_count']} adresów: {listed}.")
        if not result["sent_count"]:
            # Sama prawda: „Wysłano 0 zaproszeń” brzmiałoby jak sukces, a nie wyszedł ani jeden list.
            raise DomainError(
                "Nie wysłano żadnego zaproszenia – wszystkie adresy zostały pominięte.",
                "NOTHING_SENT",
            )
        expires_local = timezone.localtime(result["expires_at"])
        return (
            f"Wysłano {result['sent_count']} zaproszeń. Kody są ważne do "
            f"{expires_local:%Y-%m-%d %H:%M} ({LOCAL_TIME_LABEL})."
        )


class ResendInvitationView(CoordinatorActionView):
    """„Wyślij ponownie”: stary kod przestaje działać, pod ten sam adres idzie nowy."""

    def perform(self, request, pk: int) -> str:
        invitation = get_object_or_404(InvitationCode.objects.for_competition(request.competition), pk=pk)
        fresh = resend_invitation(invitation, actor=request.user, request=request)
        return f"Nowe zaproszenie wysłane na {fresh.email}. Poprzedni kod został unieważniony."


class RevokeInvitationView(CoordinatorActionView):
    """„Unieważnij”: kod przestaje być przyjmowany przy rejestracji, wiersz zostaje w tabeli."""

    def perform(self, request, pk: int) -> str:
        invitation = get_object_or_404(InvitationCode.objects.for_competition(request.competition), pk=pk)
        revoked = revoke_invitation(invitation, actor=request.user, request=request)
        return f"Zaproszenie dla {revoked.email} zostało unieważnione."


class ComputeResultsView(CoordinatorRequiredMixin, View):
    """Podgląd pełnej tabeli wyników etapu bez publikacji (dane osobowe – tylko koordynator).

    Odpowiedź jest **ekranem wyników tego etapu**, a nie pulpitem: podgląd bywa długi na kilkaset
    wierszy, a po przeliczeniu robi się przy nim dokładnie jedną rzecz – publikuje albo wraca do
    oceniania. Odmowa (praca wciąż w ocenianiu) wraca na ten sam adres z komunikatem.
    """

    template_name = "web/coordinator/results.html"

    def post(self, request, stage_id: int):
        from apps.web.views.coordinator_pages import stage_results_context

        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related(
                "edition", "qualification_rule"
            ),
            pk=stage_id,
        )
        try:
            rows = compute_stage_results(stage)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator-stage-results", args=[stage.pk]))
        return TemplateResponse(request, self.template_name, stage_results_context(stage, rows))


class PublishResultsView(CoordinatorActionView):
    """Publikacja wyników: przeliczenie, kwalifikacja i zamrożenie zanonimizowanej tabeli."""

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related(
                "edition", "qualification_rule"
            ),
            pk=stage_id,
        )
        form = PublishResultsForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wybierz tryb anonimizacji.", "INVALID_ANONYMIZATION")
        publication = publish_results(
            stage, request.user, form.cleaned_data["anonymization"], request=request
        )
        return f"Opublikowano wyniki etapu ({len(publication.rows)} wierszy)."
