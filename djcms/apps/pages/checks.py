"""System checki djcms.

``dj_pages.W001`` – opublikowana strona pod adresem zarezerwowanym dla aplikacji (reguła 5 z § 7
docs/tasks/DJ-01.md). ``config/urls.py`` i tak stawia adresy aplikacji przed ``cms.urls``, więc
taka strona niczego nie przesłoni – ale jest **niewidoczna** (redaktor ją publikuje, a pod
adresem jest panel albo healthcheck), a to chcemy zobaczyć w logu, nie w zgłoszeniu od redakcji.
Import odrzuca takie slugi sam; ten check łapie strony utworzone ręcznie w panelu.

Check pyta bazę, więc jest oznaczony tagiem ``database`` i działa wtedy, gdy Django przekaże
listę baz: przy ``migrate`` (entrypoint kontenera – czyli przy każdym starcie) i przy
``manage.py check --database default``. Zwykły ``manage.py check`` (także ``collectstatic``
w czasie budowania obrazu, bez bazy) go pomija.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.db import DatabaseError


@register(Tags.database)
def reserved_slug_pages(app_configs=None, databases=None, **kwargs):
    if not databases:
        return []
    from cms.models import PageContent, PageUrl

    reserved = sorted(settings.DJ_RESERVED_SLUGS)
    try:
        # ``path``, a nie ``slug`` + głębokość: przesłonięty jest **adres** – strona korzenia
        # o slugu ``admin`` ma ``path == "admin"``, a dziecko strony głównej (której slug nie
        # wchodzi do adresu dzieci) też może mieć taki ``path``.
        urls = list(PageUrl.objects.filter(path__in=reserved).values_list("page_id", "path"))
        if not urls:
            return []
        # ``PageContent.objects`` przy djangocms-versioning zwraca wyłącznie wersje opublikowane.
        published = set(
            PageContent.objects.filter(page_id__in=[page_id for page_id, _ in urls]).values_list(
                "page_id", flat=True
            )
        )
    except DatabaseError:
        # Baza bez migracji (pierwszy ``migrate``) – nie ma jeszcze czego sprawdzać.
        return []
    return [
        Warning(
            f"Opublikowana strona (id={page_id}) ma adres /{path}/ zarezerwowany dla aplikacji djcms.",
            hint=(
                "Adres należy do aplikacji (config/urls.py stoi przed cms.urls), więc strona jest "
                "niewidoczna. Zmień jej slug w panelu. Zarezerwowane: " + ", ".join(reserved)
            ),
            obj=f"page:{page_id}",
            id="dj_pages.W001",
        )
        for page_id, path in sorted(urls)
        if page_id in published
    ]
