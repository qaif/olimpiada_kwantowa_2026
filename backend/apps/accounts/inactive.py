"""Nieaktywne konta uczestników jednego konkursu – filtr, liczniki i bezpieczne usunięcie (ACC-DUP-02).

Prośba organizatora z 10.10.2026: „sekcja nieaktywne konta – te konta, na które nikt się nie logował,
i tu pozwól wybrać liczbę dni lub nigdy”. Typowe przypadki: zaproszenie z importu listy klasowej,
którego uczeń nigdy nie otworzył (kosiarka kont nieaktywowanych takich kont celowo nie rusza –
``apps.accounts.tasks``), konto z potwierdzonym adresem, na które nikt nie wrócił po rejestracji,
i konta sprzed roku, na które od dawna nikt nie zagląda.

Moduł **niczego nie usuwa sam z siebie** i nie jest polityką retencji: liczy listę według filtra
koordynatora, a usunięcie idzie wspólną drogą ``account_cleanup.delete_accounts`` →
``delete_account_by_coordinator`` (audyt, ochrona koordynatora, „ślad w zawodach → anonimizacja”).

Filtr jest jeden – ten sam queryset buduje listę, licznik, eksport **i** sprawdzenie w chwili
usuwania (zawężony do jednego konta pod blokadą wiersza). Dwie implementacje warunku „nie logowało
się od N dni” (SQL dla listy, Python dla usuwania) rozjechałyby się na pierwszej granicy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from .account_cleanup import (
    AccountFacts,
    BulkResult,
    collect_facts,
    delete_accounts,
    login_stamp,
)
from .models import COORDINATOR_GROUPS, Participant, User

LOGIN_NEVER = "never"
LOGIN_DAYS = "days"

ACTIVATION_ANY = ""
ACTIVATION_VERIFIED = "verified"
ACTIVATION_UNVERIFIED = "unverified"
ACTIVATION_CHOICES = (
    (ACTIVATION_ANY, "wszystkie"),
    (ACTIVATION_VERIFIED, "z potwierdzonym adresem"),
    (ACTIVATION_UNVERIFIED, "bez potwierdzonego adresu"),
)

#: Podpowiedzi liczby dni w polu filtra (``<datalist>``) – pole przyjmuje też każdą inną liczbę.
DAY_PRESETS = (7, 14, 30, 60, 90, 180, 365)
#: Podpowiedzi „konto założone co najmniej N dni temu”.
JOINED_PRESETS = (0, 1, 3, 7, 14, 30)
MAX_DAYS = 3650
DEFAULT_LOGIN_DAYS = 30
#: Konto z wczoraj nie jest „nieaktywne” – list aktywacyjny albo zaproszenie z importu (link żyje
#: 14 dni) może jeszcze iść. Tydzień jest domyślnym marginesem, który koordynator może zdjąć (0).
DEFAULT_JOINED_DAYS = 7

#: Powód pominięcia przy usunięciu – wspólny dla całej pętli, więc nie siedzi w ``BulkResult.reasons``.
SKIP_REASON = "konto nie spełnia już filtra (ktoś się zalogował) albo ma inne role"


@dataclass(frozen=True)
class InactiveFilter:
    """Filtr ekranu: ``login_days=None`` – „nigdy się nie logowało”, liczba – „od N dni albo nigdy”."""

    login_days: int | None = None
    joined_days: int = DEFAULT_JOINED_DAYS
    activation: str = ACTIVATION_ANY

    @classmethod
    def parse(cls, data) -> tuple[InactiveFilter | None, list[str]]:
        """Filtr z parametrów GET/POST. Brak parametru = wartość domyślna; zła wartość = błąd.

        Przy błędzie zwracamy ``None`` – ekran pokazuje wtedy komunikat i **pustą** listę, a POST
        kończy się 400. Lista z domyślnym filtrem w miejscu odrzuconego łatwo uchodzi za wynik
        filtra, który koordynator wpisał – a z tej listy się usuwa.
        """
        errors: list[str] = []
        login = data.get("login") or LOGIN_NEVER
        login_days = None
        if login == LOGIN_DAYS:
            login_days = _bounded_int(data.get("days"), 1, MAX_DAYS)
            if login_days is None:
                errors.append(f"Liczba dni bez logowania musi być liczbą od 1 do {MAX_DAYS}.")
        elif login != LOGIN_NEVER:
            errors.append("Nieznany rodzaj filtra logowania.")
        raw_joined = data.get("joined")
        if raw_joined in (None, ""):
            joined = DEFAULT_JOINED_DAYS
        else:
            joined = _bounded_int(raw_joined, 0, MAX_DAYS)
            if joined is None:
                errors.append(f"Wiek konta musi być liczbą dni od 0 do {MAX_DAYS}.")
        activation = data.get("activation") or ACTIVATION_ANY
        if activation not in dict(ACTIVATION_CHOICES):
            errors.append("Nieznany filtr potwierdzenia adresu.")
        if errors:
            return None, errors
        return cls(login_days=login_days, joined_days=joined, activation=activation), []

    @property
    def never(self) -> bool:
        return self.login_days is None

    def params(self) -> list[tuple[str, str]]:
        """Parametry filtra w postaci kanonicznej – do odnośników, ukrytych pól i powrotu po POST."""
        pairs = [("login", LOGIN_NEVER if self.never else LOGIN_DAYS)]
        if not self.never:
            pairs.append(("days", str(self.login_days)))
        pairs.append(("joined", str(self.joined_days)))
        if self.activation:
            pairs.append(("activation", self.activation))
        return pairs

    def querystring(self) -> str:
        return urlencode(self.params())

    def describe(self) -> str:
        """Filtr jednym zdaniem – nagłówek listy i ekran potwierdzenia."""
        if self.never:
            text = "nigdy się nie logowało"
        else:
            text = f"nie logowało się od {self.login_days} dni albo nigdy"
        if self.joined_days:
            text += f", konto założone co najmniej {self.joined_days} dni temu"
        if self.activation:
            text += f", {dict(ACTIVATION_CHOICES)[self.activation]}"
        return text

    def as_audit(self) -> dict:
        return {"login_days": self.login_days, "joined_days": self.joined_days, "activation": self.activation}


def _bounded_int(raw, low: int, high: int) -> int | None:
    try:
        value = int(str(raw).strip())
    except TypeError, ValueError:
        return None
    return value if low <= value <= high else None


def scope(competition):
    """Profile, którymi ten ekran w ogóle się zajmuje – niezależnie od filtra.

    Uczestnicy **tego** konkursu, bez kont zanonimizowanych (to już nie są osoby, którymi się
    administruje) i bez kont chronionych: koordynatora i superużytkownika ``delete_account_by_coordinator``
    i tak nie usunie, a na liście „do sprzątnięcia” byłyby wyłącznie szumem.
    """
    coordinators = User.groups.through.objects.filter(group__name__in=COORDINATOR_GROUPS).values("user_id")
    return (
        Participant.objects.filter(competition=competition)
        .exclude_anonymised()
        .exclude(user__is_superuser=True)
        .exclude(user_id__in=coordinators)
    )


def inactive_participants(competition, flt: InactiveFilter, *, now=None):
    """Profile spełniające filtr, od najstarszych kont. Granice domknięte (``<=``), patrz spec § 3.2."""
    now = now or timezone.now()
    profiles = scope(competition)
    if flt.never:
        profiles = profiles.filter(user__last_login__isnull=True)
    else:
        cutoff = now - timedelta(days=flt.login_days)
        profiles = profiles.filter(Q(user__last_login__isnull=True) | Q(user__last_login__lte=cutoff))
    if flt.joined_days:
        profiles = profiles.filter(user__date_joined__lte=now - timedelta(days=flt.joined_days))
    if flt.activation == ACTIVATION_VERIFIED:
        profiles = profiles.filter(user__email_verified_at__isnull=False)
    elif flt.activation == ACTIVATION_UNVERIFIED:
        profiles = profiles.filter(user__email_verified_at__isnull=True)
    return profiles.order_by("user__date_joined", "pk")


def counters(profiles) -> dict:
    """Liczniki nad tabelą – jedno zapytanie agregujące, niezależnie od liczby kont.

    „Ślad w zawodach” = wpis do etapu poza treningiem albo jakakolwiek praca: takie konto zostanie
    przy usunięciu zanonimizowane, a nie skasowane, i koordynator powinien wiedzieć ile ich jest,
    zanim kliknie „zaznacz wszystkie”.
    """
    from apps.competitions.models import StageEntry, StageKind
    from apps.submissions.models import Submission

    trace = Exists(
        StageEntry.objects.filter(participant_id=OuterRef("pk")).exclude(stage__kind=StageKind.TRAINING)
    )
    works = Exists(Submission.objects.filter(entry__participant_id=OuterRef("pk")))
    return profiles.annotate(inactive_trace=trace, inactive_works=works).aggregate(
        total=Count("pk"),
        never=Count("pk", filter=Q(user__last_login__isnull=True)),
        unverified=Count("pk", filter=Q(user__email_verified_at__isnull=True)),
        with_trace=Count("pk", filter=Q(inactive_trace=True) | Q(inactive_works=True)),
    )


def facts_in_order(competition, participant_ids) -> list[AccountFacts]:
    """Fakty dla listy profili w kolejności listy (strona tabeli, ekran potwierdzenia)."""
    facts = collect_facts(competition, list(participant_ids))
    return [facts[pk] for pk in participant_ids if pk in facts]


def bulk_blocker(account: AccountFacts) -> str:
    """Dlaczego konto nie wchodzi do usunięcia **zbiorczego** (pusty napis – wchodzi).

    Inne role wykluczają, bo konto jest platformowe: kaskada zabrałaby rolę w komitecie, profil
    opiekuna albo profil sąsiedniej olimpiady. Pojedynczo, z ostrzeżeniem na ekranie, wolno.
    """
    if account.protected:
        return "konto chronione"
    if account.other_roles:
        return f"inne role ({', '.join(account.other_role_labels)}) – usuń pojedynczo, jeśli trzeba"
    return ""


def _still_matches(competition, flt: InactiveFilter, now):
    """Warunek ``eligible`` dla pętli usuwania: to samo zapytanie co lista, zawężone do konta.

    Wołane na **zablokowanym** wierszu konta (``delete_accounts``), więc logowanie zatwierdzone przed
    blokadą jest już widoczne, a późniejsze czeka na koniec transakcji.
    """
    matching = inactive_participants(competition, flt, now=now)
    return lambda user: matching.filter(user_id=user.pk).exists()


def delete_inactive(
    competition, user_ids, flt: InactiveFilter, *, actor: User, request=None, now=None
) -> BulkResult:
    """Usunięcie zbiorcze: wyłącznie konta, które **teraz** spełniają filtr i nie mają innych ról."""
    now = now or timezone.now()
    ids = list(user_ids)
    profiles = scope(competition).filter(user_id__in=ids).values_list("pk", "user_id")
    by_user = {user_id: pk for pk, user_id in profiles}
    facts = collect_facts(competition, list(by_user.values()))
    blocked = {user_id for user_id, pk in by_user.items() if pk not in facts or bulk_blocker(facts[pk])}
    matches = _still_matches(competition, flt, now)
    return delete_accounts(
        ids,
        eligible=lambda user: user.pk in by_user and user.pk not in blocked and matches(user),
        actor=actor,
        request=request,
    )


def delete_inactive_account(
    competition, user_id: int, flt: InactiveFilter, *, seen_login: str, actor: User, request=None, now=None
) -> BulkResult:
    """Usunięcie jednego konta z wiersza (ACC-DUP-02 § 3.4).

    Inne role nie blokują (ekran ostrzega, co zabierze kaskada), ale konto musi **nadal** spełniać
    filtr i mieć ten sam znacznik logowania, który koordynator widział w wierszu.
    """
    matches = _still_matches(competition, flt, now or timezone.now())
    return delete_accounts(
        [user_id],
        eligible=lambda user: login_stamp(user) == seen_login and matches(user),
        actor=actor,
        request=request,
    )
