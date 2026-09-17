"""Mixin widoków HTML należących do konkursu z żądania.

Reguła odpowiedzi, zapisana raz i obowiązująca wszędzie (``docs/UNIWERSALNY-ETAP-1.md`` § 3.6):

============================================  ==========  ============================================
sytuacja                                      odpowiedź   gdzie egzekwowana
============================================  ==========  ============================================
niezalogowany                                 302         ``LoginRequiredMixin`` (bez zmian)
zalogowany, zła **rola** w tym konkursie      403         ``RoleRequiredMixin`` (bez zmian)
zalogowany, dobra rola, obiekt **cudzy**      404         queryset (``for_competition``), nie widok
host bez konkursu                             404         ``CompetitionScopedMixin.dispatch``
============================================  ==========  ============================================

Różnica 403/404 jest merytoryczna: 403 mówi „jesteś, ale nie tobie”, 404 – „nie ma tego tutaj”.
Koordynator konkursu A pytający o etap konkursu B ma dostać 404, bo **istnienie** tego etapu nie
jest jego informacją. To 404 wychodzi samo z zawężonego querysetu (``get_object_or_404``), więc nie
ma osobnej gałęzi kodu, którą dałoby się pominąć.

Mixin wchodzi do widoków w zadaniu T5; tutaj powstaje razem z resztą warstwy izolacji.
"""

from __future__ import annotations

from django.http import Http404


class CompetitionScopedMixin:
    """Widok, którego dane należą do konkursu z żądania.

    ``get_queryset`` jest tu **jedynym** miejscem zawężenia. Szablon niczego nie chroni: wiersz
    pominięty w pętli zostawia adres szczegółu otwarty.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "competition", None) is None:
            # 404, a nie 500: adres bez konkursu to adres, którego w tej instalacji nie ma.
            # Ten sam kod dostaje host spoza listy konkursów i host, którego witryny nikt nie
            # przypisał – z punktu widzenia pytającego to ta sama sytuacja.
            raise Http404("Nie ma konkursu pod tym adresem.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def competition(self):
        """Konkurs żądania. Skrót dla widoku – regułą jest ``request.competition``."""
        return self.request.competition

    def get_queryset(self):
        return super().get_queryset().for_competition(self.competition)
