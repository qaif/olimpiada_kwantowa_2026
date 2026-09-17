"""Panel koordynatora: szablony graficzne dokumentów i zaświadczenia dla opiekunów.

Dlaczego osobny moduł od ``coordinator_quality``. Tamten ekran odpowiada na pytanie „kto z tego
**etapu** dostaje dyplom i jaki” – stoi na podglądzie wyników i bez etapu nie istnieje. Te dwa
ekrany dotyczą całej edycji i nie mają z punktami nic wspólnego: jeden opisuje, **jak wygląda**
dokument, drugi – komu należy się zaświadczenie za pracę z uczniami. Wspólny plik kazałby czytać
trzysta linii kalibracji i podobieństwa, żeby dojść do formularza z tłem dyplomu.

Podział czynności jest ten sam, co w reszcie panelu: reguła domenowa mieszka w serwisie
(``apps.results.certificates``), widok ją orkiestruje, a uprawnienie rozstrzyga
``CoordinatorRequiredMixin`` – nigdy menu.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import SchoolSupervisor
from apps.competitions.services import current_edition
from apps.core.models import audit
from apps.results.certificates import (
    build_certificates_zip,
    compose_pdf,
    issue_certificate,
    issue_supervisor_certificates,
    make_default_template,
    sample_content,
    supervisors_with_participants,
)
from apps.results.models import Certificate, CertificateKind, CertificateTemplate
from apps.web.certificate_forms import CertificateTemplateForm
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/certificate_templates.html"
FORM_TEMPLATE = "web/coordinator/certificate_template_form.html"
SUPERVISORS_TEMPLATE = "web/coordinator/supervisors.html"


def templates_for_competition(competition):
    """Szablony dokumentów **tego konkursu** – razem z tymi „na wszystkie edycje”.

    Jedno wywołanie managera, bo wydanie D dało szablonom własną kolumnę
    (``CertificateTemplate.competition``). ``edition`` bywa nadal puste i to jest jego cecha,
    nie brak: puste znaczy „każda edycja” i tak wygląda winieta używana rok po roku. Różnica
    jest w tym, **czyja** to winieta: wiersz bez edycji przestał należeć do nikogo i należy do
    konkursu, w którym go wgrano, więc sąsiad go nie widzi i nie dostanie go pod swój dyplom.
    """
    return CertificateTemplate.objects.for_competition(competition)


def _template(competition, pk: int) -> CertificateTemplate:
    """Szablon widoczny w tym konkursie albo 404."""
    return get_object_or_404(templates_for_competition(competition).select_related("edition"), pk=pk)


class CertificateTemplateListView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/certificates/templates/`` – wszystkie szablony, także wyłączone.

    Wyłączone zostają na liście z tego samego powodu, dla którego zostają w bazie: „wróćmy do
    zeszłorocznej winiety” ma być jednym kliknięciem. Gdyby znikały, jedyną drogą powrotu byłoby
    ``/admin/`` albo ponowne wgranie tła.
    """

    def get(self, request):
        templates = list(templates_for_competition(request.competition).select_related("edition"))
        context = {
            "templates": templates,
            # Pusta lista jest normalnym i **poprawnym** stanem serwisu: bez ani jednego szablonu
            # dokumenty składają się układem wbudowanym. Strona mówi to wprost, bo inaczej
            # czyta się to jak „coś tu jeszcze nie działa”.
            "kinds": CertificateKind.choices,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


class CertificateTemplateFormView(CoordinatorRequiredMixin, View):
    """Dodanie (``…/templates/new/``) i zmiana (``…/templates/<id>/``) szablonu.

    Jedna klasa na oba adresy, bo to jeden formularz i jedna walidacja – różnicą jest wyłącznie
    to, czy ``instance`` istnieje. Dwie klasy znaczyłyby dwie kopie obsługi plików i układu.
    """

    def get(self, request, pk: int | None = None):
        template = _template(request.competition, pk) if pk is not None else None
        return self._render(
            request,
            template,
            CertificateTemplateForm(instance=template, competition=request.competition),
        )

    def post(self, request, pk: int | None = None):
        template = _template(request.competition, pk) if pk is not None else None
        form = CertificateTemplateForm(
            request.POST, request.FILES, instance=template, competition=request.competition
        )
        if not form.is_valid():
            return self._render(request, template, form, status=400)
        saved = form.save(commit=False)
        if template is None:
            # Autora zapisujemy wyłącznie przy utworzeniu – „kto to wgrał” jest pytaniem
            # o pochodzenie szablonu, a nie o to, kto ostatni przesunął napis o dwa punkty.
            saved.created_by = request.user
            # Właściciel również tylko przy utworzeniu: szablon należy do konkursu, w którym go
            # wgrano, i ta przynależność nie jest polem formularza – przeniesienie winiety do
            # sąsiada nie jest czynnością, którą ten ekran ma umieć.
            saved.competition = request.competition
        saved.save()
        audit(
            request.user,
            "certificate_template.updated" if template is not None else "certificate_template.created",
            saved,
            # Bez zawartości plików i bez układu: wpis audytowy ma mówić, czego dotyczy zmiana
            # i co obowiązuje, a nie być drugą kopią szablonu.
            {
                "kind": saved.kind,
                "edition_id": saved.edition_id,
                "is_active": saved.is_active,
                "has_background": bool(saved.background),
            },
            request=request,
        )
        messages.success(request, f"Szablon „{saved.name}” został zapisany.")
        return redirect(reverse("web:coordinator-certificate-templates"))

    def _render(self, request, template, form, *, status: int = 200):
        context = {"template": template, "form": form}
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class CertificateTemplateDeleteView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """``POST …/templates/<id>/delete/`` – usunięcie szablonu.

    Bez osobnego kroku potwierdzenia: na szablonie nie wisi ani jeden wystawiony dokument (PDF
    powstaje przy pobraniu, więc usunięcie szablonu zmienia wygląd przyszłych dokumentów, a nie
    unieważnia żadnego numeru), a wpis audytowy zachowuje nazwę i zakres skasowanego wiersza.
    """

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:coordinator-certificate-templates")

    def perform(self, request, pk: int) -> str:
        template = _template(request.competition, pk)
        name = template.name
        audit(
            request.user,
            "certificate_template.deleted",
            template,
            {"name": name, "kind": template.kind, "edition_id": template.edition_id},
            request=request,
        )
        template.delete()
        return (
            f"Szablon „{name}” został usunięty. "
            "Dokumenty tego rodzaju składają się od teraz układem domyślnym."
        )


class CertificateTemplateDefaultView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """``POST …/templates/<id>/default/`` – „Ustaw jako domyślny dla rodzaju”."""

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:coordinator-certificate-templates")

    def perform(self, request, pk: int) -> str:
        template = _template(request.competition, pk)
        replaced = make_default_template(template)
        audit(
            request.user,
            "certificate_template.default_set",
            template,
            {"kind": template.kind, "edition_id": template.edition_id, "replaced": replaced},
            request=request,
        )
        zakres = f"{template.kind_label}, {template.edition_label}"
        if replaced:
            return f"Szablon „{template.name}” obowiązuje teraz dla: {zakres}. Poprzedni został wyłączony."
        return f"Szablon „{template.name}” obowiązuje teraz dla: {zakres}."


class CertificateTemplatePreviewView(CoordinatorRequiredMixin, View):
    """``GET …/templates/<id>/preview/`` – przykładowy dokument złożony tym szablonem.

    Podgląd idzie przez ``compose_pdf`` z danymi „na niby”, a nie przez wystawienie dokumentu
    komukolwiek: inaczej obejrzenie układu zużywałoby numer z puli i zostawiało w rejestrze
    dyplom, którego nikt nie chciał wystawić. Plik otwiera się **w karcie**, a nie pobiera –
    układ ogląda się, poprawia i ogląda ponownie, a katalog Pobrane nie ma się z tego zapełnić.
    """

    def get(self, request, pk: int):
        template = _template(request.competition, pk)
        kind = template.kind or CertificateKind.LAUREAT
        content = sample_content(kind, template.edition)
        return FileResponse(
            iter([compose_pdf(content, template)]),
            content_type="application/pdf",
            filename=f"podglad-szablonu-{template.pk}.pdf",
        )


# --- opiekunowie szkolni ------------------------------------------------------------------------


class CoordinatorSupervisorsView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/supervisors/`` – opiekunowie z uczniami w bieżącej edycji.

    Osobny ekran obok listy kont z filtrem ``?role=supervisor`` i to nie jest powtórzenie. Tamta
    lista odpowiada na pytanie „jakie mamy konta” i nie wie nic o zawodach; ta odpowiada na
    „komu należy się zaświadczenie”, więc pokazuje **liczbę uczniów w edycji** i stan dokumentu,
    a nie datę założenia konta. Bez tej liczby przycisk „Wystaw” byłby decyzją na ślepo.
    """

    def get(self, request):
        edition = current_edition(request.competition)
        rows = supervisors_with_participants(edition) if edition is not None else []
        issued = {
            certificate.supervisor_id: certificate
            for certificate in Certificate.objects.filter(
                supervisor__isnull=False, edition=edition
            ).select_related("edition")
        }
        context = {
            "edition": edition,
            "rows": [row | {"certificate": issued.get(row["supervisor"].pk)} for row in rows],
        }
        return TemplateResponse(request, SUPERVISORS_TEMPLATE, context)


class IssueSupervisorCertificateView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """``POST /coordinator/supervisors/<id>/certificate/`` – zaświadczenie dla jednego opiekuna."""

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:coordinator-supervisors")

    def perform(self, request, pk: int) -> str:
        supervisor = get_object_or_404(
            SchoolSupervisor.objects.for_competition(request.competition).select_related("user"),
            pk=pk,
        )
        edition = current_edition(request.competition)
        if edition is None:
            return "Nie ustawiono bieżącej edycji – zaświadczenie nie ma do czego się odnosić."
        certificate, created = issue_certificate(
            edition=edition,
            kind=CertificateKind.OPIEKUN,
            supervisor=supervisor,
            actor=request.user,
            request=request,
        )
        if not created:
            return f"Ten opiekun ma już zaświadczenie w tej edycji: {certificate.number}."
        return f"Wystawiono zaświadczenie {certificate.number}."


class IssueSupervisorCertificatesView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/supervisors/certificates/`` – zaświadczenia dla wszystkich i paczka ZIP.

    Idempotentne: opiekun z zaświadczeniem w tej edycji zachowuje swój numer, a w paczce i tak
    jest jego dokument. Dzięki temu „wystaw wszystkim” wolno kliknąć po dopisaniu jednej osoby,
    zamiast wybierać brakujących ręcznie.
    """

    def post(self, request):
        edition = current_edition(request.competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – nie ma dla czego wystawiać zaświadczeń.")
            return redirect(reverse("web:coordinator-supervisors"))
        certificates = issue_supervisor_certificates(edition, actor=request.user, request=request)
        if not certificates:
            messages.error(
                request,
                "Żaden opiekun nie ma w tej edycji ucznia z wpisem do etapu – "
                "nie ma komu wystawić zaświadczenia.",
            )
            return redirect(reverse("web:coordinator-supervisors"))
        archive = build_certificates_zip(certificates)
        response = FileResponse(archive.stream, content_type="application/zip", as_attachment=True)
        response["Content-Disposition"] = 'attachment; filename="zaswiadczenia-opiekunowie.zip"'
        return response
