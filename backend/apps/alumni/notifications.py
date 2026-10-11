"""Listy mentoringu: prośba, decyzja, zakończenie, zgłoszenie problemu.

Ta sama reguła, co w listach czatu i forum: list mówi **że** i **gdzie**, a nie **co** – bez notatki
prośby, bez powodu zgłoszenia, bez treści. Notatkę i powód czyta się po zalogowaniu. Odbiorca musi
być doręczalny (``DELIVERABLE``, nie po anonimizacji); język – odbiorcy (``language_for``).

Podpis drugiej strony to „Imię N.” (``display_author``) – list bywa przekazywany dalej, a pełne
nazwisko mentora albo mentee nie jest informacją, którą poczta ma roznosić.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import MentorshipStatus

logger = logging.getLogger(__name__)

SUBJECT_REQUESTED = gettext_lazy("Absolwenci: prośba o mentoring")
SUBJECT_ACCEPTED = gettext_lazy("Absolwenci: mentor przyjął Twoją prośbę")
SUBJECT_DECLINED = gettext_lazy("Absolwenci: odpowiedź na prośbę o mentoring")
SUBJECT_ENDED = gettext_lazy("Absolwenci: relacja mentorska zakończona")
SUBJECT_FLAG = gettext_lazy("Absolwenci: zgłoszono problem z relacją mentorską")


def subject(template, competition) -> str:
    """Temat z prefiksem konkursu (wzorzec ``apps.forum.notifications._subject``)."""
    prefix = getattr(competition, "email_subject_prefix", "") or settings.EMAIL_SUBJECT_PREFIX
    return prefix + " ".join(str(template).split())


def deliverable(user_ids):
    from apps.accounts.messaging import DELIVERABLE
    from apps.accounts.models import User

    return (
        User.objects.filter(DELIVERABLE, pk__in=list(user_ids))
        .exclude_anonymised()
        .select_related("preference")
        .order_by("pk")
    )


def _send(user, competition, subject_template, lines_factory, path: str) -> None:
    from apps.accounts.activation import absolute_url, queue_mail, signature_lines
    from apps.accounts.preferences import language_for

    with language_for(user, competition):
        lines = [
            *lines_factory(),
            "",
            absolute_url(path, competition=competition),
            "",
            *signature_lines(competition),
        ]
        title = subject(subject_template, competition)
        body = "\n".join(lines)
    queue_mail(title, body, user.email, competition=competition, essential=False)


def _name(participant) -> str:
    from apps.forum.models import display_author

    return display_author(participant.user)


def mentorship_requested(row) -> None:
    for user in deliverable([row.mentor.user_id]):
        _send(
            user,
            row.competition,
            SUBJECT_REQUESTED,
            lambda: [
                _("%(name)s prosi Cię o mentoring w sieci absolwentów.") % {"name": _name(row.mentee)},
                _("Notatkę prośby przeczytasz i odpowiesz na nią po zalogowaniu:"),
            ],
            reverse("web:alumni"),
        )


def mentorship_decided(row) -> None:
    accepted = row.status == MentorshipStatus.ACCEPTED
    for user in deliverable([row.mentee.user_id]):
        if accepted:
            lines = lambda: [  # noqa: E731 - krótka fabryka zdań w języku odbiorcy
                _("%(name)s zgadza się zostać Twoim mentorem.") % {"name": _name(row.mentor)},
                _("Rozmowa czeka w Wiadomościach:"),
            ]
            path = (
                reverse("web:chat-thread", args=[row.conversation_id])
                if row.conversation_id
                else reverse("web:alumni")
            )
        else:
            lines = lambda: [  # noqa: E731
                _("%(name)s nie może teraz przyjąć Twojej prośby o mentoring.") % {"name": _name(row.mentor)},
                _("Możesz poprosić inną osobę z katalogu absolwentów:"),
            ]
            path = reverse("web:alumni-directory")
        _send(user, row.competition, SUBJECT_ACCEPTED if accepted else SUBJECT_DECLINED, lines, path)


def mentorship_ended(row, *, ended_by) -> None:
    """List do drugiej strony (albo do obu, gdy zakończył organizator)."""
    targets = [row.mentor, row.mentee]
    if ended_by is not None:
        targets = [side for side in targets if side.pk != ended_by.pk]
    for user in deliverable([side.user_id for side in targets]):
        _send(
            user,
            row.competition,
            SUBJECT_ENDED,
            lambda: [
                _("Relacja mentorska w sieci absolwentów została zakończona."),
                _("Rozmowa zostaje w Wiadomościach do odczytu. Szczegóły:"),
            ],
            reverse("web:alumni"),
        )


def flag_raised(item) -> int:
    """Zgłoszenie problemu – do **każdego** koordynatora konkursu, od razu."""
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CompetitionRole, User

    competition = item.mentorship.competition
    coordinators = User.objects.filter(_role_filter(competition, CompetitionRole.COORDINATOR)).values_list(
        "pk", flat=True
    )
    sent = 0
    for user in deliverable(coordinators):
        _send(
            user,
            competition,
            SUBJECT_FLAG,
            lambda: [
                _("Jedna ze stron relacji mentorskiej zgłosiła problem. Powód przeczytasz w panelu:"),
            ],
            reverse("web:coordinator-alumni-mentoring"),
        )
        sent += 1
    return sent
