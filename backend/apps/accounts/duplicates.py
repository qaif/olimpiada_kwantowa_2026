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
from dataclasses import dataclass

from .account_cleanup import (
    MAX_BULK_IDS,
    AccountFacts,
    BulkResult,
    collect_facts,
    delete_accounts,
    login_stamp,
)
from .models import Participant, User

__all__ = [
    "CANDIDATE",
    "DECIDE",
    "KEEP",
    "MAX_BULK_IDS",
    "BulkResult",
    "DuplicateAccount",
    "delete_candidates",
    "delete_duplicate_account",
    "duplicate_user_ids",
    "find_duplicate_groups",
    "normalize_email",
    "normalize_text",
]

KEEP = "keep"
CANDIDATE = "candidate"
DECIDE = "decide"

SUGGESTION_LABELS = {
    KEEP: "do zachowania",
    CANDIDATE: "kandydat do usunięcia",
    DECIDE: "do decyzji",
}

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
class DuplicateAccount(AccountFacts):
    """Jedno konto w grupie – fakty (``AccountFacts``) plus sugestia i jej uzasadnienie."""

    suggestion: str = DECIDE
    reason: str = ""

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


def _details(competition, participant_ids: list[int]) -> dict[int, DuplicateAccount]:
    """Konta z danymi szczegółowymi – wspólne ``collect_facts`` (4 zapytania zbiorcze)."""
    return collect_facts(competition, participant_ids, factory=DuplicateAccount)


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


def candidate_user_ids(competition) -> set[int]:
    """Identyfikatory kont, które **w tej chwili** są kandydatami do usunięcia."""
    report = find_duplicate_groups(competition, with_email_suspects=False)
    return {account.user.pk for account in report.candidates}


def duplicate_user_ids(competition) -> set[int]:
    """Identyfikatory kont, które **w tej chwili** należą do jakiejkolwiek grupy duplikatów."""
    report = find_duplicate_groups(competition, with_email_suspects=False)
    return {account.user.pk for group in report.groups for account in group.accounts}


def delete_candidates(competition, user_ids, *, actor: User, request=None) -> BulkResult:
    """Usuwa wskazane konta – wyłącznie te, które w chwili usuwania nadal są kandydatami.

    Warunki przeliczamy **teraz**, a nie wierzymy liście z formularza: między wyświetleniem ekranu
    a kliknięciem uczeń mógł się zalogować na „kopię” (bo to ją właśnie aktywował), a drugi
    koordynator mógł usunąć jedyne używane konto osoby – wtedy kopia przestaje być kopią. Pętla,
    blokada wiersza i samo usunięcie – wspólne ``account_cleanup.delete_accounts``; tutaj zostaje
    wyłącznie warunek: nadal kandydat i (na zablokowanym wierszu) nadal bez logowania.
    """
    eligible = candidate_user_ids(competition)
    return delete_accounts(
        user_ids,
        eligible=lambda user: user.pk in eligible and user.last_login is None,
        actor=actor,
        request=request,
    )


def delete_duplicate_account(
    competition, user_id: int, *, seen_login: str, actor: User, request=None
) -> BulkResult:
    """Usunięcie jednego konta z wiersza ekranu duplikatów (ACC-DUP-02 § 2).

    Dozwolone przy **każdym** koncie, którego nie odrzuci ``delete_account_by_coordinator`` – także
    przy koncie używanym; o tym, co zabiera, mówi ekran przed kliknięciem. Dwa warunki w chwili
    usuwania:

    - konto nadal należy do grupy duplikatów – inaczej drugie „Usuń” na nieodświeżonej stronie
      zabrałoby osobie ostatnie konto, gdy kopię usunął w międzyczasie ktoś inny,
    - znacznik logowania jest ten, który koordynator widział (``seen_login``) – ktoś, kto zalogował
      się po wyświetleniu ekranu, zmienił fakt, na którym opierała się decyzja.
    """
    in_groups = duplicate_user_ids(competition)
    return delete_accounts(
        [user_id],
        eligible=lambda user: user.pk in in_groups and login_stamp(user) == seen_login,
        actor=actor,
        request=request,
    )
