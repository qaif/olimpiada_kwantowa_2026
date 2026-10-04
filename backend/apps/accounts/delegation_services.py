"""Czynności delegacji krajowych: zaproszenie opiekuna, jego przyjęcie i zgłaszanie uczniów (DEL-01).

Modele i ich stan mieszkają w ``apps.accounts.delegations``; tutaj są **wszystkie** zapisy, żeby
widok panelu koordynatora, panel opiekuna i ekran zaproszenia nie miały ani jednej własnej reguły
(ta sama zasada, co w ``apps.accounts.services``: widoki orkiestrują, serwis rozstrzyga).

Granice bezpieczeństwa, każda z powodem:

- **zakres konkursu i kraju.** Opiekun dochodzi do uczniów wyłącznie przez swój wiersz
  ``DelegationLeader`` w bieżącej edycji konkursu żądania (:func:`leader_for`), a ucznia wybiera
  z querysetu zawężonego do **tej** delegacji (:func:`student_of`) – cudzy kraj i cudzy konkurs
  dają 404 z zawężenia, a nie z warunku w widoku,
- **limit pod współbieżnością.** Dodanie ucznia blokuje wiersz delegacji (``select_for_update``)
  i dopiero pod blokadą liczy miejsca. Dwóch opiekunów jednego kraju klikających „Dodaj” w tej
  samej sekundzie czeka na siebie, zamiast obu przejść warunek „jest jeszcze jedno miejsce”,
- **zgody składa uczeń.** Opiekun drużyny zakłada konto w stanie „zaproszony” – nieaktywne i bez
  używalnego hasła – a uczeń sam ustawia hasło i składa zgody pod linkiem z listu. To jest ta sama
  droga, co import listy klasowej (``apps.accounts.bulk_registration``) i z tego samego powodu:
  nikt nie może zgodzić się w cudzym imieniu,
- **adres z zaproszenia musi być adresem konta.** Przyjęcie zaproszenia przez zalogowanego
  porównuje adresy; link przesłany dalej nie zrobi z nikogo opiekuna cudzego kraju,
- **audyt bez danych osobowych.** Wpisy niosą identyfikatory i kody krajów; kogo dotyczą, mówi
  ``target_id`` – audyt czytają osoby bez wglądu w dane uczniów.
"""

from __future__ import annotations

import logging
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy import branding

from .activation import absolute_url, mark_activated, queue_mail
from .consents import ConsentKind, ConsentSource, consent_set
from .delegations import (
    INVITATION_DAYS,
    Delegation,
    DelegationInvitation,
    DelegationLeader,
    DelegationStatus,
    invitation_expiry,
)
from .models import (
    GROUP_PARTICIPANT,
    GROUP_TEAM_LEADER,
    CompetitionRole,
    ConsentRecord,
    Membership,
    Participant,
    Region,
    RegionLevel,
    User,
    hash_invitation_code,
)
from .preferences import language_for

logger = logging.getLogger(__name__)

#: Długość tokenu zaproszenia w bajtach (``token_urlsafe``). 32 bajty = 256 bitów: link jest
#: jedynym poświadczeniem do założenia konta z rolą, więc ma być nie do zgadnięcia, a nie krótki.
TOKEN_BYTES = 32

#: Szablony listów. Treść jest w plikach, a nie w kodzie – to tekst interfejsu w języku odbiorcy.
LEADER_INVITE_SUBJECT_TEMPLATE = "registration/delegation_leader_subject.txt"
LEADER_INVITE_BODY_TEMPLATE = "registration/delegation_leader_body.txt"
STUDENT_INVITE_SUBJECT_TEMPLATE = "registration/delegation_student_subject.txt"
STUDENT_INVITE_BODY_TEMPLATE = "registration/delegation_student_body.txt"
UNLINKED_SUBJECT_TEMPLATE = "registration/delegation_unlinked_subject.txt"
UNLINKED_BODY_TEMPLATE = "registration/delegation_unlinked_body.txt"

#: Zgody opiekuna drużyny – regulamin i RODO, tak samo jak u opiekuna szkolnego: podaje swoje dane
#: (imię, nazwisko, adres) na tych samych zasadach, a zgoda opiekuna prawnego i publikacja nazwiska
#: dotyczą wyłącznie ucznia.
LEADER_CONSENT_KINDS = (ConsentKind.TERMS, ConsentKind.PRIVACY)

#: Najkrótsza sensowna nazwa szkoły – ta sama granica, co w rejestracji (``MIN_SCHOOL_NAME_LENGTH``).
MIN_SCHOOL_LENGTH = 3


# --- bramki -----------------------------------------------------------------------------------


def delegations_enabled(competition) -> bool:
    """Czy ten konkurs zgłasza uczniów przez delegacje – czyta pole wczytanego wiersza."""
    return competition is not None and competition.uses_delegations


def require_delegations(competition) -> None:
    """404, gdy konkurs nie jest w trybie delegacji – ekranów delegacji wtedy **nie ma**.

    404, a nie 403: tak samo jak przy pozostałych funkcjach za przełącznikiem (``custom_regions``,
    forum) adres, którego w tym konkursie nie ma, nie istnieje. Olimpiada Kwantowa nie ma ani
    jednego z tych adresów.
    """
    if not delegations_enabled(competition):
        raise Http404("Ten konkurs nie zgłasza uczniów przez delegacje krajowe.")


def _current_edition(competition):
    from apps.competitions.services import current_edition

    edition = current_edition(competition)
    if edition is None:
        raise DomainError(
            _("Konkurs nie ma bieżącej edycji – delegacje zakłada się w bieżącej edycji."),
            "NO_CURRENT_EDITION",
            status.HTTP_409_CONFLICT,
        )
    return edition


def country_choices(competition) -> list[tuple[str, str]]:
    """Aktywne kraje konkursu (``RegionLevel.COUNTRY``) – lista wyboru „Zaproś opiekuna”."""
    return list(
        Region.objects.for_competition(competition)
        .active()
        .filter(level=RegionLevel.COUNTRY)
        .order_by("name", "id")
        .values_list("code", "name")
    )


def _country(competition, code: str) -> Region:
    """Aktywny kraj konkursu o tym kodzie albo odmowa – region, województwo i kraj wycofany odpadają."""
    country = (
        Region.objects.for_competition(competition)
        .active()
        .filter(level=RegionLevel.COUNTRY, code=(code or "").strip().lower())
        .first()
    )
    if country is None:
        raise DomainError("Wybierz kraj z listy.", "COUNTRY_INVALID", status.HTTP_400_BAD_REQUEST)
    return country


# --- delegacje (koordynator) ------------------------------------------------------------------


def delegations_of(competition, edition=None):
    """Delegacje bieżącej edycji z licznikami – **jedno** zapytanie niezależnie od liczby krajów."""
    edition = edition or _current_edition(competition)
    return (
        Delegation.objects.for_competition(competition)
        .filter(edition=edition)
        .select_related("country")
        .annotate(
            student_count=Count("students", distinct=True),
            leader_count=Count("leaders", filter=Q(leaders__removed_at__isnull=True), distinct=True),
            unlinked_count=Count("unlinked_students", distinct=True),
            pending_count=Count(
                "invitations",
                filter=Q(
                    invitations__accepted_at__isnull=True,
                    invitations__revoked_at__isnull=True,
                    invitations__expires_at__gt=timezone.now(),
                ),
                distinct=True,
            ),
        )
        .order_by("country__name", "id")
    )


def delegation_for(competition, pk: int) -> Delegation:
    """Delegacja **tego konkursu** albo 404 – zakres z querysetu, nie z warunku w widoku."""
    delegation = (
        Delegation.objects.for_competition(competition)
        .select_related("country", "edition")
        .filter(pk=pk)
        .first()
    )
    if delegation is None:
        raise Http404("Nie ma takiej delegacji w tym konkursie.")
    return delegation


def _get_or_create_delegation(competition, edition, country, *, actor, request) -> Delegation:
    """Delegacja kraju w edycji – zakładana przy pierwszym zaproszeniu opiekuna tego kraju."""
    delegation = Delegation.objects.filter(edition=edition, country=country).first()
    if delegation is not None:
        return delegation
    try:
        with transaction.atomic():
            delegation = Delegation.objects.create(
                competition=competition,
                edition=edition,
                country=country,
                max_students=competition.delegation_max_students,
            )
    except IntegrityError:
        # Dwa zaproszenia dla nowego kraju w tej samej chwili – drugie bierze wiersz pierwszego.
        return Delegation.objects.get(edition=edition, country=country)
    audit(actor, "delegation.created", delegation, {"country": country.code}, request=request)
    return delegation


def update_delegation(
    delegation: Delegation, *, max_students: int, status_value: str, note: str, actor, request=None
):
    """Limit, stan i notatka delegacji. Limit nie może spaść poniżej liczby zgłoszonych uczniów.

    Odmowa zamiast przycięcia: obniżenie limitu do 4 przy sześciu zgłoszonych nie mówi, których
    dwóch wypisać – to decyzja z opiekunem, a nie skutek uboczny zapisu formularza.
    """
    if status_value not in DelegationStatus.values:
        raise DomainError("Nieznany stan delegacji.", "STATUS_INVALID", status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        locked = Delegation.objects.select_for_update().get(pk=delegation.pk)
        taken = Participant.objects.filter(delegation=locked).count()
        if max_students < taken:
            raise DomainError(
                f"Delegacja ma już {taken} zgłoszonych uczniów – limit nie może być niższy.",
                "LIMIT_BELOW_COUNT",
                status.HTTP_400_BAD_REQUEST,
            )
        changed = [
            name
            for name, value in (("max_students", max_students), ("status", status_value), ("note", note))
            if getattr(locked, name) != value
        ]
        locked.max_students = max_students
        locked.status = status_value
        locked.note = note
        locked.save(update_fields=["max_students", "status", "note"])
    if changed:
        audit(
            actor, "delegation.updated", locked, {"fields": changed, "status": status_value}, request=request
        )
    return locked


# --- zaproszenie opiekuna -------------------------------------------------------------------


def _new_token() -> tuple[str, str]:
    """Para (token do listu, skrót do bazy). Token istnieje wyłącznie w liście i w pamięci."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_invitation_code(token)


def _normalized_email(email: str) -> str:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    value = (email or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError as exc:
        raise DomainError(
            "Podaj poprawny adres e-mail.", "EMAIL_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    return value


def _leads_elsewhere(email: str, edition, delegation) -> bool:
    """Czy konto o tym adresie prowadzi już **inną** delegację w tej edycji."""
    return (
        DelegationLeader.objects.active()
        .filter(edition=edition, user__email=email)
        .exclude(delegation=delegation)
        .exists()
    )


@transaction.atomic
def invite_leader(competition, *, email: str, country_code: str, actor, request=None) -> DelegationInvitation:
    """Zaprasza opiekuna drużyny kraju. Zakłada delegację, jeśli kraj jej jeszcze nie ma.

    Kolejny opiekun tego samego kraju **dołącza** do istniejącej delegacji – to jest cała odpowiedź
    na „kilku opiekunów jednego kraju”: dzielą wiersz delegacji, więc dzielą uczniów i limit.

    Ponowne zaproszenie tego samego adresu do tej samej delegacji nie dokłada drugiego linku, tylko
    wymienia token i wysyła list jeszcze raz (więz ``accounts_delegation_invitation_one_open``).
    Adres, który już prowadzi **inną** delegację w tej edycji, dostaje odmowę: jedna osoba – jeden
    kraj (DEL-01 § 2).
    """
    require_delegations(competition)
    email = _normalized_email(email)
    edition = _current_edition(competition)
    country = _country(competition, country_code)
    delegation = _get_or_create_delegation(competition, edition, country, actor=actor, request=request)
    if _leads_elsewhere(email, edition, delegation):
        raise DomainError(
            "Ten adres prowadzi już delegację innego kraju w tej edycji.",
            "LEADS_OTHER_DELEGATION",
            status.HTTP_409_CONFLICT,
        )
    if DelegationLeader.objects.active().filter(delegation=delegation, user__email=email).exists():
        raise DomainError(
            "Ta osoba jest już opiekunem tej delegacji.", "ALREADY_LEADER", status.HTTP_409_CONFLICT
        )
    open_invitation = _open_invitation(delegation, email)
    if open_invitation is not None:
        return resend_leader_invitation(open_invitation, actor=actor, request=request)
    token, token_hash = _new_token()
    try:
        with transaction.atomic():
            invitation = DelegationInvitation.objects.create(
                delegation=delegation,
                email=email,
                token_hash=token_hash,
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
                expires_at=invitation_expiry(),
            )
    except IntegrityError:
        # Dwa równoległe „Zaproś” pod ten sam adres (poprawka po przeglądzie): drugie wpada na więz
        # „jedno otwarte zaproszenie” – zamiast 500 odświeża zaproszenie, które właśnie powstało.
        existing = (
            DelegationInvitation.objects.select_for_update()
            .filter(delegation=delegation, email=email, accepted_at__isnull=True, revoked_at__isnull=True)
            .first()
        )
        if existing is None:
            raise
        return resend_leader_invitation(existing, actor=actor, request=request)
    _send_leader_invitation(invitation, token, request=request)
    audit(
        actor,
        "delegation.leader_invited",
        invitation,
        {"delegation": delegation.pk, "country": country.code},
        request=request,
    )
    return invitation


def _open_invitation(delegation, email: str) -> DelegationInvitation | None:
    """Otwarte (nieprzyjęte, niecofnięte) zaproszenie tego adresu do tej delegacji – pod blokadą."""
    return (
        DelegationInvitation.objects.select_for_update()
        .filter(delegation=delegation, email=email, accepted_at__isnull=True, revoked_at__isnull=True)
        .first()
    )


def resend_leader_invitation(
    invitation: DelegationInvitation, *, actor, request=None
) -> DelegationInvitation:
    """Nowy token, nowa ważność, nowy list. Stary link przestaje działać w tej samej chwili.

    Wymiana tokenu (a nie ten sam link jeszcze raz) jest tu warunkiem: token żyje wyłącznie w liście,
    więc „wyślij ponownie” bez wymiany wymagałoby trzymania go w bazie jawnie.
    """
    if invitation.accepted_at is not None:
        raise DomainError(
            "To zaproszenie zostało już przyjęte.", "INVITATION_ACCEPTED", status.HTTP_409_CONFLICT
        )
    if invitation.revoked_at is not None:
        raise DomainError(
            "To zaproszenie zostało cofnięte – wyślij nowe.", "INVITATION_REVOKED", status.HTTP_409_CONFLICT
        )
    token, token_hash = _new_token()
    invitation.token_hash = token_hash
    invitation.expires_at = invitation_expiry()
    invitation.save(update_fields=["token_hash", "expires_at"])
    _send_leader_invitation(invitation, token, request=request)
    audit(
        actor,
        "delegation.leader_invitation_resent",
        invitation,
        {"delegation": invitation.delegation_id},
        request=request,
    )
    return invitation


def revoke_leader_invitation(
    invitation: DelegationInvitation, *, actor, request=None
) -> DelegationInvitation:
    """Cofnięcie zaproszenia – znacznik, a nie skasowanie: ślad, że link istniał, zostaje."""
    if invitation.accepted_at is not None:
        raise DomainError(
            "Zaproszenie zostało już przyjęte – odwołaj opiekuna z delegacji.",
            "INVITATION_ACCEPTED",
            status.HTTP_409_CONFLICT,
        )
    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        audit(
            actor,
            "delegation.leader_invitation_revoked",
            invitation,
            {"delegation": invitation.delegation_id},
            request=request,
        )
    return invitation


def _send_leader_invitation(invitation: DelegationInvitation, token: str, *, request=None) -> None:
    """List z linkiem do ``/delegation/accept/<token>/`` – w języku konkursu (adresat może nie mieć konta)."""
    delegation = invitation.delegation
    competition = delegation.competition
    link = absolute_url(reverse("web:delegation-accept", args=[token]), request, competition)
    recipient = User.objects.filter(email=invitation.email).first()
    context = {"country": delegation.country.name, "link": link, "days": INVITATION_DAYS}
    with language_for(recipient, competition):
        context["brand"] = branding.brand_names(competition)
        subject = render_to_string(LEADER_INVITE_SUBJECT_TEMPLATE, context).strip().replace("\n", " ")
        body = render_to_string(LEADER_INVITE_BODY_TEMPLATE, context)
    queue_mail(subject, body, invitation.email, competition=competition)
    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["sent_at"])


def _invalid_invitation() -> DomainError:
    """Jeden komunikat na każdy powód (zły, wygasły, cofnięty, przyjęty) – bez wskazywania, który."""
    return DomainError(
        _("Link z zaproszeniem jest nieprawidłowy albo wygasł."),
        "DELEGATION_INVITATION_INVALID",
        status.HTTP_400_BAD_REQUEST,
    )


def read_leader_invitation(token: str, competition) -> DelegationInvitation:
    """Działające zaproszenie **tego konkursu** wskazane tokenem albo odmowa.

    Konkurs żądania wchodzi do zapytania: link z listu konkursu A otwarty pod domeną konkursu B
    nie ma tam znaczenia (i nie zdradza, że gdzie indziej istnieje).
    """
    require_delegations(competition)
    if not token or len(token) > 200:
        raise _invalid_invitation()
    invitation = (
        DelegationInvitation.objects.for_competition(competition)
        .select_related("delegation", "delegation__country", "delegation__competition")
        .filter(token_hash=hash_invitation_code(token))
        .first()
    )
    if invitation is None or not invitation.is_usable():
        raise _invalid_invitation()
    return invitation


def _validate_leader_consents(given: dict[str, bool], competition) -> None:
    by_kind = {consent.kind: consent for consent in consent_set(competition)}
    for kind in LEADER_CONSENT_KINDS:
        if not given.get(kind):
            consent = by_kind.get(kind)
            message = consent.missing_message if consent is not None else _("Ta zgoda jest wymagana.")
            raise DomainError(message, "CONSENT_REQUIRED", status.HTTP_400_BAD_REQUEST)


def _record_leader_consents(leader: DelegationLeader, given: dict[str, bool], request=None) -> None:
    """Dowody zgód opiekuna drużyny – bliźniak ``supervisors.record_supervisor_consents``."""
    from apps.core.models import client_ip

    now = timezone.now()
    ConsentRecord.objects.bulk_create(
        [
            ConsentRecord(
                team_leader=leader,
                kind=consent.kind,
                document_version=consent.version,
                given_at=now,
                source=ConsentSource.WEB,
                ip_address=client_ip(request),
            )
            for consent in consent_set(leader.delegation.competition)
            if consent.kind in LEADER_CONSENT_KINDS and given.get(consent.kind)
        ]
    )


def _account_exists() -> DomainError:
    return DomainError(
        _("Ten adres ma już konto – zaloguj się, a potem otwórz link jeszcze raz."),
        "ACCOUNT_EXISTS",
        status.HTTP_409_CONFLICT,
    )


def account_exists(email: str) -> bool:
    """Czy adres zaproszenia ma już konto – ekran prosi wtedy o zalogowanie zamiast rejestracji."""
    return User.objects.filter(email=(email or "").strip().lower()).exists()


@sensitive_variables()
@transaction.atomic
def accept_leader_invitation(
    invitation: DelegationInvitation,
    *,
    user=None,
    first_name: str = "",
    last_name: str = "",
    password: str = "",
    given: dict[str, bool],
    request=None,
) -> DelegationLeader:
    """Przyjęcie zaproszenia: konto (nowe albo zalogowane), zgody, rola i wiersz opiekuna.

    Dwie drogi i obie kończą się tym samym:

    - **zalogowany** – adres konta musi być adresem zaproszenia. Inaczej link przekazany dalej
      zrobiłby opiekunem tego, kto go otworzył,
    - **bez konta** – zakładamy je tutaj, od razu aktywne: list z tokenem przyszedł na ten adres,
      więc kliknięcie w link **jest** potwierdzeniem adresu (``mark_activated``, jak link
      aktywacyjny). Adres, który ma już konto, musi się zalogować – zaproszenie nie przejmuje
      cudzego konta ani nie zmienia jego hasła.

    Zgody (regulamin, RODO) sprawdzamy **przed** zapisem czegokolwiek, tak jak w każdej rejestracji.
    Blokada wiersza zaproszenia zamyka drugie, równoległe przyjęcie tego samego linku.
    """
    from .services import _create_user, grant_role

    locked = (
        DelegationInvitation.objects.select_for_update().select_related("delegation").get(pk=invitation.pk)
    )
    if not locked.is_usable():
        raise _invalid_invitation()
    delegation = locked.delegation
    competition = delegation.competition
    require_delegations(competition)
    given = {kind: bool(given.get(kind)) for kind in LEADER_CONSENT_KINDS}
    _validate_leader_consents(given, competition)
    if user is not None and getattr(user, "is_authenticated", False):
        if user.email.strip().lower() != locked.email:
            raise DomainError(
                _("To zaproszenie wysłano na inny adres. Wyloguj się i otwórz link jeszcze raz."),
                "EMAIL_MISMATCH",
                status.HTTP_403_FORBIDDEN,
            )
    else:
        if account_exists(locked.email):
            raise _account_exists()
        try:
            # Savepoint: konto o tym adresie mogło powstać między sprawdzeniem a zapisem (inne
            # zaproszenie na ten sam adres przyjęte w tej samej chwili, rejestracja w innym konkursie).
            # Bez niego wyścig kończył się 500 zamiast czytelnej odmowy (poprawka po przeglądzie).
            with transaction.atomic():
                user = _create_user(
                    email=locked.email,
                    password=password,
                    first_name=(first_name or "").strip(),
                    last_name=(last_name or "").strip(),
                    is_active=False,
                )
        except IntegrityError as exc:
            raise _account_exists() from exc
        except DomainError as exc:
            if exc.machine_code == "EMAIL_TAKEN":
                raise _account_exists() from exc
            raise
        user = mark_activated(
            user, actor=user, action="account.delegation_invitation_accepted", request=request
        )
    if _leads_elsewhere(user.email, delegation.edition, delegation):
        raise DomainError(
            _("To konto prowadzi już delegację innego kraju w tej edycji."),
            "LEADS_OTHER_DELEGATION",
            status.HTTP_409_CONFLICT,
        )
    leader = DelegationLeader.objects.filter(delegation=delegation, user=user).first()
    if leader is not None and leader.removed_at is not None:
        # Opiekun odwołany i zaproszony ponownie do tej samej delegacji wraca na swój wiersz – razem
        # z historią zgód; nowe zgody dochodzą niżej jako nowe wpisy dowodowe.
        leader.removed_at = None
        leader.accepted_at = timezone.now()
        leader.save(update_fields=["removed_at", "accepted_at"])
    if leader is None:
        try:
            with transaction.atomic():
                leader = DelegationLeader.objects.create(
                    delegation=delegation,
                    edition=delegation.edition,
                    user=user,
                    invited_by=locked.created_by,
                )
        except IntegrityError as exc:
            raise DomainError(
                _("To konto prowadzi już delegację innego kraju w tej edycji."),
                "LEADS_OTHER_DELEGATION",
                status.HTTP_409_CONFLICT,
            ) from exc
    grant_role(user, CompetitionRole.TEAM_LEADER, competition=competition, granted_by=locked.created_by)
    _record_leader_consents(leader, given, request=request)
    locked.accepted_at = timezone.now()
    locked.accepted_by = user
    locked.save(update_fields=["accepted_at", "accepted_by"])
    audit(user, "delegation.leader_accepted", leader, {"delegation": delegation.pk}, request=request)
    return leader


def leaders_of(delegation: Delegation):
    return (
        DelegationLeader.objects.active()
        .filter(delegation=delegation)
        .select_related("user")
        .order_by("accepted_at", "id")
    )


def invitations_of(delegation: Delegation):
    return DelegationInvitation.objects.filter(delegation=delegation).order_by("-created_at", "-id")


def remove_leader(leader: DelegationLeader, *, actor, request=None) -> None:
    """Odwołuje opiekuna z delegacji. Konto zostaje; rola znika, gdy nie prowadzi już nic w konkursie.

    Uczniowie zostają w delegacji – należą do kraju, nie do opiekuna, który ich zgłosił
    (``registered_by`` jest tylko śladem pochodzenia). Grupa Django ``team_leader`` znika dopiero
    wtedy, gdy konto nie jest opiekunem w **żadnym** konkursie: przy wyłączonym
    ``memberships_enforced`` grupa jest rolą we wszystkich konkursach naraz.

    Wiersz opiekuna **zostaje** ze znacznikiem ``removed_at`` (poprawka po przeglądzie): wiszą na nim
    dowody zgód złożonych przy przyjęciu zaproszenia, a konto – czyli dane, których dotyczą – istnieje
    dalej. Skasowanie wiersza zabrałoby je kaskadą.
    """
    user = leader.user
    competition = leader.delegation.competition
    delegation_id = leader.delegation_id
    audit(actor, "delegation.leader_removed", leader, {"delegation": delegation_id}, request=request)
    with transaction.atomic():
        leader.removed_at = timezone.now()
        leader.save(update_fields=["removed_at"])
        _drop_role_if_unused(user, competition)


def _drop_role_if_unused(user, competition) -> None:
    from django.contrib.auth.models import Group

    if not DelegationLeader.objects.active().filter(user=user, delegation__competition=competition).exists():
        Membership.objects.filter(
            user=user, competition=competition, role=CompetitionRole.TEAM_LEADER
        ).delete()
    if not Membership.objects.filter(user=user, role=CompetitionRole.TEAM_LEADER).exists():
        group = Group.objects.filter(name=GROUP_TEAM_LEADER).first()
        if group is not None:
            user.groups.remove(group)


# --- opiekun drużyny: jego delegacja i uczniowie ------------------------------------------------


def leader_for(user, competition) -> DelegationLeader | None:
    """Wiersz opiekuna tej osoby w **bieżącej** edycji konkursu żądania albo ``None``.

    Jedyna droga od konta do uczniów. Rola sama nie wystarcza (grupa Django bywa globalna,
    a opiekun odwołany z delegacji zachowuje konto) – liczy się wiersz w tej edycji i tym konkursie.
    """
    if not delegations_enabled(competition) or not getattr(user, "is_authenticated", False):
        return None
    if not user.is_active:
        return None
    from apps.competitions.services import current_edition

    edition = current_edition(competition)
    if edition is None:
        return None
    return (
        DelegationLeader.objects.for_competition(competition)
        .active()
        .select_related("delegation", "delegation__country", "delegation__edition", "delegation__competition")
        .filter(user=user, edition=edition)
        .first()
    )


def students_of(delegation: Delegation):
    return (
        Participant.objects.filter(delegation=delegation)
        .select_related("user", "registered_by")
        .order_by("user__last_name", "user__first_name", "id")
    )


def student_of(leader: DelegationLeader, pk: int) -> Participant:
    """Uczeń **tej** delegacji albo 404 – zakres z querysetu (kraj i konkurs), nie z warunku w widoku."""
    participant = (
        Participant.objects.for_competition(leader.delegation.competition)
        .filter(delegation=leader.delegation, pk=pk)
        .select_related("user")
        .first()
    )
    if participant is None:
        raise Http404("Nie ma takiego ucznia w tej delegacji.")
    return participant


def seats_left(delegation: Delegation) -> int:
    return max(0, delegation.max_students - Participant.objects.filter(delegation=delegation).count())


def registration_window_open(delegation: Delegation) -> bool:
    """Okno rejestracji edycji obowiązuje także opiekunów – ten sam przełącznik i te same daty."""
    return delegation.edition.registration_status().is_open


def stage_started(edition) -> bool:
    """Czy którykolwiek etap edycji już się zaczął – od tej chwili składu drużyny nie zmniejsza opiekun."""
    from apps.competitions.models import Stage

    return Stage.objects.filter(edition=edition, opens_at__lte=timezone.now()).exists()


def is_activated(participant: Participant) -> bool:
    return participant.user.email_verified_at is not None


def _require_open(delegation: Delegation) -> None:
    if not delegation.is_open:
        raise DomainError(
            _(
                "Delegacja jest zamknięta – lista uczniów nie może się już zmienić. "
                "Skontaktuj się z organizatorem."
            ),
            "DELEGATION_CLOSED",
            status.HTTP_409_CONFLICT,
        )


def _clean_school(school: str) -> str:
    value = (school or "").strip()[:255]
    if len(value) < MIN_SCHOOL_LENGTH:
        raise DomainError(_("Podaj pełną nazwę szkoły."), "SCHOOL_REQUIRED", status.HTTP_400_BAD_REQUEST)
    return value


def _country_code(region: Region) -> str:
    """Kod ISO kraju do ``Participant.country`` – pusty oznaczałby tam Polskę."""
    code = (region.code or "").upper()
    return code if len(code) == 2 else ""


def _email_taken(email: str) -> bool:
    return User.objects.filter(email=email).exists()


def _student_email_taken() -> DomainError:
    return DomainError(
        _("Ten adres ma już konto w serwisie – podaj inny adres ucznia albo skontaktuj się z organizatorem."),
        "EMAIL_TAKEN",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def add_student(
    leader: DelegationLeader,
    *,
    first_name: str,
    last_name: str,
    email: str,
    birth_date=None,
    school: str,
    grade=None,
    guardian_email: str = "",
    request=None,
) -> Participant:
    """Zgłasza ucznia do delegacji: konto „zaproszone”, profil z krajem delegacji, list z linkiem.

    Kolejność jest ważna. Wiersz delegacji blokujemy **pierwszy** i dopiero pod blokadą liczymy
    miejsca – inaczej dwóch opiekunów jednego kraju przeszłoby warunek limitu jednocześnie.
    Konto ucznia jest nieaktywne i bez hasła: hasło i zgody składa uczeń pod linkiem z listu
    (``/zaproszenie/<token>/``, ta sama droga co import listy klasowej).

    Adres zajęty przez **jakiekolwiek** konto jest odmową, a nie dowiązaniem: dopisanie cudzego,
    działającego konta do delegacji dawałoby opiekunowi wgląd w dane osoby, która się na to nie
    zgodziła. Taki uczeń trafia do drużyny przez organizatora.
    """
    from .services import (
        _require_grade,
        _resolve_birth,
        create_participant_with_public_code,
        grant_role,
        registration_profile,
    )

    delegation = (
        Delegation.objects.select_for_update()
        .select_related("country", "edition")
        .get(pk=leader.delegation_id)
    )
    competition = leader.delegation.competition
    require_delegations(competition)
    _require_open(delegation)
    if not registration_window_open(delegation):
        raise DomainError(
            _("Rejestracja uczniów jest obecnie zamknięta."), "REGISTRATION_CLOSED", status.HTTP_409_CONFLICT
        )
    if Participant.objects.filter(delegation=delegation).count() >= delegation.max_students:
        raise DomainError(
            _("Delegacja ma już komplet uczniów (limit: %(limit)s).") % {"limit": delegation.max_students},
            "DELEGATION_FULL",
            status.HTTP_409_CONFLICT,
        )
    email = _normalized_email(email)
    if _email_taken(email):
        raise _student_email_taken()
    profile = registration_profile(competition)
    birth_date, birth_year = _resolve_birth(birth_date, None, profile=profile)
    grade = _require_grade(grade, profile=profile)
    school = _clean_school(school)

    # Konto „zaproszone”: nieaktywne i bez używalnego hasła – dokładnie jak z importu listy klasowej
    # (``bulk_registration._create_invited_user``). Hasło ustawia uczeń pod linkiem z listu.
    user = User(
        email=email,
        first_name=(first_name or "").strip()[:150],
        last_name=(last_name or "").strip()[:150],
        is_active=False,
    )
    user.set_unusable_password()
    try:
        # Savepoint: ten sam adres zgłoszony w tej samej chwili przez opiekuna innego kraju wpada na
        # unikalność adresu – to ma być ta sama odmowa, co przy adresie zajętym wcześniej, a nie 500.
        with transaction.atomic():
            user.save()
    except IntegrityError as exc:
        raise _student_email_taken() from exc
    grant_role(user, GROUP_PARTICIPANT, competition=competition)
    participant = create_participant_with_public_code(
        user=user,
        competition=competition,
        grade=grade,
        birth_date=birth_date,
        birth_year=birth_year,
        guardian_email=(guardian_email or "").strip().lower(),
        school=school,
        country=_country_code(delegation.country),
        district=delegation.country.code,
        region=delegation.country,
        delegation=delegation,
        registered_by=leader.user,
        invited_at=timezone.now(),
    )
    send_student_invitation(participant, request=request)
    audit(
        leader.user, "delegation.student_added", participant, {"delegation": delegation.pk}, request=request
    )
    return participant


#: Pola profilu i konta, które opiekun może poprawić przed aktywacją ucznia.
EDITABLE_STUDENT_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "birth_date",
    "school",
    "grade",
    "guardian_email",
)


@transaction.atomic
def update_student(
    leader: DelegationLeader, participant: Participant, *, request=None, **fields
) -> Participant:
    """Poprawka danych ucznia – wyłącznie **przed** aktywacją jego konta.

    Po aktywacji dane należą do ucznia (poprawia je sam w profilu): opiekun, który zmieniałby imię
    albo adres działającego konta, mógłby je przejąć resetem hasła. Zmiana adresu przed aktywacją
    wysyła zaproszenie od nowa – token jest związany z adresem, więc stary link przestaje działać.
    """
    from .services import _require_grade, _resolve_birth, registration_profile

    delegation = Delegation.objects.select_for_update().get(pk=leader.delegation_id)
    _require_open(delegation)
    participant = Participant.objects.select_for_update().select_related("user").get(pk=participant.pk)
    if participant.delegation_id != delegation.pk:
        raise Http404("Nie ma takiego ucznia w tej delegacji.")
    user = participant.user
    if is_activated(participant):
        raise DomainError(
            _("Uczeń uruchomił już konto – dane poprawia sam w swoim profilu."),
            "STUDENT_ACTIVE",
            status.HTTP_409_CONFLICT,
        )
    profile = registration_profile(delegation.competition)
    changed: list[str] = []
    email_changed = False
    if "email" in fields:
        email = _normalized_email(fields["email"])
        if email != user.email:
            if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                raise DomainError(
                    _("Ten adres ma już konto w serwisie – podaj inny adres ucznia."),
                    "EMAIL_TAKEN",
                    status.HTTP_409_CONFLICT,
                )
            user.email = email
            email_changed = True
            changed.append("email")
    for name in ("first_name", "last_name"):
        if name in fields:
            value = (fields[name] or "").strip()[:150]
            if value != getattr(user, name):
                setattr(user, name, value)
                changed.append(name)
    user.save(update_fields=["email", "first_name", "last_name"])
    profile_fields: list[str] = []
    if "birth_date" in fields:
        birth_date, birth_year = _resolve_birth(fields["birth_date"], None, profile=profile)
        if birth_date != participant.birth_date or birth_year != participant.birth_year:
            participant.birth_date, participant.birth_year = birth_date, birth_year
            profile_fields += ["birth_date", "birth_year"]
    if "grade" in fields:
        grade = _require_grade(fields["grade"], profile=profile)
        if grade != participant.grade:
            participant.grade = grade
            profile_fields.append("grade")
    if "school" in fields:
        school = _clean_school(fields["school"])
        if school != participant.school:
            participant.school = school
            profile_fields.append("school")
    if "guardian_email" in fields:
        guardian = (fields["guardian_email"] or "").strip().lower()
        if guardian != participant.guardian_email:
            participant.guardian_email = guardian
            profile_fields.append("guardian_email")
    if profile_fields:
        participant.save(update_fields=profile_fields)
    changed += [name for name in profile_fields if name != "birth_year"]
    if email_changed:
        # ``EmailAddress`` allauth nie istnieje jeszcze dla konta zaproszonego, więc nie ma czego
        # przepisywać; nowy list z nowym tokenem idzie na nowy adres.
        send_student_invitation(participant, request=request)
    if changed:
        audit(leader.user, "delegation.student_updated", participant, {"fields": changed}, request=request)
    return participant


def _only_this_profile(participant: Participant) -> bool:
    """Czy konto ucznia nie ma nic poza tym jednym profilem – warunek usunięcia go przez opiekuna."""
    user = participant.user
    if Participant.objects.filter(user=user).exclude(pk=participant.pk).exists():
        return False
    roles = set(Membership.objects.filter(user=user).values_list("role", flat=True))
    if roles - {CompetitionRole.PARTICIPANT.value}:
        return False
    return not user.is_staff and not user.is_superuser


@transaction.atomic
def remove_student(leader: DelegationLeader, participant: Participant, *, request=None) -> str:
    """Wypisuje ucznia z delegacji **przed startem pierwszego etapu**. Zwraca ``deleted``/``unlinked``.

    Dwie drogi, rozstrzygane tym, **czyje** jest konto (poprawka po przeglądzie, decyzja organizatora):

    - konto **nieuruchomione** powstało z tego zgłoszenia i nikt poza opiekunem go nie użył – znika
      razem ze zgłoszeniem, tą samą funkcją, co usunięcie konta przez koordynatora
      (``profile._erase_account``),
    - konto **uruchomione** należy już do ucznia: ustawił hasło i złożył zgody. Opiekun drużyny nie
      może go skasować – wypisanie tylko **odpina** profil od delegacji (zwalnia miejsce w limicie),
      zapisuje, z której delegacji uczeń wypadł (``former_delegation``), i wysyła uczniowi list.
      O dalszym losie konta decyduje koordynator (ekran delegacji pokazuje wypisanych; usunięcie
      idzie zwykłą drogą usuwania konta).

    Konto nieuruchomione, które ma coś więcej niż ten profil (start w innym konkursie, inna rola),
    nie jest kontem „z tego zgłoszenia” – takie usunięcie należy do organizatora.
    """
    from .profile import _erase_account

    delegation = (
        Delegation.objects.select_for_update()
        .select_related("edition", "country")
        .get(pk=leader.delegation_id)
    )
    _require_open(delegation)
    if participant.delegation_id != delegation.pk:
        raise Http404("Nie ma takiego ucznia w tej delegacji.")
    if stage_started(delegation.edition):
        raise DomainError(
            _("Zawody już się rozpoczęły – ucznia może wypisać wyłącznie organizator."),
            "STAGE_STARTED",
            status.HTTP_409_CONFLICT,
        )
    if is_activated(participant):
        participant.delegation = None
        participant.former_delegation = delegation
        participant.delegation_unlinked_at = timezone.now()
        participant.save(update_fields=["delegation", "former_delegation", "delegation_unlinked_at"])
        _send_unlinked_notice(participant, delegation, request=request)
        audit(
            leader.user,
            "delegation.student_unlinked",
            participant,
            {"delegation": delegation.pk},
            request=request,
        )
        return "unlinked"
    if not _only_this_profile(participant):
        raise DomainError(
            _("To konto jest używane także poza tą delegacją – ucznia może wypisać wyłącznie organizator."),
            "ACCOUNT_SHARED",
            status.HTTP_409_CONFLICT,
        )
    audit(
        leader.user,
        "delegation.student_removed",
        participant,
        {"delegation": delegation.pk, "activated": False},
        request=request,
    )
    result = _erase_account(participant.user, actor=leader.user, request=request)
    if result == "anonymised":  # pragma: no cover - konto nieuruchomione nie ma śladu w zawodach
        Participant.objects.filter(pk=participant.pk).update(delegation=None)
    return result


def _send_unlinked_notice(participant: Participant, delegation: Delegation, *, request=None) -> None:
    """List do ucznia wypisanego z drużyny – w jego języku; konto zostaje, decyzja należy do organizatora."""
    competition = participant.competition
    context = {"first_name": participant.user.first_name, "country": delegation.country.name}
    with language_for(participant.user, competition):
        context["brand"] = branding.brand_names(competition)
        subject = render_to_string(UNLINKED_SUBJECT_TEMPLATE, context).strip().replace("\n", " ")
        body = render_to_string(UNLINKED_BODY_TEMPLATE, context)
    queue_mail(subject, body, participant.user.email, competition=competition)


def unlinked_students(delegation: Delegation):
    """Uczniowie z uruchomionym kontem wypisani z tej delegacji – czekają na decyzję koordynatora."""
    return (
        Participant.objects.filter(former_delegation=delegation, delegation__isnull=True)
        .select_related("user")
        .order_by("-delegation_unlinked_at", "id")
    )


def send_student_invitation(participant: Participant, *, request=None) -> None:
    """List do ucznia: „opiekun drużyny Twojego kraju zgłosił Cię” + link do uruchomienia konta.

    Link jest **tym samym** tokenem, co zaproszenie z importu (``bulk_registration.make_invite_token``)
    i prowadzi na ten sam ekran – reguły ważności, wiązania z adresem i jednorazowości są tam.
    """
    from .bulk_registration import INVITE_DAYS, make_invite_token

    competition = participant.competition
    link = absolute_url(
        reverse("web:student-invite", args=[make_invite_token(participant)]), request, competition
    )
    context = {
        "first_name": participant.user.first_name,
        "country": participant.delegation.country.name if participant.delegation_id else "",
        "link": link,
        "days": INVITE_DAYS,
    }
    with language_for(participant.user, competition):
        context["brand"] = branding.brand_names(competition)
        subject = render_to_string(STUDENT_INVITE_SUBJECT_TEMPLATE, context).strip().replace("\n", " ")
        body = render_to_string(STUDENT_INVITE_BODY_TEMPLATE, context)
    queue_mail(subject, body, participant.user.email, competition=competition)
    now = timezone.now()
    Participant.objects.filter(pk=participant.pk).update(invitation_sent_at=now)
    participant.invitation_sent_at = now


def resend_student_invitation(
    leader: DelegationLeader, participant: Participant, *, request=None
) -> Participant:
    """Ponowny list do ucznia, który jeszcze nie uruchomił konta."""
    if participant.delegation_id != leader.delegation_id:
        raise Http404("Nie ma takiego ucznia w tej delegacji.")
    if is_activated(participant):
        raise DomainError(_("Ten uczeń uruchomił już konto."), "STUDENT_ACTIVE", status.HTTP_409_CONFLICT)
    send_student_invitation(participant, request=request)
    audit(leader.user, "delegation.student_invitation_resent", participant, {}, request=request)
    return participant


# --- eksport i RODO -------------------------------------------------------------------------


#: Nagłówek eksportu delegacji. Po polsku – plik otwiera koordynator, a jego ekrany są po polsku.
EXPORT_HEADER = [
    "kraj",
    "kod kraju",
    "stan delegacji",
    "limit",
    "rola",
    "imię",
    "nazwisko",
    "e-mail",
    "kod publiczny",
    "data urodzenia",
    "szkoła",
    "klasa",
    "konto",
]


def export_dataset(competition):
    """Delegacje bieżącej edycji z opiekunami i uczniami – jeden wiersz na osobę.

    Jeden plik, a nie dwa: organizator przygotowuje z niego identyfikatory, listy przyjazdów
    i korespondencję z krajami, i za każdym razem potrzebuje kraju **obok** osoby. Komórki przechodzą
    przez ``apps.core.exports`` (BOM, średnik, apostrof przed „formułą” w nazwisku).
    """
    from apps.core.exports import Dataset

    delegations = list(delegations_of(competition))
    leaders: dict[int, list] = {}
    for leader in DelegationLeader.objects.active().filter(delegation__in=delegations).select_related("user"):
        leaders.setdefault(leader.delegation_id, []).append(leader)
    students: dict[int, list] = {}
    for participant in Participant.objects.filter(delegation__in=delegations).select_related("user"):
        students.setdefault(participant.delegation_id, []).append(participant)
    rows: list[list] = []
    for delegation in delegations:
        base = [
            delegation.country.name,
            delegation.country.code,
            delegation.get_status_display(),
            delegation.max_students,
        ]
        for leader in leaders.get(delegation.pk, []):
            user = leader.user
            rows.append(
                [*base, "opiekun", user.first_name, user.last_name, user.email, "", "", "", "", "aktywne"]
            )
        ordered = sorted(students.get(delegation.pk, []), key=lambda p: (p.user.last_name, p.user.first_name))
        for participant in ordered:
            rows.append(
                [
                    *base,
                    "uczeń",
                    participant.user.first_name,
                    participant.user.last_name,
                    participant.user.email,
                    participant.public_code,
                    participant.birth_date.isoformat() if participant.birth_date else "",
                    participant.school,
                    participant.grade,
                    "aktywne" if is_activated(participant) else "zaproszone",
                ]
            )
    return Dataset(
        header=EXPORT_HEADER, rows=iter(rows), count=len(rows), title="Delegacje", filename="delegacje"
    )


def export_section(user) -> list[dict]:
    """Sekcja eksportu danych konta (art. 20 RODO): delegacje, które ta osoba prowadzi.

    Bez listy uczniów – to cudze dane, nie dane opiekuna (ta sama granica, co u opiekuna szkolnego).
    """
    return [
        {
            "konkurs": leader.delegation.competition.slug,
            "kraj": leader.delegation.country.name,
            "edycja": str(leader.delegation.edition),
            "przyjeto_zaproszenie": timezone.localtime(leader.accepted_at).isoformat(),
            "odwolany": timezone.localtime(leader.removed_at).isoformat() if leader.removed_at else None,
        }
        for leader in DelegationLeader.objects.filter(user=user).select_related(
            "delegation", "delegation__country", "delegation__competition", "delegation__edition"
        )
    ]


def erase_for_user(user) -> None:
    """Usunięcie albo anonimizacja konta: rola opiekuna, wiersze delegacji i zaproszenia na ten adres.

    Zaproszenie niesie adres e-mail opiekuna, czyli jego daną osobową – po usunięciu konta nie ma
    już czemu służyć (ślad „kto i kiedy zaprosił” zostaje w audycie, bez adresu). Dowody zgód
    opiekuna znikają razem z jego wierszem (``ConsentRecord.team_leader`` z ``CASCADE``): to są
    zgody na przetwarzanie danych w roli, której już nie ma.
    """
    email = (user.email or "").strip().lower()
    # Dane pobytu na finale (LOG-01: paszport, zdrowie, zdjęcie) – ucznia i opiekuna. **Przed**
    # kaskadą skasowania konta, bo kaskada bazy nie wie o zdjęciu w storage.
    from apps.delegation_logistics.privacy import erase_for_user as erase_final_logistics

    erase_final_logistics(user)
    DelegationInvitation.objects.filter(Q(accepted_by=user) | Q(email=email)).delete()
    DelegationLeader.objects.filter(user=user).delete()
    Membership.objects.filter(user=user, role=CompetitionRole.TEAM_LEADER).delete()
