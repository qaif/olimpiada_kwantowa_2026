"""Ekran „Status ucznia” ``/coordinator/student-status/`` – przegląd zaświadczeń i decyzje.

Jedna lista na jedną edycję (domyślnie bieżącą) z czterema filtrami z zamówienia organizatora:
oczekujące, zaakceptowane, odrzucone, brak. Przy każdym wierszu z plikiem: podgląd skanu, „Akceptuj”
i „Odrzuć z powodem”. Decyzja idzie przez ``apps.student_status.services`` (blokada wiersza, audyt,
list do uczestnika) – widok wyłącznie orkiestruje.

**Kto widzi skany.** Wyłącznie koordynator – tak jak wyłącznie koordynator widzi nazwiska obok kodów
(PROJEKT.md 2.3). Recenzent i komisja nie mają do tych adresów wstępu (``CoordinatorRequiredMixin``:
403), a ich paczki ZIP dostają wyłącznie **filtr** („tylko potwierdzony status”), nigdy skan ani
nazwisko. Skan niesie datę urodzenia, pełną nazwę szkoły, pieczątkę i podpis dyrektora – to są dane,
po które recenzent nie ma powodu sięgać, a ocenianie jest ślepe.

**Zakres konkursu.** Każdy wiersz jest szukany przez ``StudentStatusCertificate.objects.
for_competition(request.competition)`` – identyfikator zaświadczenia z sąsiedniej olimpiady daje 404,
a nie cudzy plik. Edycja z parametru ``?edition=`` jest szukana tak samo.

**Podgląd skanu jest ``inline``, ale nie jest zaufaną treścią.** Plik pochodzi od uczestnika i otwiera
go koordynator – pod domeną panelu, z sesją, która widzi dane wszystkich. Dlatego: typ odpowiedzi
jest typem wyliczonym **przez serwer** z sygnatury (``application/pdf``, ``image/jpeg``,
``image/png`` – nic innego nie przejdzie walidacji), ``X-Content-Type-Options: nosniff`` zabrania
przeglądarce zgadywać, a ``Content-Security-Policy: sandbox`` odbiera dokumentowi skrypty, formularze
i dostęp do pochodzenia panelu nawet wtedy, gdyby przeglądarka jednak coś w nim wykonała (dla
obrazów – PDF ma powód opisany przy nagłówku w :class:`CoordinatorStudentStatusFileView`). Podgląd
jest też dostępny wyłącznie po **czystym** skanie antywirusowym.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views.generic import View

from apps.competitions.models import Edition
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.student_status import services
from apps.student_status.forms import RejectForm
from apps.student_status.models import CertificateStatus, StudentStatusCertificate, enabled
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/student_status.html"

#: Wierszy na stronę – tyle, ile na ekranie przydziałów etapu. Filtry jadą w adresie.
PAGE_SIZE = 100


class _CoordinatorStudentStatusMixin(CoordinatorRequiredMixin):
    """Rola (w ``dispatch``) → flaga konkursu (404) → wiersze wyłącznie tego konkursu."""

    def competition_or_404(self):
        competition = self.competition
        if not enabled(competition):
            raise Http404("Ten konkurs nie zbiera zaświadczeń o statusie ucznia.")
        return competition

    def certificate_or_404(self, pk: int) -> StudentStatusCertificate:
        competition = self.competition_or_404()
        return get_object_or_404(
            StudentStatusCertificate.objects.for_competition(competition).select_related(
                "participant", "participant__user", "edition"
            ),
            pk=pk,
        )


def _list_url(edition_id=None, state: str = "", query: str = "") -> str:
    params = {
        key: value for key, value in (("edition", edition_id), ("status", state), ("q", query)) if value
    }
    base = reverse("web:coordinator-student-status")
    return f"{base}?{urlencode(params)}" if params else base


class CoordinatorStudentStatusView(_CoordinatorStudentStatusMixin, View):
    """``GET /coordinator/student-status/`` – lista zaświadczeń edycji z filtrami i licznikami."""

    def get(self, request):
        competition = self.competition_or_404()
        editions = list(Edition.objects.for_competition(competition).order_by("-is_current", "-created_at"))
        edition = self._edition(request, competition, editions)
        state = request.GET.get("status", "")
        state = state if state in services.FILTERS else ""
        query = (request.GET.get("q") or "").strip()
        rows, counts = services.coordinator_rows(edition, state=state, query=query) if edition else ([], {})
        page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
        context = {
            "edition": edition,
            "editions": editions,
            "state": state,
            "query": query,
            "filters": [
                {
                    "key": key,
                    "label": services.STATE_LABELS[value],
                    "count": counts.get(key, 0),
                    "active": key == state,
                    "url": _list_url(edition.pk if edition else None, "" if key == state else key, query),
                }
                for key, value in services.FILTERS.items()
            ],
            "all_url": _list_url(edition.pk if edition else None, "", query),
            "page_obj": page,
            "rows": list(page.object_list),
            "reject_form": RejectForm(),
            "return_query": request.GET.urlencode(),
            # Zapytanie **bez** numeru strony – do odnośników „Poprzednia/Następna”.
            "filter_query": urlencode(
                {
                    key: value
                    for key, value in (
                        ("edition", edition.pk if edition else ""),
                        ("status", state),
                        ("q", query),
                    )
                    if value
                }
            ),
            "pending_status": CertificateStatus.PENDING,
            "accepted_status": CertificateStatus.ACCEPTED,
            "rejected_status": CertificateStatus.REJECTED,
        }
        return TemplateResponse(request, TEMPLATE, context)

    @staticmethod
    def _edition(request, competition, editions):
        """Edycja z ``?edition=<id>`` – wyłącznie spośród edycji **tego** konkursu – albo bieżąca."""
        raw = request.GET.get("edition", "")
        if raw.isdigit():
            chosen = next((edition for edition in editions if edition.pk == int(raw)), None)
            if chosen is None:
                raise Http404("Nie ma takiej edycji w tym konkursie.")
            return chosen
        return current_edition(competition) or (editions[0] if editions else None)


class CoordinatorStudentStatusFileView(_CoordinatorStudentStatusMixin, View):
    """``GET /coordinator/student-status/<id>/file/`` – podgląd skanu (``?download=1`` – pobranie).

    Tylko po czystym skanie antywirusowym. Nazwa pliku jest zbudowana z kodu uczestnika i wersji,
    a nie z nazwy od uczestnika (tej w ogóle nie przechowujemy). Każde otwarcie zostawia wpis
    ``student_status.viewed`` – skan z datą urodzenia jest daną, przy której pytanie „kto to oglądał”
    ma mieć odpowiedź.
    """

    def get(self, request, pk: int):
        from apps.core.models import audit

        certificate = self.certificate_or_404(pk)
        if not certificate.is_clean:
            raise Http404("Plik nie przeszedł skanu antywirusowego albo został już usunięty.")
        handle = services.open_scan(certificate)
        if handle is None:
            raise Http404("Plik jest niedostępny w storage.")
        download = request.GET.get("download") == "1"
        audit(
            request.user,
            "student_status.viewed",
            certificate,
            {"version": certificate.version, "download": download},
            request=request,
        )
        response = FileResponse(
            handle,
            as_attachment=download,
            filename=(
                f"status-ucznia-{certificate.participant.public_code}-v{certificate.version}."
                f"{certificate.extension}"
            ),
            content_type=certificate.mime,
        )
        response["X-Content-Type-Options"] = "nosniff"
        if certificate.mime.startswith("image/"):
            # Obraz otwarty wprost jest dokumentem przeglądarki pod domeną panelu – ``sandbox``
            # odbiera mu pochodzenie, skrypty i formularze. PDF-owi **nie** stawiamy ``sandbox``:
            # Chrome odmawia wtedy uruchomienia wbudowanej przeglądarki PDF (piaskownica wyłącza
            # wtyczki), a sama przeglądarka PDF i tak działa w procesie odciętym od strony. PDF
            # dostaje zwykłą politykę serwisu, którą dokłada ``ContentSecurityPolicyMiddleware``.
            response["Content-Security-Policy"] = (
                "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
            )
        add_never_cache_headers(response)
        return response


class _DecisionView(_CoordinatorStudentStatusMixin, View):
    """Baza akcji „Akceptuj” i „Odrzuć”: POST → serwis → komunikat → powrót na listę z filtrami."""

    def post(self, request, pk: int):
        certificate = self.certificate_or_404(pk)
        try:
            message = self.perform(request, certificate)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, message)
        return redirect(self._return_url(request, certificate))

    @staticmethod
    def _return_url(request, certificate) -> str:
        """Powrót na listę **z tymi samymi filtrami**, z których przyszło kliknięcie.

        Parametry wracają przez ukryte pole ``return_query`` i są składane od nowa z trzech znanych
        kluczy – żaden inny parametr ani adres z formularza nie trafia do przekierowania.
        """
        from django.http import QueryDict

        params = QueryDict(request.POST.get("return_query", ""))
        edition_id = (
            params.get("edition") if (params.get("edition") or "").isdigit() else certificate.edition_id
        )
        state = params.get("status", "")
        return _list_url(
            edition_id,
            state if state in services.FILTERS else "",
            (params.get("q") or "").strip()[:100],
        )

    def perform(self, request, certificate) -> str:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError


class CoordinatorStudentStatusAcceptView(_DecisionView):
    """``POST /coordinator/student-status/<id>/accept/``."""

    def perform(self, request, certificate) -> str:
        services.accept(certificate, actor=request.user, request=request)
        return (
            f"Zaakceptowano zaświadczenie uczestnika {certificate.participant.public_code}. "
            "Uczestnik dostanie wiadomość e-mail."
        )


class CoordinatorStudentStatusRejectView(_DecisionView):
    """``POST /coordinator/student-status/<id>/reject/`` z polem ``reason``."""

    def perform(self, request, certificate) -> str:
        form = RejectForm(request.POST)
        if not form.is_valid():
            raise DomainError(
                " ".join(form.errors.get("reason", ["Podaj powód odrzucenia."])), "REASON_REQUIRED", 400
            )
        services.reject(certificate, form.cleaned_data["reason"], actor=request.user, request=request)
        return (
            f"Odrzucono zaświadczenie uczestnika {certificate.participant.public_code}. "
            "Uczestnik zobaczy powód w panelu i dostanie wiadomość e-mail."
        )
