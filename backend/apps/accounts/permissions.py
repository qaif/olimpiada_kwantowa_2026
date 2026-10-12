"""Klasy uprawnień DRF dla ról RBAC.

Każdy widok w projekcie deklaruje uprawnienia jawnie – nie ma widoków bez ``permission_classes``.
Rola = odpowiedź ``apps.accounts.services.has_role``; dla recenzenta dodatkowo wymagany jest
status ACTIVE profilu.

Klasy są **cienkimi opakowaniami** i mają takie zostać: cała reguła („grupa Django czy wiersz
``Membership``”) mieszka w ``has_role``, a tutaj jest wyłącznie dobranie konkursu i połączenie
roli z profilem. Dzięki temu widoki DRF nie zmieniają deklaracji ``permission_classes`` ani przy
przełączeniu flagi ``memberships_enforced``, ani przy żadnej późniejszej zmianie reguły.

Konkurs bierzemy z żądania, które ustawia ``apps.tenancy.middleware``. Żądanie **bez konkursu**
(host nieaktywnego konkursu, alias bez tłumaczenia, wywołanie bez warstwy) nie przechodzi żadnej
z tych klas – tak samo jak ``apps.tenancy.permissions.IsCompetitionCoordinator`` (audyt 10.10.2026,
S13). Dawniej ``has_role`` schodził wtedy do globalnych grup Django, więc koordynator, recenzent
i komisja **dowolnego** konkursu mieli swoją rolę pod domeną, która żadnego konkursu nie wskazuje –
razem z dostępem do danych wszystkich konkursów. Panele HTML zamyka w tym samym przypadku 404
w ``apps.web.mixins.RoleRequiredMixin``; tutaj odpowiedzią jest zwykłe 403 uprawnienia DRF.
"""

from rest_framework.permissions import BasePermission

from .models import (
    CommitteeStatus,
    CompetitionRole,
)


def _competition(request):
    """Konkurs żądania albo ``None``.

    ``rest_framework.request.Request`` deleguje nieznane atrybuty do opakowanego ``HttpRequest``,
    więc to jest ten sam obiekt, który widzi widok HTML – jedna warstwa, jedno rozstrzygnięcie.
    """
    return getattr(request, "competition", None)


def _has_role(request, role: str) -> bool:
    """Rola w konkursie żądania; bez konkursu – nigdy (docstring modułu, S13)."""
    competition = _competition(request)
    if competition is None:
        return False
    # Import lokalny: ``services`` importuje pośrednio ten moduł przez warstwę API.
    from .services import has_role

    return has_role(request.user, competition, role)


def _active_committee_member(user, competition):
    """Profil komitetu użytkownika **w tym konkursie**, o ile istnieje i jest aktywny.

    Konkurs profilu sprawdza ``committee_profile_in`` – ta sama funkcja, co w bramce recenzenta
    i w widoczności prac komisji (``Submission.objects.for_user``). Bez tego członek komisji
    konkursu B przechodził tę bramkę pod adresem konkursu A, bo przy wyłączonym
    ``memberships_enforced`` grupa ``appeals`` jest globalna.
    """
    from .services import committee_profile_in

    member = committee_profile_in(user, competition)
    if member is None or member.status != CommitteeStatus.ACTIVE:
        return None
    return member


class IsParticipant(BasePermission):
    """Zalogowany uczestnik: rola ``participant`` w tym konkursie **i** profil w tym konkursie."""

    message = "Wymagana rola uczestnika."

    def has_permission(self, request, view) -> bool:
        from .services import participant_for

        if not _has_role(request, CompetitionRole.PARTICIPANT):
            return False
        # Sama rola nie wystarcza i nigdy nie wystarczała: endpointy uczestnika czytają profil
        # (szkoła, kod publiczny, zgody), więc konto z rolą, ale bez profilu dostałoby 500
        # zamiast 403. Profil bierzemy **z tego konkursu** – po wydaniu D profil z konkursu B
        # przestanie tu cokolwiek otwierać, a wołanie jest już dziś tym docelowym.
        return participant_for(request.user, _competition(request)) is not None


class IsActiveReviewer(BasePermission):
    """Recenzent w grupie ``reviewer`` z profilem ``CommitteeMember`` w statusie ACTIVE.

    Recenzent PENDING lub SUSPENDED dostaje 403 – sama grupa nie wystarcza. Regułę rozstrzyga
    ``apps.accounts.services.active_reviewer_profile``: ta sama funkcja decyduje o widoczności
    rozwiązań (``Submission.objects.for_user``) i o przydziałach, więc uprawnienie i widoczność
    nie mogą się rozjechać.
    """

    message = "Wymagany aktywny recenzent."

    def has_permission(self, request, view) -> bool:
        competition = _competition(request)
        if competition is None:
            # Bez konkursu nie ma „recenzenta tutaj” – patrz docstring modułu (S13).
            return False
        # Import lokalny: ``services`` importuje ``permissions`` pośrednio przez warstwę API.
        from .services import active_reviewer_profile

        return active_reviewer_profile(request.user, competition) is not None


class IsAppealsCommittee(BasePermission):
    """Komisja odwoławcza: rola ``appeals`` + aktywny profil z ``is_appeals_committee``."""

    message = "Wymagana rola komisji odwoławczej."

    def has_permission(self, request, view) -> bool:
        if not _has_role(request, CompetitionRole.APPEALS):
            return False
        member = _active_committee_member(request.user, _competition(request))
        return member is not None and member.is_appeals_committee


class IsCoordinator(BasePermission):
    """Koordynator konkursu z żądania. Bez cichej eskalacji dla superusera.

    Superużytkownik jest operatorem platformy i ma własne narzędzie (``/admin/``). Eskalacja
    zrobiłaby z każdego konta serwisowego konto z wglądem w dane uczestników – a konto serwisowe
    zakłada się po to, żeby coś wdrożyć, a nie po to, żeby czytać czyjeś prace.
    """

    message = "Wymagana rola koordynatora."

    def has_permission(self, request, view) -> bool:
        return _has_role(request, CompetitionRole.COORDINATOR)
