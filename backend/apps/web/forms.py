"""Formularze Django dla interfejsu WWW.

Formularze pilnują wyłącznie kształtu danych (typy, wymagalność, długości). Reguły domenowe –
siła hasła, unikalność e-maila, zgodność oceny ze skalą, okna czasowe – zostają w serwisach,
które te formularze wołają. Dublowanie ich tutaj rozjechałoby się z API przy pierwszej zmianie.
"""

from __future__ import annotations

import json
from datetime import datetime

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.files.uploadedfile import UploadedFile

from apps.accounts.models import Voivodeship
from apps.appeals.models import MAX_TEXT_LENGTH, MIN_ARGUMENT_LENGTH, AppealStatus
from apps.competitions.interviews import (
    MAX_DURATION_MINUTES,
    MAX_SLOT_CAPACITY,
    MAX_SLOT_COUNT,
    MIN_DURATION_MINUTES,
    MIN_SLOT_COUNT,
)
from apps.competitions.models import (
    DEFAULT_MAX_FILE_MB,
    MAX_FILE_MB_LIMIT,
    SUPPORTED_FILE_FORMATS,
    Problem,
    Stage,
)
from apps.competitions.services import STAGE_EDITABLE_FIELDS
from apps.core.api import DomainError
from apps.results.models import Anonymization
from apps.submissions.validators import MEGABYTE, validate_pdf

# Pusta pozycja na początku listy: przeglądarka inaczej wybrałaby pierwsze województwo za
# rejestrującego się i cichaczem przypisała mu okręg, którego nigdy świadomie nie wskazał.
EMPTY_VOIVODESHIP_CHOICE = ("", "— wybierz województwo —")
VOIVODESHIP_CHOICES = (EMPTY_VOIVODESHIP_CHOICE, *Voivodeship.choices)


def voivodeship_field(label: str, *, required: bool = True) -> forms.ChoiceField:
    """Pole wyboru województwa. Lista jest zamknięta – wolny tekst nie ma tu wstępu."""
    return forms.ChoiceField(label=label, choices=VOIVODESHIP_CHOICES, required=required)


class EmailAuthenticationForm(AuthenticationForm):
    """Logowanie adresem e-mail. ``AuthenticationForm`` trzyma login w polu ``username``."""

    username = forms.EmailField(
        label="Adres e-mail",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
    )

    def clean_username(self) -> str:
        return (self.cleaned_data.get("username") or "").strip().lower()


class ParticipantRegisterForm(forms.Form):
    """Rejestracja otwarta uczestnika – dane wchodzą prosto do ``register_participant``."""

    email = forms.EmailField(label="Adres e-mail", max_length=254)
    password = forms.CharField(label="Hasło", widget=forms.PasswordInput, max_length=200)
    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    school = forms.CharField(label="Szkoła", max_length=200)
    district = voivodeship_field("Województwo")
    birth_year = forms.IntegerField(label="Rok urodzenia", min_value=1900, max_value=2100)
    gdpr_consent = forms.BooleanField(label="Zgoda na przetwarzanie danych osobowych", required=False)
    guardian_consent = forms.BooleanField(label="Zgoda opiekuna", required=False)


class SocialParticipantSignupForm(forms.Form):
    """Dokończenie rejestracji po zalogowaniu przez Google/Facebooka.

    Czego tu **nie ma** i dlaczego:

    - **adresu e-mail** – przychodzi od dostawcy i jest tylko pokazywany. Edytowalne pole
      pozwalałoby założyć konto na cudzy adres, a potem przejąć je resetem hasła,
    - **hasła** – konto zakładane tą drogą nie ma użytecznego hasła; kto chce logować się także
      hasłem, ustawia je przez „Nie pamiętasz hasła?”.

    Imię i nazwisko przychodzą z profilu u dostawcy jako wartości początkowe i **są edytowalne**:
    w wynikach olimpiady ma stać nazwisko z legitymacji, a nie pseudonim z konta społecznościowego.
    """

    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    school = forms.CharField(label="Szkoła", max_length=200)
    district = voivodeship_field("Województwo")
    birth_year = forms.IntegerField(label="Rok urodzenia", min_value=1900, max_value=2100)
    gdpr_consent = forms.BooleanField(label="Zgoda na przetwarzanie danych osobowych", required=False)
    guardian_consent = forms.BooleanField(label="Zgoda opiekuna", required=False)


class CommitteeRegisterForm(forms.Form):
    """Rejestracja członka komitetu na kod zaproszenia."""

    email = forms.EmailField(label="Adres e-mail", max_length=254)
    password = forms.CharField(label="Hasło", widget=forms.PasswordInput, max_length=200)
    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    invitation_code = forms.CharField(label="Kod zaproszenia", max_length=200)
    district = voivodeship_field("Województwo (deklarowane)", required=False)


class SubmissionUploadForm(forms.Form):
    """Upload rozwiązania. Formaty, rozmiar i magic bytes sprawdza walidator z ``apps.submissions``."""

    file = forms.FileField(label="Plik rozwiązania")


class AnnotationsField(forms.CharField):
    """Ukryte pole z adnotacjami w formacie JSON (pisane przez warstwę pdf.js).

    Do serwisu trafia struktura Pythona, a nie tekst – kanoniczny kształt i limity narzuca
    ``apps.grading.services.validate_annotations``, tu jest tylko parsowanie.
    """

    widget = forms.HiddenInput

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def clean(self, value):
        raw = (super().clean(value) or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError("Nieprawidłowy format adnotacji.") from exc
        if not isinstance(parsed, list):
            raise forms.ValidationError("Adnotacje muszą być listą.")
        return parsed


class ReviewDraftForm(forms.Form):
    """Szkic recenzji: wszystko opcjonalne (ocena może być jeszcze niepełna)."""

    score = forms.IntegerField(label="Punkty", required=False, min_value=0, max_value=1000)
    comment_internal = forms.CharField(label="Komentarz wewnętrzny", required=False, widget=forms.Textarea)
    comment_for_participant = forms.CharField(
        label="Komentarz dla uczestnika", required=False, widget=forms.Textarea
    )
    annotations = AnnotationsField()


class ReviewSubmitForm(ReviewDraftForm):
    """Wystawienie oceny: ``score`` obowiązkowy, zgodność ze skalą sprawdza serwis."""

    score = forms.IntegerField(label="Punkty", required=True, min_value=0, max_value=1000)


class AppealForm(forms.Form):
    """Reklamacja uczestnika. Dolny limit długości powtarza regułę domeny dla czytelnego błędu."""

    argument = forms.CharField(
        label="Uzasadnienie reklamacji",
        widget=forms.Textarea(attrs={"rows": 5}),
        min_length=MIN_ARGUMENT_LENGTH,
        max_length=MAX_TEXT_LENGTH,
    )


class AppealDecideForm(forms.Form):
    """Decyzja komisji odwoławczej."""

    status = forms.ChoiceField(
        label="Rozstrzygnięcie",
        choices=[
            (AppealStatus.REJECTED, "odrzucona"),
            (AppealStatus.ACCEPTED, "uwzględniona"),
            (AppealStatus.PARTIALLY_ACCEPTED, "częściowo uwzględniona"),
        ],
    )
    new_score = forms.IntegerField(label="Nowa punktacja", required=False, min_value=0, max_value=1000)
    justification = forms.CharField(
        label="Uzasadnienie", widget=forms.Textarea(attrs={"rows": 4}), max_length=MAX_TEXT_LENGTH
    )


class ResolveModerationForm(forms.Form):
    """Rozstrzygnięcie rozjazdu ocen przez koordynatora."""

    score = forms.IntegerField(label="Punkty", min_value=0, max_value=1000)
    rationale = forms.CharField(label="Uzasadnienie", required=False, widget=forms.Textarea)


class AssignThirdReviewerForm(forms.Form):
    """Wyznaczenie trzeciego recenzenta (runda rozjemcza)."""

    reviewer_id = forms.IntegerField(label="Recenzent", min_value=1)


class AssignReviewersForm(forms.Form):
    """Parametr przydziału recenzentów. Minimum dwóch – inaczej rozjazd nie ma jak powstać."""

    per_submission = forms.IntegerField(label="Recenzentów na pracę", min_value=2, max_value=10, initial=2)


class VerifyDistrictForm(forms.Form):
    """Potwierdzenie okręgu członka komitetu."""

    district = voivodeship_field("Województwo")


class InvitationForm(forms.Form):
    """Generowanie kodu zaproszenia. Kod jawny jest pokazywany dokładnie raz."""

    district = voivodeship_field("Województwo (narzucone kodem)", required=False)
    valid_days = forms.IntegerField(label="Ważność (dni)", min_value=1, max_value=365, initial=14)
    max_uses = forms.IntegerField(label="Limit użyć", min_value=1, max_value=100, initial=1)
    is_appeals = forms.BooleanField(label="Komisja odwoławcza", required=False)
    requires_approval = forms.BooleanField(label="Wymaga zatwierdzenia (PENDING)", required=False)


class PublishResultsForm(forms.Form):
    """Publikacja wyników etapu wraz z wyborem trybu anonimizacji."""

    anonymization = forms.ChoiceField(label="Anonimizacja", choices=Anonymization.choices)


# --- etapy i zadania w panelu koordynatora -----------------------------------------------------

#: Format pola ``<input type="datetime-local">`` – jedyny, jaki wysyła przeglądarka. Sekundy są
#: opcjonalne (Chrome dokłada je, gdy pole ma krok sekundowy), stąd oba warianty.
LOCAL_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")

#: Górny limit treści zadania. Caddy przepuszcza 25 MB (``MAX_UPLOAD_MB``), więc 20 MB mieści się
#: z zapasem na narzut wieloczęściowego formularza.
MAX_STATEMENT_MB = 20


def _to_minute(value):
    """Wartość porównywalna z tym, co potrafi wysłać ``<input type="datetime-local">``.

    Obcinanie dotyczy **wyłącznie** dat: nazwa etapu, forma, miejsce i tolerancja przechodzą bez
    zmian, bo ich rozdzielczość w formularzu jest dokładnie taka, jak w bazie. Gdyby ta funkcja
    próbowała „normalizować” także teksty, zmiana nazwy przestałaby być widoczna w porównaniu.
    """
    return value.replace(second=0, microsecond=0) if isinstance(value, datetime) else value


#: Górna granica numeru zadania w formularzu. Model dopuszcza cokolwiek dodatniego, ale arkusz
#: etapu ma kilka zadań – trzycyfrowy numer to zawsze literówka, a nie plan zawodów.
MAX_PROBLEM_NUMBER = 99


class LocalDateTimeField(forms.DateTimeField):
    """Data i godzina w czasie lokalnym serwisu, wpisywana natywnym ``<input type="datetime-local">``.

    Konwersji stref **nie robimy ręcznie**: ``forms.DateTimeField`` przepuszcza wartość początkową
    przez ``to_current_timezone`` (czyli ``timezone.localtime``), a wpisaną – przez
    ``from_current_timezone`` (czyli ``make_aware`` w ``settings.TIME_ZONE``). Koordynator wpisuje
    więc godzinę polską, a w bazie ląduje UTC – bez ani jednej arytmetyki na godzinach w naszym
    kodzie, która przy zmianie czasu rozjeżdżałaby się dwa razy w roku.

    Widżet dostaje jawny ``format``: bez niego Django wypisałoby wartość początkową w formacie
    lokalizacji „pl” („7 listopada 2026 23:59”), którego pole ``datetime-local`` nie rozumie –
    formularz edycji otwierałby się z pustymi terminami.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", LOCAL_DATETIME_FORMATS)
        kwargs.setdefault(
            "widget",
            forms.DateTimeInput(attrs={"type": "datetime-local"}, format=LOCAL_DATETIME_FORMATS[0]),
        )
        super().__init__(**kwargs)

    def clean(self, value):
        """Ucina sekundy: pole ``datetime-local`` i tak ich nie pokazuje.

        Bez tego zapis z panelu zostawiałby w bazie sekundy przepisane z poprzedniej wartości albo
        wyzerowane zależnie od przeglądarki – a deadline „23:59:37” jest różnicą, której nikt nie
        ogłosił i której nie widać na żadnym ekranie.
        """
        value = super().clean(value)
        return value.replace(second=0, microsecond=0) if value is not None else value


class StageForm(forms.ModelForm):
    """Oś czasu etapu w panelu koordynatora.

    Kolejność terminów jest sprawdzana przez ``Stage.full_clean()`` – ModelForm woła je w
    ``_post_clean``, więc komunikaty z ``Stage.clean()`` trafiają pod właściwe pola i nie ma
    drugiej kopii tej reguły w warstwie WWW. Reguły zależne od stanu bazy (zgłoszenia, zamknięcie
    etapu) zostają w serwisie ``update_stage`` – formularz ich nie zna.

    Czego tu nie ma: ``kind`` i ``edition`` (tożsamość etapu, zmiana byłaby podmianą obiektu),
    ``results_published_at`` i ``closed_at`` (ślady zdarzeń, patrz ``STAGE_EDITABLE_FIELDS``)
    oraz skala punktacji i próg kwalifikacji – te zostają w ``/admin/``, bo są konfiguracją
    oceniania, a nie kalendarzem.
    """

    class Meta:
        model = Stage
        fields = STAGE_EDITABLE_FIELDS
        field_classes = {
            "opens_at": LocalDateTimeField,
            "deadline_at": LocalDateTimeField,
            "review_deadline_at": LocalDateTimeField,
            "appeal_window_opens_at": LocalDateTimeField,
            "appeal_window_closes_at": LocalDateTimeField,
        }
        labels = {"name": "Nazwa etapu", "format": "Forma etapu"}
        help_texts = {
            "name": ("Puste pole = nazwa domyślna dla rodzaju etapu (Eliminacje / Wojewódzki / Finał)."),
            "format": (
                "Etap w formie rozmowy nie przyjmuje plików: zamiast zadań uczestnicy "
                "zakwalifikowani do etapu zapisują się na jeden z terminów wyznaczonych przez "
                "koordynatora. Formy nie zmienisz, gdy etap ma już oddane prace albo zapisy."
            ),
            "location": "Puste dla etapu zdalnego. Np. „Kraków, Wydział Fizyki UJ”.",
            "grace_seconds": ("Tolerancja po terminie oddania. Upload zamyka się dopiero po jej upływie."),
        }

    def changed_values(self) -> dict:
        """Pola, które koordynator **faktycznie** zmienił – w rozdzielczości formularza.

        ``<input type="datetime-local">`` pracuje z dokładnością do minuty, więc otwarcie
        formularza i zapis bez zmian przepisywałoby każdą datę: sekundy zapisane spoza panelu
        (admin, seed) zniknęłyby, a w audycie stanąłby wpis o zmianie, której nie było. Gorzej
        w etapie zamkniętym – zapis samego okna reklamacji wyglądałby wtedy jak próba przesunięcia
        także otwarcia i deadline'u, czyli kończyłby się odmową.

        Porównanie należy do formularza, a nie do serwisu: to **rozdzielczość pola**, czyli sprawa
        warstwy prezentacji. ``update_stage`` porównuje wartości dokładnie.
        """
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name in STAGE_EDITABLE_FIELDS and _to_minute(self.initial.get(name)) != _to_minute(value)
        }


class StageCreateForm(StageForm):
    """Dodanie etapu do bieżącej edycji. ``kind`` wybiera się spośród rodzajów, których brakuje.

    Lista wyboru jest zawężana w widoku (``missing_stage_kinds``), a nie tutaj: to zapytanie do
    bazy o **konkretną** edycję, a formularz jej nie zna. Para (edycja, rodzaj) jest unikalna
    w bazie, więc wyścig o ostatni wolny rodzaj kończy się błędem walidacji, nie duplikatem.
    """

    class Meta(StageForm.Meta):
        fields = ("kind", *STAGE_EDITABLE_FIELDS)

    def __init__(self, *args, kind_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = list(kind_choices)


class ProblemForm(forms.ModelForm):
    """Zadanie etapu: numer, tytuł, treść w PDF, dopuszczone formaty rozwiązań i limit rozmiaru.

    ``allowed_formats`` jest w modelu polem JSON (lista), a w formularzu – zestawem pól wyboru:
    redaktor zaznacza „pdf/ipynb/py”, a nie wpisuje literał JSON, którego literówka objawiłaby się
    dopiero przy uploadzie uczestnika.

    Treść zadania jest sprawdzana **po zawartości pliku** (``%PDF-``), a nie po rozszerzeniu ani
    nagłówku ``Content-Type`` – dokładnie tak, jak rozwiązania uczestników
    (``apps.submissions.validators``). Plik trafia na storage ``private_media``: przed otwarciem
    etapu nie ma publicznego adresu, spod którego dałoby się go pobrać.
    """

    number = forms.IntegerField(label="Numer zadania", min_value=1, max_value=MAX_PROBLEM_NUMBER)
    max_file_mb = forms.IntegerField(
        label="Limit rozmiaru rozwiązania (MB)",
        min_value=1,
        max_value=MAX_FILE_MB_LIMIT,
        initial=DEFAULT_MAX_FILE_MB,
        help_text=f"Ile może ważyć plik uczestnika: 1–{MAX_FILE_MB_LIMIT} MB.",
    )
    allowed_formats = forms.MultipleChoiceField(
        label="Dozwolone formaty rozwiązań",
        choices=[(value, f".{value}") for value in SUPPORTED_FILE_FORMATS],
        widget=forms.CheckboxSelectMultiple,
        help_text="Zaznacz co najmniej jeden format.",
    )
    statement_pdf = forms.FileField(
        label="Treść zadania (PDF)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        help_text=f"Plik PDF, maksymalnie {MAX_STATEMENT_MB} MB. Nowy plik zastępuje poprzedni.",
    )
    confirm_open_stage = forms.BooleanField(
        label="Rozumiem, że uczestnicy już widzą treść tego zadania",
        required=False,
        help_text="Wymagane przy podmianie treści po otwarciu etapu.",
    )

    class Meta:
        model = Problem
        fields = ("number", "title", "statement_pdf", "allowed_formats", "max_file_mb")

    def __init__(self, *args, stage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage = stage if stage is not None else getattr(self.instance, "stage", None)
        # Pole potwierdzenia ma sens wyłącznie przy edycji zadania w otwartym etapie – przy dodawaniu
        # nie ma czego podmieniać, a pusty checkbox „rozumiem…” tylko zaciemniałby formularz.
        if not self._needs_confirmation():
            del self.fields["confirm_open_stage"]

    def _needs_confirmation(self) -> bool:
        return bool(self.instance.pk and self.stage is not None and self.stage.has_opened())

    def clean_number(self):
        """Numer unikalny w etapie – komunikat pod polem, a nie ogólny błąd o unikalności.

        ``unique_together`` z modelu dałoby błąd niezwiązany z żadnym polem („Zadanie z tymi Etap
        i Numer już istnieje”), czyli w formularzu wylądowałby nad całością, a nie przy tym polu,
        które trzeba poprawić.
        """
        number = self.cleaned_data["number"]
        if self.stage is None:  # pragma: no cover - widok zawsze podaje etap
            return number
        duplicates = Problem.objects.filter(stage=self.stage, number=number).exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError(f"Zadanie o numerze {number} już jest w tym etapie.")
        return number

    def clean_statement_pdf(self):
        """Rozmiar i treść pliku. Wartość niebędąca uploadem to plik, który już jest na storage."""
        upload = self.cleaned_data.get("statement_pdf")
        if not isinstance(upload, UploadedFile):
            return upload
        if upload.size > MAX_STATEMENT_MB * MEGABYTE:
            raise forms.ValidationError(
                f"Plik ma {upload.size} B – limit treści zadania to {MAX_STATEMENT_MB} MB."
            )
        if upload.size == 0:
            raise forms.ValidationError("Plik jest pusty.")
        try:
            validate_pdf(upload)
        except DomainError as exc:
            raise forms.ValidationError(str(exc.detail)) from exc
        return upload

    def uploaded_statement(self):
        """Nowy plik treści albo ``None``. Serwis rozpoznaje po tym, czy podmieniać treść."""
        upload = self.cleaned_data.get("statement_pdf")
        return upload if isinstance(upload, UploadedFile) else None


class InterviewSlotsForm(forms.Form):
    """Dodanie serii terminów rozmowy kwalifikacyjnej.

    Formularz opisuje **serię**, a nie pojedynczy termin, bo tak wygląda praca koordynatora:
    „w czwartek od 9:00 dwanaście rozmów po 20 minut, po jednej osobie”. Wpisywanie tego dwanaście
    razy z ręki byłoby dwunastoma okazjami do pomyłki o godzinę.

    Granice liczbowe są tu powtórzone za serwisem (``competitions.interviews``) świadomie: to
    kształt danych, więc błąd ma stanąć pod polem, a nie wrócić komunikatem po nieudanym zapisie.
    Reguły zależne od stanu (okno etapu, forma etapu, terminy w przeszłości) zostają w serwisie.
    """

    starts_at = LocalDateTimeField(label="Początek pierwszego terminu")
    duration_minutes = forms.IntegerField(
        label="Długość jednej rozmowy (min)",
        min_value=MIN_DURATION_MINUTES,
        max_value=MAX_DURATION_MINUTES,
        initial=20,
    )
    count = forms.IntegerField(
        label="Ile terminów po kolei",
        min_value=MIN_SLOT_COUNT,
        max_value=MAX_SLOT_COUNT,
        initial=1,
        help_text="Terminy powstają jeden po drugim, bez przerw między nimi.",
    )
    capacity = forms.IntegerField(
        label="Miejsc w jednym terminie",
        min_value=1,
        max_value=MAX_SLOT_CAPACITY,
        initial=1,
        help_text="Ile osób może zapisać się na ten sam termin.",
    )
    meeting_url = forms.URLField(
        label="Link do rozmowy",
        required=False,
        max_length=500,
        assume_scheme="https",
        help_text="Widoczny wyłącznie dla osób zapisanych na dany termin. Można uzupełnić później.",
    )
    note = forms.CharField(
        label="Oznaczenie",
        required=False,
        max_length=200,
        help_text="Np. „komisja A” – dla uczestnika to podpowiedź, do kogo trafia.",
    )
