"""Warstwa witryny konkursu: host + prefiks → ``request.site`` / ``request.competition_site`` (DJ-02 § 5.3).

Stoi zaraz za ``SecurityMiddleware``, **przed** wszystkim, co czyta witrynę (CSP i szablony przez
procesor kontekstu, django CMS, menu, bufor placeholderów). Robi pięć rzeczy:

1. raz na ``DJCMS_SITES_REFRESH_SECONDS`` odświeża rejestr z listy konkursów (``registry.refresh_if_due``),
2. rozstrzyga konkurs (``resolution.resolve`` – jedno zapytanie); nieznany host, który świeża lista
   z API przypisuje aktywnemu konkursowi, uzgadnia rejestr i próbuje jeszcze raz
   (``registry.refresh_for_host``); dalej brak konkursu → **pusta** 404, zanim cokolwiek się
   wyrenderuje (jak ``platform_subdomain_miss`` w aplikacji głównej – nie ma marki, którą wolno
   pokazać),
3. ustawia ``request.site`` (``django.contrib.sites.Site`` – czyta ją django CMS i łatka
   ``apps.sites.patches``), ``request.competition_site`` (``CompetitionSite`` – czyta go klient API
   i rama) oraz ``request.competition_path_prefix``,
4. przy konkursie pod prefiksem zdejmuje prefiks z ``path_info`` i ustawia ``set_script_prefix`` –
   ten sam mechanizm co ``CompetitionMiddleware`` aplikacji głównej: urlconf widzi adres od korzenia
   witryny konkursu, a ``reverse()``/``get_absolute_url()`` dokładają prefiks. ``request.path``
   zostaje z prefiksem (adres, o który prosiła przeglądarka). Prefiks skryptu jest stanem wątku –
   przywracamy go w ``finally``, także po wyjątku,
5. witryna konkursu z listy API bez żadnej strony dostaje drzewo startowe z eksportu tego konkursu
   (``apps.importer.starter.ensure_content_for_request``, D7) – albo 503, gdy importu nie da się
   zrobić w żądaniu.

Wyjątek od pustej 404 – adresy rozstrzygane **miękko**: statyki (``/djcms/static/``), healthcheck
(``/djcms/healthz/``) i media w devie (``/djcms/media/``). Nie zależą od konkursu, więc idą bez
odświeżania rejestru (healthcheck nie może pytać API i ma odpowiadać także przy pustym rejestrze)
i bez 404 dla nieznanego hosta. Dostają witrynę **leniwą** (``_soft_site``: konkursu hosta albo
zastępczą): pasek narzędzi django CMS (``ToolbarMiddleware``) czyta witrynę przy każdej odpowiedzi,
która do niego dojdzie, a plik statyczny podany przez WhiteNoise nie kosztuje przez to ani jednego
zapytania.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
from django.http import HttpResponseForbidden, HttpResponseNotFound
from django.urls import get_script_prefix, set_script_prefix
from django.utils.functional import SimpleLazyObject

from . import registry
from .models import CompetitionSite
from .resolution import LOCAL_HOSTS, Resolution, normalise_host, resolve

#: Adres healthchecka compose'a – rozstrzygany miękko (patrz docstring modułu).
HEALTHZ_PATH = "/djcms/healthz/"


def _soft_prefixes() -> tuple[str, ...]:
    return (settings.STATIC_URL, HEALTHZ_PATH, settings.MEDIA_URL)


def _soft_site(host: str) -> Site | None:
    """Witryna żądania rozstrzyganego miękko: konkursu hosta, domyślnego, a bez rejestru – pierwsza."""
    resolution = resolve(host, "/") if host else Resolution(None)
    if resolution.site is not None:
        return resolution.site.site
    default = CompetitionSite.objects.filter(is_default=True).select_related("site").first()
    return default.site if default is not None else Site.objects.order_by("pk").first()


def _host(request) -> str:
    try:
        return normalise_host(request.get_host())
    except DisallowedHost:
        # Odmowę ``ALLOWED_HOSTS`` składa Django (400) – tu nie ma czego rozstrzygać.
        return ""


class CompetitionSiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith(_soft_prefixes()):
            # Witryna **leniwa**: plik statyczny, który WhiteNoise znajdzie, nie kosztuje zapytania;
            # sięga po nią dopiero pasek narzędzi django CMS (healthcheck, brakujący plik → 404 CMS).
            host = _host(request)
            request.competition_site = None
            request.competition_path_prefix = ""
            request.site = SimpleLazyObject(lambda: _soft_site(host))
            return self.get_response(request)
        previous_script_prefix = get_script_prefix()
        try:
            resolution = self._resolve(request)
            if resolution.site is None:
                return HttpResponseNotFound()
            self._apply(request, resolution)
            # Nowy konkurs z listy API bez żadnej strony: drzewo startowe z eksportu (D7, DJ-02e).
            from apps.importer.starter import ensure_content_for_request

            starter = ensure_content_for_request(resolution.site)
            if starter is not None:
                return starter
            return self.get_response(request)
        finally:
            set_script_prefix(previous_script_prefix)

    @staticmethod
    def _resolve(request) -> Resolution:
        host = _host(request)
        if not host:
            return Resolution(None)
        registry.refresh_if_due(request=request)
        resolution = resolve(host, request.path_info)
        if resolution.site is None and host not in LOCAL_HOSTS:
            if registry.refresh_for_host(host, request=request):
                resolution = resolve(host, request.path_info)
        return resolution

    @staticmethod
    def _apply(request, resolution: Resolution) -> None:
        competition = resolution.site
        assert competition is not None  # wołane wyłącznie po rozstrzygnięciu z konkursem
        request.competition_site = competition
        request.site = competition.site
        request.competition_path_prefix = resolution.path_prefix
        if not resolution.path_prefix:
            return
        # Odcinamy **segment**, a nie stałą liczbę znaków: ``//druga/x/`` ma dać tę samą resztę.
        rest = request.path_info.lstrip("/")[len(resolution.path_prefix) :]
        request.path_info = rest if rest.startswith("/") else f"/{rest}"
        set_script_prefix(f"/{resolution.path_prefix}/")


class PrefixedCurrentPageMiddleware:
    """``request.current_page`` dla konkursu pod prefiksem ścieżki – stoi zaraz za ``CurrentPageMiddleware``.

    ``cms.utils.page.get_page_from_request`` (django CMS 5.1.3) zdejmuje z ``request.path_info``
    wynik ``reverse("pages-root")``. Pod prefiksem ``reverse`` oddaje ``/druga/``, a ``path_info``
    (po zdjęciu prefiksu przez ``CompetitionSiteMiddleware``) zaczyna się od ``/`` – korzeń nie
    zostałby zdjęty i strona bieżąca wyszłaby pusta (menu bez zaznaczenia, pasek narzędzi bez
    strony). Tu podajemy tę samą funkcję z jawną ścieżką względną do korzenia witryny; bez
    prefiksu warstwa nie robi nic.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "competition_path_prefix", ""):
            request.current_page = SimpleLazyObject(lambda: _prefixed_current_page(request))
        return self.get_response(request)


def _prefixed_current_page(request):
    from cms.utils.page import get_page_from_request

    if not hasattr(request, "_current_page_cache"):
        path = request.path_info.strip("/")
        request._current_page_cache = get_page_from_request(request, use_path=path, clean_path=False)
    return request._current_page_cache


class EditorAccessMiddleware:
    """Redaktor pracuje wyłącznie w witrynach, które może redagować (DJ-02 S12) – i nie dłużej niż sesja SSO.

    Stoi za ``AuthenticationMiddleware``. Dwie rzeczy:

    1. **termin sesji SSO** (``apps.sites.sso.session_expired``): po nim konto jest wylogowane,
       zanim żądanie dojdzie do widoku – panel odsyła na stronę logowania, a ta do ``/cms/``.
       Konto z SSO (``web:<id>``) bez znacznika terminu też jest wylogowane: takiej sesji nie
       założył ``sso_login``,
    2. **panel pod cudzą witryną** – personel bez ``is_superuser`` pod ``/djcms/admin/``:
       witryna żądania (host/prefiks) i witryna z parametru ``site`` (``cms.utils.admin
       .get_site_from_request`` – przełącznik witryn drzewa stron) muszą należeć do jego zasięgu
       (``apps.sites.permissions.editable_site_ids``), inaczej 403. Uprawnienia do stron i tak
       liczą witrynę strony, ale lista drzewa, filer i przekierowania pokazywałyby cudze tytuły.
       Pod jednym hostem stoi kilka konkursów (prefiksy ścieżki pod ``SITE_DOMAIN``), a sesja jest
       host-only – redaktor konkursu ``/druga/`` jest więc zalogowany także pod ``/djcms/admin/``
       konkursu domyślnego. Wyjątek: wylogowanie.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.contrib.auth import logout

        from . import sso

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        from_sso = user.get_username().startswith(sso.USERNAME_PREFIX)
        if sso.session_expired(request) or (from_sso and sso.SESSION_KEY not in request.session):
            logout(request)
            return self.get_response(request)
        if self._scoped(request) and not self._site_allowed(request):
            return _foreign_site()
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Obiekt z adresu (strona, treść, wersja, wtyczka…) musi należeć do witryny redaktora.

        Endpointy podglądu django CMS i djangocms-versioning sprawdzają wyłącznie prawo **oglądania**
        strony – a to ma każdy personel dla każdej strony bez ograniczeń dostępu, także roboczej
        wersji strony cudzej witryny. Zmiany i tak zamyka uprawnienie do strony (liczone z jej
        witryny); tu zamykamy odczyt.
        """
        if not self._scoped(request):
            return None
        from .permissions import editable_site_ids, object_site_id

        allowed = editable_site_ids(request.user)
        if allowed is None:
            return None
        for obj in _requested_objects(request, view_args, view_kwargs):
            site_id = object_site_id(obj)
            if site_id is not None and site_id not in allowed:
                return _foreign_site()
        if _foreign_filer_object(request, view_args, view_kwargs):
            return _foreign_site()
        return None

    @staticmethod
    def _scoped(request) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user is not None
            and user.is_authenticated
            and user.is_staff
            and not user.is_superuser
            and EditorAccessMiddleware._admin_path(request)
        )

    @staticmethod
    def _admin_path(request) -> bool:
        from django.urls import reverse

        admin_root = reverse("admin:index")
        return request.path.startswith(admin_root) and request.path != reverse("admin:logout")

    @staticmethod
    def _site_allowed(request) -> bool:
        from .permissions import editable_site_ids

        allowed = editable_site_ids(request.user)
        if allowed is None:
            return True
        site = getattr(request, "site", None)
        if site is None or site.pk not in allowed:
            return False
        requested = request.GET.get("site")
        if not requested and request.content_type == "application/x-www-form-urlencoded":
            # Formularz wieloczęściowy (wgrywanie pliku) czytamy dopiero w widoku: odczyt tutaj
            # zamknąłby filerowi zmianę ``upload_handlers``. Pola ``site`` taki formularz i tak nie ma.
            requested = request.POST.get("site")
        if not requested:
            return True
        try:
            return int(requested) in allowed
        except TypeError, ValueError:
            # ``get_site_from_request`` przy nieliczbowej wartości bierze witrynę żądania – już sprawdzoną.
            return True


def _foreign_site():
    return HttpResponseForbidden(
        "Nie redagujesz tej witryny. Wejdź do django CMS z panelu /cms/ jej konkursu.",
        content_type="text/plain; charset=utf-8",
    )


#: Podgląd, tryb edycji i tablica struktury obiektu: ``object/<typ treści>/<akcja>/<id>/``.
RENDER_OBJECT_VIEWS = frozenset(
    {
        "cms_placeholder_render_object_edit",
        "cms_placeholder_render_object_structure",
        "cms_placeholder_render_object_preview",
    }
)
PLUGIN_VIEWS = frozenset({"cms_placeholder_edit_plugin", "cms_placeholder_delete_plugin"})


def _get(model, pk):
    manager = getattr(model, "admin_manager", None) or model._default_manager
    try:
        return manager.filter(pk=int(pk)).first()
    except TypeError, ValueError:
        return None


def _model(label: str):
    from django.apps import apps

    app_label, _sep, model_name = (label or "").rpartition(".")
    try:
        return apps.get_model(app_label, model_name)
    except LookupError, ValueError:
        return None


def _requested_objects(request, view_args, view_kwargs) -> list:
    """Obiekty wskazane adresem albo parametrami żądania panelu – tylko te, które mają witrynę.

    Lista znanych kształtów adresów django CMS 5.1.3, djangocms-versioning 2.7.1 i rozszerzeń stron
    (``apps.pages.admin``). Nieznany kształt – pusta lista: rozstrzygają wtedy uprawnienia widoku.
    """
    from cms.models import CMSPlugin, Page, PageContent, Placeholder
    from django.contrib.contenttypes.models import ContentType

    match = request.resolver_match
    name = (match.url_name if match else "") or ""
    object_id = view_kwargs.get("object_id") or (view_args[0] if view_args else None)
    found = []
    if name in RENDER_OBJECT_VIEWS and len(view_args) >= 2:
        try:
            model = ContentType.objects.get_for_id(int(view_args[0])).model_class()
        except ContentType.DoesNotExist, TypeError, ValueError:
            model = None
        if model is not None:
            found.append(_get(model, view_args[1]))
    elif name in PLUGIN_VIEWS:
        found.append(_get(CMSPlugin, object_id))
    elif name == "cms_placeholder_clear_placeholder":
        found.append(_get(Placeholder, object_id))
    elif name in ("cms_placeholder_add_plugin", "cms_placeholder_move_plugin"):
        source = request.GET if request.method == "GET" else request.POST
        found.append(_get(Placeholder, source.get("placeholder_id")))
        found.append(_get(CMSPlugin, source.get("plugin_id")))
    elif name == "cms_usersettings_get_toolbar":
        model = _model(request.GET.get("obj_type", ""))
        if model is not None:
            found.append(_get(model, request.GET.get("obj_id")))
    elif name.startswith("cms_page_") and object_id is not None:
        found.append(_get(Page, object_id))
    elif name.startswith("cms_pagecontent_") and object_id is not None:
        found.append(_get(PageContent, object_id))
    elif name.startswith("djangocms_versioning_"):
        from djangocms_versioning.models import Version

        if object_id is not None:
            found.append(_get(Version, object_id))
        found.append(_get(Version, request.GET.get("compare_to")))
        found.append(_get(Page, request.GET.get("page")))
    elif name.startswith("dj_pages_") and match is not None:
        model = _model(f"dj_pages.{name.removeprefix('dj_pages_').rsplit('_', 1)[0]}")
        if model is not None and object_id is not None:
            found.append(_get(model, object_id))
        extended = request.GET.get("extended_object")
        if model is not None and extended:
            # Dodanie rozszerzenia: ``?extended_object=`` to strona albo treść – zależnie od modelu.
            found.append(_get(model._meta.get_field("extended_object").related_model, extended))
    return [obj for obj in found if obj is not None]


def _foreign_filer_object(request, view_args, view_kwargs) -> bool:
    """Folder albo plik filera spoza uprawnień folderów redaktora (``FolderPermission``, D5).

    Lista folderu po identyfikatorze pokazuje nazwę i ścieżkę folderu także temu, kto nie ma do
    niego prawa odczytu (filer filtruje wyłącznie jego zawartość) – folder innego konkursu ma być
    dla redaktora niewidoczny, tak jak w liście korzenia.
    """
    from filer.models import File, Folder

    match = request.resolver_match
    name = (match.url_name if match else "") or ""
    object_id = view_kwargs.get("object_id") or view_kwargs.get("folder_id") or view_kwargs.get("file_id")
    if (
        name.startswith("filer-directory_listing")
        or name.startswith("filer_folder_")
        or name == "filer-ajax_upload"
    ):
        folder = _get(Folder, object_id) if object_id is not None else None
        return folder is not None and not folder.has_read_permission(request)
    if name.startswith(("filer_file_", "filer_image_")):
        item = _get(File, object_id) if object_id is not None else None
        return item is not None and not item.has_read_permission(request)
    return False
