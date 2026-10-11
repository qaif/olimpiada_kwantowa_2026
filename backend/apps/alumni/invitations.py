"""Zaproszenia dla absolwentów: warsztaty, webinary, jury (ALUM-01 § 6).

**Haczyk dla innych modułów.** :func:`send_invitation` przyjmuje gotowy adres wydarzenia, więc
moduł webinarów (budowany równolegle) może zaprosić absolwentów jednym wywołaniem, a ta aplikacja
nie importuje z niego niczego – zależność idzie w jedną stronę.

**Odbiorcy** wyłącznie z sieci (zgoda), z niewyłączonymi zaproszeniami, nieukryci, z kontem
doręczalnym. Ramka listu (powitanie, zdanie „dlaczego to dostajesz”, wypis, stopka) jest w języku
**odbiorcy**; tytuł i treść pisze koordynator i jadą tak, jak je napisał.

**Wypis** bez logowania: podpisany token (``django.core.signing``, osobna sól) i nagłówki
``List-Unsubscribe``/``List-Unsubscribe-Post`` (RFC 8058) – wzorzec ``apps.forum.notifications``.
Token nie wygasa (link wypisu, który przestaje działać, jest tym, co ludzie zgłaszają jako spam)
i niczego poza wypisem nie umożliwia. ``GET`` pokazuje stronę z przyciskiem, wypisuje ``POST``.
"""

from __future__ import annotations

from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.models import audit

from .achievements import achievements_for
from .models import (
    LEVEL_RANK,
    MAX_INVITATION_BODY,
    AlumniInvitation,
    AlumniProfile,
    Interest,
    InvitationKind,
    Level,
)
from .services import _alive, bad_request, ensure_coordinator, ensure_enabled

UNSUBSCRIBE_SALT = "apps.alumni.invitations.unsubscribe"
AUDIT_SENT = "alumni.invitation_sent"


def normalise_filters(filters: dict | None) -> dict:
    """Filtry w postaci kanonicznej (to samo trafia do zapytania, do zapisu i do audytu)."""
    filters = filters or {}
    level = filters.get("min_level") or ""
    return {
        "editions": sorted({int(pk) for pk in filters.get("editions") or [] if str(pk).isdigit()}),
        "min_level": level if level in Level.values else "",
        "interests": sorted({item for item in filters.get("interests") or [] if item in Interest.values}),
        "mentors_only": bool(filters.get("mentors_only")),
    }


def audience(competition, filters: dict | None = None) -> list[AlumniProfile]:
    """Profile, do których pójdzie zaproszenie z tymi filtrami.

    Edycje i poziom liczone na **osiągnięciach** (ogłoszone wyniki), a nie na deklaracjach profilu:
    „zaproś laureatów edycji 2025/2026” ma znaczyć dokładnie to, co mówi tabela finału.
    """
    from django.db.models import Q

    from .services import _with_role

    wanted = normalise_filters(filters)
    rows = AlumniProfile.objects.for_competition(competition).filter(invitations=True)
    rows = _with_role(_alive(rows), competition).filter(participant__user__in=_deliverable_users())
    if wanted["mentors_only"]:
        rows = rows.filter(mentor_available=True)
    if wanted["interests"]:
        match = Q()
        for interest in wanted["interests"]:
            match |= Q(interests__contains=[interest]) | Q(mentor_topics__contains=[interest])
        rows = rows.filter(match)
    profiles = list(rows.select_related("participant__user", "participant__user__preference").order_by("pk"))
    if not wanted["editions"] and not wanted["min_level"]:
        return profiles
    found = achievements_for([profile.participant for profile in profiles])
    minimum = LEVEL_RANK[Level(wanted["min_level"])] if wanted["min_level"] else 0
    editions = set(wanted["editions"])
    chosen = []
    for profile in profiles:
        items = found.get(profile.participant_id, [])
        if editions:
            items = [item for item in items if item.edition_id in editions]
        if any(item.rank >= minimum for item in items):
            chosen.append(profile)
    return chosen


def _deliverable_users():
    from apps.accounts.anonymised import anonymised_q
    from apps.accounts.messaging import DELIVERABLE
    from apps.accounts.models import User

    return User.objects.filter(DELIVERABLE).exclude(anonymised_q()).values("pk")


# --- wypis ----------------------------------------------------------------------------------------------


def unsubscribe_token(profile: AlumniProfile) -> str:
    return signing.dumps({"p": profile.pk, "t": profile.token}, salt=UNSUBSCRIBE_SALT)


def unsubscribe_url(profile: AlumniProfile, competition) -> str:
    from apps.accounts.activation import absolute_url

    return absolute_url(
        reverse("web:alumni-unsubscribe", args=[unsubscribe_token(profile)]), competition=competition
    )


def read_token(token: str) -> tuple[int, str]:
    """``(pk profilu, token profilu)`` albo ``signing.BadSignature``."""
    data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    if not isinstance(data, dict) or not isinstance(data.get("p"), int) or not isinstance(data.get("t"), str):
        raise signing.BadSignature("Nieprawidłowa treść tokenu.")
    return data["p"], data["t"]


def apply_unsubscribe(competition, token: str) -> bool:
    """Wypis. Profil, którego już nie ma (zgoda wycofana), nie jest błędem – wypis jest prawdą.

    Para (pk, token profilu): po wycofaniu zgody i ponownym dołączeniu profil ma nowy token, więc
    stary link nie wypisuje nowego profilu – to byłaby decyzja podjęta za kogoś pod inną zgodą.
    """
    pk, profile_token = read_token(token)
    updated = (
        AlumniProfile.objects.for_competition(competition)
        .filter(pk=pk, token=profile_token, invitations=True)
        .update(invitations=False)
    )
    return bool(updated)


def unsubscribe_headers(url: str) -> dict[str, str]:
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


# --- wysyłka --------------------------------------------------------------------------------------------


def _clean(kind: str, title: str, body: str, url: str) -> tuple[str, str, str, str]:
    from django.core.exceptions import ValidationError

    from .validators import clean_event_url

    if kind not in InvitationKind.values:
        raise bad_request(_("Wybierz rodzaj zaproszenia."), "ALUMNI_INVITATION_KIND")
    title = " ".join((title or "").split())
    body = (body or "").strip()
    if not title or len(title) > 150:
        raise bad_request(_("Tytuł jest wymagany (do 150 znaków)."), "ALUMNI_INVITATION_TITLE")
    if not body or len(body) > MAX_INVITATION_BODY:
        raise bad_request(
            _("Treść jest wymagana (do %(limit)s znaków).") % {"limit": MAX_INVITATION_BODY},
            "ALUMNI_INVITATION_BODY",
        )
    try:
        url = clean_event_url(url)
    except ValidationError as exc:
        raise bad_request(exc.messages[0], "ALUMNI_INVITATION_URL") from exc
    return kind, title, body, url


def _mail(profile: AlumniProfile, competition, invitation: AlumniInvitation) -> None:
    from apps.accounts.activation import queue_mail, signature_lines
    from apps.accounts.preferences import language_for
    from apps.tenancy import branding

    from .notifications import subject

    user = profile.participant.user
    url = unsubscribe_url(profile, competition)
    with language_for(user, competition):
        lines = [
            _("Dzień dobry,"),
            "",
            _("organizator konkursu %(competition)s zaprasza absolwentów: %(kind)s.")
            % {"competition": branding.competition_name(competition), "kind": invitation.get_kind_display()},
            "",
            invitation.title,
            "",
            invitation.body,
        ]
        if invitation.url:
            lines += ["", _("Szczegóły i zapisy: %(url)s") % {"url": invitation.url}]
        lines += [
            "",
            _("Dostajesz ten list, bo należysz do sieci absolwentów i nie wyłączyłeś zaproszeń."),
            _("Wypisz się z zaproszeń: %(link)s") % {"link": url},
            "",
            *signature_lines(competition),
        ]
        title = subject(invitation.title, competition)
        body = "\n".join(lines)
    queue_mail(
        title, body, user.email, competition=competition, headers=unsubscribe_headers(url), essential=False
    )


@transaction.atomic
def send_invitation(
    *,
    competition,
    actor,
    kind: str,
    title: str,
    body: str,
    url: str = "",
    filters: dict | None = None,
    request=None,
) -> AlumniInvitation:
    """Wysyła zaproszenie do odbiorców z filtrów. Zapisuje **liczbę** odbiorców, nie ich listę."""
    ensure_enabled(competition)
    ensure_coordinator(actor, competition)
    kind, title, body, url = _clean(kind, title, body, url)
    wanted = normalise_filters(filters)
    recipients = audience(competition, wanted)
    if not recipients:
        raise bad_request(_("Żaden absolwent nie spełnia tych filtrów."), "ALUMNI_NO_RECIPIENTS")
    invitation = AlumniInvitation.objects.create(
        competition=competition,
        kind=kind,
        title=title,
        body=body,
        url=url,
        filters=wanted,
        recipients=len(recipients),
        created_by=actor,
    )
    for profile in recipients:
        _mail(profile, competition, invitation)
    audit(
        actor,
        AUDIT_SENT,
        invitation,
        {"kind": kind, "recipients": len(recipients), "filters": wanted},
        request=request,
    )
    return invitation


def history(competition):
    return AlumniInvitation.objects.for_competition(competition).select_related("created_by")
