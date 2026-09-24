"""Listy do uczestnika po decyzji w sprawie zaświadczenia o statusie ucznia.

Dwa listy – „zaakceptowane” i „odrzucone” (koordynator albo skaner antywirusowy) – i żadnego przy
samym wgraniu: potwierdzenie „dostaliśmy Twój plik” uczestnik widzi od razu w panelu, a list
o czymś, co przed chwilą zrobił sam, byłby szumem w skrzynce ucznia w tygodniu, w którym dostaje
już potwierdzenia prac.

Konwencja jest ta sama, co w ``apps.accounts.guardian``: temat przez ``branding.subject`` (wzorzec
z nazwą konkursu przy włączonej marce, dosłowny temat bez niej), podpis przez ``signature_lines``,
wysyłka ``queue_mail`` **po commicie** – decyzja koordynatora, która się wycofa, nie może zostawić
listu, który już wyszedł.

Treść listu nie zawiera danych osobowych poza tymi, które odbiorca i tak zna (adres jest w nagłówku
``To:``). Powód odrzucenia **jest** w treści: pisze go koordynator do uczestnika i to jest
dokładnie ta informacja, na którą uczestnik czeka.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.tenancy import branding

ACCEPTED_SUBJECT = gettext_lazy("Zaświadczenie o statusie ucznia zaakceptowane – Olimpiada Kwantowa")
REJECTED_SUBJECT = gettext_lazy("Zaświadczenie o statusie ucznia odrzucone – Olimpiada Kwantowa")
ACCEPTED_SUBJECT_TEMPLATE = gettext_lazy("Zaświadczenie o statusie ucznia zaakceptowane – %(competition)s")
REJECTED_SUBJECT_TEMPLATE = gettext_lazy("Zaświadczenie o statusie ucznia odrzucone – %(competition)s")


def _panel_link(competition) -> str:
    from apps.accounts.activation import absolute_url

    return absolute_url(reverse("web:student-status"), None, competition)


def accepted_message(certificate, competition=None) -> str:
    from apps.accounts.activation import signature_lines

    return "\n".join(
        [
            _(
                "Twoje zaświadczenie o statusie ucznia (edycja %(edition)s) zostało sprawdzone "
                "i zaakceptowane. Nie musisz nic więcej robić."
            )
            % {"edition": certificate.edition.year_label},
            "",
            _("Stan zaświadczenia widzisz po zalogowaniu w panelu uczestnika:"),
            _panel_link(competition),
            "",
            *signature_lines(competition),
        ]
    )


def rejected_message(certificate, competition=None) -> str:
    from apps.accounts.activation import signature_lines

    return "\n".join(
        [
            _("Twoje zaświadczenie o statusie ucznia (edycja %(edition)s) nie zostało zaakceptowane. Powód:")
            % {"edition": certificate.edition.year_label},
            "",
            certificate.rejection_reason,
            "",
            _(
                "Popraw zaświadczenie i wgraj nowy plik w panelu uczestnika. Brak zaświadczenia "
                "nie blokuje oddawania rozwiązań."
            ),
            _panel_link(competition),
            "",
            *signature_lines(competition),
        ]
    )


def _send(certificate, subject_template, subject_fallback, message) -> None:
    from apps.accounts.activation import queue_mail

    # Konkurs z **edycji**, a nie z kontekstu żądania: decyzję podejmuje koordynator pod domeną
    # konkursu, ale skan antywirusowy kończy się w workerze, gdzie kontekstu żądania nie ma.
    competition = certificate.edition.competition
    queue_mail(
        branding.subject(subject_template, subject_fallback, competition),
        message(certificate, competition),
        certificate.participant.user.email,
        competition=competition,
    )


def notify_accepted(certificate) -> None:
    _send(certificate, ACCEPTED_SUBJECT_TEMPLATE, ACCEPTED_SUBJECT, accepted_message)


def notify_rejected(certificate) -> None:
    _send(certificate, REJECTED_SUBJECT_TEMPLATE, REJECTED_SUBJECT, rejected_message)
