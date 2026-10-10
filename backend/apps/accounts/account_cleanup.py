"""Wspólny mechanizm sprzątania kont uczestników z panelu koordynatora (ACC-DUP-01, ACC-DUP-02).

Dwa ekrany usuwają konta: „Zdublowane konta” (``apps.accounts.duplicates``) i „Nieaktywne konta”
(``apps.accounts.inactive``). Pytania „co pokazać przy koncie” i „jak bezpiecznie usunąć listę kont”
są w obu identyczne, różni się wyłącznie **warunek** – dlatego tutaj mieszka mechanizm, a warunek
podaje wołający jako funkcję. Kopia pętli usuwania w drugim module rozjechałaby się z pierwszą przy
pierwszej poprawce (blokada wiersza, obsługa odmowy serwisu, audyt).

Zasady, których pilnuje ten moduł, a nie ekrany:

- każde usunięcie idzie przez ``apps.accounts.profile.delete_account_by_coordinator`` – ten sam
  audyt (``account.deleted_by_coordinator``), ta sama ochrona koordynatora i własnego konta, ta sama
  reguła „ślad w zawodach → anonimizacja”,
- warunek ekranu sprawdzamy **na zablokowanym wierszu konta** (``select_for_update``), tuż przed
  usunięciem: lista w formularzu jest obietnicą „najwyżej tyle”, a nie poleceniem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC

from django.db import transaction
from django.db.models import Count, Exists, OuterRef

from apps.core.api import DomainError

from .models import (
    COORDINATOR_GROUPS,
    CommitteeMember,
    Membership,
    Participant,
    SchoolSupervisor,
    User,
)

#: Górna granica jednej listy identyfikatorów w POST (zbiorcze usunięcie). Na produkcji kandydatów
#: było 19; limit chroni przed żądaniem z kilkoma tysiącami identyfikatorów, które trzymałoby
#: proces przez minuty.
MAX_BULK_IDS = 500

#: Znaczniki „innych ról” (adnotacje ``_flags_query``) z etykietą dla człowieka. Kolejność = kolejność
#: na ekranie. ``member_elsewhere`` jest pomijane, gdy stoi już ``elsewhere`` – profil uczestnika
#: w innym konkursie zawsze ma tam też członkostwo, a dwie etykiety mówiłyby to samo.
OTHER_ROLE_LABELS = (
    ("cleanup_elsewhere", "profil uczestnika w innym konkursie"),
    ("cleanup_member_elsewhere", "rola w innym konkursie"),
    ("cleanup_committee", "komitet"),
    ("cleanup_supervisor", "opiekun szkolny"),
    ("cleanup_team_leader", "opiekun drużyny"),
)


def login_stamp(user: User) -> str:
    """Znacznik ostatniego logowania do porównania „to samo, co widział koordynator”.

    ``""`` – nigdy. Inaczej ISO 8601 w UTC z mikrosekundami: obie strony porównania czytają tę samą
    kolumnę z bazy, więc równość jest dokładna, a strefa czasowa procesu nie ma znaczenia.
    """
    if user.last_login is None:
        return ""
    return user.last_login.astimezone(UTC).isoformat()


@dataclass
class AccountFacts:
    """Jedno konto uczestnika z tym, co ekran sprzątania pokazuje – bez dalszych zapytań."""

    participant: Participant
    training_stages: list[str] = field(default_factory=list)
    competition_stages: list[str] = field(default_factory=list)
    works: int = 0
    certificate: str = ""
    other_role_labels: list[str] = field(default_factory=list)
    protected: bool = False

    @property
    def user(self) -> User:
        return self.participant.user

    @property
    def other_roles(self) -> bool:
        return bool(self.other_role_labels)

    @property
    def logged_in(self) -> bool:
        return self.user.last_login is not None

    @property
    def used(self) -> bool:
        """„Używane” = ktoś z niego korzystał: logowanie, etap zawodów albo praca (ACC-DUP-01 § 3.1)."""
        return self.logged_in or bool(self.competition_stages) or self.works > 0

    @property
    def has_trace(self) -> bool:
        """Ślad w zawodach widoczny w wynikach – etap poza treningiem albo prace."""
        return bool(self.competition_stages) or self.works > 0

    @property
    def will_be_anonymised(self) -> bool:
        """Czy usunięcie skończy się anonimizacją – każdy wpis do etapu (też treningu) to ślad.

        Przybliżenie dla ekranu: rozstrzyga ``competition_footprint`` w chwili usuwania (liczy też
        wpisy z innych konkursów i recenzje). Ekran ma uprzedzić, a nie zgadywać za serwis.
        """
        return bool(self.competition_stages or self.training_stages or self.works)

    @property
    def login_stamp(self) -> str:
        return login_stamp(self.user)


def _flags_query(competition) -> dict:
    """Znaczniki „konto chronione” i „inne role” jako ``Exists`` – jedno zapytanie na wszystkie konta.

    „Inne role” to wszystko, co usunięcie konta (platformowego) zabrałoby poza tym profilem:
    profil uczestnika albo członkostwo w innym konkursie, profil komitetu, profil opiekuna szkolnego,
    opiekun drużyny narodowej. Każdy znacznik osobną adnotacją, a sumę liczy Python –
    ``Exists | Exists`` w ``annotate`` zależy od wersji Django.
    """
    from .delegations import DelegationLeader

    user = OuterRef("user_id")
    return {
        "cleanup_coordinator": Exists(
            User.groups.through.objects.filter(user_id=user, group__name__in=COORDINATOR_GROUPS)
        ),
        "cleanup_elsewhere": Exists(Participant.objects.filter(user=user).exclude(competition=competition)),
        "cleanup_member_elsewhere": Exists(
            Membership.objects.filter(user=user).exclude(competition=competition)
        ),
        "cleanup_committee": Exists(CommitteeMember.objects.filter(user=user)),
        "cleanup_supervisor": Exists(SchoolSupervisor.objects.filter(user=user)),
        "cleanup_team_leader": Exists(DelegationLeader.objects.filter(user=user)),
    }


def _role_labels(profile) -> list[str]:
    labels = []
    for name, label in OTHER_ROLE_LABELS:
        if name == "cleanup_member_elsewhere" and profile.cleanup_elsewhere:
            continue
        if getattr(profile, name):
            labels.append(label)
    return labels


def collect_facts(competition, participant_ids, *, factory=AccountFacts) -> dict[int, AccountFacts]:
    """Konta z danymi szczegółowymi – ``{pk profilu: fakty}``, cztery zapytania zbiorcze.

    ``participant_ids`` – lista albo podzapytanie (``values("pk")``) profili **tego** konkursu;
    eksport podaje podzapytanie, żeby nie wklejać tysięcy identyfikatorów w cztery zapytania.
    ``factory`` – klasa wyniku (``DuplicateAccount`` dokłada do faktów sugestię).

    Mapy uzupełniamy przez ``get``: przy podzapytaniu profil może pojawić się między pierwszym
    a kolejnym zapytaniem (rejestracja w tej sekundzie) i nie może wtedy wywrócić ekranu.
    """
    from apps.competitions.models import StageEntry, StageKind
    from apps.student_status.models import CertificateStatus, StudentStatusCertificate
    from apps.submissions.models import Submission

    profiles = (
        Participant.objects.filter(pk__in=participant_ids)
        .select_related("user")
        .annotate(**_flags_query(competition))
        .order_by("user__date_joined", "pk")
    )
    accounts = {
        profile.pk: factory(
            participant=profile,
            other_role_labels=_role_labels(profile),
            protected=profile.user.is_superuser or profile.cleanup_coordinator,
        )
        for profile in profiles
    }
    entries = (
        StageEntry.objects.filter(participant_id__in=participant_ids)
        .values_list("participant_id", "stage__kind", "stage__name")
        .order_by("stage__opens_at", "pk")
    )
    for participant_id, kind, name in entries:
        account = accounts.get(participant_id)
        if account is None:
            continue
        label = name or StageKind(kind).label
        (account.training_stages if kind == StageKind.TRAINING else account.competition_stages).append(label)
    # „Praca” = zadanie, do którego cokolwiek oddano (jak kolumna „Prace” listy uczestników), ale
    # ze **wszystkich** edycji: pytanie brzmi „czy to konto zostawiło cokolwiek w zawodach”.
    works = (
        Submission.objects.filter(entry__participant_id__in=participant_ids)
        .values("entry__participant_id")
        .annotate(total=Count("problem", distinct=True))
    )
    for row in works:
        account = accounts.get(row["entry__participant_id"])
        if account is not None:
            account.works = row["total"]
    certificates = (
        StudentStatusCertificate.objects.filter(participant_id__in=participant_ids, is_current=True)
        .values_list("participant_id", "status")
        .order_by("participant_id", "-pk")
    )
    for participant_id, status in certificates:
        account = accounts.get(participant_id)
        if account is not None and not account.certificate:
            account.certificate = CertificateStatus(status).label
    return accounts


# --- usuwanie ------------------------------------------------------------------------------------


@dataclass
class BulkResult:
    """Wynik pętli usuwania: adresy kont według skutku, plus powód każdego pominięcia."""

    deleted: list[str] = field(default_factory=list)
    anonymised: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: ``{adres: powód}`` – wyłącznie dla pominiętych z odmową serwisu (np. „własne konto”);
    #: pominięcie przez warunek ekranu ma powód wspólny, który zna wołający.
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def removed(self) -> int:
        return len(self.deleted) + len(self.anonymised)


def delete_accounts(
    user_ids: Iterable[int], *, eligible: Callable[[User], bool], actor: User, request=None
) -> BulkResult:
    """Usuwa wskazane konta – wyłącznie te, dla których ``eligible(user)`` jest prawdą **teraz**.

    Każde konto osobno: własna transakcja, wiersz konta pod ``select_for_update`` i dopiero na nim
    warunek ekranu. Logowanie w trakcie pętli zmienia ``last_login`` tego samego wiersza, więc
    wyścig „uczeń właśnie się loguje” kończy się na korzyść ucznia. Odmowa serwisu
    (``DomainError``: konto koordynatora, własne konto) to pominięcie z powodem, a nie błąd całej
    operacji – reszta listy idzie dalej.
    """
    from .profile import delete_account_by_coordinator

    result = BulkResult()
    for user_id in user_ids:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=user_id).first()
            if user is None:
                continue
            label = user.email
            if not eligible(user):
                result.skipped.append(label)
                continue
            try:
                outcome = delete_account_by_coordinator(user, actor=actor, request=request)
            except DomainError as exc:
                result.skipped.append(label)
                result.reasons[label] = str(exc.detail)
                continue
        (result.anonymised if outcome == "anonymised" else result.deleted).append(label)
    return result
