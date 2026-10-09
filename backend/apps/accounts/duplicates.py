"""Zdublowane konta uczestników jednego konkursu – wykrycie, sugestia i bezpieczne usunięcie kopii.

Skąd ten moduł (zadanie ACC-DUP-01, prośba organizatora z 9.10.2026). Ręczne zapytanie na produkcji
znalazło 17 osób z 36 kontami uczestnika w tym samym konkursie: to samo imię, nazwisko i szkoła,
różny adres e-mail. Przebieg jest prawie zawsze ten sam – literówka w adresie (``gmail.con``) albo
list aktywacyjny zatrzymany przez skrzynkę szkolną, a potem drugie konto „na czysto”. W 14 grupach
dokładnie jedno konto się logowało, pozostałe nigdy. Każda taka kopia to drugi kod publiczny tej
samej osoby na liście uczestników, w eksporcie dla kuratorium i w licznikach szkół.

Moduł **niczego nie usuwa sam z siebie**. Liczy grupy i sugestię, a usunięcie zawsze idzie przez
``apps.accounts.profile.delete_account_by_coordinator`` – tę samą drogę, ten sam audyt i tę samą
regułę „ślad w zawodach → anonimizacja”, co przycisk „Usuń konto” na ekranie edycji. Druga ścieżka
kasowania rozjechałaby się z pierwszą przy pierwszej zmianie reguły.

Dlaczego grupowanie w Pythonie, a nie w SQL-u: normalizacja NFKC i ``casefold()`` nie mają
w PostgreSQL wiernego odpowiednika (``lower()`` nie składa „ß”, ``normalize()`` nie zna casefoldingu),
a reguła ma być jedna. Koszt jest jednym zapytaniem ``values_list`` po profilach konkursu (kilka
tysięcy krotek), a dane szczegółowe dociągamy już wyłącznie dla profili w grupach – stałą liczbą
zapytań zbiorczych, niezależną od liczby osób.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

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

KEEP = "keep"
CANDIDATE = "candidate"
DECIDE = "decide"

SUGGESTION_LABELS = {
    KEEP: "do zachowania",
    CANDIDATE: "kandydat do usunięcia",
    DECIDE: "do decyzji",
}

#: Górna granica jednego zbiorczego usunięcia. Na produkcji kandydatów było 19; limit chroni przed
#: żądaniem z kilkoma tysiącami identyfikatorów, które trzymałoby proces przez minuty.
MAX_BULK_IDS = 500

#: Znaki, które w imieniu, nazwisku i nazwie szkoły nie niosą tożsamości: kropki („Jan K.”, „im.”),
#: cudzysłowy i apostrofy w każdej typograficznej odmianie („LO „Batory”” vs „LO "Batory"”).
_IGNORED = re.compile(r"[.\"'„”“‚’‘`«»]")


def normalize_text(value: str | None) -> str:
    """Postać porównawcza imienia, nazwiska albo nazwy szkoły.

    NFKC składa warianty zapisu tego samego znaku (pełnej szerokości, ligatury, „ł” złożone
    z dwóch kodów), ``casefold()`` – wielkość liter mocniej niż ``lower()``, a zbijanie spacji
    usuwa podwójne i końcowe spacje z formularza. Myślnik zostaje: „Anna-Maria” i „Anna Maria”
    bywają dwiema osobami w jednej klasie.
    """
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = _IGNORED.sub("", text)
    return " ".join(text.split())


def school_key(school_ref_id, custom_institution_ref_id, school: str) -> tuple | None:
    """Klucz szkoły: wpis z wykazu SIO, inaczej placówka organizatora, inaczej tekst.

    Rodzaj jest częścią klucza, więc identyfikator szkoły 12 i placówki 12 nie są tą samą szkołą.
    ``None`` – profil bez szkoły (pusty tekst bez odnośnika); taki profil nie trafia do żadnej grupy,
    bo dwie osoby o tym samym nazwisku „bez szkoły” to żaden sygnał.
    """
    if school_ref_id is not None:
        return ("ref", school_ref_id)
    if custom_institution_ref_id is not None:
        return ("inst", custom_institution_ref_id)
    text = normalize_text(school)
    return ("text", text) if text else None


# --- drugi, słabszy sygnał: adres e-mail z literówką w domenie ------------------------------------

#: Literówki domen widziane w zgłoszeniach i typowe dla polskich skrzynek. Mapa jest zamknięta
#: celowo: zgadywanie „najbliższej” domeny łączyłoby prawdziwe, różne adresy (``o2.pl`` / ``op.pl``).
DOMAIN_TYPOS = {
    "gmail.con": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.cm": "gmail.com",
    "gmail.om": "gmail.com",
    "gmail.pl": "gmail.com",
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "googlemail.com": "gmail.com",
    "wp.p": "wp.pl",
    "wp.pll": "wp.pl",
    "o2.p": "o2.pl",
    "onet.p": "onet.pl",
    "onet.com.pl": "onet.pl",
    "interia.p": "interia.pl",
    "interia.com": "interia.pl",
    "outlok.com": "outlook.com",
    "hotmial.com": "hotmail.com",
}

#: Zamiany końcówki domeny – ``.con`` nie istnieje, więc to zawsze literówka ``.com``.
TLD_TYPOS = {".con": ".com", ".cmo": ".com", ".ocm": ".com", ".comm": ".com"}


def normalize_email(email: str | None) -> str:
    """Adres z poprawioną literówką domeny – wyłącznie do porównania, nigdy do zapisu.

    Część lokalna zostaje bez zmian poza wielkością liter; kropki i „+” w adresie Gmaila
    **nie** są zdejmowane: ``jan.kowalski@`` i ``jankowalski@`` to w Gmailu jedna skrzynka,
    ale w każdej innej domenie dwie, a ten sygnał ma się mylić rzadko, a nie trafiać często.
    """
    email = (email or "").strip().casefold()
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return email
    for wrong, right in TLD_TYPOS.items():
        if domain.endswith(wrong):
            domain = domain[: -len(wrong)] + right
            break
    domain = DOMAIN_TYPOS.get(domain, domain)
    return f"{local}@{domain}"


# --- struktury wyniku ----------------------------------------------------------------------------


@dataclass
class DuplicateAccount:
    """Jedno konto w grupie – wszystko, czego ekran potrzebuje, bez dalszych zapytań."""

    participant: Participant
    training_stages: list[str] = field(default_factory=list)
    competition_stages: list[str] = field(default_factory=list)
    works: int = 0
    certificate: str = ""
    other_roles: bool = False
    protected: bool = False
    suggestion: str = DECIDE
    reason: str = ""

    @property
    def user(self) -> User:
        return self.participant.user

    @property
    def logged_in(self) -> bool:
        return self.user.last_login is not None

    @property
    def used(self) -> bool:
        """„Używane” = ktoś z niego korzystał: logowanie, etap zawodów albo praca (§ 3.1 spec)."""
        return self.logged_in or bool(self.competition_stages) or self.works > 0

    @property
    def suggestion_label(self) -> str:
        return SUGGESTION_LABELS[self.suggestion]

    def blockers(self) -> list[str]:
        """Powody, dla których to konto nie może być kandydatem – niezależnie od reszty grupy."""
        reasons = []
        if self.protected:
            reasons.append("konto chronione")
        if self.logged_in:
            reasons.append("logowało się")
        if self.competition_stages:
            reasons.append("ma wpis do etapu zawodów")
        if self.works:
            reasons.append("ma oddane prace")
        if self.certificate:
            reasons.append("ma zaświadczenie o statusie ucznia")
        if self.other_roles:
            reasons.append("ma inne role albo profil w innym konkursie")
        return reasons


@dataclass
class DuplicateGroup:
    """Osoba (imię, nazwisko, szkoła) z co najmniej dwoma kontami uczestnika w tym konkursie."""

    number: int
    accounts: list[DuplicateAccount]

    @property
    def full_name(self) -> str:
        user = self.accounts[0].user
        return f"{user.first_name} {user.last_name}".strip()

    @property
    def school(self) -> str:
        return self.accounts[0].participant.school

    @property
    def candidates(self) -> list[DuplicateAccount]:
        return [account for account in self.accounts if account.suggestion == CANDIDATE]


@dataclass
class EmailSuspect:
    """Konta, których adresy różnią się wyłącznie literówką domeny (sygnał słabszy, § 4.4 spec)."""

    normalized: str
    participants: list[Participant]


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup]
    email_suspects: list[EmailSuspect]

    @property
    def people(self) -> int:
        return len(self.groups)

    @property
    def accounts(self) -> int:
        return sum(len(group.accounts) for group in self.groups)

    @property
    def candidates(self) -> list[DuplicateAccount]:
        return [account for group in self.groups for account in group.candidates]


# --- wykrywanie ----------------------------------------------------------------------------------


def _suggest(accounts: list[DuplicateAccount]) -> None:
    """Rozdaje sugestie w jednej grupie (reguły w ``docs/tasks/ACC-DUP-01.md`` § 3).

    Kandydat istnieje wyłącznie w grupie, w której **co najmniej jedno** konto jest używane: bez
    tego usunięcie „kopii” mogłoby zabrać osobie jedyne konto, z którego jeszcze skorzysta (dwa
    nieaktywowane konta i tak zniknie kosiarka kont nieaktywowanych). „Do zachowania” wskazujemy
    tylko przy **dokładnie jednym** koncie używanym – przy dwóch to koordynator wie, które z nich
    jest prawdziwe, a ekran nie zgaduje. Puste kopie obok nich pozostają kandydatami: ich
    usunięcie niczego nie zabiera, niezależnie od tego, które z używanych kont zostanie.
    """
    used = [account for account in accounts if account.used]
    keeper = used[0] if len(used) == 1 else None
    for account in accounts:
        if account is keeper:
            account.suggestion, account.reason = KEEP, "jedyne używane konto w grupie"
            continue
        blockers = account.blockers()
        if used and not blockers:
            account.suggestion = CANDIDATE
            account.reason = "nigdy się nie logowało, bez etapu zawodów, prac i innych ról"
        elif not used:
            account.suggestion, account.reason = DECIDE, "żadne konto w grupie nie było używane"
        elif account.used:
            account.suggestion = DECIDE
            account.reason = f"w grupie jest kilka używanych kont ({', '.join(blockers)})"
        else:
            account.suggestion, account.reason = DECIDE, ", ".join(blockers)


def _flags_query(competition) -> dict:
    """Znaczniki „konto koordynatora” i „inne role” jako ``Exists`` – jedno zapytanie na wszystkie konta.

    „Inne role” to wszystko, co usunięcie konta (platformowego) zabrałoby poza tym profilem:
    profil uczestnika albo członkostwo w innym konkursie, profil komitetu, profil opiekuna szkolnego,
    opiekun drużyny narodowej. Takie konto nigdy nie jest kandydatem. Każdy znacznik osobną
    adnotacją, a sumę liczy Python – ``Exists | Exists`` w ``annotate`` zależy od wersji Django.
    """
    from .delegations import DelegationLeader

    user = OuterRef("user_id")
    return {
        "dup_coordinator": Exists(
            User.groups.through.objects.filter(user_id=user, group__name__in=COORDINATOR_GROUPS)
        ),
        "dup_elsewhere": Exists(Participant.objects.filter(user=user).exclude(competition=competition)),
        "dup_member_elsewhere": Exists(Membership.objects.filter(user=user).exclude(competition=competition)),
        "dup_committee": Exists(CommitteeMember.objects.filter(user=user)),
        "dup_supervisor": Exists(SchoolSupervisor.objects.filter(user=user)),
        "dup_team_leader": Exists(DelegationLeader.objects.filter(user=user)),
    }


def _other_roles(profile) -> bool:
    return any(
        getattr(profile, name)
        for name in (
            "dup_elsewhere",
            "dup_member_elsewhere",
            "dup_committee",
            "dup_supervisor",
            "dup_team_leader",
        )
    )


def _details(competition, participant_ids: list[int]) -> dict[int, DuplicateAccount]:
    """Konta z danymi szczegółowymi – cztery zapytania zbiorcze, niezależnie od liczby profili."""
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
        profile.pk: DuplicateAccount(
            participant=profile,
            other_roles=_other_roles(profile),
            protected=profile.user.is_superuser or profile.dup_coordinator,
        )
        for profile in profiles
    }
    entries = (
        StageEntry.objects.filter(participant_id__in=participant_ids)
        .values_list("participant_id", "stage__kind", "stage__name")
        .order_by("stage__opens_at", "pk")
    )
    for participant_id, kind, name in entries:
        label = name or StageKind(kind).label
        account = accounts[participant_id]
        (account.training_stages if kind == StageKind.TRAINING else account.competition_stages).append(label)
    # „Praca” = zadanie, do którego cokolwiek oddano (jak kolumna „Prace” listy uczestników), ale
    # ze **wszystkich** edycji: pytanie brzmi „czy to konto zostawiło cokolwiek w zawodach”.
    works = (
        Submission.objects.filter(entry__participant_id__in=participant_ids)
        .values("entry__participant_id")
        .annotate(total=Count("problem", distinct=True))
    )
    for row in works:
        accounts[row["entry__participant_id"]].works = row["total"]
    certificates = (
        StudentStatusCertificate.objects.filter(participant_id__in=participant_ids, is_current=True)
        .values_list("participant_id", "status")
        .order_by("participant_id", "-pk")
    )
    for participant_id, status in certificates:
        account = accounts[participant_id]
        if not account.certificate:
            account.certificate = CertificateStatus(status).label
    return accounts


def find_duplicate_groups(competition, *, with_email_suspects: bool = True) -> DuplicateReport:
    """Grupy zdublowanych kont uczestnika **tego** konkursu, z sugestią przy każdym koncie.

    Profile zanonimizowane nie wchodzą (to już nie są osoby, którymi się administruje), profile
    z pustym imieniem albo nazwiskiem też nie – pusty napis nie jest tożsamością.
    """
    rows = (
        Participant.objects.filter(competition=competition)
        .exclude_anonymised()
        .values_list(
            "pk",
            "user__first_name",
            "user__last_name",
            "school",
            "school_ref_id",
            "custom_institution_ref_id",
            "user__email",
        )
    )
    buckets: dict[tuple, list[int]] = defaultdict(list)
    by_email: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for pk, first_name, last_name, school, ref_id, inst_id, email in rows:
        if with_email_suspects:
            by_email[normalize_email(email)].append((pk, (email or "").casefold()))
        first, last = normalize_text(first_name), normalize_text(last_name)
        school_id = school_key(ref_id, inst_id, school)
        if not first or not last or school_id is None:
            continue
        buckets[(last, first, school_id)].append(pk)

    grouped = {key: ids for key, ids in buckets.items() if len(ids) > 1}
    suspects_raw = [
        ids
        for ids in by_email.values()
        # Ten sam adres po poprawce, ale **różny** w oryginale – identyczne adresy są niemożliwe
        # (adres jest kluczem logowania), więc warunek odsiewa wyłącznie pojedyncze konta.
        if len({original for _pk, original in ids}) > 1
    ]
    in_groups = {pk for ids in grouped.values() for pk in ids}
    wanted = set(in_groups) | {pk for ids in suspects_raw for pk, _ in ids}
    if not wanted:
        return DuplicateReport(groups=[], email_suspects=[])
    accounts = _details(competition, sorted(wanted))

    groups = []
    for number, key in enumerate(sorted(grouped), start=1):
        members = sorted(
            (accounts[pk] for pk in grouped[key] if pk in accounts),
            key=lambda account: (account.user.date_joined, account.participant.pk),
        )
        _suggest(members)
        groups.append(DuplicateGroup(number=number, accounts=members))

    # Para „podobnych adresów” w całości wewnątrz jednej grupy imienno-szkolnej już stoi na ekranie –
    # pokazywanie jej drugi raz w sekcji słabszego sygnału byłoby szumem.
    group_of = {account.participant.pk: group.number for group in groups for account in group.accounts}
    suspects = []
    for ids in suspects_raw:
        pks = [pk for pk, _ in ids]
        numbers = {group_of.get(pk) for pk in pks}
        if len(numbers) == 1 and None not in numbers:
            continue
        suspects.append(
            EmailSuspect(
                normalized=normalize_email(accounts[pks[0]].user.email),
                participants=[accounts[pk].participant for pk in pks],
            )
        )
    return DuplicateReport(groups=groups, email_suspects=suspects)


# --- usuwanie ------------------------------------------------------------------------------------


@dataclass
class BulkResult:
    deleted: list[str] = field(default_factory=list)
    anonymised: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def candidate_user_ids(competition) -> set[int]:
    """Identyfikatory kont, które **w tej chwili** są kandydatami do usunięcia."""
    report = find_duplicate_groups(competition, with_email_suspects=False)
    return {account.user.pk for account in report.candidates}


def delete_candidates(competition, user_ids, *, actor: User, request=None) -> BulkResult:
    """Usuwa wskazane konta – wyłącznie te, które w chwili usuwania nadal są kandydatami.

    Warunki przeliczamy **teraz**, a nie wierzymy liście z formularza: między wyświetleniem ekranu
    a kliknięciem uczeń mógł się zalogować na „kopię” (bo to ją właśnie aktywował), a drugi
    koordynator mógł usunąć jedyne używane konto osoby – wtedy kopia przestaje być kopią. Każde konto
    osobno: wiersz konta pod ``select_for_update`` i jeszcze raz ``last_login`` – logowanie
    w trakcie pętli też ma wygrać z usunięciem. Samo usunięcie idzie przez
    ``delete_account_by_coordinator`` (audyt ``account.deleted_by_coordinator``, ochrona
    koordynatora i własnego konta, anonimizacja przy śladzie w zawodach).
    """
    from .profile import delete_account_by_coordinator

    eligible = candidate_user_ids(competition)
    result = BulkResult()
    for user_id in user_ids:
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=user_id).first()
            if user is None:
                continue
            label = user.email
            if user_id not in eligible or user.last_login is not None:
                result.skipped.append(label)
                continue
            try:
                outcome = delete_account_by_coordinator(user, actor=actor, request=request)
            except DomainError:
                result.skipped.append(label)
                continue
        (result.anonymised if outcome == "anonymised" else result.deleted).append(label)
    return result
