"""Wyszukiwarki panelu w zasięgu redaktora: autocomplete Django i wybór linku djangocms-text (DJ-02g, S12).

Oba endpointy zwracają tytuły i adresy obiektów **wszystkich** witryn – nie mają witryny w adresie,
więc ``EditorAccessMiddleware`` nie ma czego porównać z zasięgiem redaktora. Warstwa woła tutejsze
funkcje zamiast widoku (``process_view``) wyłącznie dla redaktora z listą witryn
(``editable_site_ids`` ≠ ``None``); superużytkownik i platforma dostają widok bez zmian.
"""

from __future__ import annotations

import json

from django.contrib import admin
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.http import HttpResponseForbidden, JsonResponse

#: Nazwa adresu listy linków edytora tekstu (``TextPlugin.get_plugin_urls`` w djangocms-text 1.0.1).
LINK_PICKER_VIEW = "djangocms_text_textplugin_get_available_urls"

#: Pola, o które pyta interfejs redaktora (``app_label``, ``model_name``, ``field_name``) → ścieżka od
#: wyniku (modelu docelowego pola) do witryny. Jedyne dziś: formularz wyboru strony listy wersji
#: djangocms-versioning (``…/pagecontentversion/select/``, ``VersionAutocompleteSelect`` na
#: ``PageContent.page``). Autocomplete Django odpowiada dla **każdego** klucza obcego, którego model
#: docelowy ma w panelu ``search_fields`` (foldery i pliki filera, strony z rozszerzeń, pliki
#: wtyczek…) – pola spoza listy dostają 403, zanim cokolwiek zostanie wyszukane.
AUTOCOMPLETE_FIELDS = {("cms", "pagecontent", "page"): "site_id"}


def _forbidden(message: str):
    return HttpResponseForbidden(message, content_type="text/plain; charset=utf-8")


class _SiteScopedAutocomplete(AutocompleteJsonView):
    """``AutocompleteJsonView`` z wynikami zawężonymi do witryn redaktora – w zapytaniu, nie po nim.

    Filtr w ``get_queryset`` zachowuje stronicowanie (``pagination.more``), które po odfiltrowaniu
    gotowej odpowiedzi kłamałoby.
    """

    site_lookup = ""
    allowed_site_ids: frozenset[int] = frozenset()

    def get_queryset(self):
        return super().get_queryset().filter(**{f"{self.site_lookup}__in": self.allowed_site_ids})


def autocomplete(request, allowed: set[int]):
    field = (request.GET.get("app_label"), request.GET.get("model_name"), request.GET.get("field_name"))
    site_lookup = AUTOCOMPLETE_FIELDS.get(field)
    if site_lookup is None:
        return _forbidden("Tej wyszukiwarki panel redaktora nie używa.")
    view = _SiteScopedAutocomplete.as_view(
        admin_site=admin.site, site_lookup=site_lookup, allowed_site_ids=frozenset(allowed)
    )
    return view(request)


def link_picker(view_func, request, view_args, view_kwargs, allowed: set[int]):
    """Lista linków edytora tekstu: tylko strony witryn redaktora.

    ``?g=cms.page:<id>`` (nazwa i adres jednej strony – podpis istniejącego linku) dla strony cudzej
    witryny – 403. ``?q=`` (wyszukiwanie) – odpowiedź widoku bez stron spoza zasięgu; lista nie ma
    stronicowania, więc filtr po odpowiedzi niczego nie przekłamuje.
    """
    from cms.models import Page

    reference = request.GET.get("g")
    if reference:
        label, _sep, pk = reference.rpartition(":")
        if label.lower() == "cms.page":
            try:
                site_id = Page.objects.filter(pk=int(pk)).values_list("site_id", flat=True).first()
            except ValueError:
                site_id = None  # nieprawidłowy identyfikator – odpowie widok (400/404)
            if site_id is not None and site_id not in allowed:
                return _forbidden("Ta strona należy do innej witryny.")
        return view_func(request, *view_args, **view_kwargs)

    response = view_func(request, *view_args, **view_kwargs)
    if response.status_code != 200:
        return response
    data = json.loads(response.content)
    groups = data.get("results", [])
    listed = {str(child.get("id", "")) for group in groups for child in group.get("children", [])}
    page_ids = [ref.removeprefix("cms.page:") for ref in listed if ref.startswith("cms.page:")]
    own = {
        f"cms.page:{pk}"
        for pk in Page.objects.filter(
            pk__in=[pk for pk in page_ids if pk.isdigit()], site_id__in=allowed
        ).values_list("pk", flat=True)
    }
    for group in groups:
        # Tylko strony z zasięgu – pozycja bez rozpoznanej strony też wypada (odmowa domyślna).
        group["children"] = [child for child in group.get("children", []) if child.get("id") in own]
    return JsonResponse(data)
