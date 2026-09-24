"""Dwa adresy ``/cms/``, których Wagtail nie zawęża, a haki nie sięgają — zamknięte przed widokiem.

Wagtail montuje swoje adresy **przed** hakami ``register_admin_urls``, więc własnego widoku pod
tym samym adresem nie da się podstawić bez zmiany ``config/urls.py``. Warstwa pośrednia z
``process_view`` rozpoznaje widok po nazwie adresu (a nie po ścieżce — ``/cms/`` bywa zamontowany
pod prefiksem konkursu) i odpowiada, **zanim** widok Wagtaila zdąży cokolwiek odczytać:

- ``wagtailadmin_choose_page_child`` — wybór strony z rodzicem wskazanym identyfikatorem
  w adresie. Hak ``construct_page_chooser_queryset`` zawęża **dzieci**, ale tytuł rodzica
  i okruszki jego przodków widok czyta sam; cudzy rodzic kończy się więc 404,
- ``wagtailadmin_choose_page`` bez rodzica — Wagtail zaczyna wtedy od wspólnego przodka stron
  żądanego typu, który bywa stroną innego konkursu. Redaktor z ograniczeniami trafia zamiast tego
  do korzenia swojego poddrzewa (te same parametry zapytania),
- raport „Użycie typów stron” (``wagtailadmin_reports:page_types_usage``) — liczby i tytuł
  ostatnio edytowanej strony z całej instalacji. Przy jednej witrynie raport pokazuje wyłącznie
  strony redaktora i zostaje; przy kilku — 403 (:func:`page_types_report_hidden`).

Konto bez ograniczeń (``apps.cms.scope.cms_scope`` = ``None``) przechodzi bez żadnego zapytania
poza tym, które liczy zasięg — a ten liczony jest wyłącznie dla tych trzech adresów.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse

from . import scope

CHOOSE_PAGE = "wagtailadmin_choose_page"
CHOOSE_PAGE_CHILD = "wagtailadmin_choose_page_child"
PAGE_TYPES_REPORT = frozenset(
    {"wagtailadmin_reports:page_types_usage", "wagtailadmin_reports:page_types_usage_results"}
)


def page_types_report_hidden(user) -> bool:
    """Czy raport „Użycie typów stron” jest dla tego konta zamknięty."""
    if scope.is_unrestricted(user):
        return False
    from wagtail.models import Site

    return Site.objects.count() > 1


class CmsScopeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if match is None or not request.user.is_authenticated:
            return None
        name = match.view_name
        if name == CHOOSE_PAGE_CHILD:
            return self._choose_page_child(request, view_kwargs)
        if name == CHOOSE_PAGE:
            return self._choose_page(request)
        if name in PAGE_TYPES_REPORT and page_types_report_hidden(request.user):
            raise PermissionDenied
        return None

    def _choose_page_child(self, request, view_kwargs):
        from wagtail.models import Page

        user_scope = scope.cms_scope(request.user)
        if user_scope is None:
            return None
        path = (
            Page.objects.filter(pk=view_kwargs.get("parent_page_id")).values_list("path", flat=True).first()
        )
        if path is None or not user_scope.visible_path(path):
            raise Http404
        return None

    def _choose_page(self, request):
        from wagtail.models import Page

        user_scope = scope.cms_scope(request.user)
        if user_scope is None or not user_scope.page_paths:
            return None
        # Wspólny przodek poddrzewa redaktora: przy jednym konkursie — strona główna tego konkursu.
        common = user_scope.page_paths[0]
        for path in user_scope.page_paths[1:]:
            while not path.startswith(common):
                common = common[: -Page.steplen]
        start = Page.objects.filter(path=common).values_list("pk", flat=True).first()
        if start is None:  # pragma: no cover - ścieżki pochodzą z istniejących wierszy
            return None
        url = reverse(CHOOSE_PAGE_CHILD, args=(start,))
        query = request.GET.urlencode()
        return HttpResponseRedirect(f"{url}?{query}" if query else url)
