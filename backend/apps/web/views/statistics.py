"""Publiczna strona ``/statystyki/`` – liczby o ogłoszonych etapach.

Widok Django, a nie strona w CMS-ie, i montowany wprost w ``config/urls.py`` przed catch-allem
Wagtaila – tak samo jak ``/results/<id>/``. Powód jest ten sam: treść tej strony jest w całości
wyliczana z danych, a redaktor nie ma w niej niczego do napisania. Strona w drzewie stron
oznaczałaby, że adres statystyk zależy od tego, czy ktoś tę stronę utworzył i gdzie ją przeniósł.

Dostęp: bez logowania. Wszystko, co tu widać, pochodzi z zamrożonego, zanonimizowanego snapshotu
publikacji (``apps.results.statistics``) i jest już jawne w tabeli wyników.
"""

from __future__ import annotations

from django.views.generic import TemplateView

from apps.results.models import ResultsPublication
from apps.results.statistics import statistics


class StatisticsView(TemplateView):
    """``GET /statystyki/`` – po sekcji na każdy etap z ogłoszonymi wynikami.

    Pusta lista nie jest błędem i nie jest 404: przed pierwszą publikacją strona ma powiedzieć
    „jeszcze nie ma wyników”, a nie udawać, że jej nie ma. Adres bywa linkowany z regulaminu
    i z materiałów prasowych, zanim cokolwiek zostanie ogłoszone.
    """

    template_name = "web/statistics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Odczyt idzie przez pamięć podręczną (10 minut) – patrz ``apps.results.statistics``.
        # Pamięć jest wspólna dla całej instalacji i taka zostaje: liczby są policzone
        # z **zanonimizowanych snapshotów publikacji**, czyli z danych już jawnych, a klucz per
        # konkurs znaczyłby N przeliczeń tego samego zestawu wierszy. Zawężamy więc wynik –
        # jedno zapytanie po identyfikatorach etapów tego konkursu, bez względu na rozmiar tabeli.
        published = set(
            ResultsPublication.objects.for_competition(self.request.competition).values_list(
                "stage_id", flat=True
            )
        )
        context["stages"] = [row for row in statistics() if row["stage_id"] in published]
        return context
