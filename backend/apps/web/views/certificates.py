"""Dyplomy poza panelem koordynatora: własne dokumenty uczestnika i publiczna weryfikacja kodu.

Osobny moduł od ``coordinator_quality``, bo to inne role i inne reguły widoczności. Tam
koordynator wystawia dokumenty i widzi wszystkie; tutaj uczestnik pobiera **wyłącznie swoje**,
a strona weryfikacji jest dostępna bez logowania dla każdego, kto przepisze kod z papieru.

Reguła widoczności jest w każdym z tych widoków domyślnie zamknięta i sprawdzana w zapytaniu,
a nie po wczytaniu obiektu: dokument, który nie należy do zalogowanego uczestnika, jest dla niego
404 z tego samego queryseta, z którego bierze się lista – nie ma tu drugiego warunku, który
dałoby się przeoczyć przy zmianie.

Strona weryfikacji **nie** wydaje danych osobowych bez zgody na publikację nazwiska
(``apps.results.certificates.verify``). Byłaby inaczej wyszukiwarką „kto dostał dyplom o tym
numerze”, dostępną dla każdego, komu wpadł w ręce cudzy dokument albo jego zdjęcie.
"""

from __future__ import annotations

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.views.generic import TemplateView, View

from apps.results.certificates import pdf_filename, render_pdf, verify
from apps.results.models import Certificate
from apps.web.mixins import ParticipantRequiredMixin

PARTICIPANT_TEMPLATE = "web/participant/certificates.html"
VERIFY_TEMPLATE = "web/certificate_verify.html"


def _participant_certificates(participant):
    """Dokumenty uczestnika – wszystkie etapy, od najnowszego. Jedno zapytanie."""
    return (
        Certificate.objects.filter(entry__participant=participant)
        .select_related("edition", "entry__stage")
        .order_by("-issued_at", "-id")
    )


class ParticipantCertificatesView(ParticipantRequiredMixin, TemplateView):
    """``/me/certificates/`` – lista własnych dyplomów i zaświadczeń.

    Pusta lista jest normalnym stanem przez większość roku: dokumenty wystawia komitet po
    zakończeniu zawodów. Strona mówi to wprost, bo „brak dyplomów” bez wyjaśnienia czyta się
    jak informacja o wyniku.
    """

    template_name = PARTICIPANT_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["certificates"] = list(_participant_certificates(self.participant))
        return context


class ParticipantCertificateDownloadView(ParticipantRequiredMixin, View):
    """``/me/certificates/<pk>/`` – PDF własnego dokumentu, składany w locie."""

    def get(self, request, pk: int):
        certificate = get_object_or_404(_participant_certificates(self.participant), pk=pk)
        return FileResponse(
            iter([render_pdf(certificate)]),
            content_type="application/pdf",
            as_attachment=True,
            filename=pdf_filename(certificate),
        )


class CertificateVerifyView(View):
    """``/dyplomy/<code>/`` – publiczne sprawdzenie dokumentu po kodzie z papieru.

    Nieznany kod nie jest błędem 404: strona wygląda tak samo, tylko mówi „takiego dokumentu
    nie ma”. Rozróżnienie po kodzie odpowiedzi HTTP zamieniłoby ten adres w narzędzie do
    sprawdzania kodów maszynowo, a treść strony i tak jest tu jedyną odpowiedzią, po którą
    ktokolwiek tu przychodzi.
    """

    def get(self, request, code: str):
        result = verify(code)
        context = {"code": (code or "").strip().upper(), "certificate": result}
        return TemplateResponse(request, VERIFY_TEMPLATE, context)
