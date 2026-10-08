"""Ogłoszenia organizatora nad skrzynką Wiadomości (zadanie CZ-ANN-01): widoczność i zapisy.

Osobny moduł, a nie kolejne funkcje w ``apps.chat.services``: ogłoszenie nie dotyka rozmów,
moderacji ani szyfrowania, a ``services`` jest już długi i edytowany równolegle. Wspólne są tylko
dwie rzeczy – reguła roli (``services.ensure_organizer``) i renderowanie treści (filtr
``message_body``).

Decyzje:

- **jedna reguła widoczności** (:func:`visible_announcements`) dla skrzynki i pulpitu uczestnika.
  Ogłoszenie czytamy w chwili wyświetlenia, więc konto założone po publikacji widzi je od razu –
  to jest wymaganie organizatora („także tych, co dopiero się zarejestrują”),
- **rola w serwisie, nie tylko w widoku.** Każdy zapis sprawdza, że ``actor`` jest koordynatorem
  konkursu ogłoszenia. ``actor=None`` jest dozwolone wyłącznie dla wywołań spoza żądania
  (``manage.py shell``) – w audycie zostaje wtedy wpis bez aktora, czyli „decyzja operatora”,
- **audyt bez treści.** W ``diff`` tytuł i stan; treść ogłoszenia jest jawna dla wszystkich
  uczestników, ale wpis audytowy nie jest jej drugą kopią (ta sama reguła, co przy banerze
  ``cms.Announcement``). Nazwy zdarzeń mają przedrostek ``chat.`` jak reszta modułu – nazwy
  ``announcement.*`` zajmuje już baner.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.context import competition_context

from . import services
from .models import ANNOUNCEMENT_BODY_LENGTH, ANNOUNCEMENT_TITLE_LENGTH, OrganizerAnnouncement

AUDIT_CREATED = "chat.announcement.created"
AUDIT_UPDATED = "chat.announcement.updated"
AUDIT_PUBLISHED = "chat.announcement.published"
AUDIT_UNPUBLISHED = "chat.announcement.unpublished"
AUDIT_DELETED = "chat.announcement.deleted"

#: Ile ogłoszeń naraz widzi uczestnik. Kilka to komunikacja; kilkanaście to tablica, której nikt nie
#: czyta – i blok, który spycha listę rozmów poza pierwszy ekran telefonu.
VISIBLE_LIMIT = 10


def visible_q(now=None) -> Q:
    """Warunek „uczestnik widzi to teraz”: opublikowane i w swoim oknie (puste końce = bez granicy)."""
    now = now or timezone.now()
    return (
        Q(is_published=True)
        & (Q(published_from__isnull=True) | Q(published_from__lte=now))
        & (Q(published_until__isnull=True) | Q(published_until__gt=now))
    )


def visible_announcements(competition, now=None) -> list[OrganizerAnnouncement]:
    """Ogłoszenia widoczne teraz w tym konkursie – od najnowszego, najwyżej :data:`VISIBLE_LIMIT`.

    Kolejność po :attr:`OrganizerAnnouncement.shown_since` liczona w Pythonie: to „późniejsza
    z dwóch dat”, a widocznych wierszy jest kilka – wyrażenie ``GREATEST`` w SQL zależałoby od tego,
    jak baza traktuje ``NULL``, bez żadnego zysku. Jedno zapytanie po indeksie
    ``(competition, is_published)``.
    """
    if competition is None:
        return []
    rows = list(OrganizerAnnouncement.objects.for_competition(competition).filter(visible_q(now)))
    rows.sort(key=lambda row: (row.shown_since, row.pk), reverse=True)
    return rows[:VISIBLE_LIMIT]


def announcements_for_panel(competition):
    """Wszystkie ogłoszenia konkursu dla panelu koordynatora (szkice, zaplanowane i wygasłe też)."""
    return OrganizerAnnouncement.objects.for_competition(competition).select_related("created_by")


# --- zapisy -------------------------------------------------------------------------------------------


def _ensure_actor(actor, competition) -> None:
    """Koordynator tego konkursu – albo ``None`` (powłoka operatora, patrz docstring modułu)."""
    if actor is not None:
        services.ensure_organizer(actor, competition)


def _clean(title: str, body: str, published_from, published_until) -> tuple[str, str]:
    """Reguły treści w serwisie, nie tylko w formularzu – powłoka formularza nie ma."""
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise DomainError(
            "Ogłoszenie musi mieć tytuł i treść.", "ANNOUNCEMENT_EMPTY", http.HTTP_400_BAD_REQUEST
        )
    if len(title) > ANNOUNCEMENT_TITLE_LENGTH or len(body) > ANNOUNCEMENT_BODY_LENGTH:
        raise DomainError("Ogłoszenie jest za długie.", "ANNOUNCEMENT_TOO_LONG", http.HTTP_400_BAD_REQUEST)
    if published_from is not None and published_until is not None and published_until <= published_from:
        raise DomainError(
            "Koniec widoczności musi być późniejszy niż jej początek.",
            "ANNOUNCEMENT_WINDOW",
            http.HTTP_400_BAD_REQUEST,
        )
    return title, body


def _audit(actor, action: str, announcement: OrganizerAnnouncement, request=None) -> None:
    # Konkurs wpisu z kontekstu: w żądaniu ustawia go middleware, w powłoce – ``competition_context``.
    with competition_context(announcement.competition):
        audit(
            actor,
            action,
            announcement,
            {
                "title": announcement.title,
                "is_published": announcement.is_published,
                "published_from": announcement.published_from.isoformat()
                if announcement.published_from
                else None,
                "published_until": (
                    announcement.published_until.isoformat() if announcement.published_until else None
                ),
            },
            request=request,
        )


@transaction.atomic
def create_announcement(
    *,
    competition,
    title: str,
    body: str,
    actor,
    publish: bool = False,
    published_from=None,
    published_until=None,
    request=None,
) -> OrganizerAnnouncement:
    """Nowe ogłoszenie – szkic, a przy ``publish=True`` od razu opublikowane (dwa wpisy w audycie)."""
    _ensure_actor(actor, competition)
    title, body = _clean(title, body, published_from, published_until)
    now = timezone.now()
    announcement = OrganizerAnnouncement.objects.create(
        competition=competition,
        title=title,
        body=body,
        published_from=published_from,
        published_until=published_until,
        created_by=actor,
        created_at=now,
        updated_at=now,
    )
    _audit(actor, AUDIT_CREATED, announcement, request)
    if publish:
        set_published(announcement=announcement, actor=actor, published=True, request=request)
    return announcement


@transaction.atomic
def update_announcement(
    *,
    announcement: OrganizerAnnouncement,
    actor,
    title: str,
    body: str,
    published_from=None,
    published_until=None,
    request=None,
) -> OrganizerAnnouncement:
    """Zmiana treści i okna. Stanu publikacji nie rusza – do tego jest :func:`set_published`."""
    _ensure_actor(actor, announcement.competition)
    announcement.title, announcement.body = _clean(title, body, published_from, published_until)
    announcement.published_from = published_from
    announcement.published_until = published_until
    announcement.updated_at = timezone.now()
    announcement.save(update_fields=["title", "body", "published_from", "published_until", "updated_at"])
    _audit(actor, AUDIT_UPDATED, announcement, request)
    return announcement


@transaction.atomic
def set_published(
    *, announcement: OrganizerAnnouncement, actor, published: bool, request=None
) -> OrganizerAnnouncement:
    """„Opublikuj” / „Wyłącz”. Powtórzenie tej samej decyzji niczego nie zmienia i nie trafia do audytu.

    Publikacja przesuwa ``published_at`` na teraz – ogłoszenie wraca na górę listy uczestnika,
    bo dla niego pojawia się właśnie w tej chwili.
    """
    _ensure_actor(actor, announcement.competition)
    if announcement.is_published == published:
        return announcement
    now = timezone.now()
    announcement.is_published = published
    if published:
        announcement.published_at = now
    announcement.updated_at = now
    announcement.save(update_fields=["is_published", "published_at", "updated_at"])
    _audit(actor, AUDIT_PUBLISHED if published else AUDIT_UNPUBLISHED, announcement, request)
    return announcement


@transaction.atomic
def delete_announcement(*, announcement: OrganizerAnnouncement, actor, request=None) -> None:
    """Twarde usunięcie. Audyt **przed** ``delete()`` – potem nie ma z czego wziąć identyfikatora."""
    _ensure_actor(actor, announcement.competition)
    _audit(actor, AUDIT_DELETED, announcement, request)
    announcement.delete()


def publish_announcement(*, competition, title: str, body: str, actor) -> OrganizerAnnouncement:
    """Utwórz i od razu opublikuj – skrót dla operatora w ``manage.py shell``.

    ``actor`` to koordynator tego konkursu albo ``None`` (wpis audytu bez aktora). Przykład
    w ``docs/tasks/CZ-ANN-01.md`` § 8.
    """
    return create_announcement(competition=competition, title=title, body=body, actor=actor, publish=True)
