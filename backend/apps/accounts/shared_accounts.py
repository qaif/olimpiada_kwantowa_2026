"""Konta współdzielone między konkursami – strażnik operacji koordynatora (audyt 10.10.2026, W3).

``accounts.User`` jest kontem **platformy** (§ 3.4): nauczyciel bywa opiekunem w konkursie B
i recenzentem w konkursie A, a decyzja D4 przewiduje dla niego jedno konto, jedno hasło i jeden
reset hasła. Ekran kont koordynatora pokazuje takie konto obu organizatorom, bo każdy z nich ma
w nim swój profil – i do tej poprawki obaj mogli zrobić z nim wszystko, co ekran oferuje.

Część tych operacji nie zna granicy konkursu, bo działa na samym koncie: adres e-mail (login
i adres resetu hasła), blokada logowania, reset hasła, zdjęcie drugiego składnika, usunięcie
(anonimizacja czyści profile we **wszystkich** konkursach) i eksport danych. Koordynator B, który
zmienia adres takiego konta na swój, a potem klika „Nie pamiętasz hasła?”, loguje się jako
recenzent konkursu A – z jego anonimowymi pracami i ocenami. Dlatego te operacje na koncie z rolą
w innym konkursie są odmawiane (``ACCOUNT_SHARED``) i zostają decyzją administratora platformy.

Co zostaje koordynatorowi: dane **profilu w jego konkursie** (szkoła, klasa, status komitetu)
i imię z nazwiskiem – te zmiany nie przenoszą konta ani nie otwierają go nikomu nowemu.
"""

from __future__ import annotations

from rest_framework import status

from apps.core.api import DomainError

#: Komunikat odmowy – mówi, **dlaczego** i **kto** może, bo koordynator dostaje go zwykle w trakcie
#: telefonu i musi umieć odpowiedzieć dzwoniącemu, do kogo ma się zwrócić.
ACCOUNT_SHARED_MESSAGE = (
    "To konto ma role także w innym konkursie – jego adres e-mail, blokadę, hasło, drugi składnik "
    "logowania, eksport danych i usunięcie zmienia administrator serwisu."
)


def active_elsewhere(user, competition) -> bool:
    """Czy konto ma rolę albo profil w konkursie **innym** niż ``competition``.

    Dowody są te same, którymi ``users_for_competition`` rozstrzyga własność konta, plus opiekun
    drużyny narodowej (DEL-01): ``Membership`` dowolnej roli, profil uczestnika, profil komitetu
    (także oczekujący – taki człowiek nie ma jeszcze członkostwa), profil opiekuna szkolnego
    i **czynna** rola opiekuna delegacji. Odwołany opiekun delegacji (``removed_at``) roli już nie
    ma – wiersz zostaje wyłącznie jako nośnik dowodów zgód.

    ``competition=None`` (żądanie bez konkursu) liczy **każdą** rolę jako „gdzie indziej”: bez
    konkursu nie ma „tutaj”, a odwrotne rozstrzygnięcie zamieniałoby błąd rozstrzygania hosta
    w drogę do przejęcia kont wszystkich konkursów (S13). Domyślnie zamknięte, jak
    ``for_competition(None)``.
    """
    from .delegations import DelegationLeader
    from .models import CommitteeMember, Membership, Participant, SchoolSupervisor

    def elsewhere(queryset, path: str = "competition"):
        return queryset if competition is None else queryset.exclude(**{path: competition})

    candidates = (
        elsewhere(Membership.objects.filter(user=user)),
        elsewhere(Participant.objects.filter(user=user)),
        elsewhere(CommitteeMember.objects.filter(user=user)),
        elsewhere(SchoolSupervisor.objects.filter(user=user)),
        elsewhere(DelegationLeader.objects.filter(user=user).active(), "delegation__competition"),
    )
    return any(queryset.exists() for queryset in candidates)


def may_manage_shared(actor) -> bool:
    """Czy wykonawca ma władzę nad kontem ponad granicą konkursu.

    Superużytkownik i superkoordynator są koordynatorami **każdego** konkursu instalacji – dla nich
    rola w sąsiednim konkursie nie jest cudzą sprawą. Każdy inny koordynator jest organizatorem
    jednego konkursu i o koncie, które ma role także u sąsiada, nie decyduje sam.
    """
    if actor is None:
        return False
    if getattr(actor, "is_superuser", False):
        return True
    from .super_coordinator import is_super_coordinator

    return is_super_coordinator(actor)


def is_shared_for(user, competition, *, actor=None) -> bool:
    """``active_elsewhere`` z uwzględnieniem wykonawcy – do odmowy i do ukrycia przycisków."""
    return not may_manage_shared(actor) and active_elsewhere(user, competition)


def assert_not_shared(user, competition, *, actor=None) -> None:
    """Odmowa (``ACCOUNT_SHARED``) dla konta z rolą w innym konkursie – patrz docstring modułu."""
    if is_shared_for(user, competition, actor=actor):
        raise DomainError(ACCOUNT_SHARED_MESSAGE, "ACCOUNT_SHARED", status.HTTP_400_BAD_REQUEST)
