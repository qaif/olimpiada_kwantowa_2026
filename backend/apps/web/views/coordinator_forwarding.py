"""Ekran „Przekazywanie rozwiązań” ``/coordinator/submission-forwarding/``.

Jedno ustawienie i jedno pytanie: na które skrzynki serwis ma przesyłać **każdą** przyjętą pracę
uczestnika. Prośba organizatora z 20.09.2026 – komitet układa dzień pracy w poczcie, a nie
w przeglądarce, i chce mieć oddane rozwiązania tam, gdzie resztę korespondencji.

Dlaczego to nie jest wiersz w „Ustawieniach konkursu” (``/coordinator/competition/``): tamten ekran
opisuje **markę i dane organizatora**, a ten jest decyzją o przetwarzaniu danych osobowych –
wynosi pliki uczestników poza serwis, do skrzynek, nad którymi platforma nie ma żadnej kontroli.
Taka decyzja ma mieć własny adres, własny komunikat i własny wpis w audycie, a nie wpadać do
jednego zapisu razem z kolorem akcentu. Z tego samego powodu ekran stoi **bez przełącznika
funkcji**: bramką jest samo pole, a puste pole (stan domyślny każdego konkursu) znaczy „nie
przekazujemy” – więc dopisanie ekranu nie zmienia ani jednego listu.

Ślad decyzji: ``competition.forwarding_updated`` z **liczbą** adresów, nigdy z adresami. Wpis
audytowy czyta także ktoś, kto nie ma prawa do danych kontaktowych komitetu, a pytanie zadawane
nad tym wpisem brzmi „kto i kiedy to włączył”, nie „na czyją skrzynkę”.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.core.models import audit
from apps.tenancy.models import MAX_FORWARD_EMAILS, Competition
from apps.web.competition_forms import SubmissionForwardingForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/submission_forwarding.html"


class SubmissionForwardingView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/submission-forwarding/`` – adresy przekazywania przyjętych prac."""

    def get(self, request):
        competition = self._competition(request)
        return self._render(request, competition, SubmissionForwardingForm(instance=competition))

    def post(self, request):
        competition = self._competition(request)
        form = SubmissionForwardingForm(request.POST, instance=competition)
        if not form.is_valid():
            return self._render(request, competition, form, status=400)
        # Różnicę liczymy **przed** zapisem: ``_post_clean`` wpisał nową wartość do instancji,
        # ale ``form.initial`` pamięta stan sprzed formularza.
        changed = form.has_changed_addresses()
        addresses = form.addresses()
        saved = form.save()
        if changed:
            audit(
                request.user,
                "competition.forwarding_updated",
                saved,
                {"recipients": len(addresses)},
                request=request,
            )
            if addresses:
                messages.success(
                    request,
                    f"Zapisane. Każde rozwiązanie przyjęte po czystym skanie antywirusowym "
                    f"będzie przekazywane na wskazane adresy ({len(addresses)}).",
                )
            else:
                # Wyłączenie jest równie ważną informacją, co włączenie – i łatwo je zrobić przez
                # pomyłkę, czyszcząc pole „na chwilę”.
                messages.warning(request, "Przekazywanie rozwiązań zostało wyłączone.")
        else:
            # Zapis bez zmiany nie jest błędem i nie zostawia śladu: ekran bywa otwierany po to,
            # żeby sprawdzić, dokąd idą prace.
            messages.info(request, "Nic się nie zmieniło – adresy zostały bez zmian.")
        return redirect(reverse("web:coordinator-submission-forwarding"))

    def _competition(self, request):
        """Konkurs żądania. Brak konkursu = nie ma czego konfigurować, czyli 404.

        Obiekt bierzemy wprost z ``request.competition`` (ustawia go ``CompetitionMiddleware``),
        a nie z identyfikatora w adresie – i to jest cała reguła izolacji tego ekranu: nie ma tu
        adresu, pod którym dałoby się wskazać cudzy konkurs. Rola jest sprawdzona wcześniej,
        w ``CoordinatorRequiredMixin.dispatch``.
        """
        competition = request.competition
        if competition is None:
            raise Http404("Pod tym adresem nie stoi żaden konkurs.")
        return competition

    def _render(self, request, competition, form, *, status: int = 200):
        # Obowiązujące adresy czytamy **z bazy**, a nie z obiektu w ręku: po nieudanym zapisie
        # ``ModelForm._post_clean`` zdążył już wpisać do instancji wartość odrzuconą, a ramka
        # „dziś przekazujemy na…” ma pokazywać stan, który naprawdę obowiązuje.
        current = Competition.objects.filter(pk=competition.pk).first()
        context = {
            "competition": competition,
            "form": form,
            "limit": MAX_FORWARD_EMAILS,
            # Granica załącznika jest ustawieniem instalacji, a nie konkursu – ekran pokazuje ją,
            # żeby zdanie „większe pliki przychodzą bez załącznika” miało liczbę, a nie „pewien
            # rozmiar”.
            "limit_mb": settings.SUBMISSION_FORWARD_MAX_ATTACHMENT_MB,
            "current": current.forward_emails if current is not None else [],
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)
