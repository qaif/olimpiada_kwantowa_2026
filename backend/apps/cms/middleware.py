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

Druga warstwa w tym module, :class:`CmsFreezeMiddleware`, działa tą samą metodą (nazwa adresu,
odpowiedź przed widokiem) i egzekwuje zamrożenie edycji stron po przełączeniu serwisu na
django CMS (``apps.cms.freeze``, DJ-02 § 1.2 D9, reguła S13).
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse

from . import freeze, scope

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


# --- zamrożenie edycji stron (DJ-02 § 1.2 D9, reguła S13) -------------------------------------------

_PAGES = "wagtailadmin_pages:"

#: Widoki zmieniające **drzewo**: zamrożone każdą metodą i bez wyjątków (także dla stron-danych).
#: ``choose_parent`` i ``preview_on_add`` same niczego nie zapisują, ale są wyłącznie krokami
#: tworzenia strony – ekran, który prowadzi donikąd, jest gorszy niż jasna odmowa.
FROZEN_TREE_VIEWS = frozenset(
    {
        f"{_PAGES}add",
        f"{_PAGES}add_subpage",
        f"{_PAGES}choose_parent",
        f"{_PAGES}preview_on_add",
        f"{_PAGES}copy",
        f"{_PAGES}move",
        f"{_PAGES}move_confirm",
        f"{_PAGES}set_page_position",
        f"{_PAGES}delete",
        f"{_PAGES}convert_alias",
        # Tłumaczenie strony zakłada jej kopię w drzewie innego języka.
        "simple_translation:submit_page_translation",
    }
)

#: Czynności na treści jednej strony bez ekranu „tylko do odczytu”: zamrożone każdą metodą
#: (także ekran potwierdzenia), strona-dane przechodzi.
FROZEN_CONTENT_VIEWS = frozenset(
    {
        f"{_PAGES}unpublish",
        f"{_PAGES}lock",
        f"{_PAGES}unlock",
        f"{_PAGES}set_privacy",
        f"{_PAGES}revisions_unschedule",
        f"{_PAGES}workflow_action",
        f"{_PAGES}collect_workflow_action_data",
        f"{_PAGES}confirm_workflow_cancellation",
    }
)

#: Ekrany edycji: ``GET`` jest podglądem tylko do odczytu (``freeze.EditingFreezeLock``), zapis
#: (każda metoda poza bezpiecznymi, w tym autozapis) – zamrożony; strona-dane przechodzi, o ile
#: nie zmienia sluga.
FROZEN_EDIT_VIEWS = frozenset({f"{_PAGES}edit", f"{_PAGES}revisions_revert"})

#: Akcje zbiorcze (``/cms/bulk/<app>/<model>/<akcja>/``) – zamrożone dla modelu stron.
BULK_ACTION_VIEW = "wagtail_bulk_action"
BULK_PAGE_MODEL = ("wagtailcore", "page")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_WATCHED = FROZEN_TREE_VIEWS | FROZEN_CONTENT_VIEWS | FROZEN_EDIT_VIEWS | {BULK_ACTION_VIEW}


class CmsFreezeMiddleware:
    """Egzekwuje zamrożenie edycji stron (``apps.cms.freeze``) przed widokiem Wagtaila.

    Rozpoznanie po **nazwie** adresu (jak ``CmsScopeMiddleware``), więc działa też pod prefiksem
    konkursu. Nazwa spoza listy – bez żadnego zapytania; stan zamrożenia jest czytany dopiero dla
    adresów z listy, a przy wyłączonym zamrożeniu warstwa niczego nie zmienia.

    Konto niezalogowane przechodzi: te same widoki odsyłają je do logowania, a zapisać nie może
    niczego i tak. Odmowa to 403 – ekran panelu z komunikatem albo JSON w kształcie błędu Wagtaila
    (autozapis, okna modalne), żeby edytor pokazał powód, a nie „błąd sieci”.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if match is None or match.view_name not in _WATCHED:
            return None
        if not request.user.is_authenticated or not freeze.is_frozen():
            return None
        name = match.view_name

        if name == BULK_ACTION_VIEW:
            model = (view_kwargs.get("app_label", "").lower(), view_kwargs.get("model_name", "").lower())
            if model == BULK_PAGE_MODEL:
                return _denied(request, "Akcje zbiorcze na stronach są wyłączone.")
            return None
        if name in FROZEN_TREE_VIEWS:
            return _denied(
                request,
                "Tworzenie, przenoszenie, kopiowanie i usuwanie stron odbywa się teraz w django CMS.",
            )
        if name in FROZEN_EDIT_VIEWS and request.method in SAFE_METHODS:
            return None

        page = _page(view_kwargs)
        if page is None:
            return None  # nieistniejąca strona – 404 widoku Wagtaila
        if not freeze.is_exempt(page):
            return _denied(request, "Ta strona jest w Wagtailu tylko do odczytu.")
        if name in FROZEN_EDIT_VIEWS:
            slug = request.POST.get("slug")
            if slug is not None and slug != page.slug:
                return _denied(
                    request,
                    f"Adres strony „{page.title}” nie może się zmienić: aplikacja i django CMS "
                    f"znajdują ją po slugu „{page.slug}”. Treść można edytować dalej.",
                )
        return None


def _page(view_kwargs):
    from wagtail.models import Page

    page_id = view_kwargs.get("page_id")
    if page_id is None:
        return None
    return Page.objects.filter(pk=page_id).first()


def _denied(request, reason: str):
    state = freeze.freeze_state()
    if not request.accepts("text/html"):
        return JsonResponse(
            {
                "success": False,
                "error_code": "editing_frozen",
                "error_message": f"{state.banner_message} {reason}",
            },
            status=403,
        )
    return TemplateResponse(
        request,
        "cms/admin/editing_frozen.html",
        {
            "message": state.banner_message,
            "reason": reason,
            "djcms_url": freeze.djcms_url(),
            "back_url": reverse("wagtailadmin_explore_root"),
        },
        status=403,
    )
