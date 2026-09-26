"""System checki djcms.

``dj_pages.W001`` – opublikowana strona pod adresem, który należy do aplikacji (reguła 5 z § 7
docs/tasks/DJ-01.md, S5 docs/tasks/DJ-02.md): pierwszy segment zarezerwowany albo ścieżka pasująca
do tras aplikacji głównej z kontraktu (``apps.pages.validation.path_collides_with_app``). Caddy
wysyła taki adres do ``web``, więc strona jest **niewidoczna** – redaktor ją publikuje, a pod
adresem jest aplikacja. Formularze django CMS odrzucają taki adres już przy zapisie; ten check
łapie strony sprzed reguły, z importu i z ręcznych zmian w bazie – w każdej witrynie.

Check pyta bazę, więc jest oznaczony tagiem ``database`` i działa wtedy, gdy Django przekaże
listę baz: przy ``migrate`` (entrypoint kontenera – czyli przy każdym starcie) i przy
``manage.py check --database default``. Zwykły ``manage.py check`` (także ``collectstatic``
w czasie budowania obrazu, bez bazy) go pomija.
"""

from __future__ import annotations

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError

from .validation import path_collides_with_app


@register(Tags.database)
def reserved_slug_pages(app_configs=None, databases=None, **kwargs):
    if not databases:
        return []
    from cms.models import PageContent, PageUrl

    try:
        # ``path``, a nie ``slug`` + głębokość: przesłonięty jest **adres** – dziecko strony
        # głównej (której slug nie wchodzi do adresu dzieci) ma ``path`` równy swojemu slugowi.
        urls = [
            (page_id, site_id, path, reason)
            for page_id, site_id, path in PageUrl.objects.values_list("page_id", "page__site_id", "path")
            if (reason := path_collides_with_app(path))
        ]
        if not urls:
            return []
        # ``PageContent.objects`` przy djangocms-versioning zwraca wyłącznie wersje opublikowane.
        published = set(
            PageContent.objects.filter(page_id__in=[page_id for page_id, *_ in urls]).values_list(
                "page_id", flat=True
            )
        )
    except DatabaseError:
        # Baza bez migracji (pierwszy ``migrate``) – nie ma jeszcze czego sprawdzać.
        return []
    return [
        Warning(
            f"Opublikowana strona (id={page_id}, witryna {site_id}) ma adres /{path}/: {reason}.",
            hint=(
                "Caddy kieruje ten adres do aplikacji głównej, więc strona jest niewidoczna. "
                "Zmień jej slug (albo slug rodzica) w panelu djcms."
            ),
            obj=f"page:{page_id}",
            id="dj_pages.W001",
        )
        for page_id, site_id, path, reason in sorted(urls)
        if page_id in published
    ]
