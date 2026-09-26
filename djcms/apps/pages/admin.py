"""Panel rozszerzeń stron. ``PageExtensionAdmin``/``PageContentExtensionAdmin`` ukrywają modele z indeksu
admina – redaktor otwiera formularz z paska narzędzi strony (``cms_toolbars.py``), a nie z listy
wszystkich rekordów.

**Zasięg redaktora** (DJ-02g, S12): lista pod ``/djcms/admin/dj_pages/<model>/`` nadal istnieje
(adres wpisany ręcznie), więc pokazuje wyłącznie rekordy witryn redaktora
(``apps.sites.permissions.editable_site_ids``), a akcji zbiorczych nie ma wcale: ``delete_selected``
usuwa ``QuerySet.delete()`` – z pominięciem ``delete_model`` i jego sprawdzenia uprawnień do strony
(z ``select_across`` – rekordy wszystkich witryn naraz). Zostaje usuwanie pojedynczego rekordu.

Rozszerzenia treści (``NewsMeta``, ``DocumentMeta``, ``ArchiveMeta``) należą do **wersji** treści,
więc zapis i usunięcie wolno tylko w wersji roboczej: zmiana metryki opublikowanej wersji z pominięciem
versioningu zmieniłaby stronę publiczną bez publikacji i bez śladu w historii. Pasek narzędzi i tak
wyłącza tę pozycję poza trybem edycji (wersji roboczej) – panel pilnuje tego samego dla adresu
wpisanego ręcznie.
"""

from __future__ import annotations

from cms.extensions import PageContentExtensionAdmin, PageExtensionAdmin
from cms.models import PageContent
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.live import client
from apps.sites.permissions import editable_site_ids, object_site_id

from .models import ArchiveMeta, DocumentMeta, MenuExtension, NewsMeta


class SiteScopedExtensionAdmin:
    """Lista rozszerzeń tylko z witryn redaktora i bez akcji zbiorczych (docstring modułu).

    ``site_lookup`` – ścieżka od rozszerzenia do witryny strony. Superużytkownik i platforma
    (``editable_site_ids`` → ``None``) widzą wszystko.
    """

    site_lookup = ""
    #: ``None`` wyłącza także ``delete_selected`` z ``AdminSite`` (``ModelAdmin.get_actions`` → ``{}``).
    actions = None

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        allowed = editable_site_ids(request.user)
        if allowed is None:
            return queryset
        return queryset.filter(**{f"{self.site_lookup}__in": allowed})

    def has_delete_permission(self, request, obj=None):
        if obj is not None:
            allowed = editable_site_ids(request.user)
            if allowed is not None and object_site_id(obj) not in allowed:
                return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # Akcji zbiorczych nie ma; gdyby wróciły – każdy rekord przez ``delete_model`` i jego sprawdzenia.
        for obj in queryset:
            self.delete_model(request, obj)


@admin.register(MenuExtension)
class MenuExtensionAdmin(SiteScopedExtensionAdmin, PageExtensionAdmin):
    site_lookup = "extended_object__site_id"


def _is_draft(content: PageContent | None) -> bool:
    from djangocms_versioning.constants import DRAFT
    from djangocms_versioning.models import Version

    if content is None:
        return False
    try:
        return Version.objects.get_for_content(content).state == DRAFT
    except Version.DoesNotExist:
        return False


class DraftOnlyContentExtensionAdmin(SiteScopedExtensionAdmin, PageContentExtensionAdmin):
    """Zapis i usunięcie rozszerzenia treści tylko w wersji roboczej (docstring modułu)."""

    site_lookup = "extended_object__page__site_id"

    def _target_content(self, request, obj) -> PageContent | None:
        if obj is not None and obj.extended_object_id:
            return obj.extended_object
        pk = request.GET.get("extended_object")
        return PageContent.admin_manager.filter(pk=pk).first() if pk else None

    def save_model(self, request, obj, form, change):
        if not _is_draft(self._target_content(request, obj)):
            raise PermissionDenied("Metrykę strony zmienia się w wersji roboczej (tryb edycji).")
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        # Bez obiektu (lista) – jak w Django; z obiektem: tylko metryka wersji roboczej.
        if obj is not None and not _is_draft(obj.extended_object):
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if not _is_draft(obj.extended_object):
            raise PermissionDenied("Metrykę strony usuwa się w wersji roboczej (tryb edycji).")
        super().delete_model(request, obj)


@admin.register(NewsMeta)
class NewsMetaAdmin(DraftOnlyContentExtensionAdmin):
    pass


@admin.register(DocumentMeta)
class DocumentMetaAdmin(DraftOnlyContentExtensionAdmin):
    pass


class ArchiveMetaForm(forms.ModelForm):
    """Edycja z listy ``GET editions``; przy niedostępnym API – zwykłe pole liczbowe (§ 6.1 speca).

    Lista to wygoda, a nie granica: edycji, która zniknęła z listy (inny konkurs, usunięta),
    nie zgubimy – bieżąca wartość zostaje na liście jako „edycja #id”, a wtyczka wyników i tak
    dostanie od API pustą odpowiedź dla cudzej edycji.
    """

    class Meta:
        model = ArchiveMeta
        fields = ("edition_id",)

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        result = client.get("editions", request=request)
        editions = (result.data or {}).get("editions") if result.ok else None
        if not isinstance(editions, list):
            self.fields[
                "edition_id"
            ].help_text += " Lista edycji jest chwilowo niedostępna – wpisz numer edycji z systemu zawodów."
            return
        choices = [("", "— bez powiązania —")]
        known = set()
        for edition in editions:
            if isinstance(edition, dict) and isinstance(edition.get("id"), int):
                known.add(edition["id"])
                choices.append((edition["id"], str(edition.get("title") or edition.get("year_label") or "")))
        current = self.instance.edition_id if self.instance else None
        if current and current not in known:
            choices.append((current, f"edycja #{current} (spoza listy)"))
        field = self.fields["edition_id"]
        self.fields["edition_id"] = forms.TypedChoiceField(
            label=field.label,
            help_text=field.help_text,
            choices=choices,
            coerce=int,
            empty_value=None,
            required=False,
        )


@admin.register(ArchiveMeta)
class ArchiveMetaAdmin(DraftOnlyContentExtensionAdmin):
    form = ArchiveMetaForm

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)

        class RequestBoundForm(form_class):
            def __init__(self, *args, **inner):
                super().__init__(*args, request=request, **inner)

        return RequestBoundForm
