"""Reguła „zaświadczenie od N warsztatów”: kto spełnia próg i wystawienie brakujących dokumentów.

Trzy decyzje, które łatwo zepsuć przy zmianie (uzasadnienia w ``docs/tasks/WS-CERT-02.md``):

- **liczymy różne warsztaty z harmonogramu**, a nie wiersze obecności: obecność przy kluczu, którego
  w harmonogramie już nie ma (temat zmieniono, zajęcia odwołano), nie stoi na zaświadczeniu, więc
  nie może się też liczyć do progu – inaczej dokument wystawiony „za trzy warsztaty” wyliczałby dwa,
- **jeden dokument na osobę i edycję**, sprawdzany po uczestniku, a nie po wpisie: ręczne „Wystaw
  (ZIP)” i reguła dają ten sam dokument, a uczestnik z dwoma numerami zaświadczenia z jednej edycji
  miałby kłopot w każdej rekrutacji,
- **reguła nie odbiera** – zdjęcie kratki poniżej progu zostawia wystawiony dokument (patrz spec § 3).

Liczba zapytań nie zależy od liczby uczestników przy ocenie (obecności – jedno ``GROUP BY``, wpisy –
jedno, istniejące dokumenty – jedno); rośnie wyłącznie z liczbą **nowych** dokumentów, bo każdy z nich
to numer, wiersz i wpis audytu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone

from apps.core.models import audit
from apps.results.models import Certificate, CertificateKind

from .models import AutoIssuedCertificate, AutoIssuePolicy, AutoIssueTrigger

logger = logging.getLogger(__name__)

#: Ile wierszy „poniżej progu” pokazuje ekran. Lista jest informacyjna (kogo brakuje), a nie
#: narzędziem pracy – przy tysiącu osób z jedną obecnością pełna tabela tylko by przeszkadzała.
PENDING_LIMIT = 200


def policy_for(edition) -> AutoIssuePolicy | None:
    """Reguła edycji albo ``None`` (brak wiersza = wyłączone)."""
    if edition is None:
        return None
    return AutoIssuePolicy.objects.filter(edition=edition).select_related("template").first()


def schedule_keys(competition) -> list[str]:
    """Klucze warsztatów z harmonogramu **tego konkursu** – ta sama strona, co kolumny obecności."""
    from apps.cms.workshops import workshop_rows, workshops_page

    return [row["key"] for row in workshop_rows(workshops_page(competition))]


def attendance_counts(competition, keys, participant_ids=None) -> dict[int, int]:
    """``{uczestnik: liczba różnych warsztatów z harmonogramu}`` – jednym zapytaniem z ``GROUP BY``.

    Zawężenie do konkursu idzie przez profil uczestnika (``Participant.competition``): obecność nie ma
    własnej kolumny konkursu, a profil jest per konkurs (§ 3.3 planu wielokonkursowego), więc
    obecności uczestnika olimpiady B nigdy nie wchodzą do liczenia w A.
    """
    from apps.cms.models import WorkshopAttendance

    if not keys:
        return {}
    queryset = WorkshopAttendance.objects.filter(participant__competition=competition, workshop_key__in=keys)
    if participant_ids is not None:
        queryset = queryset.filter(participant_id__in=list(participant_ids))
    rows = (
        queryset.order_by()
        .values("participant_id")
        .annotate(attended=Count("workshop_key", distinct=True))
        .values_list("participant_id", "attended")
    )
    return dict(rows)


def first_entries(edition, participant_ids) -> dict:
    """Najwcześniejszy wpis każdego uczestnika w edycji – bez kont usuniętych.

    Ta sama reguła co ``apps.results.certificates.participants_with_workshops`` (kolejność
    ``stage__opens_at``, potem ``stage_id``): dokument z reguły i dokument z ręcznego ZIP-a mają wisieć
    na tym samym wpisie, bo unikalność w bazie to (wpis, rodzaj).
    """
    from apps.accounts.anonymised import anonymised_q
    from apps.competitions.models import StageEntry

    entries: dict = {}
    if not participant_ids:
        return entries
    for entry in (
        StageEntry.objects.filter(participant_id__in=list(participant_ids), stage__edition=edition)
        .exclude(anonymised_q("participant__user"))
        .select_related("participant__user", "stage")
        .order_by("stage__opens_at", "stage_id")
    ):
        entries.setdefault(entry.participant_id, entry)
    return entries


def certified_participants(edition, participant_ids=None) -> set[int]:
    """Uczestnicy, którzy **już mają** zaświadczenie z warsztatów w tej edycji (z dowolnego wpisu)."""
    queryset = Certificate.objects.filter(
        edition=edition, kind=CertificateKind.WARSZTATY, entry__isnull=False
    )
    if participant_ids is not None:
        queryset = queryset.filter(entry__participant_id__in=list(participant_ids))
    return set(queryset.values_list("entry__participant_id", flat=True))


def meets_threshold(attended: int, threshold: int) -> bool:
    """Czy liczba warsztatów spełnia próg. Osobno, bo to jest **cała** reguła i ma własny test."""
    return threshold >= 1 and attended >= threshold


@dataclass
class Evaluation:
    """Stan edycji względem progu – wspólny dla zadania i dla ekranu koordynatora."""

    threshold: int
    workshops: int
    #: Spełniają próg, mają wpis w edycji, a dokumentu jeszcze nie mają: ``[(wpis, liczba)]``.
    due: list = field(default_factory=list)
    #: Poniżej progu, bez dokumentu, z co najmniej jedną obecnością: ``[(wpis, liczba)]``.
    below: list = field(default_factory=list)
    certified: int = 0


def evaluate(edition, threshold: int, participant_ids=None) -> Evaluation:
    """Kto spełnia próg, a kto jest poniżej – bez zapisu czegokolwiek."""
    competition = edition.competition
    keys = schedule_keys(competition)
    counts = attendance_counts(competition, keys, participant_ids)
    result = Evaluation(threshold=threshold, workshops=len(keys))
    if not counts:
        return result
    certified = certified_participants(edition, counts.keys())
    result.certified = len(certified)
    entries = first_entries(edition, [pk for pk in counts if pk not in certified])
    for participant_id in sorted(entries):
        attended = counts[participant_id]
        target = result.due if meets_threshold(attended, threshold) else result.below
        target.append((entries[participant_id], attended))
    return result


@dataclass
class RunResult:
    issued: list = field(default_factory=list)
    notified: int = 0


def issue_due(edition, *, trigger: str, participant_ids=None, actor=None, request=None) -> RunResult:
    """Wystawia zaświadczenia wszystkim, którzy spełniają próg reguły tej edycji. Idempotentne.

    Reguła wyłączona albo jej brak – nic (zwraca pusty wynik). ``participant_ids`` zawęża ocenę do
    osób, których obecność właśnie się zmieniła (zadanie po zapisie obecności); ``None`` = cała
    edycja („Przelicz teraz”).
    """
    from apps.results.certificates import issue_certificate

    result = RunResult()
    policy = policy_for(edition)
    if policy is None or not policy.enabled:
        return result
    evaluation = evaluate(edition, policy.min_workshops, participant_ids)
    for entry, _attended in evaluation.due:
        try:
            certificate, created = issue_certificate(
                edition=edition,
                kind=CertificateKind.WARSZTATY,
                entry=entry,
                actor=actor,
                request=request,
            )
        except IntegrityError:
            # Równoległy przebieg (drugie zadanie, ręczne „Wystaw (ZIP)”) wystawił ten dokument
            # chwilę wcześniej – unikalność (wpis, rodzaj) to złapała. Dokument jest, listu nie ma.
            logger.info("Zaświadczenie z warsztatów dla wpisu %s wystawione równolegle.", entry.pk)
            continue
        if not created:
            continue
        AutoIssuedCertificate.objects.create(
            certificate=certificate, edition=edition, trigger=trigger, threshold=policy.min_workshops
        )
        result.issued.append(certificate)
    if policy.notify_participants:
        from .notifications import notify_ready

        for certificate in result.issued:
            if notify_ready(certificate):
                result.notified += 1
    if result.issued:
        audit(
            actor,
            "workshop_certificates.auto_issued",
            edition,
            # Liczby, bez nazwisk i bez listy osób – każdy dokument ma i tak własny wpis
            # ``certificate.issued`` z numerem i identyfikatorem wpisu.
            {
                "issued": len(result.issued),
                "notified": result.notified,
                "threshold": policy.min_workshops,
                "trigger": trigger,
            },
            request=request,
        )
    return result


def mark_notified(certificate) -> bool:
    """Zaznacza wysłanie listu – ``False``, gdy list do tego dokumentu już wyszedł (zadanie powtórzone)."""
    updated = AutoIssuedCertificate.objects.filter(certificate=certificate, notified_at__isnull=True).update(
        notified_at=timezone.now()
    )
    return bool(updated)


__all__ = [
    "AutoIssueTrigger",
    "Evaluation",
    "PENDING_LIMIT",
    "evaluate",
    "issue_due",
    "meets_threshold",
    "policy_for",
]
