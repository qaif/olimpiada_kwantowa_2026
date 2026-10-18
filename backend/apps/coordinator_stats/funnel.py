"""Lejek uczestnika: od rejestracji do warsztatów, z filtrami i tabelą „gdzie odpadają” (STATS-02 § 1).

Wszystko liczy baza i liczy **stałą liczbą zapytań**: kroki lejka to jedno ``aggregate`` z
``COUNT(*) FILTER (WHERE …)`` po populacji, a warunki „ma wpis / pracę / zaświadczenie / obecność”
są podzapytaniami ``EXISTS`` – nie złączeniami, które mnożyłyby wiersze profilu przez jego wpisy.
Tabele „gdzie odpadają” to dwa grupowania tej samej populacji. Żadna liczba nie powstaje w pętli
po uczestnikach, więc koszt ekranu nie zależy od tego, czy edycja ma stu, czy dziesięć tysięcy osób.

Wynik to wyłącznie liczby i nazwy grup (województwo, szkoła) – bez osób. Grupy mniejsze niż próg
``K_ANONYMITY`` (STAT-01) nie trafiają do „gdzie odpadają”: procent z trzech rejestracji niczego
nie mówi, a przy jednej szkole i jednym uczniu byłby informacją o konkretnej osobie.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from apps.accounts.consents import MINOR_MAX_AGE, consent_set
from apps.accounts.messaging import parse_school_key
from apps.accounts.models import Participant, Voivodeship
from apps.cms.models import WorkshopAttendance
from apps.competitions.models import Edition, Stage, StageEntry, StageKind
from apps.results.statistics import width_class
from apps.school_stats.services import K_ANONYMITY
from apps.student_status.models import CertificateStatus, StudentStatusCertificate
from apps.submissions.models import Submission, SubmissionStatus

from . import domains

#: Czas życia wyniku w pamięci podręcznej (sekundy). Krótki, bo to dane operacyjne – koordynator
#: patrzy tu w dniu akcji „aktywuj konto” i chce widzieć skutek; pięć minut wystarcza, żeby kilka
#: odświeżeń i eksport CSV zaraz po obejrzeniu ekranu nie liczyły tego samego od nowa.
CACHE_SECONDS = 300
#: Ile grup pokazuje każda tabela „gdzie odpadają”.
DROPOFF_LIMIT = 10

STRICT = "strict"
INDEPENDENT = "independent"
MODES = (STRICT, INDEPENDENT)

#: Podpisy kroków lejka i podwierszy („w tym …” – rozbicie kroku, liczone względem kroku przed nim).
STEP_LABELS = {
    "registered": _("Rejestracja (profil uczestnika)"),
    "activated": _("Aktywacja konta (potwierdzony adres e-mail)"),
    "logged_in": _("Pierwsze logowanie"),
    "consents": _("Zgody (w tym zgoda opiekuna niepełnoletniego)"),
    "enrolled": _("Zapis do etapu edycji"),
    "enrolled_training": _("w tym etap treningowy"),
    "enrolled_first": _("w tym Etap I"),
    "submitted": _("Pierwsza wysłana praca"),
    "student_status": _("Zaświadczenie o statusie ucznia zaakceptowane"),
    "workshop": _("Obecność na co najmniej jednym warsztacie"),
}
#: Kroki główne (łańcuch lejka) i podwiersze (rozbicie kroku „zapis do etapu”).
CHAIN = (
    "registered",
    "activated",
    "logged_in",
    "consents",
    "enrolled",
    "submitted",
    "student_status",
    "workshop",
)
SUBSTEPS = {"enrolled": ("enrolled_training", "enrolled_first")}


@dataclass(frozen=True)
class Filters:
    """Filtry ekranu po walidacji formularza – wyłącznie wartości proste (klucz pamięci podręcznej)."""

    region: str = ""
    school: str = ""
    domain: str = ""
    registered_from: date | None = None
    registered_to: date | None = None
    mode: str = STRICT

    def cache_part(self) -> str:
        return json.dumps(
            [
                self.region,
                self.school,
                self.domain,
                self.registered_from.isoformat() if self.registered_from else "",
                self.registered_to.isoformat() if self.registered_to else "",
                self.mode,
            ]
        )


@dataclass
class FunnelResult:
    steps: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    schools: list[dict] = field(default_factory=list)
    first_stage_name: str = ""
    has_training: bool = False


# --- reguły pomocnicze (bez bazy – sprawdzane testem) ---------------------------------------------


def adult_cutoff(today: date) -> date:
    """Najpóźniejsza data urodzenia osoby **już pełnoletniej** w dniu ``today``.

    ``birth_date <= adult_cutoff(today)`` znaczy dokładnie to samo, co ``not is_minor(birth_date,
    today=today)`` – także dla urodzonych 29 lutego (``consents.adulthood_date`` daje im 1 marca)
    i dla ``today`` = 29 lutego (rok −18 nie jest przestępny, więc granicą jest 28 lutego). Warunek
    w SQL i reguła w Pythonie nie mogą się rozjechać – pilnuje tego test.
    """
    try:
        return today.replace(year=today.year - MINOR_MAX_AGE)
    except ValueError:  # 29 lutego, a rok −18 nieprzestępny
        return date(today.year - MINOR_MAX_AGE, 2, 28)


def minor_q(today: date) -> Q:
    """Warunek „niepełnoletni” – ``consents.is_minor`` przepisane na SQL.

    Pełna data: urodzony po :func:`adult_cutoff`. Bez daty: rocznik nieznany (``0``) albo
    ``rok bieżący − rocznik <= 18`` – reguła zachowawcza, dokładnie jak w ``is_minor``.
    """
    return Q(birth_date__isnull=False, birth_date__gt=adult_cutoff(today)) | (
        Q(birth_date__isnull=True)
        & (Q(birth_year__isnull=True) | Q(birth_year=0) | Q(birth_year__gte=today.year - MINOR_MAX_AGE))
    )


def guardian_required(competition) -> bool:
    """Czy konkurs w ogóle zbiera zgodę wymaganą od niepełnoletnich (zestaw zgód konkursu)."""
    return any(consent.required_for_minor for consent in consent_set(competition))


def _local_start(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def share(part: int | None, whole: int | None) -> float | None:
    if part is None or not whole:
        return None
    return round(part * 100 / whole, 1)


# --- populacja i warunki --------------------------------------------------------------------------


def next_edition(edition: Edition) -> Edition | None:
    """Edycja założona po ``edition`` w tym samym konkursie – górna granica okna edycji minionej."""
    return (
        Edition.objects.filter(competition_id=edition.competition_id, created_at__gt=edition.created_at)
        .order_by("created_at", "id")
        .first()
    )


def first_stage(stages: list[Stage]) -> Stage | None:
    """„Etap I” z etapów edycji: eliminacje, a bez nich pierwszy etap zawodów w kolejności terminów."""
    contest = [stage for stage in stages if stage.kind != StageKind.TRAINING]
    contest.sort(key=lambda stage: (stage.kind != StageKind.ELIM, stage.opens_at, stage.pk))
    return contest[0] if contest else None


def population(competition, edition: Edition, filters: Filters, *, until: datetime | None = None):
    """Profile uczestników edycji po filtrach – queryset bez duplikatów (warunki przez ``pk__in``).

    „Uczestnik edycji” jak w komunikatach (``messaging.current_edition_participants``): wpis do
    etapu tej edycji albo konto założone od powstania edycji; dla edycji minionej konto także
    **przed** powstaniem następnej (``until``).
    """
    base = Participant.objects.for_competition(competition).exclude_anonymised()
    joined = Q(user__date_joined__gte=edition.created_at)
    if until is not None:
        joined &= Q(user__date_joined__lt=until)
    entries = StageEntry.objects.filter(stage__edition=edition, participant__isnull=False).values(
        "participant_id"
    )
    queryset = base.filter(Q(pk__in=entries) | joined)
    if filters.region:
        queryset = queryset.filter(_region_q(competition, filters.region))
    if filters.school:
        condition = parse_school_key(filters.school)
        queryset = queryset.filter(condition) if condition is not None else queryset.none()
    if filters.domain:
        queryset = queryset.filter(domains.email_q(filters.domain))
    if filters.registered_from:
        queryset = queryset.filter(user__date_joined__gte=_local_start(filters.registered_from))
    if filters.registered_to:
        queryset = queryset.filter(
            user__date_joined__lt=_local_start(filters.registered_to + timedelta(days=1))
        )
    return queryset


def _region_q(competition, value: str) -> Q:
    """``r<id>`` – region konkursu (flaga ``custom_regions``), inaczej kod województwa.

    Region tak samo, jak w komunikatach: profil sprzed włączenia flagi ma wyłącznie ``district``,
    a kody regionów startowych są wartościami ``district`` (``accounts.0026``).
    """
    from apps.accounts.models import Region

    if value.startswith("r") and value[1:].isdigit():
        region = Region.objects.for_competition(competition).filter(pk=int(value[1:])).first()
        if region is None:
            return Q(pk__in=[])
        return Q(region=region) | Q(region__isnull=True, district=region.code)
    if value in Voivodeship.values:
        return Q(district=value)
    return Q(pk__in=[])


def conditions(
    competition, edition: Edition, *, first: Stage | None, until: datetime | None, today: date
) -> tuple[dict, dict]:
    """Adnotacje ``Exists`` i warunek każdego kroku (``Q`` na profilu po adnotacjach).

    ``first`` – „Etap I” (:func:`first_stage`); ``None`` – edycja bez etapu zawodów, wiersz pusty.
    """
    stage_entries = StageEntry.objects.filter(participant=OuterRef("pk"), stage__edition=edition)
    work = Submission.objects.filter(
        entry__participant=OuterRef("pk"), entry__stage__edition=edition
    ).exclude(status=SubmissionStatus.REJECTED_INFECTED)
    from apps.quiz.models import AttemptStatus, QuizAttempt

    quiz = QuizAttempt.objects.filter(
        entry__participant=OuterRef("pk"),
        entry__stage__edition=edition,
        status__in=[AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED],
    )
    certificate = StudentStatusCertificate.objects.filter(
        participant=OuterRef("pk"), edition=edition, is_current=True, status=CertificateStatus.ACCEPTED
    )
    # Obecność nie ma kolumny edycji (klucz warsztatu jest tekstem z harmonogramu) – edycji
    # przypisujemy odhaczenia z jej okna: od powstania edycji do powstania następnej.
    attendance = WorkshopAttendance.objects.filter(
        participant=OuterRef("pk"), created_at__gte=edition.created_at
    )
    if until is not None:
        attendance = attendance.filter(created_at__lt=until)
    annotations = {
        "has_entry": Exists(stage_entries),
        "has_training": Exists(stage_entries.filter(stage__kind=StageKind.TRAINING)),
        "has_work": Exists(work),
        "has_quiz": Exists(quiz),
        "has_certificate": Exists(certificate),
        "has_workshop": Exists(attendance),
    }
    if first is not None:
        annotations["has_first"] = Exists(stage_entries.filter(stage=first))
    consents = Q(gdpr_consent_at__isnull=False, terms_accepted_at__isnull=False)
    if guardian_required(competition):
        consents &= Q(guardian_consent=True) | ~minor_q(today)
    steps = {
        "registered": Q(),
        "activated": Q(user__email_verified_at__isnull=False),
        "logged_in": Q(user__last_login__isnull=False),
        "consents": consents,
        "enrolled": Q(has_entry=True),
        "enrolled_training": Q(has_training=True),
        "enrolled_first": Q(has_first=True) if first is not None else None,
        "submitted": Q(has_work=True) | Q(has_quiz=True),
        "student_status": Q(has_certificate=True),
        "workshop": Q(has_workshop=True),
    }
    return annotations, steps


def _effective(steps: dict, mode: str) -> dict:
    """Warunek liczony dla każdego wiersza: sam krok (niezależnie) albo krok i wszystkie poprzednie."""
    if mode != STRICT:
        return dict(steps)
    effective = {}
    prefix = previous = Q()
    for name in CHAIN:
        prefix &= steps[name]
        effective[name] = prefix
        for sub in SUBSTEPS.get(name, ()):
            # Podwiersz „w tym …” to krok **poprzedni** łańcucha i własny warunek – nie zależy od
            # drugiego podwiersza (trening i Etap I są równoległe).
            effective[sub] = None if steps[sub] is None else (previous & steps[sub])
        previous = prefix
    return effective


def compute(competition, edition: Edition, filters: Filters, *, today: date | None = None) -> FunnelResult:
    """Lejek i tabele „gdzie odpadają” – stała liczba zapytań bez względu na liczbę uczestników."""
    today = today or timezone.localdate()
    following = next_edition(edition)
    until = following.created_at if following is not None else None
    queryset = population(competition, edition, filters, until=until)
    stages = list(Stage.objects.filter(edition=edition).only("id", "kind", "name", "opens_at"))
    first = first_stage(stages)
    annotations, steps = conditions(competition, edition, first=first, until=until, today=today)
    effective = _effective(steps, filters.mode)
    aggregates = {
        name: Count("pk", filter=condition) if condition else Count("pk")
        for name, condition in effective.items()
        if condition is not None
    }
    counts = queryset.annotate(**annotations).aggregate(**aggregates)
    result = FunnelResult(
        first_stage_name=first.display_name if first is not None else "",
        has_training=any(stage.kind == StageKind.TRAINING for stage in stages),
    )
    registered = counts.get("registered") or 0
    previous_count = None
    for name in CHAIN:
        count = counts.get(name)
        result.steps.append(_step_row(name, count, previous_count, registered, sub=False))
        for sub in SUBSTEPS.get(name, ()):
            result.steps.append(_step_row(sub, counts.get(sub), previous_count, registered, sub=True))
        previous_count = count
    result.regions = _dropoff(queryset, ("district",), _region_label)
    result.schools = _dropoff(queryset, ("school_ref_id", "school_ref__city", "school"), _school_label)
    return result


def _step_row(name: str, count, previous, registered: int, *, sub: bool) -> dict:
    vs_registered = share(count, registered)
    return {
        "key": name,
        "label": str(STEP_LABELS[name]),
        "count": count,
        "sub": sub,
        # Pierwszy krok nie ma poprzedniego; podwiersz liczy się względem kroku przed rozbiciem.
        "vs_previous": None if name == "registered" else share(count, previous),
        "vs_registered": vs_registered,
        "width": width_class(vs_registered or 0),
    }


def _dropoff(queryset, fields: tuple[str, ...], label) -> list[dict]:
    """Grupy z najniższą aktywacją (aktywowani / zarejestrowani), od progu ``K_ANONYMITY`` rejestracji."""
    rows = (
        queryset.values(*fields)
        .annotate(
            registered=Count("pk"),
            activated=Count("pk", filter=Q(user__email_verified_at__isnull=False)),
            logged_in=Count("pk", filter=Q(user__last_login__isnull=False)),
        )
        .filter(registered__gte=K_ANONYMITY)
        .order_by()
    )
    items = []
    for row in rows:
        items.append(
            {
                "label": label(row),
                "registered": row["registered"],
                "activated": row["activated"],
                "logged_in": row["logged_in"],
                "activation": share(row["activated"], row["registered"]),
                "login": share(row["logged_in"], row["registered"]),
            }
        )
    items.sort(key=lambda item: (item["activation"] or 0, -item["registered"], item["label"]))
    for item in items:
        item["width"] = width_class(item["activation"] or 0)
    return items[:DROPOFF_LIMIT]


def _region_label(row: dict) -> str:
    code = row.get("district") or ""
    try:
        return str(Voivodeship(code).label)
    except ValueError:
        return code or str(_("brak województwa"))


def _school_label(row: dict) -> str:
    name = " ".join((row.get("school") or "").split()) or str(_("brak szkoły"))
    city = row.get("school_ref__city")
    if row.get("school_ref_id") is None:
        return f"{name} ({_('nazwa wpisana ręcznie')})"
    return f"{name}, {city}" if city else name


def cached(competition, edition: Edition, filters: Filters, *, today: date | None = None) -> FunnelResult:
    """:func:`compute` przez pamięć podręczną (:data:`CACHE_SECONDS`) – ekran i CSV liczą raz.

    Język w kluczu, bo wynik niesie gotowe podpisy (kroki, „nazwa wpisana ręcznie”); dzień – bo
    reguła wieku w kroku zgód zależy od daty.
    """
    today = today or timezone.localdate()
    digest = hashlib.sha256(filters.cache_part().encode()).hexdigest()[:24]
    language = translation.get_language() or ""
    key = f"coordinator_stats:funnel:{competition.pk}:{edition.pk}:{today.isoformat()}:{language}:{digest}"
    result = cache.get(key)
    if result is None:
        result = compute(competition, edition, filters, today=today)
        cache.set(key, result, CACHE_SECONDS)
    return result
