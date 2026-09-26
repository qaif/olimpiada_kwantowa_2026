"""``dj_pages.W003`` – odnośnik aplikacji głównej do strony, której w djcms nie ma (S16, DJ-02 § 7).

Aplikacja główna linkuje do stron konkursu z miejsc poza treścią redakcyjną: zgody przy rejestracji
(regulamin, RODO – ``apps.accounts.consents.document_url``), strona warsztatów, materiały. Lista tych
ścieżek przychodzi z API (``linked_paths`` w ``GET competitions``, rejestr ``apps.sites``). Po
przełączeniu serwisu na djcms każda z nich musi prowadzić do opublikowanej strony witryny konkursu
– inaczej uczestnik klika „regulamin” w formularzu rejestracji i dostaje 404.

Identyfikator ``dj_pages.W003`` (z numeracji checków stron, DJ-02 § 9.2), a kod w aplikacji importu:
sprawdzenie dzieli logikę z ``verify_cutover`` (``apps.importer.verification``). Tag ``database``
– jak ``dj_pages.W001``: działa przy ``migrate`` i ``manage.py check --database default``.
"""

from __future__ import annotations

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError


@register(Tags.database)
def linked_paths_published(app_configs=None, databases=None, **kwargs):
    if not databases:
        return []
    from apps.sites.models import CompetitionSite

    from .verification import missing_linked_paths

    warnings = []
    try:
        for competition in CompetitionSite.objects.filter(is_active=True).select_related("site"):
            for path in missing_linked_paths(competition):
                warnings.append(
                    Warning(
                        f"Konkurs „{competition.slug}”: aplikacja główna linkuje do {path}, a w witrynie "
                        "djcms nie ma tam opublikowanej strony.",
                        hint=(
                            "Opublikuj stronę pod tą ścieżką (albo przywróć jej slug) – inaczej odnośnik "
                            "z aplikacji (zgody, warsztaty) prowadzi na 404."
                        ),
                        obj=f"competition:{competition.slug}",
                        id="dj_pages.W003",
                    )
                )
    except DatabaseError:
        return []  # baza bez migracji – nie ma czego sprawdzać
    return warnings
