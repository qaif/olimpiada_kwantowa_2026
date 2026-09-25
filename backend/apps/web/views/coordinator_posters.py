"""Ekran „Plakaty do pobrania” ``/coordinator/posters/`` – lista, statystyki pobrań i edycja.

Jeden ekran na dwie rzeczy, które koordynator robi razem: układa listę plakatów (dodaj, popraw,
opublikuj, przesuń, usuń) i patrzy, jak się pobierają. Rozdzielenie ich na dwa adresy znaczyłoby,
że po opublikowaniu nowego plakatu trzeba przejść gdzie indziej, żeby zobaczyć, czy ktoś go w ogóle
pobiera – a to jest pytanie, które pada zaraz po publikacji.

Statystyki są **podwójne** (uwaga organizatora z 23.09.2026): przy każdej liczbie pobrań stoi liczba
unikalnych adresów IP, w oknach 7 dni, 30 dni i od początku. Jak te liczby powstają i dlaczego wiersz
sumy nie jest sumą kolumny – ``apps.promo.stats``. Wykres dzienny pokazuje oba szeregi; parametr
``?material=<id>`` zawęża go do jednego plakatu.

Dodanie i edycja mają osobne adresy (``new/``, ``<id>/edit/``), a nie formularz nad listą jak
w komunikatach: formularz z dwoma polami plików jest długi, a błąd walidacji pliku ma wrócić na
ekran, na którym nie trzeba przewijać listy, żeby go zobaczyć. Pozostałe akcje (publikacja,
kolejność, usunięcie, przywrócenie) to POST na adres listy z polem ``action`` – ten sam wzorzec,
co ``apps.web.views.coordinator_announcements``.

Każda zmiana zostawia wpis audytowy ``promo.*`` z identyfikatorem plakatu i nazwami zmienionych pól
– bez tytułu i bez nazwy pliku od przesyłającego (tytuł jest jawny na stronie, ale wpis audytowy
nie jest jego drugą kopią). Eksport CSV zostawia ``export.generated`` jak każdy eksport panelu.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import F
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.generic import View

from apps.core.exports import Dataset, csv_response
from apps.core.models import audit
from apps.promo import services, stats
from apps.promo.forms import GROUP_DATALIST_ID, PromoMaterialForm, existing_groups
from apps.promo.models import IP_HASH_RETENTION_MONTHS, PromoMaterial
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/posters.html"
FORM_TEMPLATE = "web/coordinator/poster_form.html"


class _PostersMixin(CoordinatorRequiredMixin):
    """Konkurs żądania i plakaty wyłącznie tego konkursu – jedno miejsce dla wszystkich widoków."""

    def _competition(self):
        """Brak konkursu = nie ma czego redagować, czyli 404 (ta sama reguła, co slider sponsorów)."""
        competition = self.request.competition
        if competition is None:
            raise Http404("Pod tym adresem nie stoi żaden konkurs.")
        return competition

    def _materials(self):
        return PromoMaterial.objects.for_competition(self._competition())

    def _material(self, pk):
        return get_object_or_404(self._materials(), pk=pk)

    def _ordered(self) -> list[PromoMaterial]:
        """Najpierw lista bieżąca w kolejności ze strony, potem archiwum (``NULL`` na początku)."""
        return list(self._materials().order_by(F("archived_at").asc(nulls_first=True), "position", "id"))


class CoordinatorPostersView(_PostersMixin, View):
    """``GET|POST /coordinator/posters/`` – lista plakatów ze statystykami oraz akcje na wierszach."""

    #: Akcje z formularzy w wierszach tabeli – zamknięta lista, bo wartość przychodzi z POST-a.
    ACTIONS = ("publish", "unpublish", "up", "down", "delete", "restore")

    def get(self, request):
        competition = self._competition()
        materials = self._ordered()
        rows = stats.material_stats(competition, materials)
        chart_material = self._chart_material(request, materials)
        context = {
            "active_rows": [row for row in rows if not row.material.is_archived],
            "archived_rows": [row for row in rows if row.material.is_archived],
            "totals": stats.totals(competition),
            "series": stats.daily_series(competition, material=chart_material),
            "chart_material": chart_material,
            "materials": [material for material in materials if not material.is_archived],
            "short_days": stats.SHORT_WINDOW_DAYS,
            "long_days": stats.LONG_WINDOW_DAYS,
            "chart_days": stats.CHART_DAYS,
            "retention_months": IP_HASH_RETENTION_MONTHS,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)

    def post(self, request):
        action = request.POST.get("action")
        pk = request.POST.get("pk", "")
        if action not in self.ACTIONS or not pk.isdigit():
            raise Http404("Nieznana akcja.")
        material = self._material(int(pk))
        getattr(self, f"_{action}")(request, material)
        return redirect(reverse("web:coordinator-posters"))

    def _chart_material(self, request, materials):
        """Plakat z ``?material=<id>`` – wyłącznie spośród plakatów **tego** konkursu, inaczej wszystkie."""
        raw = request.GET.get("material", "")
        if not raw.isdigit():
            return None
        return next((material for material in materials if material.pk == int(raw)), None)

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
            "promo.published" if published else "promo.unpublished",
            material,
            {"is_published": published},
            request=request,
        )
        if published:
            messages.success(request, f"Plakat „{material.title}” jest teraz na stronie /plakaty/.")
        else:
            messages.success(request, f"Plakat „{material.title}” zdjęto ze strony – został jako szkic.")

    def _up(self, request, material):
        self._move(request, material, services.MOVE_UP)

    def _down(self, request, material):
        self._move(request, material, services.MOVE_DOWN)

    def _move(self, request, material, direction: str):
        if services.move(material, direction):
            audit(request.user, "promo.reordered", material, {"direction": direction}, request=request)

    def _delete(self, request, material):
        # Audyt **przed** skasowaniem: po ``delete()`` nie ma z czego wziąć identyfikatora celu.
        will_archive = material.downloads.exists()
        audit(
            request.user,
            "promo.archived" if will_archive else "promo.deleted",
            material,
            {"format": material.file_format},
            request=request,
        )
        outcome = services.remove(material)
        if outcome == "archived":
            messages.success(
                request,
                f"Plakat „{material.title}” ma już pobrania, więc trafił do archiwum: zniknął ze strony, "
                "a jego statystyki zostały.",
            )
        else:
            messages.success(request, f"Plakat „{material.title}” został usunięty.")

    def _restore(self, request, material):
        if services.restore(material):
            audit(request.user, "promo.restored", material, {}, request=request)
            messages.success(request, f"Plakat „{material.title}” wrócił z archiwum jako szkic.")


class PosterFormView(_PostersMixin, View):
    """``GET|POST /coordinator/posters/new/`` i ``/coordinator/posters/<id>/edit/`` – jeden formularz."""

    def get(self, request, pk=None):
        material = self._editable(pk)
        return self._render(request, PromoMaterialForm(instance=material), material)

    def post(self, request, pk=None):
        material = self._editable(pk)
        form = PromoMaterialForm(request.POST, request.FILES, instance=material)
        if not form.is_valid():
            return self._render(request, form, material, status=400)
        saved, changes = services.save_material(form, self._competition(), user=request.user)
        created = changes.pop("created")
        audit(request.user, "promo.created" if created else "promo.updated", saved, changes, request=request)
        if created:
            state = "opublikowany" if saved.is_published else "zapisany jako szkic"
            messages.success(request, f"Plakat „{saved.title}” został {state}.")
        else:
            messages.success(request, f"Zapisano zmiany plakatu „{saved.title}”.")
        return redirect(reverse("web:coordinator-posters"))

    def _editable(self, pk):
        """Nowy plakat (``pk`` brak) albo istniejący z tego konkursu. Archiwum się nie edytuje."""
        if pk is None:
            return PromoMaterial(competition=self._competition())
        material = self._material(pk)
        if material.is_archived:
            raise Http404("Plakat jest w archiwum – przywróć go, żeby go zmienić.")
        return material

    def _render(self, request, form, material, *, status: int = 200):
        context = {
            "form": form,
            "material": material,
            "editing": material.pk is not None,
            "groups": existing_groups(self._competition()),
            "groups_datalist_id": GROUP_DATALIST_ID,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class PosterFileView(_PostersMixin, View):
    """``GET /coordinator/posters/<id>/file/`` – plik plakatu dla koordynatora, **bez** licznika.

    Koordynator musi móc sprawdzić, co wgrał, zanim plakat pójdzie na stronę – publiczny adres
    pobrania szkicu nie widzi (404). Ten widok oddaje plik także szkicu i archiwum, i nie zapisuje
    pobrania: to jest podgląd pracy, a nie zasięg promocji.
    """

    def get(self, request, pk: int):
        material = self._material(pk)
        try:
            handle = material.file.open("rb")
        except FileNotFoundError, OSError:
            raise Http404("Plik plakatu jest niedostępny w storage.") from None
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=material.download_name,
            content_type=material.content_type,
        )
        add_never_cache_headers(response)
        return response


class PostersExportView(_PostersMixin, View):
    """``GET /coordinator/posters/export.csv`` – tabela statystyk jako CSV (średnik, UTF-8 z BOM)."""

    def get(self, request):
        competition = self._competition()
        materials = self._ordered()
        rows = stats.csv_rows(stats.material_stats(competition, materials), stats.totals(competition))
        dataset = Dataset(
            header=stats.CSV_HEADER,
            rows=iter(rows),
            count=len(materials),
            title="Pobrania plakatów",
            filename="pobrania-plakatow",
        )
        audit(
            request.user,
            "export.generated",
            competition,
            {"kind": "promo_downloads", "format": "csv", "rows": dataset.count},
            request=request,
        )
        return csv_response(dataset)
