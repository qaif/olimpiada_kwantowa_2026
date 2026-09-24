"""Panel uczestnika: „Status ucznia” – wzór zaświadczenia, wgranie skanu i stan sprawy.

Osobny moduł i osobny adres (``/me/status-ucznia/``), a nie kolejna zakładka pulpitu, z tych samych
powodów, co kafel wpisowego (``participant_fees``): funkcja istnieje wyłącznie w konkursie z flagą
``student_status_certificate``, więc ma być wyłączalna w jednym miejscu i przy wyłączonej fladze
kosztować pulpit **zero zapytań**; a wgranie pliku ma własny formularz z komunikatami błędów, które
w zakładce pulpitu nie miałyby gdzie stanąć.

Na pulpicie stoi wyłącznie **przypomnienie** (``student_status_card_context``) – dopóki status nie
jest zaakceptowany. Status niczego nie blokuje: prace oddaje się tak samo z zaświadczeniem i bez
niego (prośba organizatora z 24.09.2026 mówi o tym wprost). Blokada uploadu prac zależna od
dokumentu, który szkoła wystawia w swoim tempie, zamieniałaby opóźnienie sekretariatu w utracony
etap zawodów.

Bramki w kolejności: rola (``ParticipantRequiredMixin`` – anonim 302, obcy 403), dopiero potem flaga
i edycja bieżąca (404). Kolejność jest ta sama, co w panelu koordynatora: odpowiedź nie mówi osobie
spoza roli, jak ten konkurs jest skonfigurowany.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.generic import View

from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.student_status import services
from apps.student_status.forms import ScanUploadForm
from apps.student_status.models import CertificateStatus, enabled
from apps.student_status.pdf import compose_pdf, pdf_filename
from apps.student_status.validators import MAX_FILE_MB
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin

TEMPLATE = "web/participant/student_status.html"


def student_status_card_context(competition, participant, edition) -> dict:
    """Kontekst przypomnienia na pulpicie albo **pusty słownik** – wtedy szablon nic nie włącza.

    Pusto, bez ani jednego zapytania, gdy konkurs nie zbiera zaświadczeń albo nie ma edycji
    bieżącej. Flagę czyta ta funkcja, a nie szablon (§ 2.1 punkt 3): flaga czytana w szablonie jest
    flagą, której nie widać w teście widoku. Przy włączonej fladze – jedno zapytanie o wiersz bieżący.
    """
    if edition is None or not enabled(competition):
        return {}
    return {"student_status": services.status_summary(participant, edition)}


class _StudentStatusMixin(ParticipantRequiredMixin):
    """Wspólna bramka: flaga konkursu i edycja bieżąca. Brak którejkolwiek = 404."""

    def edition_or_404(self):
        if not enabled(self.competition):
            raise Http404("Ten konkurs nie zbiera zaświadczeń o statusie ucznia.")
        edition = current_edition(self.competition)
        if edition is None:
            raise Http404("Konkurs nie ma bieżącej edycji.")
        return edition


class StudentStatusView(_StudentStatusMixin, ThrottledFormMixin, View):
    """``GET|POST /me/status-ucznia/`` – stan zaświadczenia, historia wersji i formularz wgrania.

    Limit żądań jest scope'em ``upload`` – tym samym, co wysyłka rozwiązań. To jest ta sama czynność
    (plik od uczestnika do prywatnego storage i do skanera) i ten sam koszt po stronie serwera;
    osobny scope znaczyłby, że uczestnik ma dwa niezależne budżety na zapchanie kolejki ``scan``.
    """

    throttle_scope = "upload"

    def get(self, request):
        edition = self.edition_or_404()
        return self._render(request, edition, ScanUploadForm())

    def post(self, request):
        edition = self.edition_or_404()
        form = ScanUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, edition, form, status=400)
        try:
            services.upload_scan(
                self.participant, edition, form.cleaned_data["file"], actor=request.user, request=request
            )
        except DomainError as exc:
            form.add_error("file", str(exc.detail))
            if exc.machine_code == "STUDENT_STATUS_ALREADY_ACCEPTED":
                # Przy zaakceptowanym zaświadczeniu formularza na stronie nie ma, więc błąd przy polu
                # nie miałby gdzie stanąć – mówi go komunikat nad treścią (POST wysłany z drugiej
                # karty przeglądarki, otwartej jeszcze przed akceptacją).
                messages.error(request, str(exc.detail))
            return self._render(request, edition, form, status=400)
        messages.success(
            request,
            "Zaświadczenie zostało wysłane. Koordynator sprawdzi je i poinformuje Cię e-mailem o decyzji.",
        )
        return redirect(reverse("web:student-status"))

    def _render(self, request, edition, form, *, status: int = 200):
        summary = services.status_summary(self.participant, edition)
        context = {
            **summary,
            "participant": self.participant,
            "history": [row for row in services.history(self.participant, edition) if not row.is_current],
            "form": form,
            "max_file_mb": MAX_FILE_MB,
            "rejected_status": CertificateStatus.REJECTED,
            "pending_status": CertificateStatus.PENDING,
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)


class StudentStatusTemplateView(_StudentStatusMixin, View):
    """``GET /me/status-ucznia/wzor.pdf`` – imienny wzór zaświadczenia do wydrukowania.

    Składany przy każdym pobraniu z bieżących danych profilu (``apps.student_status.pdf``), więc
    poprawka szkoły w profilu jest od razu na następnym wydruku. ``no-store``, bo to jest dokument
    z datą urodzenia – przeglądarka nie ma go trzymać w pamięci podręcznej współdzielonego komputera
    w pracowni szkolnej.
    """

    def get(self, request):
        edition = self.edition_or_404()
        participant = self.participant
        content = compose_pdf(
            participant,
            edition,
            competition=self.competition,
            panel_url=request.build_absolute_uri(reverse("web:student-status")),
        )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{pdf_filename(participant)}"'
        add_never_cache_headers(response)
        return response


class StudentStatusOwnFileView(_StudentStatusMixin, View):
    """``GET /me/status-ucznia/plik/`` – własny, **bieżący** plik uczestnika (żeby zobaczyć, co wysłał).

    Wyłącznie wiersz bieżący i wyłącznie własny: identyfikatora w adresie nie ma, więc nie ma czego
    podmienić na cudzy. Plik zainfekowany nie istnieje (usuwa go skan), a plik jeszcze
    nieprzeskanowany wolno pobrać jego autorowi – to jego własne bajty, tak samo jak przy pracach.
    """

    def get(self, request):
        edition = self.edition_or_404()
        certificate = services.current_certificate(self.participant, edition)
        if certificate is None or not certificate.has_file:
            raise Http404("Nie ma wgranego pliku.")
        handle = services.open_scan(certificate)
        if handle is None:
            raise Http404("Plik jest niedostępny w storage.")
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=f"zaswiadczenie-v{certificate.version}.{certificate.extension}",
            content_type=certificate.mime,
        )
        add_never_cache_headers(response)
        return response
