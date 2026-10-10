"""Panel przekierowań – zawężony do witryn, które redaktor może edytować (DJ-02 § 8, S12).

Zakres = ``GlobalPagePermission`` redaktora (własne i grup): lista witryn uprawnienia, a uprawnienie
bez witryn znaczy „wszystkie” (grupa platformy, D5). Superużytkownik – wszystko. Wpis spoza zakresu
nie istnieje dla redaktora (lista, edycja, usuwanie – 404), a pole witryny pokazuje tylko jego
witryny. Wpis zapisany w panelu ma źródło ``manual`` – także wpis z importu albo automatu po edycji
(redakcja przejęła go i ``--replace`` ani automat już go nie ruszą). Cel bezwzględny – tylko na host
platformy, chyba że zapisuje konto platformy (``apps.seo.targets``).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.sites.models import Site
from django.db.models import Q

from .models import Redirect, RedirectSource


def editable_site_ids(user) -> set[int] | None:
    """Identyfikatory witryn redaktora; ``None`` = wszystkie (superużytkownik, uprawnienie bez witryn)."""
    if user.is_superuser:
        return None
    from cms.models import GlobalPagePermission

    ids: set[int] = set()
    permissions = (
        GlobalPagePermission.objects.filter(Q(user=user) | Q(group__user=user))
        .prefetch_related("sites")
        .distinct()
    )
    for permission in permissions:
        sites = [site.pk for site in permission.sites.all()]
        if not sites:
            return None
        ids.update(sites)
    return ids


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = ("old_path", "new_path", "is_permanent", "source", "site")
    list_filter = ("source", "is_permanent")
    search_fields = ("old_path", "new_path")
    fields = ("site", "old_path", "new_path", "is_permanent", "source", "created_at")
    readonly_fields = ("source", "created_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("site")
        ids = editable_site_ids(request.user)
        return queryset if ids is None else queryset.filter(site_id__in=ids)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "site":
            ids = editable_site_ids(request.user)
            queryset = Site.objects.order_by("domain")
            kwargs["queryset"] = queryset if ids is None else queryset.filter(pk__in=ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_form(self, request, obj=None, change=False, **kwargs):
        """Formularz, w którym ``Redirect.clean`` wie, czy cel może wyjść poza hosty platformy."""
        from apps.sites.permissions import is_platform_editor

        base = super().get_form(request, obj, change=change, **kwargs)
        platform = is_platform_editor(request.user)

        class RedirectForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                self.instance.allow_foreign_host = platform

        return RedirectForm

    def save_model(self, request, obj, form, change):
        obj.source = RedirectSource.MANUAL
        super().save_model(request, obj, form, change)
