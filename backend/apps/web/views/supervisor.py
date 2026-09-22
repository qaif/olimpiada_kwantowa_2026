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

Import listy uczniów (``/supervisor/import/``) i jego bliźniak dla koordynatora
(``/coordinator/accounts/import/``) też stoją tutaj, choć drugi z nich wisi pod adresem panelu
organizatora. To jest **jedna funkcja z dwojgiem drzwi**: ten sam plik, ten sam podgląd, ten sam
zapis i ta sama treść ekranu – różni je wyłącznie rama strony (panel organizatora kontra zwykła
strona serwisu), to, czyja szkoła wchodzi domyślnie, i to, czy wolno wskazać cudzego opiekuna
szkolnego (dodatkowa kolumna). Rozbicie na dwa moduły znaczyłoby dwie kopie reguły „co pokazać
przed zapisem”, a ta jest tu najważniejsza.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView, View

from apps.accounts.bulk_registration import (
    MAX_ROWS,
    STATE_INVITED,
    columns_for,
    extra_columns,
    header_line,
    import_students,
    invitation_state,
    preview_upload,
    resend_invitation,
    unpack_rows,
)
from apps.accounts.consents import ConsentSource
from apps.accounts.models import Participant
from apps.accounts.supervisors import (
    confirm_participation,
    has_confirmed,
    normalize_supervisor_email,
    register_supervisor,
    registration_enabled_for_request,
    student_rows,
    students_of,
)
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.results.certificates import pdf_filename, render_pdf
from apps.results.models import Certificate
from apps.web.forms import REQUIRED_CSS_CLASS, SchoolChoiceMixin
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin
from apps.web.supervisor_forms import SupervisorRegisterForm
from apps.web.supervisor_mixins import SupervisorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from .public import ServiceFormView, remember_registration

DASHBOARD_TEMPLATE = "web/supervisor/dashboard.html"
REGISTER_TEMPLATE = "web/supervisor/register.html"
STUDENTS_TEMPLATE = "web/supervisor/students.html"
# Import ma po dwa szablony strony na ekran: treść jest jedna (fragmenty ``_import_*.html``),
# ale rama inna – opiekun dostaje zwykłą stronę serwisu, koordynator swój panel z menu bocznym.
# Jeden szablon „warunkowo rozszerzający” dwie ramy jest niewykonalny, bo obie mają inne nazwy
# bloków (``content`` kontra ``panel_content``), a udawanie jednej z nich rozjechałoby panel.
IMPORT_TEMPLATE = "web/supervisor/import.html"
IMPORT_PREVIEW_TEMPLATE = "web/supervisor/import_preview.html"
IMPORT_TEMPLATE_COORDINATOR = "web/supervisor/import_coordinator.html"
IMPORT_PREVIEW_TEMPLATE_COORDINATOR = "web/supervisor/import_preview_coordinator.html"


class RegisterSupervisorView(ThrottledFormMixin, ServiceFormView):
    """``/register/supervisor/`` – założenie konta opiekuna szkolnego.

    Ten sam scope limitu (``register``) i ta sama droga po rejestracji, co przy uczestniku
    i komitecie: konto powstaje nieaktywne i czeka na link z listu, a strona „sprawdź skrzynkę”
    jest wspólna dla wszystkich rejestracji.

    Adres istnieje tylko wtedy, gdy organizator **tej witryny** oferuje tę rolę
    (``cms.SiteSettings.supervisor_registration_enabled``, domyślnie wyłączone i **per witryna** –
    patrz ``apps.accounts.supervisors.registration_enabled``). Wyłączony przełącznik daje **404**,
    a nie 403 ani stronę „funkcja niedostępna”: z zewnątrz ten adres ma nie istnieć, a komunikat
    „rejestracja opiekunów jest wyłączona” byłby ogłoszeniem, że jednak istnieje i wypada dopytać.
    """

    template_name = REGISTER_TEMPLATE
    form_class = SupervisorRegisterForm
    success_url = reverse_lazy("web:register-done")
    throttle_scope = "register"
    success_message = ""
    registration_kind = "supervisor"

    def dispatch(self, request, *args, **kwargs):
        # Bramka stoi w ``dispatch``, a nie w ``get``/``post`` z osobna: POST na ukryty adres
        # miałby inaczej własną, nieogrodzoną drogę do zakładania kont. Pytamy o witrynę **tego**
        # żądania (``registration_enabled_for_request``), nie o instalację: drugi konkurs na tej
        # samej instalacji ma prawo trzymać tę rolę wyłączoną, mimo że pierwszy ją włączył.
        if not registration_enabled_for_request(request):
            raise Http404("Rejestracja opiekunów szkolnych nie jest prowadzona.")
        return super().dispatch(request, *args, **kwargs)

    def call_service(self, form):
        # ``source`` dokładamy tutaj, a nie w formularzu, z tego samego powodu, co przy
        # uczestniku: to fakt o **drodze** żądania, a nie dana wpisana przez opiekuna.
        register_supervisor(**form.cleaned_data, source=ConsentSource.WEB, request=self.request)
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
        edition = current_edition(self.competition)
        context.update(
            {
                "supervisor": supervisor,
                "edition": edition,
                "rows": student_rows(supervisor, edition),
                "confirmed": has_confirmed(supervisor, edition),
                "certificates": list(
                    Certificate.objects.for_competition(self.competition)
                    .filter(supervisor=supervisor)
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
            self.supervisor, current_edition(self.competition), actor=request.user, request=request
        )
        if result["confirmed"]:
            return "Dziękujemy – udział szkoły w tej edycji został potwierdzony."
        return "Potwierdzenie udziału szkoły zostało wycofane."


class SupervisorCertificateDownloadView(SupervisorRequiredMixin, View):
    """``/supervisor/certificates/<pk>/`` – własne zaświadczenie opiekuna jako PDF."""

    def get(self, request, pk: int):
        certificate = get_object_or_404(
            Certificate.objects.for_competition(self.competition)
            .filter(supervisor=self.supervisor)
            .select_related("edition", "supervisor__user"),
            pk=pk,
        )
        return FileResponse(
            iter([render_pdf(certificate)]),
            content_type="application/pdf",
            as_attachment=True,
            filename=pdf_filename(certificate),
        )


# --- import listy uczniów --------------------------------------------------------------------

#: Rozszerzenia przyjmowane przez pole pliku. Atrybut ``accept`` jest **podpowiedzią** okna wyboru
#: pliku, a nie zabezpieczeniem – rozstrzyga ``apps.accounts.bulk_registration.read_table``.
UPLOAD_ACCEPT = ".csv,.xlsx"


class StudentImportForm(SchoolChoiceMixin):
    """Plik z listą uczniów i szkoła, do której cała lista należy.

    Szkoła jest **jedna dla całego pliku** i nie ma jej w kolumnach. Powód nie jest oszczędnością
    klików: nazwa szkoły wchodzi do grupowania w publikowanych wynikach (próg k-anonimowości),
    więc musi być u wszystkich uczniów placówki zapisana identycznie. Dwadzieścia razy wpisana
    ręcznie w arkuszu daje dwadzieścia wariantów tej samej szkoły.

    Blok wyboru szkoły jest ten sam, co w rejestracji (``SchoolChoiceMixin`` + ``school-picker.js``),
    ale przy opiekunie **wolno go zostawić pustym**: wtedy wchodzi szkoła z jego profilu. To jest
    przypadek domyślny – nauczyciel importuje własną klasę – a zmuszanie go do wybierania po raz
    drugi tego, co podał przy rejestracji, byłoby pytaniem o znaną odpowiedź. Koordynator „własnej
    szkoły” nie ma, więc u niego blok zostaje obowiązkowy.
    """

    required_css_class = REQUIRED_CSS_CLASS

    file = forms.FileField(
        label="Plik z listą uczniów",
        help_text=f"CSV albo XLSX, maksymalnie {MAX_ROWS} wierszy. Pierwszy wiersz to nagłówki kolumn.",
        widget=forms.ClearableFileInput(attrs={"accept": UPLOAD_ACCEPT}),
    )

    def __init__(self, *args, fallback_school: str = "", fallback_school_ref=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fallback_school = fallback_school
        self.fallback_school_ref = fallback_school_ref
        self.order_fields(["file", *self.school_field_names])

    def clean(self):
        """Blok „szkoła” ma reguły domieszki; pusty wybór zastępuje szkoła z profilu opiekuna.

        Domieszka dopisuje błąd pod polem, gdy nikt nic nie wskazał – i słusznie, bo w rejestracji
        szkoła jest obowiązkowa. Tutaj mamy odpowiedź zapasową, więc ten jeden błąd zdejmujemy
        i wstawiamy szkołę opiekuna. Reguły **nie powtarzamy**: gdy wyboru nie ma i zapasu też nie,
        błąd domieszki zostaje nietknięty.
        """
        cleaned = super().clean()
        if not (self.fallback_school or self.fallback_school_ref):
            return cleaned
        if cleaned.get("school_id") or (cleaned.get("school") or "").strip():
            return cleaned
        for name in ("school", "school_query"):
            self.errors.pop(name, None)
        cleaned["school_id"] = getattr(self.fallback_school_ref, "pk", None)
        cleaned["school"] = self.fallback_school
        return cleaned


class StudentImportConfirmForm(forms.Form):
    """Zatwierdzenie podglądu: koszyk wierszy i szkoła, oba przeniesione z poprzedniego ekranu.

    Koszyk (``rows``) jest podpisany (``bulk_registration.pack_rows``), więc między podglądem
    a zatwierdzeniem nie da się podmienić listy. Szkoły nie podpisujemy – wybrać dowolną można
    i tak na poprzednim ekranie, więc podpis niczego by nie strzegł.
    """

    rows = forms.CharField(widget=forms.HiddenInput)
    school = forms.CharField(required=False, max_length=255, widget=forms.HiddenInput)
    school_id = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)


class BaseStudentImportView(View):
    """Wspólne ciało obu importów: podgląd (POST z plikiem) i zapis (POST z koszykiem).

    Podgląd jest POST-em, a nie GET-em z parametrem, bo powstaje z wgranego pliku; zapis jest
    drugim POST-em z tego samego adresu. Trzeciego stanu nie ma: po zapisie wracamy na listę
    z komunikatem (wzorzec POST/redirect/GET), więc odświeżenie nie wysyła zaproszeń drugi raz.
    """

    #: Czy plik może nieść kolumnę „e-mail opiekuna szkolnego” (import koordynatora).
    with_supervisor_column = False
    #: Rama strony. Treść obu ekranów jest wspólna (fragmenty ``_import_*.html``); różni je
    #: wyłącznie to, w co jest oprawiona.
    import_template = IMPORT_TEMPLATE
    preview_template = IMPORT_PREVIEW_TEMPLATE

    def default_supervisor_email(self) -> str:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def fallback_school(self):
        """Szkoła wchodząca, gdy nikt nie wskazał innej. Koordynator nie ma żadnej."""
        return "", None

    def success_url(self) -> str:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    # --- ekrany ---------------------------------------------------------------------------

    def get(self, request):
        school, school_ref = self.fallback_school()
        return self._render_form(
            request, StudentImportForm(fallback_school=school, fallback_school_ref=school_ref)
        )

    def post(self, request):
        if request.POST.get("rows"):
            return self._commit(request)
        return self._preview(request)

    def _preview(self, request):
        school, school_ref = self.fallback_school()
        form = StudentImportForm(
            request.POST,
            request.FILES,
            fallback_school=school,
            fallback_school_ref=school_ref,
        )
        if not form.is_valid():
            return self._render_form(request, form, status=400)
        try:
            resolved_name, resolved_ref = self._resolve_school(form.cleaned_data)
            preview = preview_upload(
                form.cleaned_data["file"],
                with_supervisor=self.with_supervisor_column,
                default_supervisor_email=self.default_supervisor_email(),
                competition=self.competition,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render_form(request, form, status=400)
        context = self._context(
            request,
            preview=preview,
            school_name=resolved_name,
            school_id=getattr(resolved_ref, "pk", None),
        )
        return TemplateResponse(request, self.preview_template, context)

    def _commit(self, request):
        form = StudentImportConfirmForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Podgląd importu jest nieczytelny. Wgraj plik jeszcze raz.")
            return redirect(request.path)
        try:
            rows = unpack_rows(form.cleaned_data["rows"])
            school_name, school_ref = self._resolve_school(form.cleaned_data)
            summary = import_students(
                rows,
                school_name=school_name,
                school_ref=school_ref,
                default_supervisor_email=self.default_supervisor_email(),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(request.path)
        messages.success(
            request,
            "Import zakończony: zaproszono {created}, dopisano do istniejących kont {linked}, "
            "pominięto {skipped}.".format(**summary),
        )
        return redirect(self.success_url())

    # --- pomocnicze -----------------------------------------------------------------------

    def _resolve_school(self, cleaned: dict):
        """Para „(nazwa do pokazania, wiersz rejestru albo ``None``)” – regułą jest serwis kont.

        Wołamy ``_resolve_school`` z ``apps.accounts.services``, a nie własną wersję: to ta sama
        reguła, która obowiązuje przy rejestracji uczestnika (pierwszeństwo wyboru ze słownika,
        minimalna długość nazwy wpisanej ręcznie), i nie ma powodu, żeby import miał drugą.
        """
        from apps.accounts.services import _resolve_school

        return _resolve_school(cleaned.get("school") or "", cleaned.get("school_id"))

    def _context(self, request, **extra) -> dict:
        """Kontekst obu ekranów importu – instrukcja, wzorcowy nagłówek i treść podglądu.

        Kolumny dokładane przez konkurs (``extra_columns``) wchodzą do instrukcji **i** decydują
        o kolumnach tabeli podglądu. Jedno źródło, bo to jest ta sama lista: rubryka wymieniona
        w opisie pliku, której podgląd nie pokazuje, znaczy dla nauczyciela „wpisałem i zniknęło”.
        Przy konkursie bez flag etapu 2 lista jest pusta i oba ekrany zostają dzisiejsze (§ 0.1).
        """
        competition = self.competition
        keys = {column.key for column in extra_columns(competition)}
        return {
            "columns": columns_for(with_supervisor=self.with_supervisor_column, competition=competition),
            "header_line": header_line(with_supervisor=self.with_supervisor_column, competition=competition),
            "with_supervisor_column": self.with_supervisor_column,
            "show_region": "region" in keys,
            "show_category": "category" in keys,
            "show_institution": bool(
                keys & {"institution_type", "institution_name", "custom_institution_id"}
            ),
            "max_rows": MAX_ROWS,
            "post_url": request.path,
            **extra,
        }

    def _render_form(self, request, form, *, status: int = 200):
        return TemplateResponse(
            request, self.import_template, self._context(request, form=form), status=status
        )


class SupervisorImportView(SupervisorRequiredMixin, BaseStudentImportView):
    """``/supervisor/import/`` – nauczyciel wgrywa listę swojej klasy.

    CAPTCHY tu nie ma i nie będzie: opiekun jest zalogowany, a jego konto przeszło rejestrację
    z CAPTCHĄ i aktywacją adresu. Dokładanie obrazka przed każdą czynnością zalogowanego
    użytkownika chroniłoby przed niczym, a kosztowałoby dostępność.
    """

    def default_supervisor_email(self) -> str:
        # Adres bierzemy z **konta** opiekuna, a nie z pola formularza: to ten sam adres, po którym
        # panel odnajduje uczniów, i jedyny, co do którego wiemy, że należy do zalogowanej osoby.
        return normalize_supervisor_email(self.supervisor.user.email)

    def fallback_school(self):
        supervisor = self.supervisor
        return supervisor.school or "", supervisor.school_ref

    def success_url(self) -> str:
        return reverse("web:supervisor-students")


class CoordinatorStudentImportView(CoordinatorRequiredMixin, BaseStudentImportView):
    """``/coordinator/accounts/import/`` – ten sam import w rękach organizatora.

    Dwie różnice wobec wersji nauczycielskiej, obie wynikające z tego, kto to robi:

    - plik może mieć kolumnę **„e-mail opiekuna szkolnego”**, więc jednym wgraniem da się rozdzielić
      uczniów między kilku nauczycieli. Nauczyciel tej kolumny mieć nie może – przypisywałby
      uczniów komuś innemu,
    - szkoła jest **obowiązkowa** w formularzu: koordynator nie ma „własnej” szkoły, a domyślenie
      się jej z czegokolwiek byłoby zgadywaniem.
    """

    with_supervisor_column = True
    import_template = IMPORT_TEMPLATE_COORDINATOR
    preview_template = IMPORT_PREVIEW_TEMPLATE_COORDINATOR

    def default_supervisor_email(self) -> str:
        # Pusty: uczeń bez wskazanego opiekuna po prostu go nie ma. Wpisanie tu adresu
        # koordynatora dałoby mu panel opiekuna pełen cudzych uczniów.
        return ""

    def success_url(self) -> str:
        return reverse("web:coordinator-accounts")


# --- lista uczniów opiekuna z widocznym stanem zaproszenia ------------------------------------


class SupervisorStudentsView(SupervisorRequiredMixin, TemplateView):
    """``/supervisor/students/`` – kto przyjął zaproszenie, a kto jeszcze nie.

    Ekran osobny od pulpitu, bo odpowiada na inne pytanie. Pulpit mówi „co się dzieje z pracami
    moich uczniów”, ten – „kto w ogóle ma już konto”. Sklejenie ich znaczyłoby tabelę, w której
    połowa wierszy ma puste kolumny etapów, bo dotyczy osób, które jeszcze nie weszły do serwisu.

    Lista jest ta sama, co na pulpicie (``students_of``), czyli powstaje z decyzji uczniów –
    także tych, którzy zapisali się sami i wpisali adres opiekuna z tablicy. Stan zaproszenia
    (``invitation_state``) rozróżnia te przypadki i nie udaje, że każdy uczeń jest „z importu”.
    """

    template_name = STUDENTS_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supervisor = self.supervisor
        # Uczniowie **tego** konkursu: ``students_of`` wiąże ich adresem e-mail opiekuna, a ten
        # adres jest jeden na całą platformę. Nauczyciel prowadzący klasę w dwóch olimpiadach
        # zobaczyłby bez tego zawężenia obie listy pod każdą z domen.
        rows = [
            {"participant": participant, "state": invitation_state(participant)}
            for participant in students_of(supervisor)
            if participant.competition_id in (None, getattr(self.competition, "pk", None))
        ]
        context.update(
            {
                "supervisor": supervisor,
                "rows": rows,
                "invited_count": sum(1 for row in rows if row["state"] == STATE_INVITED),
            }
        )
        return context


class ResendInvitationView(ActionViewMixin, SupervisorRequiredMixin, View):
    """POST ``/supervisor/students/<pk>/resend/`` – ponowne wysłanie linku z zaproszeniem.

    Uczeń musi być **na liście tego opiekuna**, i to jest cała reguła dostępu: filtr po
    znormalizowanym adresie z konta opiekuna, a nie po szkole ani po tym, kto wgrał plik.
    Nauczyciel, któremu uczeń cofnął wskazanie, traci razem z podglądem także ten przycisk.
    """

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:supervisor-students")

    def perform(self, request, pk: int) -> str:
        email = normalize_supervisor_email(self.supervisor.user.email)
        participant = get_object_or_404(
            Participant.objects.for_competition(self.competition)
            .select_related("user")
            .filter(supervisor_email__iexact=email),
            pk=pk,
        )
        resend_invitation(participant, actor=request.user, request=request)
        return "Zaproszenie zostało wysłane ponownie."
