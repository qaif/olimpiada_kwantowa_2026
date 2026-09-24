"""Panel koordynatora: materiały z warsztatów – ``/coordinator/workshops/materials/``.

Ekran jest **listą warsztatów** z harmonogramu (strona „Warsztaty”, blok ``schedule``), a pod
każdym – jego materiały z akcjami: podgląd, zmiana, publikacja, kolejność, usunięcie. Warsztat bez
materiałów też stoi na liście, bo to przy nim jest przycisk „Dodaj materiał”. Pod listą jest
sekcja materiałów **osieroconych**: przypiętych do klucza, którego w harmonogramie już nie ma (ktoś
zmienił temat albo datę – ``apps.workshop_materials.models``), z listą wyboru „przepnij do…”
i podpowiedzią wiersza z tą samą datą.

Dodanie materiału to osobny adres (``new/``), a wgrywanie pliku – cztery małe punkty JSON-owe, które
woła skrypt ``static/js/workshop-material-upload.js``:

- ``upload/start/`` – ten sam formularz (bez pliku) + nazwa i rozmiar pliku; zakłada materiał
  i wgrywanie wieloczęściowe, oddaje identyfikator i rozmiar części,
- ``<id>/upload/sign/`` – adresy kolejnych części (najwyżej ``PARTS_PER_SIGN`` naraz),
- ``<id>/upload/complete/`` – sprawdzenie i złożenie pliku (``services.complete_upload``),
- ``<id>/upload/abort/`` – przerwanie przez koordynatora.

Wszystkie są ``POST`` z tokenem CSRF (skrypt wysyła ``FormData`` z polem ``csrfmiddlewaretoken``
z formularza strony) i wszystkie widzą wyłącznie materiały **tego** konkursu. Plik nigdy nie
przechodzi przez gunicorna – dlaczego, mówi ``apps.workshop_materials.storage``.

Bramki są dwie i w tej kolejności: rola (``CoordinatorRequiredMixin.dispatch``: anonim → logowanie,
inna rola → 403) i dopiero potem przełącznik ``workshop_materials`` (404) – ta sama kolejność, co
przy forum, żeby odpowiedź nie mówiła osobie bez roli, jak konkurs jest skonfigurowany.

Każda zmiana zostawia wpis audytowy ``workshop_material.*`` z identyfikatorem materiału, rodzajem
i nazwami zmienionych pól – bez tytułu, opisu i nazwy pliku od przesyłającego (audyt mówi, co się
stało, a nie jest drugą kopią treści).
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.generic import View

from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.workshop_materials import services, stats
from apps.workshop_materials.access import feature_enabled
from apps.workshop_materials.forms import MaterialForm, NewMaterialForm, UploadStartForm
from apps.workshop_materials.models import MaterialKind, MaterialStatus, WorkshopMaterial
from apps.workshop_materials.storage import PART_SIZE, VIDEO_URL_TTL_SECONDS, get_material_storage
from apps.workshop_materials.viewing import signed_url

LIST_TEMPLATE = "web/coordinator/workshop_materials.html"
FORM_TEMPLATE = "web/coordinator/workshop_material_form.html"


class _MaterialsMixin(CoordinatorRequiredMixin):
    """Konkurs żądania za przełącznikiem i materiały wyłącznie tego konkursu."""

    def _competition(self):
        competition = self.request.competition
        if not feature_enabled(competition):
            raise Http404("Ten konkurs nie prowadzi materiałów z warsztatów.")
        return competition

    def _materials(self):
        return WorkshopMaterial.objects.for_competition(self._competition())

    def _material(self, pk):
        return get_object_or_404(self._materials(), pk=pk)

    def _list_url(self) -> str:
        return reverse("web:coordinator-workshop-materials")


class WorkshopMaterialsView(_MaterialsMixin, View):
    """``GET|POST /coordinator/workshops/materials/`` – lista warsztatów z materiałami i akcje."""

    #: Akcje z formularzy w wierszach – zamknięta lista, bo wartość przychodzi z POST-a.
    ACTIONS = ("publish", "unpublish", "up", "down", "delete", "attach", "rescan")

    def get(self, request):
        competition = self._competition()
        materials = list(self._materials().order_by("workshop_key", "position", "id"))
        scheduled, orphaned = services.grouped(competition, materials, include_empty=True)
        rows = services.schedule(competition)
        viewers = stats.unique_viewers(materials)
        for material in materials:
            material.unique_viewers = viewers.get(material.pk, 0)
        for group in orphaned:
            # Podpowiedź przepięcia: wiersz harmonogramu z tą samą datą, o ile jest dokładnie jeden.
            same_day = [row for row in rows if group.date_value and row["date_value"] == group.date_value]
            group.suggested_key = same_day[0]["key"] if len(same_day) == 1 else ""
        context = {
            "groups": scheduled,
            "orphans": orphaned,
            "rows": rows,
            "status": MaterialStatus,
            "kinds": MaterialKind,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)

    def post(self, request):
        action = request.POST.get("action")
        pks = request.POST.getlist("pk")
        if action not in self.ACTIONS or not pks or not all(pk.isdigit() for pk in pks):
            raise Http404("Nieznana akcja.")
        # Kilka identyfikatorów przyjmuje wyłącznie „przepnij” (cała grupa osierocona naraz);
        # pozostałe akcje dotyczą jednego wiersza i drugi identyfikator byłby zepsutym żądaniem.
        if len(pks) > 1 and action != "attach":
            raise Http404("Ta akcja dotyczy jednego materiału.")
        # Każdy identyfikator przez zawężony queryset – materiał sąsiada daje 404, zanim cokolwiek
        # się zmieni (pobieramy wszystkie przed pierwszą zmianą).
        materials = [self._material(int(pk)) for pk in pks]
        for material in materials:
            getattr(self, f"_{action}")(request, material)
        return redirect(self._list_url())

    def _publish(self, request, material):
        self._set_published(request, material, True)

    def _unpublish(self, request, material):
        self._set_published(request, material, False)

    def _set_published(self, request, material, published: bool):
        if not services.set_published(material, published):
            messages.info(request, "Nic się nie zmieniło.")
            return
        audit(
            request.user,
            "workshop_material.published" if published else "workshop_material.unpublished",
            material,
            {"is_published": published},
            request=request,
        )
        if not published:
            messages.success(request, f"Materiał „{material.title}” zdjęto – został jako szkic.")
        elif material.is_ready:
            messages.success(request, f"Materiał „{material.title}” jest widoczny dla zalogowanych.")
        else:
            messages.success(
                request, f"Materiał „{material.title}” pojawi się u widzów, gdy skończy się jego sprawdzanie."
            )

    def _up(self, request, material):
        self._move(request, material, services.MOVE_UP)

    def _down(self, request, material):
        self._move(request, material, services.MOVE_DOWN)

    def _move(self, request, material, direction: str):
        if services.move(material, direction):
            audit(
                request.user,
                "workshop_material.reordered",
                material,
                {"direction": direction},
                request=request,
            )

    def _delete(self, request, material):
        # Audyt **przed** skasowaniem: po ``delete()`` nie ma z czego wziąć identyfikatora celu.
        audit(
            request.user,
            "workshop_material.deleted",
            material,
            {"kind": material.kind, "format": material.file_format, "size": material.size_bytes},
            request=request,
        )
        title = material.title
        services.remove(material)
        messages.success(request, f"Materiał „{title}” został usunięty razem z plikiem.")

    def _attach(self, request, material):
        row = services.row_by_key(material.competition, request.POST.get("workshop", ""))
        if row is None:
            messages.error(request, "Wybierz warsztat z bieżącego harmonogramu.")
            return
        if services.attach(material, row):
            audit(
                request.user,
                "workshop_material.attached",
                material,
                {"workshop": row["key"]},
                request=request,
            )
            messages.success(request, f"Materiał „{material.title}” przypięto do warsztatu „{row['topic']}”.")

    def _rescan(self, request, material):
        if material.status != MaterialStatus.SCANNING:
            messages.info(request, "Ten materiał nie czeka na sprawdzenie.")
            return
        services.enqueue_scan(material)
        messages.success(request, f"Plik „{material.title}” wrócił do kolejki skanera antywirusowego.")


class WorkshopMaterialFormView(_MaterialsMixin, View):
    """``GET|POST …/materials/new/`` i ``…/materials/<id>/edit/``.

    POST na ``new/`` zapisuje **wyłącznie odnośnik** – film i plik zapisuje skrypt przez
    ``upload/start/``. Bez JavaScriptu formularz dalej działa dla odnośnika, a dla pliku mówi
    wprost, że wgrywanie wymaga skryptu (``<noscript>`` w szablonie), zamiast udawać zapis.
    """

    def get(self, request, pk=None):
        material = self._editable(pk)
        return self._render(request, self._form(material), material)

    def post(self, request, pk=None):
        material = self._editable(pk)
        form = self._form(material, data=request.POST)
        if not form.is_valid():
            return self._render(request, form, material, status=400)
        if material.pk is None:
            if form.cleaned_data["kind"] != MaterialKind.LINK:
                form.add_error(
                    None, "Film i plik wgrywa się przyciskiem na tej stronie (wymaga JavaScriptu)."
                )
                return self._render(request, form, material, status=400)
            saved = services.create_link(
                self._competition(),
                row=form.selected_row(),
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                url=form.cleaned_data["url"],
                is_published=form.cleaned_data["is_published"],
                user=request.user,
            )
            audit(request.user, "workshop_material.created", saved, {"kind": saved.kind}, request=request)
            messages.success(request, f"Odnośnik „{saved.title}” został zapisany.")
            return redirect(self._list_url())
        changed = [name for name in form.changed_data if name != "workshop"]
        saved = form.save()
        if services.attach(saved, form.selected_row()):
            changed.append("workshop")
        audit(request.user, "workshop_material.updated", saved, {"fields": sorted(changed)}, request=request)
        messages.success(request, f"Zapisano zmiany materiału „{saved.title}”.")
        return redirect(self._list_url())

    def _editable(self, pk):
        if pk is None:
            return WorkshopMaterial(competition=self._competition())
        return self._material(pk)

    def _form(self, material, data=None):
        rows = services.schedule(self._competition())
        if material.pk is None:
            initial = {"workshop": self.request.GET.get("workshop", "")}
            return NewMaterialForm(data=data, instance=material, rows=rows, initial=initial)
        return MaterialForm(data=data, instance=material, rows=rows)

    def _render(self, request, form, material, *, status: int = 200):
        context = {
            "form": form,
            "material": material,
            "editing": material.pk is not None,
            "has_schedule": bool(form.rows),
            "part_size": PART_SIZE,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


def _error(message: str, *, status: int = 400, errors: dict | None = None) -> JsonResponse:
    payload = {"error": message}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


class UploadStartView(_MaterialsMixin, View):
    """``POST …/materials/upload/start/`` – zakłada materiał i wgrywanie. Odpowiedź JSON."""

    http_method_names = ["post"]

    def post(self, request):
        competition = self._competition()
        form = UploadStartForm(
            data=request.POST,
            instance=WorkshopMaterial(competition=competition),
            rows=services.schedule(competition),
        )
        if not form.is_valid():
            errors = {field: [str(e) for e in errs] for field, errs in form.errors.items()}
            return _error("Popraw zaznaczone pola.", errors=errors)
        try:
            material = services.start_upload(
                competition,
                row=form.selected_row(),
                kind=form.cleaned_data["kind"],
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                is_published=form.cleaned_data["is_published"],
                filename=form.cleaned_data["filename"],
                size=form.cleaned_data["size"],
                user=request.user,
            )
        except services.UploadError as exc:
            return _error(str(exc))
        audit(
            request.user,
            "workshop_material.upload_started",
            material,
            {"kind": material.kind, "format": material.file_format, "size": material.size_bytes},
            request=request,
        )
        return JsonResponse(
            {
                "id": material.pk,
                "part_size": PART_SIZE,
                "parts": services.part_count(material.size_bytes),
                "sign_url": reverse("web:coordinator-workshop-material-upload-sign", args=[material.pk]),
                "complete_url": reverse(
                    "web:coordinator-workshop-material-upload-complete", args=[material.pk]
                ),
                "abort_url": reverse("web:coordinator-workshop-material-upload-abort", args=[material.pk]),
            }
        )


class UploadSignView(_MaterialsMixin, View):
    """``POST …/materials/<id>/upload/sign/`` – adresy części ``part=1&part=2…``. Odpowiedź JSON.

    Adresy części są krótkotrwałymi poświadczeniami zapisu – odpowiedź dostaje ``no-store``, żeby
    nie została w pamięci żadnego pośrednika ani przeglądarki.
    """

    http_method_names = ["post"]

    def post(self, request, pk: int):
        material = self._material(pk)
        numbers = [int(value) for value in request.POST.getlist("part") if value.isdigit()]
        try:
            urls = services.sign_parts(material, numbers)
        except services.UploadError as exc:
            return _error(str(exc))
        response = JsonResponse({"urls": {str(number): url for number, url in urls.items()}})
        add_never_cache_headers(response)
        return response


class UploadCompleteView(_MaterialsMixin, View):
    """``POST …/materials/<id>/upload/complete/`` – sprawdzenie i złożenie pliku. Odpowiedź JSON."""

    http_method_names = ["post"]

    def post(self, request, pk: int):
        material = self._material(pk)
        material_id, kind = material.pk, material.kind
        try:
            material = services.complete_upload(material)
        except services.UploadError as exc:
            # Materiał już nie istnieje (``complete_upload`` sprząta po porażce) – audyt po
            # identyfikatorze z chwili wejścia, bez treści komunikatu (bywa w nim nazwa formatu).
            audit(
                request.user,
                "workshop_material.upload_rejected",
                WorkshopMaterial(pk=material_id),
                {"kind": kind},
                request=request,
            )
            return _error(str(exc))
        audit(
            request.user,
            "workshop_material.uploaded",
            material,
            {"kind": material.kind, "format": material.file_format, "size": material.size_bytes},
            request=request,
        )
        if material.status == MaterialStatus.SCANNING:
            note = "Plik jest wgrany i czeka na sprawdzenie antywirusowe – zwykle trwa to kilkanaście sekund."
        else:
            note = "Film jest wgrany."
        messages.success(request, f"„{material.title}”: {note}")
        return JsonResponse({"status": material.status, "redirect": self._list_url()})


class UploadAbortView(_MaterialsMixin, View):
    """``POST …/materials/<id>/upload/abort/`` – przerwanie wgrywania: części i wiersz znikają."""

    http_method_names = ["post"]

    def post(self, request, pk: int):
        material = self._material(pk)
        try:
            services.abort_upload(material)
        except services.UploadError as exc:
            return _error(str(exc))
        return JsonResponse({"status": "aborted"})


class CoordinatorMaterialPreviewView(_MaterialsMixin, View):
    """``GET …/materials/<id>/preview/`` – podgląd dla koordynatora, także szkicu; **bez** licznika.

    Koordynator musi móc sprawdzić, co wgrał, zanim materiał pójdzie do widzów. Plik przed werdyktem
    skanera (albo odrzucony) nie ma podglądu – nawet dla koordynatora nie otwieramy pliku, o którym
    nie wiadomo, czy jest czysty.
    """

    def get(self, request, pk: int):
        material = self._material(pk)
        if material.kind == MaterialKind.LINK:
            return HttpResponseRedirect(material.url)
        if not material.is_ready or not material.object_key:
            raise Http404("Ten materiał nie jest jeszcze gotowy.")
        response = HttpResponseRedirect(
            signed_url(material, get_material_storage(), ttl=VIDEO_URL_TTL_SECONDS)
        )
        add_never_cache_headers(response)
        return response
