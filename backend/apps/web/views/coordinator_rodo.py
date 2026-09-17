"""Ekrany RODO w panelu koordynatora: retencja danych i rejestr czynności przetwarzania.

Obie strony odpowiadają na pytania, które organizatorowi zadaje ktoś z zewnątrz – uczestnik
(„jak długo trzymacie moje dane?”) albo organ nadzorczy („proszę o rejestr czynności”). Dotąd
odpowiedź na pierwsze z nich była w kodzie, a na drugie – w arkuszu, który rozjeżdżał się
z systemem. Teraz obie mają adres.

Rozdział ról między tym modułem a resztą jest ten sam, co wszędzie: reguły domenowe mieszkają
w ``apps.accounts.retention`` i ``apps.accounts.processing_register``, a widok wyłącznie
orkiestruje i wybiera szablon.

Jedna decyzja warta uzasadnienia: przycisk „Wykonaj teraz” wywołuje anonimizację **synchronicznie**,
a nie przez kolejkę. Powód jest ten sam, dla którego ten ekran w ogóle istnieje – operacja jest
nieodwracalna, więc koordynator ma zobaczyć jej wynik od razu, w odpowiedzi na własne kliknięcie,
a nie dowiedzieć się o nim z logu workera. Rozmiar pracy na to pozwala: rocznik to setki kont,
a każde z nich to kilka zapytań.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.accounts.processing_register import (
    ACTIVITIES,
    CSV_HEADERS,
    REGISTER_DATE,
    REGISTER_VERSION,
    as_rows,
)
from apps.accounts.retention import anonymise_expired_editions, plan
from apps.core.exports import Dataset, csv_response
from apps.web.mixins import CoordinatorRequiredMixin

RETENTION_TEMPLATE = "web/coordinator/retention.html"
REGISTER_TEMPLATE = "web/coordinator/processing_register.html"

#: Trzon nazwy pobieranego rejestru; ``csv_response`` dokleja do niej znacznik czasu. Wersja
#: w nazwie, bo plik bywa załącznikiem do pisma i leży potem w cudzym katalogu obok wersji
#: wcześniejszej.
REGISTER_FILENAME = f"rejestr-czynnosci-przetwarzania-{REGISTER_VERSION}"


class RetentionView(CoordinatorRequiredMixin, View):
    """``/coordinator/retention/`` – plan retencji danych i jego ręczne uruchomienie.

    GET jest **dry-runem**: pokazuje edycje po terminie, ich termin retencji, liczbę kont do
    anonimizacji i listę kont wstrzymanych razem z powodem. Ta sama funkcja (``retention.plan``)
    zasila komendę ``manage.py retention_report``, więc tabela w panelu i raport z terminala nie
    mogą pokazać różnych liczb.

    POST uruchamia to samo, co zadanie okresowe. Przycisk istnieje nie dlatego, że automat bywa
    zawodny, tylko dlatego, że po zmianie ``Edition.data_retention_months`` organizator chce
    zobaczyć skutek decyzji od razu – a nie następnego dnia rano.
    """

    def get(self, request):
        return self._render(request)

    def post(self, request):
        # Przebieg **tego** konkursu. Bez argumentu anonimizacja objęłaby edycje sąsiada, czyli
        # nieodwracalna operacja na cudzych danych osobowych wychodziłaby z przycisku, który
        # opisuje tabelę widoczną wyżej – a ta pokazuje wyłącznie edycje tego konkursu.
        result = anonymise_expired_editions(competition=request.competition)
        if result["anonymised"]:
            messages.success(
                request,
                "Retencja danych: zanonimizowano {anonymised} kont uczestników "
                "({blocked} wstrzymanych).".format(**result),
            )
        else:
            messages.info(
                request,
                "Retencja danych: nie było czego anonimizować ({blocked} kont wstrzymanych).".format(
                    **result
                ),
            )
        return redirect(reverse("web:coordinator-retention"))

    def _render(self, request):
        # Plan i przebieg liczy serwis retencji (jedno źródło dla panelu i dla komendy
        # ``retention_report``), a konkurs podajemy mu wprost – tym samym argumentem, którym
        # zawęża się przycisk „Wykonaj teraz”. Dzięki temu tabela i skutek jej kliknięcia opisują
        # ten sam zbiór edycji; zawężanie wyniku w widoku dawałoby dwie reguły zamiast jednej.
        plans = plan(competition=request.competition)
        context = {
            "now": timezone.now(),
            "plans": plans,
            "total_due": sum(len(item.due) for item in plans),
            "total_blocked": sum(len(item.blocked) for item in plans),
        }
        return TemplateResponse(request, RETENTION_TEMPLATE, context)


class ProcessingRegisterView(CoordinatorRequiredMixin, View):
    """``/coordinator/processing-register/`` – rejestr czynności przetwarzania (art. 30 RODO).

    Treść jest **danymi w kodzie** (``apps.accounts.processing_register``), więc ta strona nie ma
    ani jednego zdania własnego: renderuje to, co zmienia się razem z systemem i przechodzi przez
    recenzję kodu. Dane kontaktowe administratora dokłada widok z ``cms.SiteSettings`` – organizator
    zmienia je w ``/cms/``, a nie przez wydanie aplikacji.

    ``?format=csv`` oddaje ten sam rejestr jako plik. Osobny parametr, a nie osobny adres, bo to
    jest ten sam dokument w drugiej postaci – a postać wybiera się przy pobieraniu, nie przy
    linkowaniu.
    """

    def get(self, request):
        if request.GET.get("format") == "csv":
            return self._csv()
        return TemplateResponse(request, REGISTER_TEMPLATE, self._context(request))

    def _context(self, request) -> dict:
        from apps.cms.models import SiteSettings

        return {
            "activities": ACTIVITIES,
            "version": REGISTER_VERSION,
            "register_date": REGISTER_DATE,
            # ``for_request`` zamiast ``for_site``: ekran jest za logowaniem i zawsze ma żądanie,
            # a biblioteka sama znajduje witrynę pasującą do domeny.
            "site_settings": SiteSettings.for_request(request),
        }

    def _csv(self):
        """Rejestr jako plik – tą samą drogą, co eksporty koordynatora (``apps.core.exports``).

        Dzięki temu rejestr dostaje BOM UTF-8 i średnik jako separator, czyli otwiera się
        w polskim Excelu bez rozsypanych ogonków. Dokument idzie do organu nadzorczego i do
        dokumentacji organizatora – plik, którego nie da się otworzyć dwoma kliknięciami, byłby
        odpowiedzią pozorną.
        """
        rows = as_rows()
        return csv_response(
            Dataset(
                header=list(CSV_HEADERS),
                rows=iter(rows),
                count=len(rows),
                title="Rejestr czynności przetwarzania",
                filename=REGISTER_FILENAME,
            )
        )
