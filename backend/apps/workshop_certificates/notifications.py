"""List „Twoje zaświadczenie z warsztatów jest gotowe” – wyłącznie dla dokumentów wystawionych przez regułę.

Konwencja ta sama, co w ``apps.student_status.notifications``: temat przez ``branding.subject``
(wzorzec z nazwą konkursu przy włączonej marce, dosłowny temat bez niej), podpis przez
``signature_lines``, język **odbiorcy** (``language_for`` – list wychodzi z workera, a nie z żądania
uczestnika), wysyłka ``queue_mail``.

List jest **nieobowiązkowy** (``essential=False``, MAIL-02): dokument i tak czeka w panelu, a adres,
który twardo odbił, nie ma dostawać kolejnych powiadomień. Treść nie niesie danych osobowych poza
edycją i linkiem do panelu – numer i kod dokumentu uczestnik zobaczy po zalogowaniu.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.tenancy import branding

READY_SUBJECT = gettext_lazy("Zaświadczenie z warsztatów jest gotowe – Olimpiada Kwantowa")
READY_SUBJECT_TEMPLATE = gettext_lazy("Zaświadczenie z warsztatów jest gotowe – %(competition)s")


def ready_message(certificate, competition=None) -> str:
    from apps.accounts.activation import absolute_url, signature_lines

    return "\n".join(
        [
            _(
                "Wystawiliśmy Ci zaświadczenie o udziale w warsztatach (edycja %(edition)s). "
                "Dokument w PDF-ie pobierzesz po zalogowaniu, w zakładce „Dyplomy”:"
            )
            % {"edition": certificate.edition.year_label},
            absolute_url(reverse("web:participant-certificates"), None, competition),
            "",
            _("Na zaświadczeniu są wymienione tematy i terminy zajęć, na których byłeś(-aś)."),
            "",
            *signature_lines(competition),
        ]
    )


def notify_ready(certificate) -> bool:
    """Kolejkuje list do odbiorcy dokumentu. ``False``, gdy list do tego dokumentu już wyszedł.

    Znacznik ``notified_at`` stawiamy **przed** kolejkowaniem: zadanie dostarczone dwa razy nie może
    wysłać dwóch listów, a utrata jednego powiadomienia (dokument i tak jest w panelu) jest mniejszym
    złem niż duplikat w skrzynce ucznia.
    """
    from apps.accounts.activation import queue_mail
    from apps.accounts.preferences import language_for

    from .rules import mark_notified

    if not mark_notified(certificate):
        return False
    competition = certificate.edition.competition
    user = certificate.entry.participant.user
    with language_for(user, competition):
        subject = str(branding.subject(READY_SUBJECT_TEMPLATE, READY_SUBJECT, competition))
        body = ready_message(certificate, competition)
    queue_mail(subject, body, user.email, competition=competition, essential=False)
    return True
