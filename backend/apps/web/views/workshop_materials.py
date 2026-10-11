"""„Materiały z warsztatów” dla zalogowanych – lista, odtwarzacz i pobranie.

Trzy adresy pod ``/warsztaty/materialy/``, bo tam szuka ich czytelnik: strona „Warsztaty”
(``/warsztaty/``) jest treścią Wagtaila, a te widoki stoją w urlconfie **przed** drzewem stron
(``config/urls.py``), więc podstrona o slugu ``materialy`` w ``/cms/`` nie przykryje ich
i nie zostanie przez nie przykryta po cichu – zawsze wygrywa aplikacja.

- ``/warsztaty/materialy/`` – warsztaty z harmonogramu (i osierocone migawki), pod każdym jego
  opublikowane, gotowe materiały. **Bez** żadnego podpisanego adresu: lista prowadzi do
  odtwarzacza albo do pobrania, a podpis powstaje dopiero tam,
- ``/warsztaty/materialy/<id>/`` – strona materiału. Dla filmu: ``<video>`` z adresem podpisanym
  na dwie godziny (``apps.workshop_materials.viewing``) i ``controlsList="nodownload"``; dla pliku
  i odnośnika – opis i przycisk, a dla Markdownu, tekstu i kodu także **podgląd** złożony po stronie
  serwera (``apps.workshop_materials.preview``, WM-FMT-01) – za tymi samymi bramkami, co pobranie,
  i liczony jako wyświetlenie,
- ``/warsztaty/materialy/<id>/pobierz/`` – plik: przekierowanie na adres podpisany na pięć minut;
  odnośnik: przekierowanie pod adres podany przez koordynatora. Tu liczy się wyświetlenie.

Bramki (``MaterialsAccessMixin``), w tej kolejności:

1. przełącznik ``workshop_materials`` wyłączony → **404** dla każdego, także anonima,
2. anonim → logowanie z powrotem pod ten sam adres (``LoginRequiredMixin``),
3. konto bez roli **w tym konkursie** → 403 (``apps.workshop_materials.access.can_view``),
4. materiał cudzego konkursu, nieopublikowany albo niegotowy → 404 (zawężony queryset).

Każda odpowiedź dostaje ``Cache-Control: no-store, private`` – strona z podpisanym adresem nie może
zostać w żadnej pamięci po drodze, a pamięć stron publicznych (``apps.web.page_cache``) i tak nie
obsługuje zalogowanych ani tych ścieżek (nie ma ich na allow-liście).
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.generic import View

from apps.accounts.models import CompetitionRole
from apps.accounts.services import has_role
from apps.workshop_materials import services
from apps.workshop_materials.access import can_view, feature_enabled
from apps.workshop_materials.models import MaterialKind
from apps.workshop_materials.preview import build_preview
from apps.workshop_materials.stats import record_view
from apps.workshop_materials.storage import get_material_storage
from apps.workshop_materials.viewing import signed_url

LIST_TEMPLATE = "web/workshop_materials/list.html"
DETAIL_TEMPLATE = "web/workshop_materials/detail.html"


class MaterialsAccessMixin(LoginRequiredMixin):
    """Przełącznik → logowanie → przynależność do konkursu. Patrz docstring modułu.

    Wyłącznie ``GET``: ``HEAD`` (domyślnie alias ``get`` w Django) liczyłby wyświetlenie i podpisywał
    adres przy samym sprawdzeniu, czy strona istnieje – a nikt nie ogląda filmu ``HEAD``-em.
    """

    http_method_names = ["get"]

    def dispatch(self, request, *args, **kwargs):
        competition = getattr(request, "competition", None)
        if not feature_enabled(competition):
            raise Http404("Ten konkurs nie prowadzi materiałów z warsztatów.")
        if request.user.is_authenticated and not can_view(request.user, competition):
            raise PermissionDenied(
                "Materiały z warsztatów są dostępne dla uczestników i opiekunów tego konkursu."
            )
        response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response

    def _material(self, pk: int):
        return get_object_or_404(services.visible_materials(self.request.competition), pk=pk)

    def _count(self, material) -> None:
        """Wyświetlenie – z pominięciem koordynatora tego konkursu (``apps.workshop_materials.stats``)."""
        if not has_role(self.request.user, self.request.competition, CompetitionRole.COORDINATOR):
            record_view(material, self.request.user)


class WorkshopMaterialsView(MaterialsAccessMixin, View):
    """``GET /warsztaty/materialy/`` – materiały pogrupowane po warsztatach."""

    def get(self, request):
        materials = list(services.visible_materials(request.competition))
        scheduled, orphaned = services.grouped(request.competition, materials, include_empty=False)
        return TemplateResponse(
            request, LIST_TEMPLATE, {"groups": scheduled + orphaned, "kinds": MaterialKind}
        )


class WorkshopMaterialDetailView(MaterialsAccessMixin, View):
    """``GET /warsztaty/materialy/<id>/`` – odtwarzacz filmu albo opis pliku/odnośnika."""

    def get(self, request, pk: int):
        material = self._material(pk)
        video_url = ""
        preview = None
        if material.kind == MaterialKind.VIDEO:
            video_url = signed_url(material, get_material_storage())
            self._count(material)
        elif material.kind == MaterialKind.FILE:
            preview = build_preview(material, get_material_storage())
            if preview is not None and preview.shown:
                self._count(material)
        context = detail_context(
            request,
            material,
            video_url=video_url,
            preview=preview,
            download_url=reverse("web:workshop-material-open", args=[material.pk]),
        )
        return TemplateResponse(request, DETAIL_TEMPLATE, context)


def detail_context(request, material, *, video_url: str = "", preview=None, download_url: str = "") -> dict:
    """Kontekst strony materiału – wspólny dla widza i podglądu koordynatora.

    ``download_url`` jest parametrem, bo u widza prowadzi przez ``workshop-material-open`` (licznik
    i bramki), a u koordynatora – wprost na krótko podpisany adres (materiał bywa szkicem, którego
    ``workshop-material-open`` nie wyda).
    """
    group = services.grouped(request.competition, [material], include_empty=False)
    workshop = (group[0] or group[1])[0]
    return {
        "material": material,
        "workshop": workshop,
        "video_url": video_url,
        "preview": preview,
        "download_url": download_url,
        "kinds": MaterialKind,
    }


class WorkshopMaterialOpenView(MaterialsAccessMixin, View):
    """``GET /warsztaty/materialy/<id>/pobierz/`` – przekierowanie na treść i wyświetlenie w statystyce.

    Film nie ma tu czego szukać (ogląda się go na stronie materiału), więc wraca na nią – dzięki
    temu nie istnieje adres, który oddaje surowy plik filmu jednym kliknięciem.
    """

    def get(self, request, pk: int):
        material = self._material(pk)
        if material.kind == MaterialKind.VIDEO:
            return redirect("web:workshop-material", pk=material.pk)
        if material.kind == MaterialKind.LINK:
            target = material.url
        else:
            target = signed_url(material, get_material_storage())
        self._count(material)
        return HttpResponseRedirect(target)
