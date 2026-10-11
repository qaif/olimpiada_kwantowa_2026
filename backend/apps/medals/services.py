"""Czynności na medalach: schemat, podgląd, ręczne zmiany, ogłoszenie, dokumenty i eksport (MED-01).

Widoki tylko orkiestrują; każda czynność zapisująca jest tutaj, sprawdza rolę koordynatora **w tym
konkursie** (nie tylko widok) i zostawia wpis w audycie bez danych osobowych – w ``diff`` są
identyfikatory, nagrody i liczniki, a nigdy nazwisko ani treść uzasadnienia ręcznej zmiany.

Ranking pochodzi z ``apps.results.services.compute_stage_results`` w trybie podglądu – bez zapisu
i bez bramki „ocenianie zakończone”. Ogłoszenie ma za to własną, mocniejszą bramkę: wyniki etapu
muszą być opublikowane, a bieżące sumy równe sumom z publikacji (``entry_totals``). Medal liczony
z innej tabeli niż ta, którą świat zobaczył, byłby nagrodą bez pokrycia.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import CompetitionRole
from apps.competitions.models import Stage, StageEntry, StageEntryStatus
from apps.competitions.scoring import problem_maxima_by_number
from apps.core.api import DomainError
from apps.core.models import AuditLog, audit
from apps.core.points import points_json, to_points
from apps.results.models import NAMED_ANONYMIZATIONS, Anonymization, ResultsPublication
from apps.results.services import build_snapshot, compute_stage_results

from .awards import SchemeParams, Thresholds, compute_awards, count_awards, country_table, final_awards
from .models import Award, MedalOverride, MedalScheme, TiePolicy, enabled

logger = logging.getLogger(__name__)

#: Najdłuższe uzasadnienie ręcznej zmiany i odmrożenia – tyle mieści kolumna, a więcej to protokół.
MAX_JUSTIFICATION = 2000


def _error(detail: str, code: str, status_code: int = http.HTTP_409_CONFLICT) -> DomainError:
    return DomainError(detail, code, status_code)


# --- bramki --------------------------------------------------------------------------------------


def require_enabled(competition) -> None:
    """404 poza konkursem z flagą ``medals`` – dla świata tej funkcji tam po prostu nie ma."""
    if not enabled(competition):
        raise Http404("Ten konkurs nie przyznaje medali.")


def require_coordinator(actor, competition) -> None:
    """Rola koordynatora **tego** konkursu – sprawdzana w serwisie, nie tylko w widoku."""
    from apps.accounts.services import has_role

    if actor is None or not has_role(actor, competition, CompetitionRole.COORDINATOR):
        raise _error(
            "Tę czynność wykonuje wyłącznie koordynator konkursu.", "FORBIDDEN", http.HTTP_403_FORBIDDEN
        )


def stage_for(competition, stage_id: int) -> Stage:
    """Etap **tego** konkursu (obcy → 404). Trening nie przyznaje medali, więc też 404."""
    stage = (
        Stage.objects.for_competition(competition)
        .select_related("edition", "edition__competition")
        .filter(pk=stage_id)
        .first()
    )
    if stage is None or stage.is_training:
        raise Http404("Nie ma takiego etapu w tym konkursie.")
    return stage


def stages_of(competition) -> list[Stage]:
    """Etapy zawodów bieżącej edycji – kandydaci na ranking ostateczny."""
    from apps.competitions.services import current_edition

    edition = current_edition(competition)
    if edition is None:
        return []
    return [stage for stage in edition.stages.order_by("opens_at", "id") if not stage.is_training]


def scheme_for(stage: Stage, *, create: bool = False) -> MedalScheme:
    """Schemat etapu. Brak wiersza = schemat domyślny (IPhO) – **niezapisany**, chyba że ``create``.

    Odczyt (GET ekranu, podgląd) niczego nie zakłada: wiersz powstaje dopiero przy pierwszej
    czynności zapisującej (progi, ręczna zmiana, ogłoszenie), którą woła widok POST.
    """
    if create:
        scheme, _created = MedalScheme.objects.get_or_create(stage=stage)
    else:
        scheme = MedalScheme.objects.filter(stage=stage).first() or MedalScheme(stage=stage)
    # Etap już wczytany (z edycją i konkursem) – bez drugiego zapytania przy każdym odczycie.
    scheme.stage = stage
    return scheme


def publication_of(stage: Stage) -> ResultsPublication | None:
    return ResultsPublication.objects.filter(stage=stage).first()


def _assert_not_frozen(scheme: MedalScheme) -> None:
    if scheme.is_frozen:
        raise _error(
            "Medale tego etapu są już ogłoszone. Żeby coś zmienić, najpierw je odmroź (z uzasadnieniem).",
            "AWARDS_FROZEN",
        )


def _justification(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise _error("Uzasadnienie jest obowiązkowe.", "JUSTIFICATION_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if len(cleaned) > MAX_JUSTIFICATION:
        raise _error(
            f"Uzasadnienie może mieć najwyżej {MAX_JUSTIFICATION} znaków.",
            "JUSTIFICATION_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    return cleaned


# --- schemat -------------------------------------------------------------------------------------

SCHEME_FIELDS = (
    "gold_percent",
    "silver_percent",
    "bronze_percent",
    "tie_policy",
    "hm_percent_of_best",
    "hm_full_solution",
)


@transaction.atomic
def update_scheme(scheme: MedalScheme, *, values: dict, actor, request=None) -> MedalScheme:
    """Zapisuje progi i kryteria. Suma pul medalowych nie może przekroczyć 100 % pola."""
    competition = scheme.stage.edition.competition
    require_coordinator(actor, competition)
    scheme = (
        MedalScheme.objects.select_for_update()
        .select_related("stage__edition__competition")
        .get(pk=scheme.pk)
    )
    _assert_not_frozen(scheme)
    total = sum(
        (Decimal(values[name]) for name in ("gold_percent", "silver_percent", "bronze_percent")), Decimal(0)
    )
    if total > 100:
        raise _error(
            "Złoto, srebro i brąz razem nie mogą przekroczyć 100 % uczestników.",
            "PERCENT_OVER_100",
            http.HTTP_400_BAD_REQUEST,
        )
    before = {name: _jsonable(getattr(scheme, name)) for name in SCHEME_FIELDS}
    for name in SCHEME_FIELDS:
        setattr(scheme, name, values[name])
    scheme.updated_at = timezone.now()
    scheme.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    scheme.save()
    after = {name: _jsonable(getattr(scheme, name)) for name in SCHEME_FIELDS}
    audit(
        actor,
        "medals.scheme_updated",
        scheme,
        {"stage_id": scheme.stage_id, "before": before, "after": after},
        request=request,
    )
    return scheme


def _jsonable(value):
    return points_json(value) if isinstance(value, Decimal) else value


# --- podgląd -------------------------------------------------------------------------------------


@dataclass
class Preview:
    """Robocza tabela koordynatora: wiersze z nagrodą wyliczoną, ręczną i ostateczną."""

    rows: list[dict] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    computed: dict[int, str] = field(default_factory=dict)
    final: dict[int, str] = field(default_factory=dict)
    overrides: dict[int, MedalOverride] = field(default_factory=dict)
    final_counts: dict[str, int] = field(default_factory=dict)


def _ranking(stage: Stage) -> list[dict]:
    """Ranking etapu z ``apps.results`` – w trybie podglądu, bez zapisu (GET niczego nie zmienia)."""
    return compute_stage_results(stage, preview=True)


def preview(scheme: MedalScheme, rows: list[dict] | None = None) -> Preview:
    """Nagrody z bieżącego rankingu. Wolno zawsze – podgląd niczego nie ogłasza."""
    stage = scheme.stage
    rows = _ranking(stage) if rows is None else rows
    computed, thresholds = compute_awards(rows, SchemeParams.of(scheme), problem_maxima_by_number(stage))
    overrides = (
        {override.entry_id: override for override in scheme.overrides.select_related("created_by")}
        if scheme.pk is not None
        else {}
    )
    final = final_awards(computed, {entry_id: override.award for entry_id, override in overrides.items()})
    eligible = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    return Preview(
        rows=rows,
        thresholds=thresholds,
        computed=computed,
        final=final,
        overrides=overrides,
        final_counts=count_awards(final[row["entry_id"]] for row in eligible),
    )


def empty_pools(scheme: MedalScheme, thresholds: Thresholds) -> list[str]:
    """Nagrody, których pula jest niezerowa, a próg pusty – ostrzeżenie na ekranie (``EXCLUSIVE``).

    W polityce „w granicach puli” grupa remisowa większa od puli nie dostaje tej nagrody wcale:
    w polu trzech równych wyników nikt nie dostaje złota. To jest poprawny skutek reguły, ale
    koordynator ma go zobaczyć przed ogłoszeniem, a nie po nim.
    """
    if scheme.tie_policy != TiePolicy.EXCLUSIVE:
        return []
    labels = dict(Award.choices)
    return [
        str(labels[award])
        for award in (Award.GOLD, Award.SILVER, Award.BRONZE)
        if thresholds.slots.get(award) and thresholds.cutoffs.get(award) is None
    ]


# --- ręczne zmiany -------------------------------------------------------------------------------


def _entry_of(scheme: MedalScheme, entry_id: int) -> StageEntry:
    entry = StageEntry.objects.filter(pk=entry_id, stage_id=scheme.stage_id).first()
    if entry is None:
        raise Http404("Nie ma takiego wpisu w tym etapie.")
    return entry


@transaction.atomic
def set_override(scheme: MedalScheme, *, entry_id: int, award: str, justification: str, actor, request=None):
    """Ręczna nagroda dla jednego wpisu – z obowiązkowym uzasadnieniem. Druga zmiana nadpisuje pierwszą."""
    competition = scheme.stage.edition.competition
    require_coordinator(actor, competition)
    scheme = MedalScheme.objects.select_for_update().get(pk=scheme.pk)
    _assert_not_frozen(scheme)
    if award not in Award.values:
        raise _error("Nieznana nagroda.", "INVALID_AWARD", http.HTTP_400_BAD_REQUEST)
    entry = _entry_of(scheme, entry_id)
    if entry.status == StageEntryStatus.DISQUALIFIED:
        raise _error(
            "Zdyskwalifikowany uczestnik nie dostaje nagrody – najpierw zmień decyzję o dyskwalifikacji.",
            "ENTRY_DISQUALIFIED",
        )
    text = _justification(justification)
    now = timezone.now()
    override, created = MedalOverride.objects.update_or_create(
        scheme=scheme,
        entry=entry,
        defaults={
            "award": award,
            "justification": text,
            "created_by": actor if getattr(actor, "is_authenticated", False) else None,
            "updated_at": now,
        },
    )
    # Treść uzasadnienia **nie** idzie do audytu: bywa zdaniem o konkretnej osobie, a dziennik
    # zdarzeń czytają ludzie bez prawa do tych danych. Jest w wierszu zmiany, przy autorze i dacie.
    audit(
        actor,
        "medals.override_set",
        override,
        {"stage_id": scheme.stage_id, "entry_id": entry.pk, "award": award, "created": created},
        request=request,
    )
    return override


@transaction.atomic
def remove_override(scheme: MedalScheme, *, entry_id: int, actor, request=None) -> None:
    competition = scheme.stage.edition.competition
    require_coordinator(actor, competition)
    scheme = MedalScheme.objects.select_for_update().get(pk=scheme.pk)
    _assert_not_frozen(scheme)
    override = MedalOverride.objects.filter(scheme=scheme, entry_id=entry_id).first()
    if override is None:
        raise Http404("Ten wpis nie ma ręcznej nagrody.")
    audit(
        actor,
        "medals.override_removed",
        override,
        {"stage_id": scheme.stage_id, "entry_id": entry_id, "award": override.award},
        request=request,
    )
    override.delete()


# --- ogłoszenie ----------------------------------------------------------------------------------


def countries_of(entry_ids) -> dict[int, tuple[str, str]]:
    """``{entry_id: (kod, nazwa)}`` – kraj delegacji, a bez niej region uczestnika. Jedno zapytanie."""
    countries: dict[int, tuple[str, str]] = {}
    entries = StageEntry.objects.filter(pk__in=list(entry_ids)).select_related(
        "participant__delegation__country", "participant__region"
    )
    for entry in entries:
        participant = entry.participant
        region = None
        if participant is not None:
            delegation = participant.delegation
            region = delegation.country if delegation is not None else participant.region
        countries[entry.pk] = (region.code, region.name) if region is not None else ("", "")
    return countries


def _public_rows(rows, final, overrides, publication, countries) -> list[dict]:
    """Publiczna tabela medali – podpis wiersza z ``build_snapshot`` w trybie publikacji wyników.

    Podpis liczy **ta sama** funkcja, co ogłoszona tabela wyników, więc zgody na publikację
    nazwiska są te same co do wiersza. Kraj stoi przy wierszu tylko tam, gdzie tabela wyników
    i tak go pokazuje (tryb ``CODE``), albo przy wierszu podpisanym nazwiskiem za zgodą w trybie
    imiennym (``FULL``/``FULL_ALL``); przy pozostałych – w tym przy „inicjałach i szkole” – sam podpis
    bez kraju: kod (albo inicjały), kraj i wynik w sześcioosobowej delegacji wskazują osobę.

    Publikacja „tylko awansujący” zostawia w tabeli medali wyłącznie wiersze z nagrodą: medal jest
    ogłoszeniem sam w sobie, ale reszty pola wyniki tego etapu nie ogłosiły.
    """
    eligible = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    for row in eligible:
        row.setdefault("qualified", row["status"] == StageEntryStatus.QUALIFIED)
    items = build_snapshot(eligible, publication.anonymization)
    public = []
    for row, item in zip(eligible, items, strict=True):
        award = final.get(row["entry_id"], Award.NONE)
        if publication.qualified_only and award == Award.NONE:
            continue
        # Nazwisko za zgodą wyłącznie w trybie imiennym; „inicjały i szkoła” (INITIALS_SCHOOL) też
        # zmienia podpis, ale nie jest zgodą na nic – kraj przy inicjałach i szkole zawęża do osoby.
        named = publication.anonymization in NAMED_ANONYMIZATIONS and item["display"] != row["public_code"]
        show_country = publication.anonymization == Anonymization.CODE or named
        code, name = countries.get(row["entry_id"], ("", ""))
        public.append(
            {
                "rank": item["rank"],
                "display": item["display"],
                "country": name if show_country else "",
                "country_code": code if show_country else "",
                "total": item["total"],
                "award": str(award),
                "manual": row["entry_id"] in overrides,
            }
        )
    return public


#: Stany wpisu po publikacji wyników: ``apply_qualification`` ustawia każdemu niezdyskwalifikowanemu
#: albo „zakwalifikowany”, albo „niezakwalifikowany”. Każdy inny stan znaczy zmianę po publikacji.
PUBLISHED_STATUSES = frozenset(
    {StageEntryStatus.QUALIFIED, StageEntryStatus.NOT_QUALIFIED, StageEntryStatus.DISQUALIFIED}
)


def changes_since_publication(stage: Stage, publication: ResultsPublication, rows: list[dict]) -> str:
    """Opis rozjazdu bieżącej tabeli z ogłoszoną albo pusty napis, gdy tabela jest ta sama.

    Trzy sprawdzenia, bo medal liczony z innej tabeli niż ogłoszona byłby nagrodą bez pokrycia:

    - **te same wpisy** – identyfikatory z ``entry_totals`` publikacji,
    - **te same sumy** – jak dotąd,
    - **te same stany** – publikacja nie przechowuje stanu wiersza (snapshot jest zanonimizowany),
      więc porównujemy liczności stanów z wpisem audytu ``results.qualification_applied``, który
      ``publish_results`` zapisuje w tej samej transakcji. Dyskwalifikacja po publikacji zmienia
      pole i pule medali – i zmienia te liczności. Publikacja bez takiego wpisu (dane sprzed audytu)
      przechodzi bez tego sprawdzenia, z ostrzeżeniem w logu.
    """
    published = publication.entry_totals or {}
    current_ids = {row["entry_id"] for row in rows}
    published_ids = {int(key) for key in published}
    if current_ids != published_ids:
        return f"wpisy: {len(current_ids ^ published_ids)} dodanych albo usuniętych"
    totals = [
        row["entry_id"]
        for row in rows
        if to_points(published.get(str(row["entry_id"]))) != to_points(row["total"])
    ]
    if totals:
        return f"sumy punktów: {len(totals)} wpisów"
    if any(row["status"] not in PUBLISHED_STATUSES for row in rows):
        return "stany wpisów: wpis bez rozstrzygnięcia kwalifikacji"
    applied = (
        AuditLog.objects.filter(
            action="results.qualification_applied",
            target_type="competitions.stage",
            target_id=str(stage.pk),
            at__lte=publication.published_at,
        )
        .order_by("-at", "-id")
        .first()
    )
    if applied is None:
        logger.warning(
            "Etap %s: brak wpisu audytu kwalifikacji – stanów wpisów nie da się porównać.", stage.pk
        )
        return ""
    counts = {
        "qualified": sum(1 for row in rows if row["status"] == StageEntryStatus.QUALIFIED),
        "not_qualified": sum(1 for row in rows if row["status"] == StageEntryStatus.NOT_QUALIFIED),
        "disqualified": sum(1 for row in rows if row["status"] == StageEntryStatus.DISQUALIFIED),
    }
    if any(applied.diff.get(key) != value for key, value in counts.items()):
        return "stany wpisów: kwalifikacja albo dyskwalifikacja zmieniona po publikacji"
    return ""


@transaction.atomic
def freeze(scheme: MedalScheme, *, actor, request=None) -> MedalScheme:
    """Ogłasza medale: zamraża nagrody, progi, tabelę publiczną i ranking krajów."""
    stage = scheme.stage
    competition = stage.edition.competition
    require_coordinator(actor, competition)
    scheme = MedalScheme.objects.select_for_update().get(pk=scheme.pk)
    scheme.stage = stage
    _assert_not_frozen(scheme)
    publication = publication_of(stage)
    if publication is None:
        raise _error(
            "Najpierw opublikuj wyniki etapu – medale ogłasza się z ogłoszonego rankingu.",
            "RESULTS_NOT_PUBLISHED",
        )
    rows = _ranking(stage)
    changed = changes_since_publication(stage, publication, rows)
    if changed:
        raise _error(
            f"Wyniki zmieniły się po publikacji ({changed}). Opublikuj wyniki ponownie, "
            "a dopiero potem ogłoś medale.",
            "RESULTS_CHANGED",
        )
    result = preview(scheme, rows)
    countries = countries_of(row["entry_id"] for row in rows)
    eligible = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    scheme.awards = {
        str(row["entry_id"]): {
            "award": result.final[row["entry_id"]],
            "computed": result.computed[row["entry_id"]],
            "overridden": row["entry_id"] in result.overrides,
            "total": points_json(row["total"]),
            "rank": row["rank"],
        }
        for row in eligible
    }
    scheme.thresholds = result.thresholds.as_json() | {"final_counts": result.final_counts}
    scheme.public_rows = _public_rows(rows, result.final, result.overrides, publication, countries)
    scheme.country_table = country_table(
        eligible, result.final, countries, awarded_only=publication.qualified_only
    )
    scheme.frozen_at = timezone.now()
    scheme.frozen_by = actor if getattr(actor, "is_authenticated", False) else None
    scheme.publication_published_at = publication.published_at
    scheme.save()
    audit(
        actor,
        "medals.frozen",
        scheme,
        {
            "stage_id": stage.pk,
            "field_size": result.thresholds.field_size,
            "counts": result.final_counts,
            "overrides": len(result.overrides),
        },
        request=request,
    )
    logger.info("Etap %s: ogłoszono medale (%s).", stage.pk, result.final_counts)
    return scheme


@transaction.atomic
def unfreeze(scheme: MedalScheme, *, justification: str, actor, request=None) -> MedalScheme:
    """Zdejmuje ogłoszenie (strona publiczna znika do ponownego ogłoszenia). Z uzasadnieniem."""
    competition = scheme.stage.edition.competition
    require_coordinator(actor, competition)
    scheme = MedalScheme.objects.select_for_update().get(pk=scheme.pk)
    if not scheme.is_frozen:
        raise _error("Medale tego etapu nie są ogłoszone.", "AWARDS_NOT_FROZEN")
    text = _justification(justification)
    frozen_at = scheme.frozen_at
    scheme.frozen_at = None
    scheme.frozen_by = None
    scheme.publication_published_at = None
    scheme.awards = {}
    scheme.thresholds = {}
    scheme.public_rows = []
    scheme.country_table = []
    scheme.save()
    # Uzasadnienie odmrożenia dotyczy tabeli, a nie osoby – zostaje w audycie (ekran prosi
    # o niewpisywanie danych osobowych).
    audit(
        actor,
        "medals.unfrozen",
        scheme,
        {"stage_id": scheme.stage_id, "frozen_at": frozen_at.isoformat(), "reason": text},
        request=request,
    )
    return scheme


def published_scheme(competition, stage_id: int) -> MedalScheme:
    """Ogłoszony schemat etapu dla strony publicznej – inaczej 404 (przed ogłoszeniem medali nie ma)."""
    require_enabled(competition)
    scheme = (
        MedalScheme.objects.for_competition(competition)
        .select_related("stage", "stage__edition")
        .filter(stage_id=stage_id, frozen_at__isnull=False)
        .first()
    )
    if scheme is None:
        raise Http404("Medale tego etapu nie zostały ogłoszone.")
    return scheme


def frozen_scheme_for_results(stage, competition=None) -> MedalScheme | None:
    """Ogłoszone medale etapu albo ``None`` – do odnośnika ze strony wyników.

    Konkurs podaje wołający (``request.competition`` – etap pochodzi z querysetu zawężonego do
    niego), więc bez flagi nie pada **ani jedno** zapytanie: budżet strony wyników Olimpiady
    Kwantowej (``tenancy/tests/test_invariants``) zostaje nietknięty.
    """
    if competition is None:
        competition = stage.edition.competition
    if not enabled(competition):
        return None
    return MedalScheme.objects.filter(stage=stage, frozen_at__isnull=False).first()


def republished_since_freeze(scheme: MedalScheme, publication: ResultsPublication | None) -> bool:
    """Czy wyniki ogłoszono ponownie po ogłoszeniu medali – medale zostały przy poprzedniej tabeli."""
    return bool(
        scheme.is_frozen
        and publication is not None
        and scheme.publication_published_at is not None
        and publication.published_at != scheme.publication_published_at
    )


# --- dokumenty -----------------------------------------------------------------------------------


@dataclass
class IssueReport:
    medals: int = 0
    participation: int = 0
    created: int = 0
    stale: list[str] = field(default_factory=list)
    #: Dokumenty, których język ucznia trzeba było zastąpić angielskim w chwili wystawienia
    #: (pismo niedostępne na serwerze) – koordynator dostaje ostrzeżenie z ich numerami.
    fallbacks: list[str] = field(default_factory=list)


@transaction.atomic
def issue_certificates(scheme: MedalScheme, *, participation: bool, actor, request=None) -> IssueReport:
    """Dyplomy medalowe (z zamrożonych nagród) i – opcjonalnie – zaświadczenia o udziale.

    Idempotentne jak każde „Wystaw” (``results.certificates.issue_certificate``): drugi przebieg oddaje
    te same numery. Język dokumentu przypina się przy pierwszym wystawieniu – ten, w którym dokument
    naprawdę się złoży (odwrót na angielski trafia do raportu). Dyplom medalowy, którego
    rodzaj nie zgadza się już z ogłoszoną nagrodą (medale odmrożono i zmieniono), trafia do raportu –
    dokumentu wydanego komuś do ręki system sam nie unieważnia.
    """
    from apps.results.certificates import issue_certificate
    from apps.results.models import Certificate

    from .documents import AWARD_KINDS, KIND_AWARDS, PARTICIPATION_KIND, remember_language, student_language

    stage = scheme.stage
    competition = stage.edition.competition
    require_coordinator(actor, competition)
    if not scheme.is_frozen:
        raise _error("Dokumenty medalowe wystawia się po ogłoszeniu medali.", "AWARDS_NOT_FROZEN")
    report = IssueReport()
    entries = {
        entry.pk: entry
        for entry in StageEntry.objects.filter(pk__in=[int(key) for key in scheme.awards]).select_related(
            "participant__user__preference"
        )
    }
    for key, item in scheme.awards.items():
        entry = entries.get(int(key))
        if entry is None or entry.participant is None:
            continue
        language = student_language(entry.participant.user, competition)
        kinds = []
        if item["award"] in AWARD_KINDS:
            kinds.append(AWARD_KINDS[item["award"]])
        if participation:
            kinds.append(PARTICIPATION_KIND)
        for kind in kinds:
            certificate, created = issue_certificate(
                edition=stage.edition, kind=kind, entry=entry, actor=actor, request=request
            )
            _pinned, fell_back = remember_language(certificate, language)
            if fell_back and created:
                report.fallbacks.append(certificate.number)
            report.created += int(created)
            if kind == PARTICIPATION_KIND:
                report.participation += 1
            else:
                report.medals += 1
    for certificate in Certificate.objects.filter(
        entry__stage=stage, kind__in=list(KIND_AWARDS)
    ).select_related("entry"):
        expected = scheme.awards.get(str(certificate.entry_id), {}).get("award")
        if KIND_AWARDS[certificate.kind] != expected:
            report.stale.append(certificate.number)
    audit(
        actor,
        "medals.certificates_issued",
        scheme,
        {
            "stage_id": stage.pk,
            "medals": report.medals,
            "participation": report.participation,
            "created": report.created,
            "stale": len(report.stale),
            "language_fallbacks": len(report.fallbacks),
        },
        request=request,
    )
    return report


def stage_certificates(scheme: MedalScheme):
    """Dokumenty medalowe i zaświadczenia o udziale tego etapu – do paczki ZIP."""
    from apps.results.models import Certificate

    from .documents import KIND_AWARDS, PARTICIPATION_KIND

    return list(
        Certificate.objects.filter(entry__stage=scheme.stage, kind__in=[*KIND_AWARDS, PARTICIPATION_KIND])
        .select_related("edition", "edition__competition", "entry__participant__user")
        .order_by("number")
    )


# --- eksport na galę -----------------------------------------------------------------------------


def ceremony_rows(scheme: MedalScheme) -> list[dict]:
    """Wiersze eksportu koordynatora: zamrożone nagrody + imiona i nazwiska **z tej chwili**.

    Nazwiska nie leżą w zamrożonym JSON-ie i to jest decyzja: konto usunięte po ogłoszeniu
    (anonimizacja) ma wyjść z listy na galę bez nazwiska, a nie z nazwiskiem zapamiętanym na zapas.
    """
    awards = scheme.awards or {}
    entries = StageEntry.objects.filter(pk__in=[int(key) for key in awards]).select_related(
        "participant__user", "participant__delegation__country", "participant__region"
    )
    countries = countries_of(entry.pk for entry in entries)
    rows = []
    for entry in entries:
        item = awards[str(entry.pk)]
        participant = entry.participant
        user = participant.user if participant is not None else None
        rows.append(
            {
                "entry_id": entry.pk,
                "rank": item["rank"],
                "award": item["award"],
                "computed": item["computed"],
                "overridden": item["overridden"],
                "total": item["total"],
                "name": (user.get_full_name() if user is not None else "") or "",
                "first_name": user.first_name if user is not None else "",
                "last_name": user.last_name if user is not None else "",
                "public_code": participant.public_code if participant is not None else "",
                "school": participant.school if participant is not None else "",
                "country": countries.get(entry.pk, ("", ""))[1],
            }
        )
    rows.sort(key=lambda row: (row["rank"], row["public_code"]))
    return rows


def audit_export(scheme: MedalScheme, kind: str, rows: int, *, actor, request=None) -> None:
    """Eksport danych osobowych jest zdarzeniem w audycie – kto i kiedy wyniósł listę ludzi."""
    audit(
        actor,
        "medals.exported",
        scheme,
        {"stage_id": scheme.stage_id, "format": kind, "rows": rows},
        request=request,
    )
