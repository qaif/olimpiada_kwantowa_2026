"""Ekran „Ustawienia konkursu” ``/coordinator/competition/``.

Jedyne miejsce w serwisie, w którym organizator zmienia **swój konkurs**, a nie jego rocznik:
nazwę razem z odmianą, znak, dane podmiotu prowadzącego, adres kontaktowy, nadawcę listów
i przełączniki funkcji. Wszystko, co dotyczy jednej edycji (okno rejestracji, retencja, etapy),
zostaje tam, gdzie było – ten ekran świadomie niczego z tamtych nie dubluje.

**Dlaczego ekran jest za przełącznikiem.** ``competition_settings_page`` ma w katalogu flag
wartość ``False`` (``docs/UNIWERSALNY-ETAP-1.md`` § 0.5), a reguła § 0 mówi, że konkurs bez ani
jednego wpisu w ``feature_flags`` ma zachowywać się **dokładnie tak jak dotąd** – łącznie z tym,
co stoi w menu. Ekran włączony domyślnie dokładałby Olimpiadzie Kwantowej pozycję nawigacji,
której przed wdrożeniem tam nie było, czyli zmianę widoczną dla koordynatora i niewynikającą
z polecenia organizatora. Włączenie jest więc świadomą decyzją: operator platformy przestawia
flagę, a od tej chwili ekran obsługuje sam siebie (w tym własne wyłączenie).

Wyłączony przełącznik daje **404**, a nie 403: adres, którego w tej instalacji nie ma, nie
istnieje – tak samo jak adres cudzego konkursu (§ 3.6). Rola jest sprawdzana osobno i wcześniej,
więc uczestnik dostaje 403 niezależnie od stanu flagi.

Ślad decyzji: ``competition.updated`` z **listą nazw zmienionych pól**. Bez wartości – wpis
audytowy czytają także osoby, które nie mają prawa do danych kontaktowych organizatora, a pytanie
zadawane nad tym wpisem brzmi „kto i kiedy to ruszył”, nie „jaki był poprzedni telefon”.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.core.models import audit
from apps.web.competition_forms import CompetitionSettingsForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/competition.html"

#: Nazwa przełącznika, który włącza ten ekran. Stała, a nie napis w dwóch miejscach: czyta ją
#: i widok, i test strzegący domyślnego stanu Konkursu #1.
FEATURE = "competition_settings_page"


class CompetitionSettingsView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/competition/`` – marka, organizator, poczta i przełączniki.

    Jeden adres i jeden formularz: konkurs ma kilkanaście pól, które czyta się i zmienia razem
    („zmieniamy nazwę, więc i odmianę, i prefiks tematu listów”). Rozbicie tego na trzy ekrany
    kosztowałoby dwa przeładowania przy każdej takiej zmianie, a zyskałoby wyłącznie krótsze
    strony.
    """

    def get(self, request):
        competition = self._competition(request)
        return self._render(request, competition, CompetitionSettingsForm(instance=competition))

    def post(self, request):
        competition = self._competition(request)
        form = CompetitionSettingsForm(request.POST, instance=competition)
        if not form.is_valid():
            return self._render(request, competition, form, status=400)
        # Różnicę liczymy **przed** zapisem: ``ModelForm._post_clean`` wpisał już nowe wartości
        # do instancji, ale ``form.initial`` pamięta stan sprzed formularza, a ``feature_flags``
        # jeszcze nie zostało ruszone (nie ma go wśród pól modelu tego formularza).
        changed = form.changed_fields()
        saved = form.save(commit=False)
        saved.feature_flags = form.feature_flags()
        saved.save()
        if changed:
            audit(request.user, "competition.updated", saved, {"fields": changed}, request=request)
            messages.success(request, "Ustawienia konkursu zostały zapisane.")
        else:
            # Zapis bez zmiany nie jest błędem i nie zostawia śladu: formularz bywa otwierany po to,
            # żeby coś sprawdzić, a wpis audytowy o zmianie, której nie było, psuje wartość audytu.
            messages.info(request, "Nic się nie zmieniło – ustawienia zostały bez zmian.")
        if not saved.has_feature(FEATURE):
            # Koordynator właśnie zamknął sobie ten ekran. Powrót na niego dałby 404 bez żadnego
            # wyjaśnienia, więc odsyłamy na pulpit razem z komunikatem, co się stało.
            messages.warning(
                request,
                "Ekran „Ustawienia konkursu” został wyłączony. Ponowne włączenie wymaga operatora platformy.",
            )
            return redirect(reverse("web:coordinator"))
        return redirect(reverse("web:coordinator-competition"))

    def _competition(self, request):
        """Konkurs żądania, o ile ekran jest w nim włączony. Inaczej 404.

        Obiekt bierzemy wprost z ``request.competition`` (ustawia go ``CompetitionMiddleware``),
        a nie z zapytania po identyfikatorze z adresu – i to jest cała reguła izolacji tego
        ekranu: nie ma tu adresu, pod którym dałoby się wskazać cudzy konkurs.

        Sprawdzenie jest w metodzie widoku, a nie w ``dispatch``: bramkę roli stawia wcześniej
        ``CoordinatorRequiredMixin`` (``RoleRequiredMixin.dispatch``), więc anonim i uczestnik
        dostają 302 albo 403, **zanim** cokolwiek zdradzi stan przełącznika tego konkursu.
        """
        competition = request.competition
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ekran ustawień konkursu jest w tej instalacji wyłączony.")
        return competition

    def _render(self, request, competition, form, *, status: int = 200):
        context = {
            "competition": competition,
            "form": form,
            # Pola, których ten ekran nie przyjmuje – pokazane, bo „pod jakim adresem stoi mój
            # konkurs” jest pytaniem, które pada tutaj, a odpowiedź należy do operatora (§ 8, D7).
            "readonly_rows": [
                ("Identyfikator", competition.slug),
                ("Domena", competition.primary_domain or competition.site.hostname),
                ("Tryb adresowania", competition.get_routing_mode_display()),
                ("Prefiks ścieżki", competition.path_prefix or "—"),
            ],
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)
