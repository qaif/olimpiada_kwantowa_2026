"""Rejestracja modeli ``apps.cms`` w edytorze Wagtaila i zawężenie ``/cms/`` do konkursu redaktora.

**Komunikaty organizatora jako snippet.** Ekranem podstawowym jest ``/coordinator/announcements/``
– komunikat pisze się wtedy, gdy coś już nie działa, a wtedy organizator jest w swoim panelu, nie
w edytorze treści. Wpis w ``/cms/`` jest drugą drogą i ma konkretnego adresata: redakcja bywa kimś
innym niż organizator zawodów i pracuje w Wagtailu – dla niej pasek z komunikatem jest elementem
serwisu obok stron i dokumentów.

Obie drogi zapisują ten sam model, więc żadna reguła się nie dubluje: okna czasowego pilnuje
``Announcement.clean()`` i constraint w bazie, a pamięć podręczną baneru czyści sygnał
``post_save`` (``apps.cms.announcements``) niezależnie od tego, kto zapisał.

**Zawężenie do konkursu** (``apps.cms.scope``). Komunikat należy do konkursu, a uprawnienie
modelowe Wagtaila — do wszystkich wierszy naraz. Dlatego snippet ma własną politykę uprawnień
(:class:`CompetitionScopedPolicy`: lista, zbiorcze usuwanie, historia) **i** widoki, które
cudzy wiersz kończą 404 (edycja, usunięcie, kopia, użycie, historia, podgląd). Dwie warstwy, bo
Wagtail sprawdza uprawnienie do **obiektu** tylko w części widoków, a do reszty trafia się po
samym identyfikatorze w adresie — to jest dokładnie IDOR, przed którym ma chronić zawężenie.

Pozostałe haki: wybór strony pokazuje wyłącznie poddrzewo redaktora i drogę do niego, okno
wyboru komunikatu — komunikaty jego konkursów, API panelu (``/cms/api/main/images|documents/``) —
pliki jego kolekcji, a raport „Użycie typów stron” znika z menu redaktora z ograniczeniami
w instalacji z kilkoma witrynami (sam adres zamyka ``apps.cms.middleware``).

**Zamrożenie edycji stron** (``apps.cms.freeze``, DJ-02 § 1.2 D9): baner nad każdym ekranem
``/cms/``, objaśnienie na ekranie edycji strony-danych i menu akcji ograniczone do informacji
o blokadzie. To jest warstwa **informacyjna** – zapis zamyka ``CmsFreezeMiddleware``, a przyciski
chowa tester uprawnień stron.

**Przejście do django CMS** (``apps.cms.djcms_sso``, DJ-02 D6): pozycja menu „Edytuj w django CMS”
i widok ``cms_djcms_handoff`` pod ``/cms/django-cms/`` (nie ``/cms/djcms/``: pod ``SITE_DOMAIN``
Caddy kieruje każdy adres ``/<segment>/djcms/…`` do djcms).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import path, reverse
from django.utils.html import format_html, json_script
from django.utils.safestring import mark_safe
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.admin.ui.sidebar import LinkMenuItem as LinkMenuItemComponent
from wagtail.documents.api.admin.views import DocumentsAdminAPIViewSet
from wagtail.images.api.admin.views import ImagesAdminAPIViewSet
from wagtail.permission_policies import ModelPermissionPolicy
from wagtail.permissions import register_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views import chooser as snippet_chooser_views
from wagtail.snippets.views import snippets as snippet_views
from wagtail.snippets.views.chooser import SnippetChooserViewSet
from wagtail.snippets.views.snippets import SnippetViewSet

from . import djcms_sso, freeze, scope
from .models import Announcement


class CompetitionScopedPolicy(ModelPermissionPolicy):
    """Uprawnienie modelowe **i** konkurs wiersza w zasięgu redaktora (``apps.cms.scope``).

    Konto bez ograniczeń (superużytkownik, superkoordynator, grupa z prawami do korzenia drzewa)
    dostaje odpowiedź Wagtaila bez zmian — zawężenie niczego mu nie odbiera.
    """

    def user_has_permission_for_instance(self, user, action, instance):
        return super().user_has_permission_for_instance(
            user, action, instance
        ) and scope.competition_in_scope(instance.competition_id, user)

    def user_has_any_permission_for_instance(self, user, actions, instance):
        return any(self.user_has_permission_for_instance(user, action, instance) for action in actions)

    def instances_user_has_any_permission_for(self, user, actions):
        return scope.filter_by_competition(super().instances_user_has_any_permission_for(user, actions), user)


#: Rejestracja **przed** ``register_snippet``: viewset zakłada inaczej politykę zastępczą, a Wagtail
#: odmawia podmiany polityki, która już raz została wydana (``PolicyRegistry.register``).
register_permission_policy(Announcement, CompetitionScopedPolicy(Announcement))


class _ScopedObjectMixin:
    """Cudzy komunikat pod znanym identyfikatorem to 404 — tak samo jak obiekt innego konkursu
    w panelu koordynatora (``apps/web/mixins.py``): istnienie cudzego wiersza nie jest informacją."""

    def get_object(self, *args, **kwargs):
        obj = super().get_object(*args, **kwargs)
        if not scope.competition_in_scope(obj.competition_id, self.request.user):
            raise Http404
        return obj


class _ScopedCreateMixin:
    """Nowy komunikat dostaje konkurs żądania (``Announcement.save``) — więc wolno go dodać tylko
    pod adresem konkursu, który jest w zasięgu redaktora. Inaczej redaktor konkursu A, zalogowany
    w ``/cms/`` pod adresem konkursu B, ogłaszałby komunikaty na stronie B."""

    def dispatch(self, request, *args, **kwargs):
        competition = getattr(request, "competition", None)
        if not scope.competition_in_scope(getattr(competition, "pk", None), request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def _scoped(view_class, mixin=_ScopedObjectMixin):
    return type(f"Scoped{view_class.__name__}", (mixin, view_class), {})


class _ScopedChooserListMixin:
    """Lista wyboru komunikatu: wyłącznie konkursy redaktora."""

    def get_object_list(self):
        return scope.filter_by_competition(super().get_object_list(), self.request.user)


class _ScopedChosenMixin:
    """Wybór komunikatu po identyfikatorze w adresie — cudzy wiersz to 404 (``ObjectDoesNotExist``)."""

    def get_object(self, pk):
        return scope.filter_by_competition(self.model_class.objects.all(), self.request.user).get(pk=pk)

    def get_objects(self, pks):
        return scope.filter_by_competition(self.model_class.objects.filter(pk__in=pks), self.request.user)


class AnnouncementChooserViewSet(SnippetChooserViewSet):
    """Okno „wybierz komunikat” — Wagtail zakłada je każdemu snippetowi i sprawdza w nim wyłącznie
    ``access_admin``, więc bez zawężenia listowałoby komunikaty wszystkich konkursów."""

    choose_view_class = type(
        "ScopedChooseView", (_ScopedChooserListMixin, snippet_chooser_views.ChooseView), {}
    )
    choose_results_view_class = type(
        "ScopedChooseResultsView", (_ScopedChooserListMixin, snippet_chooser_views.ChooseResultsView), {}
    )
    chosen_view_class = type(
        "ScopedChosenView", (_ScopedChosenMixin, snippet_chooser_views.SnippetChosenView), {}
    )
    chosen_multiple_view_class = type(
        "ScopedChosenMultipleView", (_ScopedChosenMixin, snippet_chooser_views.SnippetChosenMultipleView), {}
    )


class AnnouncementViewSet(SnippetViewSet):
    """Komunikaty w ``/cms/`` → Fragmenty. Kolumny listy odpowiadają na „co i czy wisi”."""

    model = Announcement
    icon = "warning"
    menu_label = "Komunikaty"
    list_display = ("text", "level", "starts_at", "ends_at", "is_active")
    list_filter = ("level", "is_active")
    search_fields = ("text",)

    add_view_class = _scoped(snippet_views.CreateView, _ScopedCreateMixin)
    edit_view_class = _scoped(snippet_views.EditView)
    delete_view_class = _scoped(snippet_views.DeleteView)
    copy_view_class = _scoped(snippet_views.CopyView)
    usage_view_class = _scoped(snippet_views.UsageView)
    history_view_class = _scoped(snippet_views.HistoryView)
    inspect_view_class = _scoped(snippet_views.InspectView)
    chooser_viewset_class = AnnouncementChooserViewSet

    panels = [
        MultiFieldPanel([FieldPanel("text"), FieldPanel("level")], heading="Komunikat"),
        MultiFieldPanel(
            [FieldPanel("link_url"), FieldPanel("link_label")],
            heading="Odnośnik (opcjonalny)",
        ),
        MultiFieldPanel(
            [FieldPanel("starts_at"), FieldPanel("ends_at"), FieldPanel("is_active")],
            heading="Kiedy pokazywać",
        ),
        FieldPanel("dismissible"),
    ]

    def get_queryset(self, request):
        """Lista komunikatów: wyłącznie konkursy redaktora (konto bez ograniczeń — wszystkie)."""
        return scope.filter_by_competition(Announcement.objects.all(), request.user)


register_snippet(AnnouncementViewSet)


@hooks.register("construct_page_chooser_queryset")
def scope_page_chooser(pages, request):
    """Wybór strony (link, strona docelowa bloku): poddrzewo redaktora i droga do niego od korzenia."""
    return scope.filter_pages(pages, request.user)


@hooks.register("construct_reports_menu")
def hide_page_types_report(request, menu_items):
    """Raport „Użycie typów stron” liczy strony **całej** instalacji — redaktor z ograniczeniami go
    nie widzi, gdy witryn jest więcej niż jedna (przy jednej raport pokazuje wyłącznie jego strony).
    """
    from apps.cms.middleware import page_types_report_hidden

    if page_types_report_hidden(request.user):
        menu_items[:] = [item for item in menu_items if item.name != "page-types-usage"]


class _ScopedMediaAPIMixin:
    """API panelu (``/cms/api/main/images/``, ``/documents/``): wyłącznie kolekcje redaktora.

    Wagtail oddaje tu każdemu z ``access_admin`` **wszystkie** obrazy i dokumenty spoza kolekcji
    z ograniczeniem widoczności — z tytułami. Redaktor z ograniczeniami dostaje to samo, co
    w bibliotece i w oknie wyboru: pliki kolekcji, do których ma uprawnienia.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        user_scope = scope.cms_scope(self.request.user)
        if user_scope is None:
            return queryset
        return queryset.filter(collection_id__in=user_scope.collection_ids)


ScopedImagesAdminAPIViewSet = type(
    "ScopedImagesAdminAPIViewSet", (_ScopedMediaAPIMixin, ImagesAdminAPIViewSet), {}
)
ScopedDocumentsAdminAPIViewSet = type(
    "ScopedDocumentsAdminAPIViewSet", (_ScopedMediaAPIMixin, DocumentsAdminAPIViewSet), {}
)


@hooks.register("construct_admin_api")
def scope_admin_api(router):
    """Podmiana punktów końcowych ``images`` i ``documents``. ``apps.cms`` stoi w ``INSTALLED_APPS``
    **za** ``wagtail.images`` i ``wagtail.documents``, więc ten hak biegnie po ich hakach i jego
    rejestracja jest ostatnim słowem (``WagtailAPIRouter.register_endpoint`` nadpisuje nazwę)."""
    router.register_endpoint("images", ScopedImagesAdminAPIViewSet)
    router.register_endpoint("documents", ScopedDocumentsAdminAPIViewSet)


# --- zamrożenie edycji stron (DJ-02 § 1.2 D9) ---------------------------------------------------------

#: Skrypt banera. Treść przychodzi w ``<script type="application/json">`` (``json_script`` escapuje
#: ``<``, ``>`` i ``&``) i trafia do DOM-u wyłącznie przez ``textContent`` – komunikat operatora
#: nie jest nigdy interpretowany jako HTML. Polityka CSP panelu dopuszcza skrypty inline
#: (``apps.web.middleware.build_admin_policy``).
FREEZE_BANNER_SCRIPT = """
(function () {
  var source = document.getElementById("cms-freeze-banner-data");
  if (!source) { return; }
  var data = JSON.parse(source.textContent);
  function mount() {
    var host = document.querySelector("#main .content");
    if (!host || document.querySelector("[data-cms-freeze-banner]")) { return; }
    var box = document.createElement("div");
    box.setAttribute("data-cms-freeze-banner", "");
    box.setAttribute("role", "status");
    box.className = "help-block help-warning";
    box.style.margin = "1rem";
    var lead = document.createElement("strong");
    lead.textContent = data.message;
    box.appendChild(lead);
    box.appendChild(document.createTextNode(" " + data.details + " "));
    var link = document.createElement("a");
    link.href = data.url;
    link.textContent = data.link_label;
    box.appendChild(link);
    host.insertBefore(box, host.firstChild);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
"""

FREEZE_BANNER_DETAILS = (
    "Strony są tu tylko do odczytu. Wyjątki: „Warsztaty” i „Partnerzy” (dane aplikacji) – ich treść "
    "edytujesz dalej tutaj. Ustawienia serwisu, komunikaty, obrazy i dokumenty działają bez zmian."
)

#: Objaśnienie na ekranie edycji strony-danych: co z niej trafia na stronę publiczną (ryzyko 8).
DATA_PAGE_NOTES = {
    "cms.contentpage": (
        "Tę stronę edytujesz dalej w Wagtailu, mimo przeniesienia edycji do django CMS: tabela "
        "warsztatów jest danymi aplikacji (obecności, zaświadczenia, zapowiedź), a django CMS "
        "pokazuje ją na żywo. Adresu strony (sluga) nie można zmienić."
    ),
    "cms.partnerspage": (
        "Tę stronę edytujesz dalej w Wagtailu, mimo przeniesienia edycji do django CMS: lista "
        "partnerów zasila slider sponsorów, a django CMS pokazuje ją na żywo. Adresu strony "
        "(sluga) nie można zmienić."
    ),
}


@hooks.register("insert_global_admin_js")
def editing_freeze_banner():
    """Baner „edycja przeniesiona do django CMS” nad treścią każdego ekranu ``/cms/``."""
    state = freeze.freeze_state()
    if not state.active:
        return ""
    data = {
        "message": state.banner_message,
        "details": FREEZE_BANNER_DETAILS,
        "url": freeze.djcms_url(),
        "link_label": "Przejdź do django CMS",
    }
    return format_html(
        "{}<script>{}</script>",
        json_script(data, "cms-freeze-banner-data"),
        mark_safe(FREEZE_BANNER_SCRIPT),  # noqa: S308 - stała modułu, bez danych z zewnątrz
    )


@hooks.register("before_edit_page")
def explain_data_page_during_freeze(request, page):
    """Na ekranie edycji strony-danych mówi, dlaczego ta jedna strona nie jest zamrożona."""
    if request.method != "GET" or not request.accepts("text/html"):
        return None
    if not freeze.is_frozen() or not freeze.is_exempt(page):
        return None
    note = DATA_PAGE_NOTES.get(page.specific_class._meta.label_lower)
    if note:
        messages.info(request, note)
    return None


@hooks.register("construct_page_action_menu")
def freeze_page_action_menu(menu_items, request, context):
    """Menu akcji zamrożonej strony: wyłącznie informacja o blokadzie (bez zapisu i publikacji).

    Blokada zamrożenia robi to już sama (Wagtail chowa pozycje przy ``locked_for_user``); hak
    domyka pozycje dokładane przez inne haki i ekran tworzenia strony.
    """
    if not freeze.is_frozen():
        return
    page = context.get("page")
    if page is not None and freeze.is_exempt(page):
        return
    menu_items[:] = [item for item in menu_items if item.name == "action-page-locked"]


# --- przejście do django CMS (SSO, DJ-02 D6) ------------------------------------------------------------


@hooks.register("register_admin_urls")
def djcms_handoff_urls():
    """``/cms/django-cms/`` – Wagtail opakowuje adresy z tego haka w ``require_admin_access``."""
    return [path("django-cms/", djcms_sso.handoff, name=freeze.DJCMS_HANDOFF_URL_NAME)]


class DjcmsMenuItem(MenuItem):
    """„Edytuj w django CMS” – tylko przy ustawionym kluczu i dla redaktora konkursu tego adresu.

    Adres liczony **przy każdym renderowaniu**: Wagtail buduje menu raz na proces, a ``/cms/`` bywa
    pod prefiksem konkursu (``/druga/cms/``) – adres z pierwszego żądania prowadziłby wszystkich
    pod cudzy prefiks.
    """

    def is_shown(self, request):
        competition = getattr(request, "competition", None)
        if competition is None or not djcms_sso.sso_enabled():
            return False
        return bool(djcms_sso.competition_abilities(request.user, competition))

    def is_active(self, request):
        return request.path.startswith(reverse(freeze.DJCMS_HANDOFF_URL_NAME))

    def render_component(self, request):
        return LinkMenuItemComponent(
            self.name,
            self.label,
            reverse(freeze.DJCMS_HANDOFF_URL_NAME),
            icon_name=self.icon_name,
            classname=self.classname,
            attrs=self.attrs,
        )


@hooks.register("register_admin_menu_item")
def djcms_menu_item():
    return DjcmsMenuItem(
        "Edytuj w django CMS", "", name="djcms-handoff", icon_name="link-external", order=250
    )
