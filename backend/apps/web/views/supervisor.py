"""Panel opiekuna szkolnego ``/supervisor/`` oraz publiczna rejestracja opiekuna.

Panel jest jednym ekranem do czytania: lista uczniów, którzy wskazali ten adres e-mail,
i stan ich prac na ścieżce „oddane → w ocenie → oceniona → wyniki”. Żadnej czynności
zmieniającej przebieg zawodów tu nie ma i nie będzie – jedyny zapis, jaki opiekun wykonuje,
to oświadczenie o udziale własnej szkoły w edycji oraz pobranie własnego zaświadczenia.

Skąd uprawnienie: z decyzji ucznia, który wpisał adres opiekuna w swoim profilu. Cała reguła
mieszka w ``apps.accounts.supervisors`` i widok jej nie powtarza; tutaj jest wyłącznie
orkiestracja, tak samo jak w pozostałych panelach.

Rejestracja opiekuna stoi w tym module, a nie wśród widoków publicznych, bo jest częścią tej
samej funkcji serwisu i czyta się ją razem z panelem, do którego prowadzi. Limit żądań i CAPTCHA
są te same, co przy pozostałych rejestracjach.
"""

from __future__ import annotations

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView, View

from apps.accounts.supervisors import (
    confirm_participation,
    has_confirmed,
    register_supervisor,
    student_rows,
)
from apps.competitions.services import current_edition
from apps.results.certificates import pdf_filename, render_pdf
from apps.results.models import Certificate
from apps.web.mixins import ActionViewMixin
from apps.web.supervisor_forms import SupervisorRegisterForm
from apps.web.supervisor_mixins import SupervisorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from .public import ServiceFormView, remember_registration

DASHBOARD_TEMPLATE = "web/supervisor/dashboard.html"
REGISTER_TEMPLATE = "web/supervisor/register.html"


class RegisterSupervisorView(ThrottledFormMixin, ServiceFormView):
    """``/register/supervisor/`` – założenie konta opiekuna szkolnego.

    Ten sam scope limitu (``register``) i ta sama droga po rejestracji, co przy uczestniku
    i komitecie: konto powstaje nieaktywne i czeka na link z listu, a strona „sprawdź skrzynkę”
    jest wspólna dla wszystkich rejestracji.
    """

    template_name = REGISTER_TEMPLATE
    form_class = SupervisorRegisterForm
    success_url = reverse_lazy("web:register-done")
    throttle_scope = "register"
    success_message = ""
    registration_kind = "supervisor"

    def call_service(self, form):
        register_supervisor(**form.cleaned_data, request=self.request)
        remember_registration(self.request, form.cleaned_data["email"], self.registration_kind)


class SupervisorDashboardView(SupervisorRequiredMixin, TemplateView):
    """``/supervisor/`` – uczniowie, którzy wskazali ten adres, i stan ich prac.

    Punktów przed ogłoszeniem wyników etapu tu nie ma: wiersz niesie ścieżkę statusu, a liczbę
    dopiero wtedy, gdy jest publiczna. Reguła stoi w serwisie (``student_rows``), żeby ekran
    nie mógł jej obejść przez dopisanie kolumny.
    """

    template_name = DASHBOARD_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supervisor = self.supervisor
        edition = current_edition()
        context.update(
            {
                "supervisor": supervisor,
                "edition": edition,
                "rows": student_rows(supervisor, edition),
                "confirmed": has_confirmed(supervisor, edition),
                "certificates": list(
                    Certificate.objects.filter(supervisor=supervisor)
                    .select_related("edition")
                    .order_by("-issued_at", "-id")
                ),
            }
        )
        return context


class ConfirmParticipationView(ActionViewMixin, SupervisorRequiredMixin, View):
    """POST „Potwierdzam udział szkoły” – i to samo kliknięcie oświadczenie wycofuje."""

    success_url = "/supervisor/"

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:supervisor")

    def perform(self, request) -> str:
        result = confirm_participation(
            self.supervisor, current_edition(), actor=request.user, request=request
        )
        if result["confirmed"]:
            return "Dziękujemy – udział szkoły w tej edycji został potwierdzony."
        return "Potwierdzenie udziału szkoły zostało wycofane."


class SupervisorCertificateDownloadView(SupervisorRequiredMixin, View):
    """``/supervisor/certificates/<pk>/`` – własne zaświadczenie opiekuna jako PDF."""

    def get(self, request, pk: int):
        certificate = get_object_or_404(
            Certificate.objects.filter(supervisor=self.supervisor).select_related(
                "edition", "supervisor__user"
            ),
            pk=pk,
        )
        return FileResponse(
            iter([render_pdf(certificate)]),
            content_type="application/pdf",
            as_attachment=True,
            filename=pdf_filename(certificate),
        )
