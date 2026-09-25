"""„Plakaty do pobrania” – lista ``/plakaty/`` i pobranie ``/plakaty/<id>/pobierz/``.

Dwa widoki, dwie różne umowy z pamięcią podręczną stron (``apps.web.page_cache``):

- **lista** stoi na allow-liście tej pamięci: jest identyczna dla każdego gościa, a pamięć
  unieważnia się przy każdym zapisie plakatu (sygnał w ``page_cache``),
- **pobranie** nie stoi i stać nie może – każde żądanie musi dojść do widoku, bo to widok liczy
  pobranie (``apps.promo.tracking``). Odpowiedź niesie dodatkowo ``Cache-Control: … no-store,
  private``, żeby żadne proxy po drodze nie oddało pliku z pominięciem licznika.

Plik oddaje aplikacja (``FileResponse`` jako załącznik), a nie przekierowanie na adres w storage –
ten sam wzorzec, co treść zadania (``apps.competitions.api.ProblemStatementView``) i dokumenty
Wagtaila (``WAGTAILDOCS_SERVE_METHOD = "serve_view"``). Przekierowanie na podpisany adres S3 byłoby
tańsze dla serwera, ale podpisany adres żyje kilka minut i krąży dalej w wiadomościach, a nazwy
pliku do zapisu (``Content-Disposition``) nie da się na publicznym buckecie ustawić bez podpisu.

Oba widoki widzą wyłącznie plakaty **konkursu z żądania** i wyłącznie opublikowane – jedną
definicją (``apps.promo.availability.public_materials``), tą samą, z której korzysta odnośnik
w stopce. Plakat cudzego konkursu, szkic i plakat w archiwum dają to samo 404.
"""

from __future__ import annotations

import logging

from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.cache import add_never_cache_headers
from django.utils.http import content_disposition_header
from django.views.generic import View

from apps.promo.availability import public_materials
from apps.promo.cards import build_cards
from apps.promo.tracking import record_download
from apps.web.throttle import ThrottledFormMixin, check, consume, throttle_keys

logger = logging.getLogger(__name__)

TEMPLATE = "web/posters.html"


def _competition(request):
    competition = getattr(request, "competition", None)
    if competition is None:
        raise Http404("Pod tym adresem nie stoi żaden konkurs.")
    return competition


class PostersView(View):
    """``GET /plakaty/`` – siatka kart: podgląd, tytuł, opis, format i rozmiar, „Pobierz”.

    Pliki z tą samą grupą stoją na jednej karcie z przyciskiem na każdy plik
    (``apps.promo.cards``); karty składa Python z tej samej, jednej listy – liczba zapytań strony
    nie zależy od grupowania.

    Konkurs bez opublikowanych plakatów dostaje 404, a nie pustą stronę: odnośnik w stopce
    znika w tym samym momencie (``apps.promo.availability``), więc na pustą listę dałoby się trafić
    tylko ze starej zakładki – a tam 404 mówi prawdę („tego tu już nie ma”).
    """

    def get(self, request):
        materials = list(public_materials(_competition(request)))
        if not materials:
            raise Http404("Ten konkurs nie ma plakatów do pobrania.")
        return TemplateResponse(request, TEMPLATE, {"cards": build_cards(materials)})


class PosterDownloadView(ThrottledFormMixin, View):
    """``GET|HEAD /plakaty/<id>/pobierz/`` – plik plakatu jako załącznik, z zapisem pobrania.

    ``HEAD`` ma **własną** metodę, a nie alias ``get`` (domyślny w ``django.views.generic.View``):
    alias zapisałby pobranie przy samym sprawdzeniu, czy plik istnieje – a tak robią podglądy
    linków i menedżery pobierania przed właściwym żądaniem.

    **Limit per adres IP** (scope ``poster_download``, stawka w ``REST_FRAMEWORK``): ten sam
    licznik i ta sama odpowiedź 429 z ``Retry-After``, co przy formularzach
    (``apps.web.throttle``). ``ThrottledFormMixin`` sam liczy wyłącznie POST-y, więc tu służy za
    źródło ``throttled_response``, a sprawdzenie i zużycie limitu woła ``dispatch`` wprost – dla
    ``GET`` i ``HEAD`` (każde z nich kosztuje odczyt ze storage). Odbite żądanie nie dochodzi do
    widoku, więc nie zapisuje pobrania.
    """

    http_method_names = ["get", "head"]
    throttle_scope = "poster_download"

    def dispatch(self, request, *args, **kwargs):
        if request.method in ("GET", "HEAD"):
            keys = throttle_keys(self.throttle_scope, request)
            wait = check(self.throttle_scope, keys)
            if wait is not None:
                return self._no_store(self.throttled_response(request, wait))
            consume(self.throttle_scope, keys)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk: int):
        material = self._material(request, pk)
        try:
            handle = material.file.open("rb")
        except FileNotFoundError, OSError:
            # Wiersz bez pliku (skasowany ręcznie z magazynu, awaria storage) to stan danych,
            # nie błąd programu – 404 dla czytelnika, ślad w logu dla operatora. Pobranie się nie
            # liczy, bo żadnego pliku nikt nie dostał.
            logger.warning("Plik plakatu #%s jest niedostępny w storage.", material.pk, exc_info=True)
            raise Http404("Plik plakatu jest chwilowo niedostępny.") from None
        record_download(request, material)
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=material.download_name,
            content_type=material.content_type,
        )
        return self._no_store(response)

    def head(self, request, pk: int):
        """Nagłówki bez treści – i to samo 404 co ``get``, gdy pliku nie ma w storage.

        Menedżer pobierania, który sprawdza plik ``HEAD``-em, nie może dostać „200, 5 MB”, a zaraz
        potem 404 na ``GET``. ``exists`` to jedno żądanie do S3 (``HeadObject``), bez pobierania pliku.
        """
        material = self._material(request, pk)
        try:
            present = material.file.storage.exists(material.file.name)
        except Exception:  # noqa: BLE001 - awaria storage to dla czytelnika to samo, co brak pliku
            logger.warning("Nie udało się sprawdzić pliku plakatu #%s.", material.pk, exc_info=True)
            present = False
        if not present:
            raise Http404("Plik plakatu jest chwilowo niedostępny.")
        response = HttpResponse(content_type=material.content_type)
        response["Content-Length"] = str(material.file_size)
        response["Content-Disposition"] = content_disposition_header(True, material.download_name)
        return self._no_store(response)

    def _material(self, request, pk: int):
        return get_object_or_404(public_materials(_competition(request)), pk=pk)

    @staticmethod
    def _no_store(response):
        """Żadnej kopii po drodze – każde pobranie ma przejść przez ten widok (docstring modułu).

        ``add_never_cache_headers`` daje ``Cache-Control: max-age=0, no-cache, no-store,
        must-revalidate, private`` i ``Expires`` w przeszłości – komplet, który rozumie także
        starsze proxy szkolne.
        """
        add_never_cache_headers(response)
        return response
