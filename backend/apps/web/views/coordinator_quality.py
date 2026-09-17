"""Ekrany jakości oceniania i dokumentów: kalibracja, podobieństwa, kwalifikacja ręczna, dyplomy.

Cztery narzędzia w jednym module, bo łączy je rola i moment w kalendarzu zawodów: wszystkie
czyta koordynator **po** ocenianiu, przygotowując posiedzenie komitetu. Kalibracja odpowiada na
pytanie „czy któryś recenzent odstaje”, podobieństwa – „czy któreś dwie prace są tą samą pracą”,
kwalifikacja ręczna jest zapisem decyzji, które na tym posiedzeniu zapadają, a dyplomy – jej
skutkiem na papierze.

Podział pracy jest ten sam, co w pozostałych modułach panelu: widok orkiestruje, reguła domenowa
stoi w serwisie (``apps.grading.calibration``, ``apps.submissions.similarity``,
``apps.results.manual``, ``apps.results.certificates``). Żaden z tych ekranów nie liczy niczego
sam i żaden nie sięga do bazy po dane, których serwis by mu nie podał.

Cała nawigacja działa **bez JavaScriptu**: sortowanie kalibracji i próg podobieństwa jadą zwykłymi
odnośnikami i formularzem GET, a każda zmiana stanu jest osobnym POST-em z tokenem CSRF.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView, View

from apps.accounts.supervisors import supervisors_for_edition
from apps.competitions.models import Stage, StageEntry
from apps.core.api import DomainError
from apps.grading.calibration import (
    MIN_REVIEWS_FOR_TENDENCY,
    SORT_COLUMNS,
    TENDENCY_THRESHOLD,
    stage_calibration,
)
from apps.results.certificates import (
    build_certificates_zip,
    issue_certificate,
    pdf_filename,
    render_pdf,
)
from apps.results.manual import set_manual_qualification
from apps.results.models import Certificate, CertificateKind
from apps.results.services import compute_stage_results
from apps.submissions.models import SIMILARITY_STORE_THRESHOLD, SubmissionSimilarity
from apps.submissions.similarity import (
    DEFAULT_REPORT_THRESHOLD,
    MAX_PAIRS_PER_PROBLEM,
    comparable_problems,
    diff_table,
    pairs_for_stage,
    toggle_report,
)
from apps.web.coordinator_forms import (
    CertificateIssueForm,
    ManualQualificationForm,
    SimilarityFilterForm,
)
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin
from apps.web.scoping import for_competition_or_unclaimed

CALIBRATION_TEMPLATE = "web/coordinator/calibration.html"
SIMILARITY_TEMPLATE = "web/coordinator/similarity.html"
SIMILARITY_PAIR_TEMPLATE = "web/coordinator/similarity_pair.html"
CERTIFICATES_TEMPLATE = "web/coordinator/certificates.html"


def _stage(competition, stage_id: int) -> Stage:
    """Etap **tego konkursu**, z doczytaną edycją – stoi w nagłówku każdego z tych ekranów.

    404 dla etapu cudzego konkursu wychodzi z querysetu, a nie z gałęzi w widoku: cztery ekrany
    tego modułu wołają tę jedną funkcję i żaden nie może jej pominąć (§ 3.6).
    """
    return get_object_or_404(
        Stage.objects.for_competition(competition).select_related("edition"), pk=stage_id
    )


# --- 1. Kalibracja recenzentów ------------------------------------------------------------------


class StageCalibrationView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/calibration/`` – kto punktuje surowiej, a kto łagodniej.

    Sortowanie jedzie parametrem ``?sort=kolumna`` (z minusem = malejąco), więc stan ekranu
    mieści się w adresie: da się go odświeżyć, zapisać w zakładkach i wkleić w wiadomości do
    reszty komitetu. Dokładnie tak samo działa symulacja progu i przeglądarka audytu.
    """

    template_name = CALIBRATION_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = _stage(self.competition, self.kwargs["stage_id"])
        result = stage_calibration(stage, sort=self.request.GET.get("sort"))
        context.update(
            {
                "stage": stage,
                **result,
                # Kolumny sortowalne razem z gotowym parametrem odwracającym kierunek – szablon
                # nie ma sam składać wartości ``?sort=``, bo wtedy reguła „minus znaczy malejąco”
                # istniałaby w dwóch miejscach.
                "sort_links": {
                    column: (
                        f"-{column}" if result["sort"] == column and not result["descending"] else column
                    )
                    for column in SORT_COLUMNS
                },
                "threshold": TENDENCY_THRESHOLD,
                "min_reviews": MIN_REVIEWS_FOR_TENDENCY,
            }
        )
        return context


# --- 2. Podobieństwo rozwiązań ------------------------------------------------------------------


def _threshold(request) -> float:
    """Próg pokazywania par z adresu. Wartość spoza zakresu wraca do domyślnej, bez błędu.

    Adres z ekranu bywa wklejany w wiadomościach i przepisywany ręcznie, a literówka w parametrze
    nie może kończyć się stroną błędu – zwłaszcza że wynik i tak jest widoczny na ekranie obok
    pola z progiem.
    """
    form = SimilarityFilterForm(request.GET or None)
    if form.is_valid() and form.cleaned_data.get("threshold") is not None:
        return float(form.cleaned_data["threshold"])
    return DEFAULT_REPORT_THRESHOLD


class StageSimilarityView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/similarity/`` – pary prac o wysokim podobieństwie kodu.

    Ekran jest wyłącznie odczytem: przeliczenie uruchamia osobny przycisk (POST → zadanie Celery),
    bo przy komplecie prac finału trwa ono minuty, a wejście na stronę nie ma prawa niczego liczyć
    ani zapisywać.
    """

    template_name = SIMILARITY_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = _stage(self.competition, self.kwargs["stage_id"])
        threshold = _threshold(self.request)
        context.update(
            {
                "stage": stage,
                "threshold": threshold,
                "form": SimilarityFilterForm(initial={"threshold": threshold}),
                "pairs": pairs_for_stage(stage, threshold=threshold),
                # Zadania, których ekran w ogóle dotyczy. Bez tej listy pusta tabela w etapie
                # z samymi zadaniami PDF-owymi wyglądałaby jak „nic nie znaleziono”, a znaczy
                # „nie ma czego porównywać”.
                "problems": comparable_problems(stage),
                "store_threshold": SIMILARITY_STORE_THRESHOLD,
                "max_pairs": MAX_PAIRS_PER_PROBLEM,
            }
        )
        return context


class RecomputeSimilarityView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST „Przelicz”: zleca porównanie prac etapu zadaniu w tle (kolejka domyślna)."""

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-similarity", args=[stage_id])

    def perform(self, request, stage_id: int) -> str:
        from apps.submissions.tasks import recompute_similarity

        stage = _stage(request.competition, stage_id)
        if not comparable_problems(stage):
            raise DomainError(
                "W tym etapie nie ma zadań oddawanych jako kod – nie ma czego porównywać.",
                "NO_COMPARABLE_PROBLEMS",
            )
        recompute_similarity.delay(stage.pk, request.user.pk)
        return (
            "Przeliczanie podobieństw zostało zlecone. Wynik pojawi się na tej stronie, "
            "kiedy zadanie się skończy – odśwież ją za chwilę."
        )


def _pair(competition, pair_id: int) -> SubmissionSimilarity:
    """Para prac **tego konkursu** albo 404 – porównanie pokazuje kod dwóch cudzych rozwiązań."""
    return get_object_or_404(
        SubmissionSimilarity.objects.for_competition(competition).select_related(
            "stage",
            "problem",
            "submission_a__entry__participant__user",
            "submission_b__entry__participant__user",
        ),
        pk=pair_id,
    )


class SimilarityPairView(CoordinatorRequiredMixin, TemplateView):
    """``…/similarity/<pair_id>/`` – porównanie dwóch prac linia po linii.

    Kod uczestnika trafia tu na stronę jako HTML (tabela ``difflib.HtmlDiff``), więc przechodzi
    przez dwie niezależne bariery: escapowanie w bibliotece i białą listę znaczników w
    ``apps.submissions.similarity.sanitise_html``. ``mark_safe`` stoi **dopiero za** nimi i tylko
    tutaj – to jedyne miejsce w serwisie, w którym treść pliku od użytkownika nie jest tekstem.
    """

    template_name = SIMILARITY_PAIR_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pair = _pair(self.competition, self.kwargs["pair_id"])
        if pair.stage_id != self.kwargs["stage_id"]:
            # Adres niesie etap i parę; niezgodność znaczy sklejony ręcznie odnośnik, a nie stan,
            # który ekran ma obsłużyć.
            raise Http404("Ta para nie należy do wskazanego etapu.")
        left = pair.submission_a.entry.participant.public_code
        right = pair.submission_b.entry.participant.public_code
        context.update(
            {
                "stage": pair.stage,
                "pair": pair,
                "left_code": left,
                "right_code": right,
                "left_participant": pair.submission_a.entry.participant,
                "right_participant": pair.submission_b.entry.participant,
                "diff": mark_safe(diff_table(pair, left_label=left, right_label=right)),  # noqa: S308
            }
        )
        return context


class ReportSimilarityView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST „Zgłoś do komitetu” – i to samo kliknięcie cofa zgłoszenie."""

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-similarity", args=[stage_id])

    def perform(self, request, stage_id: int, pair_id: int) -> str:
        pair = _pair(self.competition, pair_id)
        if pair.stage_id != stage_id:
            raise Http404("Ta para nie należy do wskazanego etapu.")
        reported = toggle_report(pair, actor=request.user, request=request)
        if reported:
            return "Para została oznaczona do rozpatrzenia przez komitet."
        return "Zgłoszenie pary zostało cofnięte."


# --- 3. Kwalifikacja ręczna ---------------------------------------------------------------------


class ManualQualificationView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST ``/coordinator/entries/<id>/manual-qualification/`` – decyzja komitetu o jednym wpisie.

    Wraca tam, skąd przyszła (``?next=`` z formularza w tabeli), bo ten sam formularz stoi na
    dwóch ekranach: w symulacji progu i w podglądzie wyników na pulpicie. Adres powrotu bierzemy
    z zamkniętej listy własnych widoków, a nie z dowolnego parametru – inaczej przycisk panelu
    byłby otwartym przekierowaniem.
    """

    def get_success_url(self, entry_id: int, *args, **kwargs) -> str:
        entry = getattr(self, "entry", None)
        stage_id = entry.stage_id if entry is not None else None
        if self.request.POST.get("next") == "dashboard" or stage_id is None:
            return reverse("web:coordinator")
        return reverse("web:coordinator-stage-simulation", args=[stage_id])

    def perform(self, request, entry_id: int) -> str:
        self.entry = get_object_or_404(
            StageEntry.objects.for_competition(request.competition).select_related("participant", "stage"),
            pk=entry_id,
        )
        form = ManualQualificationForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wybierz decyzję z listy.", "INVALID_DECISION")
        result = set_manual_qualification(
            self.entry,
            form.cleaned_data["decision"],
            form.cleaned_data["reason"],
            actor=request.user,
            request=request,
        )
        if result["results_stale"]:
            # Ostrzeżenie, a nie odmowa – tak samo jak przy korekcie oceny końcowej: ogłoszona
            # tabela jest dokumentem z chwili publikacji i zmienia ją dopiero ponowne ogłoszenie.
            messages.warning(
                request,
                "Wyniki tego etapu są już ogłoszone – decyzja wejdzie do tabeli dopiero po "
                "ponownym przeliczeniu i publikacji.",
            )
        code = self.entry.participant.public_code
        if not form.cleaned_data["decision"]:
            return f"Decyzja komitetu dla {code} została zdjęta – rozstrzyga próg punktowy."
        return f"Zapisano decyzję komitetu dla {code}."


# --- 4. Dyplomy i zaświadczenia -----------------------------------------------------------------


def _certificates_by_entry(stage: Stage) -> dict[int, list[Certificate]]:
    """Wystawione dokumenty wpisów tego etapu – jedno zapytanie na cały ekran.

    Lista, a nie pojedynczy dokument: jeden wpis może mieć zaświadczenie o udziale **i** dyplom
    finalisty (unikalność w bazie dotyczy pary wpis + rodzaj). Tabela pokazuje wtedy oba numery,
    bo oba zostały wystawione i oba mogą być cytowane.
    """
    issued: dict[int, list[Certificate]] = {}
    for certificate in Certificate.objects.filter(entry__stage=stage).order_by("issued_at", "id"):
        issued.setdefault(certificate.entry_id, []).append(certificate)
    return issued


class StageCertificatesView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/stages/<id>/certificates/`` – wystawianie dyplomów i zaświadczeń.

    Tabela stoi na **podglądzie wyników** (``compute_stage_results(preview=True)``), a nie na
    samych wpisach: koordynator wystawia dyplomy, patrząc na punkty i kolejność, a podgląd jest
    jedyną drogą do tej tabeli, która niczego nie zapisuje przy wejściu na stronę.

    Opiekunowie są na osobnej liście, bo ich zaświadczenie dotyczy **edycji**, a nie etapu –
    i wystawia się je raz, niezależnie od tego, z którego etapu koordynator akurat patrzy.
    """

    template_name = CERTIFICATES_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = _stage(self.competition, self.kwargs["stage_id"])
        issued = _certificates_by_entry(stage)
        rows = compute_stage_results(stage, preview=True)
        supervisors = supervisors_for_edition(stage.edition)
        supervisor_certificates = {
            certificate.supervisor_id: certificate
            for certificate in Certificate.objects.filter(supervisor__in=supervisors, edition=stage.edition)
        }
        context.update(
            {
                "stage": stage,
                "rows": [row | {"certificates": issued.get(row["entry_id"], [])} for row in rows],
                "form": CertificateIssueForm(initial={"kind": CertificateKind.UCZESTNIK}),
                "supervisor_rows": [
                    {"supervisor": supervisor, "certificate": supervisor_certificates.get(supervisor.pk)}
                    for supervisor in supervisors
                ],
                "kinds": CertificateKind.choices,
            }
        )
        return context


class IssueCertificateView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """POST „Wystaw” – jeden dokument dla jednego wpisu albo dla jednego opiekuna."""

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-certificates", args=[stage_id])

    def perform(self, request, stage_id: int) -> str:
        stage = _stage(request.competition, stage_id)
        form = CertificateIssueForm(request.POST)
        if not form.is_valid():
            raise DomainError("Wybierz rodzaj dokumentu.", "INVALID_CERTIFICATE_KIND")
        entry_id = (request.POST.get("entry") or "").strip()
        supervisor_id = (request.POST.get("supervisor") or "").strip()
        entry = supervisor = None
        if entry_id:
            entry = get_object_or_404(
                StageEntry.objects.for_competition(request.competition).select_related("participant"),
                pk=entry_id,
            )
            if entry.stage_id != stage.pk:
                raise Http404("Ten wpis nie należy do wskazanego etapu.")
        elif supervisor_id:
            from apps.accounts.models import SchoolSupervisor

            supervisor = get_object_or_404(
                for_competition_or_unclaimed(SchoolSupervisor.objects.all(), request.competition),
                pk=supervisor_id,
            )
        certificate, created = issue_certificate(
            edition=stage.edition,
            kind=form.cleaned_data["kind"],
            entry=entry,
            supervisor=supervisor,
            actor=request.user,
            request=request,
        )
        if not created:
            return f"Dokument dla tego odbiorcy już istnieje: {certificate.number}."
        return f"Wystawiono dokument {certificate.number}."


class IssueAllCertificatesView(CoordinatorRequiredMixin, View):
    """POST „Wystaw wszystkim (ZIP)”: dokument dla każdego wpisu etapu i paczka z kompletem.

    Rodzaj jest jeden dla całej paczki, bo tak wygląda ta czynność w praktyce: komitet ustala
    listę laureatów i wystawia ją hurtem, a potem robi to samo z finalistami. Wpis, który ma już
    dokument tego rodzaju, dostaje ten sam numer (``issue_certificate`` jest idempotentne) –
    powtórne kliknięcie nie mnoży dyplomów.
    """

    def post(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        form = CertificateIssueForm(request.POST)
        if not form.is_valid():
            # Komunikat i powrót na ten sam ekran, a nie renderowanie go tutaj: strona dyplomów
            # potrzebuje pełnego kontekstu (podgląd wyników, opiekunowie), a jedynym błędem,
            # jaki może tu wystąpić, jest nierozpoznany rodzaj dokumentu.
            messages.error(request, "Wybierz rodzaj dokumentu.")
            return redirect(reverse("web:coordinator-stage-certificates", args=[stage.pk]))
        entries = list(
            StageEntry.objects.filter(stage=stage)
            .select_related("participant", "participant__user")
            .order_by("id")
        )
        certificates = [
            issue_certificate(
                edition=stage.edition,
                kind=form.cleaned_data["kind"],
                entry=entry,
                actor=request.user,
                request=request,
            )[0]
            for entry in entries
        ]
        archive = build_certificates_zip(certificates)
        response = FileResponse(archive.stream, content_type="application/zip", as_attachment=True)
        response["Content-Disposition"] = (
            f'attachment; filename="dyplomy-etap-{stage.pk}-{form.cleaned_data["kind"].lower()}.zip"'
        )
        return response


class CertificateDownloadView(CoordinatorRequiredMixin, View):
    """GET: pojedynczy dokument jako PDF. Składany w locie – w storage go nie ma."""

    def get(self, request, pk: int):
        certificate = get_object_or_404(
            Certificate.objects.for_competition(request.competition).select_related(
                "edition", "entry__participant__user", "supervisor__user"
            ),
            pk=pk,
        )
        return FileResponse(
            iter([render_pdf(certificate)]),
            content_type="application/pdf",
            as_attachment=True,
            filename=pdf_filename(certificate),
        )
