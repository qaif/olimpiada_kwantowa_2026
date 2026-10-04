"""Odnośnik „Treść w języku: …” na karcie zadania ucznia (TR-01 § 2).

Znacznik wstawiony w ``web/participant/_problem_card.html`` **w tej samej linii**, co odnośnik do
wersji oficjalnej: w konkursie bez delegacji (Olimpiada Kwantowa) zwraca pusty napis bez jednego
zapytania, więc karta zadania zostaje co do bajtu taka, jak była.
"""

from __future__ import annotations

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _

from .. import languages

register = template.Library()


@register.simple_tag(takes_context=True)
def student_translation_link(context, problem) -> str:
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if request is None or competition is None or not competition.uses_delegations:
        return ""
    if not request.user.is_authenticated:
        return ""
    from apps.accounts.services import participant_for

    from ..services import approved_for_student

    revision = approved_for_student(participant_for(request.user, competition), problem)
    if revision is None:
        return ""
    language = revision.translation.language
    return format_html(
        ' <a class="btn btn--primary btn--small" lang="{}" href="{}">{}</a>',
        language,
        reverse("web:student-translation", args=[problem.pk]),
        _("Treść w języku: %(language)s") % {"language": languages.native_name(language)},
    )
