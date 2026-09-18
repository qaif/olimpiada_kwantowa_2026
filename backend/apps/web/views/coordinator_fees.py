"""Ekrany wpisowego w panelu koordynatora: cennik, rejestr należności i webhooki dostawców.

Trzy adresy za jedną flagą ``fees`` (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.1, § 2.2):

- ``/coordinator/fees/`` – **cennik**: ile kosztuje start w edycji, ewentualnie osobno dla
  kategorii, z jakim terminem płatności i czy brak wpłaty blokuje oddanie pracy (decyzja D16),
- ``/coordinator/fees/register/`` – **rejestr należności**: kto ile ma zapłacić, co wpłynęło,
  kto jest zwolniony; tu też stoją wszystkie czynności na pojedynczej należności,
- ``/coordinator/fees/payments/`` – **webhooki dostawców**: sekret podpisu (pokazywany raz)
  i dziennik doręczeń, których system nie umiał dopasować.

**Dlaczego 404, a nie 403, przy wyłączonej fladze.** Adresu, którego w tej instalacji nie ma, nie
ma tak samo jak adresu cudzego konkursu (§ 2.1). Olimpiada Kwantowa jest bezpłatna i ma taka
zostać, więc po wdrożeniu wydania K wszystkie trzy adresy odpowiadają jej 404 – dokładnie tak, jak
przed nim. Rola sprawdza się **wcześniej i osobno** (``CoordinatorRequiredMixin``), więc uczestnik
dostaje 403 niezależnie od flagi i nie dowiaduje się z odpowiedzi, jak ten konkurs skonfigurowano.

**Reguły są w serwisie, widok orkiestruje** (§ 2.1). Ten moduł nie zmienia ani jednego pola
należności samodzielnie: nalicza przez ``assign_fee_if_due``, zapisuje wpłatę przez
``record_payment``, zwalnia przez ``mark_exempt``, umarza przez ``waive_fee``, zwraca przez
``record_refund``, identyfikator wpłaty nadaje przez ``set_reference``, a dokument wydaje przez
``issue_fee_document``. Wpisy audytowe pisze ``apps.tenancy.fees`` – **bez wyjątków**: montaż
wydania K przeniósł tam ``set_reference``, który przez chwilę stał w tym pliku jako brakujący
serwis.

**Formularze są tutaj, a nie w ``apps.web.coordinator_forms``** – ta sama decyzja i to samo
uzasadnienie, co w ``apps.web.views.coordinator_integrations``: opisują wyłącznie te ekrany, nie
mają odpowiednika w API i wprost wymieniają pola ``FeeSchedule``. Trzymanie ich we wspólnym module
związałoby warstwę WWW z kształtem cennika, który zmienia się razem z obszarem finansów.

**Czego tu nie ma i nie będzie.** Numeracji faktur zgodnej z ustawą, rejestru VAT, korekt
i wyliczania podatku – system jest rejestrem należności, a nie programem księgowym (decyzja D15).
``FeeSchedule.vat_rate`` jest **zapisem decyzji** organizatora i wychodzi na dokument takim, jakim
go wpisał; ekran nie liczy z niego ani grosza.
"""

from __future__ import annotations

from io import BytesIO

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.models import Category, Edition
from apps.competitions.scoping import require_competition
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.integrations.inbound import (
    create_payment_endpoint,
    payment_endpoints_for,
    payment_events_for,
    rotate_payment_secret,
)
from apps.integrations.models import UNMATCHED_REASONS
from apps.tenancy.documents import render_document
from apps.tenancy.fees import (
    DEFAULT_CURRENCY,
    DEFAULT_DUE_DAYS,
    FEATURE,
    FeeSchedule,
    FeeStatus,
    ParticipantFee,
    assign_fee_if_due,
    create_fee_schedule,
    fee_rows,
    fee_totals,
    issue_fee_document,
    mark_exempt,
    record_payment,
    record_refund,
    schedules_for,
    set_reference,
    update_fee_schedule,
    waive_fee,
)
from apps.web.mixins import CoordinatorRequiredMixin

SCHEDULES_TEMPLATE = "web/coordinator/fees.html"
REGISTER_TEMPLATE = "web/coordinator/fees_register.html"
PAYMENTS_TEMPLATE = "web/coordinator/fees_payments.html"

#: Klucz w sesji, pod którym czeka świeżo wylosowany sekret webhooka – do pokazania **raz**.
#: Ta sama droga, co przy kluczu API (``coordinator_integrations.PLAIN_KEY_SESSION``): komunikaty
#: ``messages`` bywają renderowane ponownie na kolejnym ekranie, a to jest sekret podpisu.
PLAIN_SECRET_SESSION = "fees_payment_secret"

#: Ile doręczeń pokazuje dziennik uzgodnień. Tyle samo, co dziennik webhooków wychodzących –
#: ekran odpowiada na pytanie „co ostatnio przyszło i czego nie umieliśmy dopasować”.
EVENT_LIMIT = 50

#: Filtry rejestru należności: wartość w adresie → zbiór stanów. ``REFUNDED`` jest po stronie
#: „do zapłaty”, bo pieniądze wróciły i należność znów czeka na rozstrzygnięcie – tak samo liczy
#: ją ``fee_totals`` w kolumnie „zaległe”.
STATUS_FILTERS: dict[str, tuple[str, ...]] = {
    "paid": (FeeStatus.PAID,),
    "unpaid": (FeeStatus.DUE, FeeStatus.REFUNDED),
    "exempt": (FeeStatus.EXEMPT, FeeStatus.WAIVED),
}

#: Podpisy filtrów w kolejności pokazywania. Krotka par, a nie słownik: kolejność jest treścią ekranu.
STATUS_LABELS: tuple[tuple[str, str], ...] = (
    ("", "wszystkie"),
    ("unpaid", "do zapłaty"),
    ("paid", "zapłacone"),
    ("exempt", "zwolnione i umorzone"),
)


def form_errors(form) -> str:
    """Błędy formularza jako jedno zdanie do ``messages`` – bez nazw pól technicznych."""
    return "; ".join(text for errors in form.errors.values() for text in errors)


# --- formularze -------------------------------------------------------------------------------


class FeeScheduleForm(forms.Form):
    """Cennik wpisowego: kwota, waluta, termin i bramka z decyzji D16.

    Formularz sprawdza **kształt** (czy kwota jest liczbą, czy stawka mieści się w zakresie
    procent), a reguły – zgodność konkursu edycji i kategorii, jedyność ceny podstawowej,
    zaokrąglenie kwoty – zostają w ``apps.tenancy.fees``. Powtórzenie ich tutaj byłoby drugim
    miejscem wiedzącym to samo o cenniku.

    Przy **edycji istniejącego** cennika pola ``edition`` i ``category`` znikają: one mówią, czego
    ten cennik dotyczy, a nie ile kosztuje. Przepięcie cennika na inny rocznik zmieniłoby znaczenie
    należności już z niego naliczonych, a te noszą kwotę skopiowaną w chwili naliczenia i mają
    dalej tłumaczyć, skąd się wzięła.
    """

    #: Pola, które wolno zmienić na cenniku już istniejącym – i zarazem komplet argumentów
    #: ``update_fee_schedule``. Jedna krotka, żeby ekran i serwis nie mogły się rozjechać.
    MUTABLE: tuple[str, ...] = (
        "name",
        "amount",
        "currency",
        "vat_rate",
        "due_days",
        "blocks_submission",
        "is_active",
    )

    edition = forms.ModelChoiceField(
        queryset=Edition.objects.none(), label="Edycja", empty_label="— wybierz edycję —"
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.none(),
        label="Kategoria",
        required=False,
        empty_label="cena podstawowa edycji",
    )
    name = forms.CharField(label="Nazwa", max_length=120)
    amount = forms.DecimalField(label="Kwota", max_digits=10, decimal_places=2, min_value=0)
    currency = forms.CharField(label="Waluta", max_length=3, min_length=3, initial=DEFAULT_CURRENCY)
    vat_rate = forms.IntegerField(
        label="Stawka VAT (%)",
        required=False,
        min_value=0,
        max_value=100,
        help_text="Puste = zwolnione. System nie liczy podatku – wpisana wartość trafia na dokument.",
    )
    due_days = forms.IntegerField(label="Termin płatności (dni)", min_value=0, initial=DEFAULT_DUE_DAYS)
    blocks_submission = forms.BooleanField(
        label="Brak wpłaty blokuje oddanie pracy",
        required=False,
        help_text="Domyślnie wyłączone. Przelew idzie dwa dni, a termin oddania pracy na niego nie czeka.",
    )
    is_active = forms.BooleanField(label="Aktywny", required=False, initial=True)

    def __init__(self, *args, competition=None, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None:
            # Cennik istniejący: rocznik i kategoria są jego tożsamością, a nie ceną.
            del self.fields["edition"]
            del self.fields["category"]
            return
        # Listy wyboru **zawężone konkursem**: ``ModelChoiceField`` jest tu realną bramką zapisu,
        # a nie ozdobą ekranu – bez zawężenia dałoby się jednym POST-em wycenić cudzy rocznik.
        self.fields["edition"].queryset = Edition.objects.for_competition(competition).order_by(
            "-created_at", "-id"
        )
        self.fields["category"].queryset = Category.objects.for_competition(competition).order_by(
            "position", "id"
        )

    def clean_currency(self) -> str:
        """Kod ISO 4217 wielkimi literami – walidator serwisu i tak przyjmuje wyłącznie takie."""
        return (self.cleaned_data.get("currency") or "").strip().upper()


class ReferenceForm(forms.Form):
    """Identyfikator wpłaty: jedno pole i żadnej reguły domenowej."""

    reference = forms.CharField(
        label="Identyfikator wpłaty",
        max_length=120,
        help_text="Ten sam ciąg, który dostawca przyśle w potwierdzeniu. Po nim webhook dopasuje wpłatę.",
    )

    def clean_reference(self) -> str:
        return (self.cleaned_data.get("reference") or "").strip()


class ReasonForm(forms.Form):
    """Powód zwolnienia, umorzenia albo zwrotu. Decyzja bez uzasadnienia nie jest decyzją."""

    reason = forms.CharField(label="Powód", max_length=200)

    def clean_reason(self) -> str:
        return (self.cleaned_data.get("reason") or "").strip()


class PaymentEndpointForm(forms.Form):
    """Poświadczenie dostawcy. Sekretu tu nie ma – losuje go serwis (``create_payment_endpoint``)."""

    provider = forms.SlugField(
        label="Dostawca",
        max_length=40,
        help_text="Identyfikator w adresie webhooka, np. przelewy24.",
    )


# --- dokument rozliczeniowy ----------------------------------------------------------------------


def fee_document_pdf(rendered, fee: ParticipantFee) -> bytes:
    """Rachunek jako PDF – ta sama ścieżka ReportLab, którą składa się dyplom i protokół etapu.

    Nowego generatora nie ma i nie ma go z premedytacją (§ 1.5.2): czcionki rejestruje
    ``apps.results.certificates.register_fonts``, układ jest jedną tabelą, a wszystkie napisy
    przychodzą z ``tenancy.DocumentTemplate`` rodzaju ``INVOICE``. Dokument **nie jest fakturą
    w rozumieniu ustawy** (D15) – „Oznaczenie” jest kluczem do wiersza rejestru, a nie numerem
    faktury, i tak jest podpisane na papierze.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.results.certificates import FONT_BOLD, FONT_REGULAR, register_fonts

    register_fonts()
    title_style = ParagraphStyle("fee-title", fontName=FONT_BOLD, fontSize=16, leading=20, spaceAfter=8)
    body_style = ParagraphStyle("fee-body", fontName=FONT_REGULAR, fontSize=10.5, leading=15)
    note_style = ParagraphStyle("fee-note", fontName=FONT_REGULAR, fontSize=8.5, leading=12)

    rows = [
        ["Oznaczenie (nie jest numerem faktury)", fee.reference],
        ["Kwota", f"{fee.amount:.2f} {fee.currency}"],
        ["Termin płatności", fee.due_on.isoformat() if fee.due_on else "—"],
        ["Stawka VAT", "—" if fee.schedule.vat_rate is None else f"{fee.schedule.vat_rate}%"],
        ["Cennik", fee.schedule.name],
    ]
    table = Table(rows, colWidths=[70 * mm, 104 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
                ("FONTNAME", (1, 0), (1, -1), FONT_REGULAR),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=rendered.title or "Rachunek",
        author=rendered.author or "",
    )
    story = [Paragraph(rendered.title, title_style)]
    if rendered.statement:
        story += [Paragraph(rendered.statement, body_style), Spacer(1, 6 * mm)]
    story.append(table)
    if rendered.signature_line:
        story += [Spacer(1, 14 * mm), Paragraph(rendered.signature_line, body_style)]
    if rendered.footer_note:
        story += [Spacer(1, 8 * mm), Paragraph(rendered.footer_note, note_style)]
    document.build(story)
    return buffer.getvalue()


def fee_document_response(rendered, fee: ParticipantFee) -> HttpResponse:
    """Rachunek do pobrania. Nazwa pliku z oznaczenia należności – **bez nazwiska**.

    Plik krąży dalej: leży w katalogu pobranych, wraca załącznikiem do listu i bywa przesyłany
    księgowej. Kod publiczny uczestnika jest pseudonimem i wystarcza do odnalezienia wiersza
    w rejestrze; nazwisko w nazwie pliku byłoby daną osobową w miejscu, w którym nikt jej nie
    kasuje.
    """
    response = HttpResponse(fee_document_pdf(rendered, fee), content_type="application/pdf")
    name = fee.reference.replace("/", "-")
    response["Content-Disposition"] = f'attachment; filename="rachunek-{name}.pdf"'
    return response


#: Zdanie dopisywane do komunikatu, gdy rachunku nie da się złożyć. Rachunek **musi** pochodzić
#: z szablonu (``apps.tenancy.fees.issue_fee_document``), a szablony włącza osobna flaga – bez tego
#: wyjaśnienia koordynator widziałby wyłącznie „brak wersji szablonu” i nie wiedziałby, gdzie iść.
DOCUMENT_HINT = (
    "Rachunek powstaje z szablonu dokumentu rodzaju „faktura / rachunek” – ustaw go na ekranie "
    "„Szablony dokumentów”."
)


# --- wspólna bramka ---------------------------------------------------------------------------


class FeeScreenMixin(CoordinatorRequiredMixin):
    """Rola koordynatora, konkurs żądania i flaga ``fees`` – jedno wejście dla wszystkich ekranów."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile pobiera wpisowe. Inaczej 404 (§ 2.1).

        Konkurs bierzemy z kontekstu żądania (``CompetitionMiddleware``), a nie z identyfikatora
        w adresie – dzięki temu nie ma tu adresu, pod którym dałoby się wskazać cudzy cennik.
        Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej
        ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo 403, **zanim**
        odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = require_competition(getattr(request, "competition", None))
        if not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie pobiera wpisowego – ekran jest wyłączony.")
        return competition

    def fee_or_404(self, competition, pk: int) -> ParticipantFee:
        """Należność **tego konkursu** albo 404 – zakres z querysetu, nie z widoku."""
        return get_object_or_404(
            ParticipantFee.objects.for_competition(competition).select_related(
                "schedule", "schedule__edition", "participant", "participant__user"
            ),
            pk=pk,
        )

    def register_redirect(self, request):
        """Powrót na rejestr z zachowaniem wybranej edycji i filtru – tam, skąd przyszło kliknięcie."""
        url = reverse("web:coordinator-fees-register")
        query = request.POST.get("back") or ""
        return redirect(f"{url}?{query}" if query else url)


# --- cennik -----------------------------------------------------------------------------------


def _initial(schedule: FeeSchedule) -> dict:
    """Wartości cennika do formularza edycji – dokładnie te pola, które wolno zmienić."""
    return {field: getattr(schedule, field) for field in FeeScheduleForm.MUTABLE}


class FeeScheduleListView(FeeScreenMixin, View):
    """``GET|POST /coordinator/fees/`` – cenniki konkursu i formularz nowego."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return TemplateResponse(request, SCHEDULES_TEMPLATE, self._context(competition))

    def post(self, request):
        competition = self.competition_or_404(request)
        form = FeeScheduleForm(request.POST, competition=competition)
        if not form.is_valid():
            return self._invalid(request, competition, form)
        try:
            create_fee_schedule(competition, actor=request.user, request=request, **form.cleaned_data)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._invalid(request, competition, form, status=exc.status_code)
        except ValidationError as exc:
            # Jedyna droga tutaj prowadzi przez więz bazy: druga cena podstawowa tej samej edycji
            # albo drugi cennik tej samej kategorii. Komunikat serwisu jest wtedy jedyną
            # informacją, co poprawić – i wraca razem z tym, co koordynator zdążył wpisać.
            messages.error(request, "; ".join(exc.messages))
            return self._invalid(request, competition, form)
        messages.success(request, "Cennik wpisowego zapisany.")
        return redirect(reverse("web:coordinator-fees"))

    def _invalid(self, request, competition, form, *, status: int = 400):
        return TemplateResponse(
            request, SCHEDULES_TEMPLATE, self._context(competition, form=form), status=status
        )

    def _context(self, competition, *, form=None) -> dict:
        """Jeden kontekst ekranu – ten sam dla ``GET`` i dla nieudanego ``POST``."""
        schedules = list(
            schedules_for(competition).select_related("edition", "category").order_by("-edition__id", "id")
        )
        return {
            "schedules": [
                {"schedule": schedule, "form": FeeScheduleForm(instance=schedule, initial=_initial(schedule))}
                for schedule in schedules
            ],
            "form": form or FeeScheduleForm(competition=competition),
        }


class FeeScheduleEditView(FeeScreenMixin, View):
    """``POST /coordinator/fees/<pk>/`` – zmiana cennika. Nie rusza należności już naliczonych."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        schedule = get_object_or_404(schedules_for(competition), pk=pk)
        form = FeeScheduleForm(request.POST, instance=schedule)
        if not form.is_valid():
            messages.error(request, f"Cennika nie zapisano: {form_errors(form)}")
            return redirect(reverse("web:coordinator-fees"))
        try:
            update_fee_schedule(schedule, actor=request.user, request=request, **form.cleaned_data)
        except (DomainError, ValidationError) as exc:
            messages.error(
                request, str(exc.detail) if isinstance(exc, DomainError) else "; ".join(exc.messages)
            )
            return redirect(reverse("web:coordinator-fees"))
        messages.success(
            request,
            "Cennik zmieniony. Należności naliczone wcześniej zostały ze swoją kwotą – "
            "nowa cena obowiązuje od następnego naliczenia.",
        )
        return redirect(reverse("web:coordinator-fees"))


# --- rejestr należności --------------------------------------------------------------------------


def _editions_of(competition) -> list[Edition]:
    return list(Edition.objects.for_competition(competition).order_by("-created_at", "-id"))


def _selected_edition(competition, requested: str, editions: list[Edition]):
    """Edycja wskazana w żądaniu albo bieżąca.

    Wartość spoza konkursu jest **ignorowana**, a nie podnosi błędu: parametr w adresie jest
    danymi od nadawcy żądania i bywa uszkodzony przez skrócenie linku – rejestr ma się wtedy
    otworzyć na edycji bieżącej, a nie odmówić.
    """
    if requested:
        for edition in editions:
            if str(edition.pk) == requested:
                return edition
    return current_edition(competition)


def register_context(request, competition) -> dict:
    """Wiersze, sumy i filtry rejestru – jedno miejsce dla widoku i dla jego testów."""
    editions = _editions_of(competition)
    edition = _selected_edition(competition, request.GET.get("edition") or "", editions)
    chosen = request.GET.get("status") or ""
    rows = fee_rows(edition) if edition is not None else []
    allowed = STATUS_FILTERS.get(chosen)
    return {
        "editions": editions,
        "edition": edition,
        "rows": [row for row in rows if allowed is None or row["status"] in allowed],
        "total_rows": len(rows),
        "totals": fee_totals(edition) if edition is not None else {},
        "status": chosen,
        "status_labels": STATUS_LABELS,
        "reference_form": ReferenceForm(),
        "reason_form": ReasonForm(),
        # Ciąg zapytania wraca w ukrytym polu każdej akcji, żeby po zapisie wrócić na ten sam
        # widok listy, a nie na jej początek z domyślnym filtrem.
        "back": request.GET.urlencode(),
    }


class FeeRegisterView(FeeScreenMixin, View):
    """``GET /coordinator/fees/register/`` – kto ile ma zapłacić i co z tego wpłynęło.

    Edycja i filtr jadą w adresie (``?edition=…&status=…``), a nie w sesji: rejestr bywa wysyłany
    jako odnośnik („popatrz na te nieopłacone”), a stan schowany w sesji znaczyłby, że dwie osoby
    pod tym samym adresem widzą co innego.
    """

    def get(self, request):
        competition = self.competition_or_404(request)
        return TemplateResponse(request, REGISTER_TEMPLATE, register_context(request, competition))


class FeeActionView(FeeScreenMixin, View):
    """Baza czynności na jednej należności: formularz, serwis, komunikat, powrót na rejestr.

    Każda czynność jest osobnym adresem i osobnym POST-em, a nie jednym adresem z polem „akcja”:
    rozróżnianie po nazwie przycisku zależy od tego, czy przeglądarka go przyśle, a przy wysyłce
    klawiaturą nie zawsze przysyła (ta sama uwaga stoi przy ekranie integracji).
    """

    form_class: type[forms.Form] = ReasonForm
    success_message = ""

    def perform(self, fee, data, request):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        fee = self.fee_or_404(competition, pk)
        form = self.form_class(request.POST)
        if not form.is_valid():
            messages.error(request, form_errors(form))
            return self.register_redirect(request)
        try:
            self.perform(fee, form.cleaned_data, request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.register_redirect(request)
        messages.success(request, self.success_message)
        return self.register_redirect(request)


class FeeReferenceView(FeeActionView):
    """``POST …/reference/`` – identyfikator wpłaty przed założeniem płatności u dostawcy."""

    form_class = ReferenceForm
    success_message = "Identyfikator wpłaty zapisany. Webhook dopasuje po nim potwierdzenie."

    def perform(self, fee, data, request):
        set_reference(fee, data["reference"], actor=request.user, request=request)


class FeePaymentView(FeeActionView):
    """``POST …/payment/`` – wpłata odnotowana ręcznie (przelew, gotówka w sekretariacie)."""

    form_class = ReferenceForm
    success_message = "Wpłata zapisana."

    def perform(self, fee, data, request):
        record_payment(fee, external_reference=data["reference"], actor=request.user, request=request)


class FeeExemptView(FeeActionView):
    """``POST …/exempt/`` – zwolnienie regulaminowe: ta osoba nigdy nie miała płacić."""

    success_message = "Należność oznaczona jako zwolniona."

    def perform(self, fee, data, request):
        mark_exempt(fee, reason=data["reason"], actor=request.user, request=request)


class FeeWaiveView(FeeActionView):
    """``POST …/waive/`` – umorzenie uznaniowe: miała płacić, organizator odstąpił."""

    success_message = "Należność umorzona."

    def perform(self, fee, data, request):
        waive_fee(fee, reason=data["reason"], actor=request.user, request=request)


class FeeRefundView(FeeActionView):
    """``POST …/refund/`` – zwrot wpłaty. Data wpływu zostaje: to jest fakt obok drugiego faktu."""

    success_message = "Zwrot zapisany."

    def perform(self, fee, data, request):
        record_refund(fee, reason=data["reason"], actor=request.user, request=request)


def _participants_of(edition):
    """Uczestnicy z wpisem do któregokolwiek etapu tej edycji – bez duplikatów."""
    from apps.accounts.models import Participant
    from apps.competitions.models import StageEntry

    ids = StageEntry.objects.filter(stage__edition=edition).values_list("participant_id", flat=True)
    return Participant.objects.filter(pk__in=set(ids)).select_related("user")


class FeeChargeView(FeeScreenMixin, View):
    """``POST /coordinator/fees/register/charge/`` – nalicza wpisowe uczestnikom tej edycji.

    Naliczamy tym, którzy mają **wpis do któregokolwiek etapu** edycji, a nie wszystkim kontom
    konkursu: wpisowe jest opłatą za start, a konto założone i porzucone przed zapisem do etapu
    startem nie jest.

    Czynność jest **idempotentna** – ``assign_fee_if_due`` oddaje istniejący wiersz bez zmiany,
    także wtedy, gdy cennik zdążył w międzyczasie podrożeć. Dlatego wolno ją powtórzyć po
    dopisaniu kolejnych uczestników i dlatego komunikat mówi o stanie rejestru, a nie o liczbie
    kliknięć.
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        editions = _editions_of(competition)
        edition = _selected_edition(competition, request.POST.get("edition") or "", editions)
        if edition is None:
            messages.error(request, "Ten konkurs nie ma edycji bieżącej – nie ma komu naliczyć wpisowego.")
            return self.register_redirect(request)
        before = ParticipantFee.objects.filter(schedule__edition=edition).count()
        for participant in _participants_of(edition):
            assign_fee_if_due(participant, edition, actor=request.user, request=request)
        after = ParticipantFee.objects.filter(schedule__edition=edition).count()
        if after == before:
            messages.info(
                request,
                "Nie naliczono niczego nowego: ta edycja nie ma aktywnego cennika albo wszyscy "
                "zapisani mają już swoją należność.",
            )
        else:
            messages.success(
                request,
                f"Naliczono {after - before} nowych należności. Rejestr tej edycji obejmuje {after} "
                "uczestników.",
            )
        return self.register_redirect(request)


class FeeDocumentView(FeeScreenMixin, View):
    """``GET …/document/`` – wydaje rachunek i od razu go oddaje.

    Jedna czynność, nie dwie. Rachunku nie przechowujemy (tak samo jak dyplomu): powstaje przy
    każdym pobraniu z **zapamiętanej** wersji szablonu, a „wydanie” jest zapisaniem tej wersji
    w rejestrze (``record_fee_document`` wewnątrz ``issue_fee_document``). Rozdzielenie na „wydaj”
    i „pobierz” dałoby dwa kliknięcia na jedną sprawę i stan pośredni, w którym dokument jest
    wydany, ale nikt go nie widział.

    ``GET`` mimo zapisu – i to jest świadomy wyjątek od reguły „każda zmiana to POST”. Odpowiedzią
    jest **plik**, a nie strona, więc nie ma dokąd przekierować; tą samą drogą chodzi pobranie
    dyplomu (``coordinator_certificates``). Zapis jest przy tym idempotentny: drugie pobranie
    nadpisuje tę samą wersję tą samą wartością.
    """

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        fee = self.fee_or_404(competition, pk)
        try:
            rendered = issue_fee_document(fee, render_document, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, f"{exc.detail} {DOCUMENT_HINT}")
            return self.register_redirect(request)
        return fee_document_response(rendered, fee)


# --- webhooki dostawców ---------------------------------------------------------------------------


class PaymentEndpointsView(FeeScreenMixin, View):
    """``GET|POST /coordinator/fees/payments/`` – sekrety dostawców i doręczenia bez dopasowania.

    Sekret w postaci jawnej pokazuje się **raz**, zaraz po założeniu poświadczenia, i jedzie przez
    przekierowanie w sesji – nie w komunikacie ``messages``. Powód jest ten sam, co przy kluczu
    API: komunikaty bywają renderowane ponownie na kolejnym ekranie, a sesja koordynatora leży po
    stronie serwera, więc sekret nie ląduje ani w ciasteczku, ani w historii przeglądarki.
    """

    def get(self, request):
        competition = self.competition_or_404(request)
        # ``pop`` zamiast ``get``: odświeżenie strony ma sekretu już nie pokazać.
        secret = request.session.pop(PLAIN_SECRET_SESSION, None)
        return TemplateResponse(request, PAYMENTS_TEMPLATE, self._context(competition, plain_secret=secret))

    def post(self, request):
        competition = self.competition_or_404(request)
        form = PaymentEndpointForm(request.POST)
        if not form.is_valid():
            return self._invalid(request, competition, form)
        try:
            endpoint = create_payment_endpoint(
                competition, provider=form.cleaned_data["provider"], actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._invalid(request, competition, form, status=exc.status_code)
        except ValidationError as exc:
            # Druga próba założenia tego samego dostawcy: więz ``(konkurs, dostawca)`` pilnuje,
            # żeby pod jednym adresem nie stały dwa ważne sekrety naraz.
            messages.error(request, "; ".join(exc.messages))
            return self._invalid(request, competition, form)
        request.session[PLAIN_SECRET_SESSION] = endpoint.secret
        messages.success(request, f"Webhook dostawcy {endpoint.provider} założony.")
        return redirect(reverse("web:coordinator-fees-payments"))

    def _invalid(self, request, competition, form, *, status: int = 400):
        return TemplateResponse(
            request, PAYMENTS_TEMPLATE, self._context(competition, form=form), status=status
        )

    def _context(self, competition, *, form=None, plain_secret=None) -> dict:
        return {
            "endpoints": payment_endpoints_for(competition),
            # Powód rozwijamy **tutaj**, a nie w szablonie: ``UNMATCHED_REASONS`` jest słownikiem,
            # a szukanie w nim po kluczu wymagałoby pętli w szablonie albo własnego filtru – czyli
            # trzeciego miejsca wiedzącego, co znaczą te kody.
            "events": [
                {"event": event, "reason": UNMATCHED_REASONS.get(event.unmatched_reason, "")}
                for event in payment_events_for(competition, matched=False, limit=EVENT_LIMIT)
            ],
            "form": form or PaymentEndpointForm(),
            "plain_secret": plain_secret,
        }


class PaymentSecretRotateView(FeeScreenMixin, View):
    """``POST /coordinator/fees/payments/<pk>/rotate/`` – wymiana sekretu, bez okresu przejściowego."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        endpoint = next((row for row in payment_endpoints_for(competition) if row.pk == pk), None)
        if endpoint is None:
            # 404, a nie 403: istnienie cudzego poświadczenia nie jest informacją tego koordynatora.
            raise Http404("Nie ma takiego webhooka płatności w tym konkursie.")
        rotate_payment_secret(endpoint, actor=request.user, request=request)
        request.session[PLAIN_SECRET_SESSION] = endpoint.secret
        messages.success(
            request,
            "Sekret wymieniony. Wklej nowy u dostawcy – doręczenia podpisane starym będą odrzucane "
            "od tej chwili.",
        )
        return redirect(reverse("web:coordinator-fees-payments"))
