"""Raport postępu oceniania etapu i przypomnienia dla recenzentów (panel koordynatora).

Osobny moduł od ``apps.grading.services``, bo odpowiada na inne pytanie. Serwisy **zmieniają**
stan oceniania (przydziel, wystaw, rozstrzygnij rozjazd) i każdy z nich jest regułą domenową.
Tutaj mieszka wyłącznie odczyt: ile prac jest w którym stanie i kto z komitetu zalega. Rozdział
jest praktyczny, a nie estetyczny – raport wolno wołać z dowolnego ekranu bez obawy, że coś
zapisze, a serwisy nie puchną o kod, którego domena nie potrzebuje.

Zasady modułu:

- **liczniki liczy baza, nie Python**: jedno zapytanie agregujące na komplet stanów prac i jedno
  na tabelę recenzentów. Etap finału ma tysiące prac i kilkudziesięciu recenzentów – pętla po
  wierszach kosztowałaby zapytanie na wiersz i ekran przestałby się otwierać w połowie edycji,
- **segmenty paska sumują się do całości**: stany prac są rozłączne, bo pasek, którego kawałki
  się nakładają, kłamie o postępie. Liczby, które rozłączne nie są (prace z oceną końcową,
  reklamacje), stoją obok paska jako osobne wartości,
- **audyt bez danych osobowych**: przypomnienie zostawia liczniki, nigdy adresów recenzentów
  (``apps.core.models`` – ta sama reguła, co w całym systemie).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import CommitteeMember
from apps.appeals.models import Appeal, AppealStatus
from apps.competitions.models import Stage
from apps.core.models import audit
from apps.submissions.models import Submission, SubmissionStatus
from apps.tenancy import branding

from .models import Review, ReviewStatus
from .services import reviewer_pool

logger = logging.getLogger(__name__)

#: Po ilu dniach od przydziału recenzja jest „zaległa”, dopóki model recenzji nie ma własnego
#: terminu (``Review.due_at``). Siedem dni to najkrótszy odstęp, po którym przypomnienie nie jest
#: poganianiem: recenzent dostaje pracę razem z kilkoma innymi i tydzień jest minimum, żeby usiąść
#: do kompletu. Wartość obowiązuje **wyłącznie** w zastępstwie prawdziwego terminu – gdy pole
#: ``due_at`` istnieje, zaległość liczy się po nim i ta stała nie bierze udziału w rachunku.
FALLBACK_OVERDUE_DAYS = 7

#: Stany recenzji, które recenzent ma jeszcze do zrobienia. ``CANCELLED`` nie jest robotą – to
#: przydział, który stracił przedmiot (koordynator odebrał pracę, uczestnik wysłał nową wersję).
PENDING_REVIEW_STATUSES = (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT)

#: Rozłączny podział stanów zgłoszenia na segmenty paska postępu. Kolejność jest kolejnością
#: drogi pracy przez system i zarazem kolejnością segmentów na pasku – od „dopiero oddane”
#: do „ocenione”. ``REJECTED_INFECTED`` stoi na końcu, bo praca odrzucona przez antywirusa nigdy
#: do oceniania nie weszła; liczymy ją, żeby suma segmentów zgadzała się z liczbą wszystkich prac.
PROGRESS_SEGMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("submitted", "oddane", (SubmissionStatus.SUBMITTED, SubmissionStatus.SCANNING)),
    ("locked", "zablokowane", (SubmissionStatus.LOCKED,)),
    ("assigned", "przydzielone", (SubmissionStatus.IN_REVIEW,)),
    ("moderation", "w moderacji", (SubmissionStatus.MODERATION,)),
    ("appealed", "reklamacja", (SubmissionStatus.APPEALED,)),
    (
        "graded",
        "ocenione",
        (SubmissionStatus.GRADED_PROVISIONAL, SubmissionStatus.FINAL),
    ),
    ("rejected", "odrzucone (wirus)", (SubmissionStatus.REJECTED_INFECTED,)),
)


def review_has_due_at() -> bool:
    """Czy model recenzji ma własny termin oddania (``Review.due_at``).

    Pytanie zadajemy **w czasie działania**, a nie przy imporcie, i to jest istotne: pole jest
    dokładane niezależnie od tego raportu, a raport ma zacząć liczyć zaległości po prawdziwym
    terminie w tej samej chwili, w której pole się pojawi – bez przepisywania tego modułu.
    Dopóki pola nie ma, zaległość liczy się po ``assigned_at`` (patrz ``FALLBACK_OVERDUE_DAYS``),
    a ekran mówi o tym wprost, żeby nikt nie wziął heurystyki za termin regulaminowy.
    """
    return any(field.name == "due_at" for field in Review._meta.get_fields())


def overdue_filter(now=None) -> Q:
    """Warunek „recenzja jest zaległa”: nieoddana, a termin minął.

    Zaległa może być wyłącznie recenzja, którą wciąż da się zrobić – ``SUBMITTED`` jest zrobiona,
    a ``CANCELLED`` nie ma już przedmiotu. Termin bierzemy z ``Review.due_at``, jeśli model go ma;
    w przeciwnym razie z chwili przydziału powiększonej o ``FALLBACK_OVERDUE_DAYS``.
    """
    now = now or timezone.now()
    pending = Q(status__in=PENDING_REVIEW_STATUSES)
    if review_has_due_at():
        return pending & Q(due_at__isnull=False) & Q(due_at__lt=now)
    return pending & Q(assigned_at__lt=now - timedelta(days=FALLBACK_OVERDUE_DAYS))


def _percentages(counts: dict[str, int], total: int) -> dict[str, int]:
    """Udziały segmentów w procentach, zaokrąglone do pełnych 5% i zsumowane dokładnie do 100.

    Skok co 5% nie jest oszczędnością, tylko warunkiem rysowania paska **samym arkuszem stylów**:
    polityka bezpieczeństwa nie dopuszcza stylu w atrybucie, więc szerokość segmentu musi być
    klasą CSS z zamkniętej listy (``static/css/coordinator-tools.css``). Dokładne liczby stoją
    obok paska i to one są odpowiedzią – pasek jest ilustracją proporcji, nie pomiarem.

    Reszta z zaokrągleń idzie do największego segmentu, dzięki czemu kawałki zawsze wypełniają
    całą szerokość i nie zostaje „szpara”, która wyglądałaby jak brakujące prace.
    """
    if total <= 0:
        return {key: 0 for key in counts}
    shares = {key: round(value * 100 / total / 5) * 5 for key, value in counts.items()}
    present = [key for key, value in counts.items() if value]
    if not present:
        return shares
    drift = 100 - sum(shares.values())
    widest = max(present, key=lambda key: counts[key])
    shares[widest] = max(0, shares[widest] + drift)
    return shares


def stage_progress(stage: Stage) -> dict:
    """Postęp oceniania etapu: rozłączne liczniki prac plus liczby, które rozłączne nie są.

    Trzy zapytania na cały ekran, niezależnie od liczby prac: agregat po stanach zgłoszeń,
    agregat po reklamacjach i agregat po recenzjach. ``with_final_grade`` liczymy osobno od
    segmentu „ocenione”, bo to dwie różne rzeczy: stan pracy mówi, gdzie jest w procesie,
    a istnienie ``FinalGrade`` – czy ma już wpisaną ocenę (praca po reklamacji ma ocenę i nie
    jest w segmencie „ocenione”).
    """
    filters = {key: Count("id", filter=Q(status__in=statuses)) for key, _label, statuses in PROGRESS_SEGMENTS}
    aggregate = Submission.objects.filter(entry__stage=stage).aggregate(
        total=Count("id"),
        with_final_grade=Count("id", filter=Q(final_grade__isnull=False)),
        **filters,
    )
    appeals = Appeal.objects.filter(submission__entry__stage=stage).aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=AppealStatus.OPEN)),
    )
    total = aggregate["total"] or 0
    counts = {key: aggregate[key] or 0 for key, _label, _statuses in PROGRESS_SEGMENTS}
    shares = _percentages(counts, total)
    return {
        "stage": stage,
        "total": total,
        "with_final_grade": aggregate["with_final_grade"] or 0,
        "appeals": appeals["total"] or 0,
        "open_appeals": appeals["open"] or 0,
        "segments": [
            {"key": key, "label": label, "count": counts[key], "share": shares[key]}
            for key, label, _statuses in PROGRESS_SEGMENTS
        ],
    }


def reviewer_rows(stage: Stage, now=None) -> list[dict]:
    """Tabela recenzentów etapu: przydzielone / szkice / wystawione / zaległe.

    Wiersze powstają z **całej puli aktywnych recenzentów**, a nie z samych recenzji: koordynator
    musi zobaczyć także tych, którzy nie dostali w tym etapie ani jednej pracy – zero przydziałów
    jest informacją o rozkładzie obciążenia, a nie brakiem wiersza. Liczniki dokłada jeden agregat
    pogrupowany po recenzencie (``values`` + ``annotate``), więc koszt nie rośnie z liczbą recenzji.
    """
    now = now or timezone.now()
    overdue = overdue_filter(now)
    counters = {
        row["reviewer_id"]: row
        for row in Review.objects.filter(submission__entry__stage=stage)
        .values("reviewer_id")
        .annotate(
            assigned=Count("id", filter=Q(status=ReviewStatus.ASSIGNED)),
            draft=Count("id", filter=Q(status=ReviewStatus.DRAFT)),
            submitted=Count("id", filter=Q(status=ReviewStatus.SUBMITTED)),
            overdue=Count("id", filter=overdue),
        )
    }
    rows = []
    for member in reviewer_pool():
        counts = counters.get(member.pk, {})
        rows.append(
            {
                "member": member,
                "assigned": counts.get("assigned", 0),
                "draft": counts.get("draft", 0),
                "submitted": counts.get("submitted", 0),
                "overdue": counts.get("overdue", 0),
            }
        )
    # Najpierw zalegający, potem najbardziej obciążeni – ekran ma odpowiadać na pytanie
    # „do kogo zadzwonić”, a nie „kto jest pierwszy w alfabecie”.
    rows.sort(key=lambda row: (-row["overdue"], -(row["assigned"] + row["draft"]), row["member"].user.email))
    return rows


REMINDER_SUBJECT = "Przypomnienie o zaległych recenzjach – Olimpiada Kwantowa"

#: Ten sam temat jako wzorzec z nazwą konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1). Stała
#: wyżej zostaje odwrotem; wybiera między nimi ``apps.tenancy.branding.subject``.
REMINDER_SUBJECT_TEMPLATE = "Przypomnienie o zaległych recenzjach – %(competition)s"


def reminder_message(stage: Stage, pending: int, overdue: int, competition=None) -> str:
    """Treść przypomnienia. Poza adresem odbiorcy (nagłówek ``To:``) zero danych osobowych.

    W liście nie ma ani pseudonimów prac, ani tytułów zadań: recenzent i tak widzi komplet po
    zalogowaniu, a lista przydziałów w skrzynce pocztowej byłaby wyciekiem tego, co ocenianie
    ślepe ma chronić.
    """
    return "\n".join(
        [
            f"W etapie „{stage.display_name}” czekają na Ciebie recenzje.",
            "",
            f"Do zrobienia: {pending} (w tym po terminie: {overdue}).",
            "",
            "Prace są dostępne w panelu recenzenta po zalogowaniu.",
            "",
            # Podpis przez moduł marki; odwrotem jest ten sam **nietłumaczony** literał, co dotąd –
            # ekrany i listy komitetu są po polsku (§ 1.6.4).
            "--",
            branding.signature(competition),
            "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
        ]
    )


def _queue_reminder(
    stage: Stage, member: CommitteeMember, pending: int, overdue: int, competition=None
) -> None:
    """Kolejkuje jeden list **po commicie** – wzorzec z ``apps.accounts.activation.queue_mail``.

    Wysyłka jest skutkiem ubocznym kliknięcia, a nie jego warunkiem: niedostępny MTA nie może
    zamienić zapisanego wpisu audytowego w błąd 500.
    """
    from apps.core.tasks import mail_from

    recipient = (member.user.email or "").strip()
    if not recipient:
        return
    subject = branding.subject(REMINDER_SUBJECT_TEMPLATE, REMINDER_SUBJECT, competition)
    message = reminder_message(stage, pending, overdue, competition)
    from_email = mail_from(competition)

    def _enqueue() -> None:
        from apps.core.tasks import send_mail_task

        send_mail_task.delay(subject, message, [recipient], from_email)

    transaction.on_commit(_enqueue)


@transaction.atomic
def remind_reviewers(
    stage: Stage,
    *,
    member: CommitteeMember | None = None,
    only_overdue: bool = False,
    actor=None,
    request=None,
) -> dict:
    """Wysyła przypomnienia o niedokończonych recenzjach etapu. Zwraca liczniki do komunikatu.

    Dwa tryby w jednej funkcji, bo różni je wyłącznie zakres odbiorców: ``member`` przypomina
    jednej osobie (przycisk w wierszu tabeli), a ``only_overdue`` – wszystkim, którzy mają choć
    jedną recenzję po terminie (przycisk nad tabelą). Rozbicie na dwie funkcje znaczyłoby dwie
    treści listu i dwa wpisy audytowe o tym samym zdarzeniu.

    Recenzent bez zaległych recenzji **nie dostaje listu** w trybie zbiorczym, a recenzent bez
    żadnej roboty nie dostaje go nigdy: przypomnienie o pustej liście jest spamem, a spam od
    organizatora uczy komitet ignorować jego pocztę.

    Do audytu (``reviewer.reminded``) idą wyłącznie liczby – ani adresów, ani pseudonimów prac.
    """
    rows = reviewer_rows(stage)
    if member is not None:
        rows = [row for row in rows if row["member"].pk == member.pk]
    selected = []
    for row in rows:
        pending = row["assigned"] + row["draft"]
        if pending <= 0:
            continue
        if only_overdue and row["overdue"] <= 0:
            continue
        selected.append((row, pending))
    # Konkurs czytamy **raz** na całe kliknięcie, a nie raz na recenzenta: to jeden odczyt zamiast
    # dwóch zapytań na każdy list, a odpowiedź jest ta sama – etap należy do jednej edycji.
    competition = stage.edition.competition
    for row, pending in selected:
        _queue_reminder(stage, row["member"], pending, row["overdue"], competition)
    summary = {
        "stage_id": stage.pk,
        "reviewers": len(selected),
        "reviews": sum(pending for _row, pending in selected),
        "overdue": sum(row["overdue"] for row, _pending in selected),
        "scope": "one" if member is not None else ("overdue" if only_overdue else "all"),
    }
    audit(actor, "reviewer.reminded", stage, summary, request=request)
    logger.info(
        "Etap %s: przypomnienie o recenzjach do %s recenzentów (%s recenzji, %s po terminie)",
        stage.pk,
        summary["reviewers"],
        summary["reviews"],
        summary["overdue"],
    )
    return summary
