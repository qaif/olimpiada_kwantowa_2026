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
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile

from apps.accounts.consents import BY_KIND, CONSENT_FIELD_NAMES, CONSENTS, ConsentKind, is_minor, labels
from apps.accounts.models import GRADE_CHOICES, User, Voivodeship
from apps.accounts.services import (
    MAX_INVITATION_EMAILS,
    MAX_INVITATION_NOTE_LENGTH,
    parse_email_list,
)
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
    Edition,
    Problem,
    Stage,
)
from apps.competitions.services import REGISTRATION_EDITABLE_FIELDS, STAGE_EDITABLE_FIELDS
from apps.core.api import DomainError
from apps.results.models import Anonymization
from apps.submissions.validators import MEGABYTE, validate_pdf
from apps.web.captcha import CaptchaFormMixin

# Pusta pozycja na początku listy: przeglądarka inaczej wybrałaby pierwsze województwo za
# rejestrującego się i cichaczem przypisała mu okręg, którego nigdy świadomie nie wskazał.
EMPTY_VOIVODESHIP_CHOICE = ("", "— wybierz województwo —")
VOIVODESHIP_CHOICES = (EMPTY_VOIVODESHIP_CHOICE, *Voivodeship.choices)


def voivodeship_field(label: str, *, required: bool = True) -> forms.ChoiceField:
    """Pole wyboru województwa. Lista jest zamknięta – wolny tekst nie ma tu wstępu."""
    return forms.ChoiceField(label=label, choices=VOIVODESHIP_CHOICES, required=required)


def phone_field(*, required: bool = True) -> forms.CharField:
    """Telefon kontaktowy. Kształt numeru sprowadza do jednej postaci ``accounts.phones``.

    Pole jest ``type="tel"``, a nie ``text``: na telefonie otwiera klawiaturę numeryczną, a to
    właśnie na telefonie ten numer najczęściej się wpisuje. Walidacji kształtu tu nie ma –
    rozstrzyga ``normalize_phone`` w serwisie, bo ta sama reguła obowiązuje API i rejestrację
    przez dostawcę zewnętrznego.
    """
    return forms.CharField(
        label="Telefon",
        max_length=32,
        required=required,
        help_text="Do kontaktu w sprawach organizacyjnych, np. +48 600 000 000.",
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
    )


def password_field(label: str = "Hasło") -> forms.CharField:
    """Pole hasła przy zakładaniu konta.

    ``autocomplete="new-password"`` jest tu istotne: bez niego przeglądarka podstawia **zapisane**
    hasło do innego konta w tym serwisie, a menedżer haseł nie proponuje wygenerowania nowego.
    """
    return forms.CharField(
        label=label,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        max_length=200,
    )


#: Nazwa pola powtórzenia hasła. Do serwisu **nie trafia** – jest zdejmowane w ``clean()``
#: (widoki wołają serwisy ``**form.cleaned_data``, więc każdy nadmiarowy klucz byłby TypeError).
PASSWORD_CONFIRM_FIELD = "password2"

PASSWORD_MISMATCH_MESSAGE = "Hasła nie są identyczne."


def clean_password_pair(form: forms.Form, cleaned: dict | None) -> dict:
    """Sprawdza powtórzenie hasła i siłę hasła, zdejmując pole pomocnicze z ``cleaned_data``.

    Dwie rzeczy naraz, obie po to, żeby błąd stanął **pod polem**, a nie w chmurce nad formularzem:

    - zgodność obu pól. Literówka w haśle przy rejestracji jest nieodwracalna w praktyce: konto
      powstaje z hasłem, którego nikt nie zna, a odzyskanie go wymaga przejścia przez resetem
      hasła adresu, którego uczestnik jeszcze nie potwierdził,
    - ``validate_password`` z ``AUTH_PASSWORD_VALIDATORS``. Nie jest to druga definicja reguły:
      lista walidatorów w ustawieniach jest jedna, a serwis (``accounts.services``) woła ją dalej
      i pozostaje rozstrzygający dla API. Tutaj wołamy ją wyłącznie po to, żeby „hasło za krótkie”
      wylądowało przy polu hasła, a nie jako błąd niezwiązany z żadnym polem.

    Sprawdzenie podobieństwa do danych konta (``UserAttributeSimilarityValidator``) dostaje
    niezapisany ``User`` z tym, co jest w formularzu – tak samo robi ``accounts.services``.
    """
    cleaned = cleaned if cleaned is not None else {}
    password = cleaned.get("password") or ""
    confirmation = cleaned.pop(PASSWORD_CONFIRM_FIELD, None)
    if password and confirmation is not None and password != confirmation:
        form.add_error(PASSWORD_CONFIRM_FIELD, PASSWORD_MISMATCH_MESSAGE)
        return cleaned
    if password:
        candidate = User(
            email=cleaned.get("email") or "",
            first_name=cleaned.get("first_name") or "",
            last_name=cleaned.get("last_name") or "",
        )
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as exc:
            form.add_error("password", exc.messages)
    return cleaned


#: Pola bloku „szkoła” renderowanego ręcznie w szablonie (``web/_school_picker.html``). Reszta
#: formularza idzie zwykłą pętlą, więc ta krotka jest jedynym miejscem, które trzeba zmienić,
#: gdyby blok urósł o kolejne pole.
SCHOOL_FIELD_NAMES = ("school_id", "school_query", "school_custom", "school")

#: Klasa doklejana przez Django do etykiety (i do akapitu ``as_p``) każdego pola wymaganego.
#: Gwiazdkę dorysowuje arkusz (``label.required::after`` w static/css/app.css) – w HTML-u nie ma
#: jej ani razu, bo czytnik ekranu i tak dostaje wymagalność z atrybutu ``required`` na kontrolce,
#: a przeczytana na głos „gwiazdka” po każdej etykiecie byłaby szumem. Stała, a nie napis wpisany
#: w czterech klasach: formularze rejestracji mają wyglądać tak samo, a nie prawie tak samo.
REQUIRED_CSS_CLASS = "required"


class SchoolChoiceMixin(forms.Form):
    """Wybór szkoły ze słownika SIO albo – świadomą decyzją – wpisanie jej ręcznie.

    Cztery pola zamiast jednego, bo jedno pole tekstowe nie potrafi odróżnić „nie znalazłem swojej
    szkoły” od „nie chciało mi się szukać”:

    - ``school_query`` – to, co uczestnik widzi i w co pisze. Do serwisu **nie trafia**,
    - ``school_id`` – ukryty wynik wyboru z podpowiedzi; to on wiąże profil z rejestrem,
    - ``school_custom`` – kratka „mojej szkoły nie ma na liście”. Jej rolą jest **odsłonić** pole
      wolnego tekstu (skrypt trzyma je schowane, dopóki nie jest potrzebne), a nie poświadczyć,
      że wpis jest serio – patrz ``clean()``,
    - ``school`` – nazwa wpisana ręcznie, dla szkół spoza wykazu.

    ``clean()`` sprowadza to do dokładnie dwóch kluczy, jakich oczekuje
    ``accounts.services.register_participant``: ``school`` (tekst) i ``school_id``.

    Atrybuty ``data-picker`` stoją przy widżetach, a nie w szablonie, bo szablon renderuje ten
    blok na czterech ekranach (rejestracja hasłem, przez dostawcę, edycja profilu) – jedna
    definicja to jedno miejsce, w którym punkt zaczepienia może się rozjechać ze skryptem
    ``static/js/school-picker.js``.
    """

    school_id = forms.IntegerField(
        required=False, min_value=1, widget=forms.HiddenInput(attrs={"data-picker": "school-id"})
    )
    school_query = forms.CharField(
        label="Szkoła",
        required=False,
        max_length=255,
        help_text="Zacznij pisać nazwę lub miejscowość i wybierz szkołę z podpowiedzi.",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "role": "combobox",
                "aria-expanded": "false",
                "aria-autocomplete": "list",
                "aria-controls": "school-suggestions",
                "data-picker": "query",
            }
        ),
    )
    school_custom = forms.BooleanField(
        label="Mojej szkoły nie ma na liście",
        required=False,
        widget=forms.CheckboxInput(attrs={"data-picker": "custom"}),
    )
    school = forms.CharField(
        label="Nazwa szkoły",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"data-picker": "free-input"}),
    )

    @property
    def school_field_names(self) -> tuple[str, ...]:
        """Nazwy pól bloku „szkoła” – szablon pomija je w zwykłej pętli po polach formularza."""
        return SCHOOL_FIELD_NAMES

    def clean(self):
        """Mapuje blok na kwargi serwisu. Dokładnie jedna droga zostaje wypełniona.

        Reguła **nie jest** twardym „albo/albo”, i to jest poprawka po zgłoszeniu z produkcji:
        uczestnik, któremu nie działały podpowiedzi (zablokowany skrypt), wpisał nazwę szkoły
        w widoczne pole „Nazwa szkoły”, nie zaznaczył kratki „Mojej szkoły nie ma na liście”
        i usłyszał od serwera, żeby wybrał szkołę z listy. Wpisany tekst jest jednoznaczną
        odpowiedzią na pytanie „jaka szkoła” – kratka służy do **odsłonięcia** tego pola, a nie
        do poświadczenia, że wpis jest serio. Odmowa z powodu niezaznaczonej kratki odsyłała
        człowieka do listy, której akurat u niego nie było.
        """
        cleaned = super().clean()
        custom = cleaned.get("school_custom")
        query = (cleaned.get("school_query") or "").strip()
        # Pola pomocnicze nie mają prawa dojechać do serwisu – widoki wołają go
        # ``**form.cleaned_data``, więc każdy nadmiarowy klucz byłby TypeError.
        cleaned.pop("school_query", None)
        cleaned.pop("school_custom", None)
        free_text = (cleaned.get("school") or "").strip()
        cleaned["school"] = free_text
        if custom:
            # Zaznaczony wyjątek unieważnia wcześniejszy wybór z listy: liczy się ostatnia decyzja
            # uczestnika, a nie kolejność, w jakiej klikał.
            cleaned["school_id"] = None
            if not free_text:
                self.add_error("school", "Podaj nazwę szkoły.")
        elif cleaned.get("school_id"):
            # Wybór ze słownika wygrywa: nazwę i tak przepisze ``_resolve_school`` z rejestru,
            # więc trzymanie obok niej wolnego tekstu dawałoby dwie wersje tej samej szkoły.
            cleaned["school"] = ""
        elif not free_text:
            # Nic nie wybrano i nic nie wpisano. Dwa różne komunikaty, bo to dwie różne sytuacje:
            # ktoś, kto **coś** wpisał w pole wyszukiwarki, jest o krok od celu i trzeba mu podać
            # obie wyjścia; ktoś, kto nie tknął bloku, potrzebuje zwykłego „to pole jest wymagane”.
            self.add_error(
                "school_query",
                (
                    "Wybierz szkołę z podpowiedzi albo zaznacz „Mojej szkoły nie ma na liście” "
                    "i wpisz jej nazwę."
                )
                if query
                else "Wybierz szkołę z listy albo zaznacz, że nie ma jej na liście.",
            )
        return cleaned


class ConsentFieldsMixin(forms.Form):
    """Blok „Zgody” obu formularzy rejestracji uczestnika.

    Pola powstają **w** ``__init__``, a nie jako atrybuty klasy, i to jest sedno: etykieta każdej
    zgody zawiera odnośnik do dokumentu i nazwę organizatora, a jedno i drugie czyta się z bazy
    (drzewo stron, ``cms.SiteSettings``). Pole zdefiniowane na poziomie klasy zapamiętałoby te
    wartości przy imporcie modułu, czyli raz na proces – przeniesienie dokumentu w ``/cms/`` albo
    zmiana nazwy fundacji objawiłyby się dopiero po restarcie aplikacji.

    Czego ten mixin **nie** robi: nie ustawia ``required=True`` na regulaminie i RODO. Zgody
    wymagane rozstrzyga serwis (``accounts.services.validate_consents``), bo ta sama reguła
    obowiązuje API i logowanie społecznościowe; formularz oddaje wtedy komunikat serwisu jako
    błąd niezwiązany z polem. Wyjątkiem jest zgoda opiekuna – tam błąd **musi** stanąć pod polem,
    bo wynika z innego pola tego samego formularza (rocznika) i bez wskazania palcem uczestnik
    nie wie, czego od niego chcą.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        texts = labels()
        for consent in CONSENTS:
            self.fields[consent.field_name] = forms.BooleanField(
                label=texts[consent.kind],
                required=False,
                help_text=consent.help_text,
                # Bez dwukropka: etykietą jest całe zdanie oświadczenia zakończone kropką,
                # a domyślny ``label_suffix`` dokleiłby do niej „:”.
                label_suffix="",
            )
        # Pola dołożone po ``super().__init__`` stają na końcu ``self.fields`` niezależnie od
        # ``field_order`` – tam je zresztą chcemy mieć, ale kolejność ma wynikać z deklaracji,
        # a nie z tego, w którym momencie powstało pole.
        self.order_fields(self.field_order)

    @property
    def consent_field_names(self) -> tuple[str, ...]:
        """Nazwy pól zgód – szablon renderuje je w osobnym ``<fieldset>``, poza zwykłą pętlą."""
        return CONSENT_FIELD_NAMES

    def clean(self):
        """Zgoda opiekuna wymagana dla niepełnoletniego – błąd pod polem, nie nad formularzem.

        Rocznik bywa niepoprawny (pole nie przeszło walidacji) – wtedy milczymy i zostawiamy
        rozstrzygnięcie serwisowi: dopisywanie drugiego błędu do formularza, w którym pierwszy
        jest oczywisty, tylko zaciemnia, co poprawić.
        """
        cleaned = super().clean()
        birth_year = cleaned.get("birth_year")
        guardian = BY_KIND[ConsentKind.GUARDIAN]
        if birth_year and is_minor(birth_year) and not cleaned.get(guardian.field_name):
            self.add_error(guardian.field_name, guardian.missing_message)
        return cleaned


class EmailAuthenticationForm(AuthenticationForm):
    """Logowanie adresem e-mail. ``AuthenticationForm`` trzyma login w polu ``username``."""

    username = forms.EmailField(
        label="Adres e-mail",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
    )

    def clean_username(self) -> str:
        return (self.cleaned_data.get("username") or "").strip().lower()


def grade_field() -> forms.TypedChoiceField:
    """Klasa uczestnika. Lista zamknięta – rocznik spoza 1–5 nie istnieje w szkole ponadpodstawowej."""
    return forms.TypedChoiceField(
        label="Klasa",
        choices=[("", "— wybierz klasę —"), *((str(value), label) for value, label in GRADE_CHOICES)],
        coerce=int,
        empty_value=None,
    )


#: Kolejność pól w formularzach uczestnika. Jawna, bo pola bloku „szkoła” przychodzą z domieszki,
#: a Django ustawia pola klas bazowych **przed** własnymi – bez tego adres e-mail stanąłby pod
#: wyborem szkoły. Województwo musi poprzedzać blok szkoły także w DOM: podpowiedzi zawężają się
#: do wybranego województwa, więc pytanie o nie po wskazaniu szkoły byłoby odwróceniem kolejności.
PARTICIPANT_FIELD_ORDER = (
    "email",
    "password",
    # Powtórzenie hasła stoi **bezpośrednio** pod hasłem: para pól ma być czytana jako jedna
    # rubryka, a cokolwiek między nimi zamieniłoby ją w dwa niezależne pytania o hasło.
    PASSWORD_CONFIRM_FIELD,
    "first_name",
    "last_name",
    "phone",
    "district",
    *SCHOOL_FIELD_NAMES,
    "grade",
    "birth_year",
    # Zgody na końcu i w jednym bloku: to osobne oświadczenia, a nie kolejne dane osobowe –
    # szablon renderuje je w ``<fieldset class="consents">`` (patrz ``ConsentFieldsMixin``).
    *CONSENT_FIELD_NAMES,
)


class ParticipantRegisterForm(CaptchaFormMixin, ConsentFieldsMixin, SchoolChoiceMixin):
    """Rejestracja otwarta uczestnika – dane wchodzą prosto do ``register_participant``.

    ``CaptchaFormMixin`` stoi pierwszy: dokłada CAPTCHĘ i pułapki antyspamowe na koniec formularza
    (patrz apps/web/captcha.py). Formularz społecznościowy poniżej ich **nie** ma – tam rejestracja
    jest już za zalogowaniem u dostawcy, więc bot musiałby najpierw przejść OAuth Google/Facebooka.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = [name for name in PARTICIPANT_FIELD_ORDER]

    email = forms.EmailField(label="Adres e-mail", max_length=254)
    password = password_field()
    password2 = password_field("Powtórz hasło")
    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    phone = phone_field()
    district = voivodeship_field("Województwo")
    grade = grade_field()
    birth_year = forms.IntegerField(label="Rok urodzenia", min_value=1900, max_value=2100)

    def clean(self):
        return clean_password_pair(self, super().clean())


class SocialParticipantSignupForm(ConsentFieldsMixin, SchoolChoiceMixin):
    """Dokończenie rejestracji po zalogowaniu przez Google/Facebooka.

    Czego tu **nie ma** i dlaczego:

    - **adresu e-mail** – przychodzi od dostawcy i jest tylko pokazywany. Edytowalne pole
      pozwalałoby założyć konto na cudzy adres, a potem przejąć je resetem hasła,
    - **hasła** – konto zakładane tą drogą nie ma użytecznego hasła; kto chce logować się także
      hasłem, ustawia je przez „Nie pamiętasz hasła?”.

    Imię i nazwisko przychodzą z profilu u dostawcy jako wartości początkowe i **są edytowalne**:
    w wynikach olimpiady ma stać nazwisko z legitymacji, a nie pseudonim z konta społecznościowego.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = [
        name for name in PARTICIPANT_FIELD_ORDER if name not in ("email", "password", PASSWORD_CONFIRM_FIELD)
    ]

    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    phone = phone_field()
    district = voivodeship_field("Województwo")
    grade = grade_field()
    birth_year = forms.IntegerField(label="Rok urodzenia", min_value=1900, max_value=2100)


class CommitteeRegisterForm(CaptchaFormMixin):
    """Rejestracja członka komitetu na kod zaproszenia.

    CAPTCHA jest tu także po to, żeby kodu zaproszenia nie dało się zgadywać maszynowo – limit
    prób (scope ``register``) ogranicza liczbę strzałów, a CAPTCHA podnosi koszt każdego z nich.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = [
        "email",
        "password",
        PASSWORD_CONFIRM_FIELD,
        "first_name",
        "last_name",
        "invitation_code",
        "district",
    ]

    email = forms.EmailField(label="Adres e-mail", max_length=254)
    password = password_field()
    password2 = password_field("Powtórz hasło")
    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    invitation_code = forms.CharField(label="Kod zaproszenia", max_length=200)
    district = voivodeship_field("Województwo (deklarowane)", required=False)

    def clean(self):
        return clean_password_pair(self, super().clean())


#: Kolejność pól edycji własnego profilu. Bez e-maila i hasła: adres zmienia się osobnym
#: formularzem z potwierdzeniem na nowej skrzynce, hasło – przez „Nie pamiętasz hasła?”.
PARTICIPANT_PROFILE_FIELD_ORDER = (
    "first_name",
    "last_name",
    "phone",
    "district",
    *SCHOOL_FIELD_NAMES,
    "grade",
    "birth_year",
)


def participant_profile_initial(participant) -> dict:
    """Wartości początkowe formularza edycji profilu, łącznie z odtworzeniem stanu bloku „szkoła”.

    Blok szkoły trzyma stan w trzech polach i żadne z nich nie jest w bazie: ``school_query`` jest
    tym, co widać, a ``school_custom`` – decyzją „mojej szkoły nie ma na liście”. Odtwarzamy je
    z tego, co w bazie **jest**: pusty ``school_ref`` znaczy szkołę wpisaną ręcznie, więc formularz
    otwiera się od razu w trybie wolnego tekstu. Bez tego uczestnik, który chce zmienić klasę,
    musiałby przy każdym zapisie szukać swojej szkoły od nowa.
    """
    from_registry = participant.school_ref_id is not None
    return {
        "first_name": participant.user.first_name,
        "last_name": participant.user.last_name,
        "phone": participant.phone,
        "district": participant.district,
        "grade": participant.grade,
        "birth_year": participant.birth_year,
        "school_id": participant.school_ref_id,
        "school_query": participant.school,
        "school_custom": not from_registry,
        "school": "" if from_registry else participant.school,
    }


class ParticipantProfileForm(SchoolChoiceMixin):
    """Edycja własnych danych uczestnika (``/me/profile/``).

    Zakres jest dokładnie taki, jak w rejestracji **minus** poświadczenia i minus zgody: zgody są
    oświadczeniami z własną historią (``ConsentRecord``) i nie zmienia się ich zapisem formularza
    danych. ``public_code`` nie jest edytowalny nigdzie – to identyfikator w ogłoszonych tabelach.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = [name for name in PARTICIPANT_PROFILE_FIELD_ORDER]

    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    phone = phone_field()
    district = voivodeship_field("Województwo")
    grade = grade_field()
    birth_year = forms.IntegerField(label="Rok urodzenia", min_value=1900, max_value=2100)


class AccountNamesForm(forms.Form):
    """Imię i nazwisko dla konta bez profilu uczestnika (``/account/profile/``).

    Województwa członka komitetu tu nie ma **świadomie**: to na nim opiera się reguła konfliktu
    interesów przy przydziale recenzji, więc recenzent, który mógłby je sobie przestawić, mógłby też
    wejść na prace ze swojego województwa. Zmiana (i usunięcie) zostaje u koordynatora.
    """

    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)


class EmailChangeForm(forms.Form):
    """Wniosek o zmianę adresu e-mail konta. Adres zmienia się dopiero po kliknięciu w potwierdzenie."""

    new_email = forms.EmailField(
        label="Nowy adres e-mail",
        max_length=254,
        help_text=(
            "Na ten adres wyślemy link potwierdzający. Do czasu potwierdzenia logujesz się "
            "dotychczasowym adresem."
        ),
    )


class ActivationResendForm(forms.Form):
    """Ponowna wysyłka linku aktywacyjnego. Odpowiedź jest zawsze ta sama – bez enumeracji kont."""

    email = forms.EmailField(label="Adres e-mail", max_length=254)

    def clean_email(self) -> str:
        return (self.cleaned_data.get("email") or "").strip().lower()


class AccountDeleteForm(forms.Form):
    """Potwierdzenie usunięcia własnego konta.

    Oba pola są opcjonalne **w formularzu**, bo które z nich jest wymagane, zależy od konta:
    hasło dla konta hasłowego, przepisanie adresu dla konta zakładanego przez Google/Facebooka
    (ono użytecznego hasła nie ma). Rozstrzyga to serwis
    (``accounts.profile.verify_self_deletion_credentials``) – tam, gdzie wiadomo, jakie to konto.
    """

    password = forms.CharField(
        label="Aktualne hasło",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        max_length=200,
    )
    email = forms.CharField(label="Adres e-mail konta", required=False, max_length=254)
    confirm = forms.BooleanField(
        label="Rozumiem, że tej operacji nie da się odwrócić.",
        label_suffix="",
    )


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


class SetReviewScoreForm(forms.Form):
    """Korekta punktów pojedynczej recenzji. Zgodność ze skalą etapu rozstrzyga serwis."""

    score = forms.IntegerField(label="Punkty", min_value=0, max_value=1000)
    rationale = forms.CharField(label="Notatka", required=False, widget=forms.Textarea)


class OverrideFinalGradeForm(forms.Form):
    """Korekta oceny końcowej. Uzasadnienie jest obowiązkowe – minimalną długość pilnuje serwis."""

    score = forms.IntegerField(label="Punkty", min_value=0, max_value=1000)
    rationale = forms.CharField(label="Uzasadnienie", widget=forms.Textarea)


class ReviewerPickForm(forms.Form):
    """Wskazanie recenzenta z listy – wspólne dla reguły „z góry” i dla przydziału jednej pracy.

    Formularz sprawdza wyłącznie kształt (liczba dodatnia). Czy ta osoba jest aktywnym recenzentem
    i czy nie jest w konflikcie województwa, rozstrzyga serwis – ta sama reguła obowiązuje API.
    """

    reviewer_id = forms.IntegerField(label="Recenzent", min_value=1)


class AssignReviewersForm(forms.Form):
    """Parametr przydziału recenzentów. Minimum dwóch – inaczej rozjazd nie ma jak powstać."""

    per_submission = forms.IntegerField(label="Recenzentów na pracę", min_value=2, max_value=10, initial=2)


class VerifyDistrictForm(forms.Form):
    """Województwo członka komitetu ustalane przez koordynatora.

    Pole jest nieobowiązkowe, bo województwo członka komitetu jest opcjonalne: pusta pozycja
    („— brak —”) usuwa je, a nie jest błędem formularza.
    """

    district = voivodeship_field("Województwo", required=False)


class InvitationForm(forms.Form):
    """Generowanie kodu zaproszenia. Kod jawny jest pokazywany dokładnie raz."""

    district = voivodeship_field("Województwo (narzucone kodem)", required=False)
    valid_days = forms.IntegerField(label="Ważność (dni)", min_value=1, max_value=365, initial=14)
    max_uses = forms.IntegerField(label="Limit użyć", min_value=1, max_value=100, initial=1)
    is_appeals = forms.BooleanField(label="Komisja odwoławcza", required=False)
    requires_approval = forms.BooleanField(label="Wymaga zatwierdzenia (PENDING)", required=False)


class BulkInvitationForm(forms.Form):
    """Zaproszenia e-mailem: lista adresów i te same parametry, co przy pojedynczym kodzie.

    Czego tu **nie ma**: limitu użyć. Każdy adres dostaje własny kod jednorazowy i to jest cały
    sens tej sekcji – kod wspólny dla listy osób nie dałby się unieważnić pojedynczo ani powiedzieć
    po fakcie, kto z niego skorzystał.

    Adresy rozbija ``accounts.services.parse_email_list`` – ten sam parser, który dostaje serwis,
    więc formularz i wysyłka nie mogą policzyć dwóch różnych list. Błędne adresy zatrzymują
    **całą** wysyłkę i są wypisane z nazwy: przy częściowej wysyłce koordynator nie miałby jak
    stwierdzić, do kogo kod poszedł, a do kogo nie – kodów nie da się odtworzyć i porównać.
    """

    emails = forms.CharField(
        label="Adresy e-mail",
        widget=forms.Textarea(attrs={"rows": 6, "autocomplete": "off"}),
        help_text=(
            "Jeden adres na wiersz; dopuszczalne są też przecinki, średniki i spacje. "
            f"Najwyżej {MAX_INVITATION_EMAILS} adresów na raz."
        ),
    )
    district = voivodeship_field("Województwo (narzucone kodem)", required=False)
    valid_days = forms.IntegerField(label="Ważność (dni)", min_value=1, max_value=365, initial=14)
    is_appeals = forms.BooleanField(label="Komisja odwoławcza", required=False)
    requires_approval = forms.BooleanField(label="Wymaga zatwierdzenia (PENDING)", required=False)
    note = forms.CharField(
        label="Dopisek do listu (opcjonalnie)",
        required=False,
        max_length=MAX_INVITATION_NOTE_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Jedno zdanie od koordynatora, np. okoliczności zaproszenia. Trafia do treści listu.",
    )

    def clean_emails(self) -> list[str]:
        """Zwraca **listę** adresów – sprawdzoną, bez powtórzeń i bez różnic wielkości liter."""
        valid, invalid = parse_email_list(self.cleaned_data["emails"])
        if invalid:
            raise DjangoValidationError(
                "Te adresy nie wyglądają na poprawne: {}. Popraw je albo usuń – "
                "dopóki na liście jest błędny adres, nie wysyłamy żadnego zaproszenia.".format(
                    ", ".join(invalid)
                )
            )
        if not valid:
            raise DjangoValidationError("Podaj co najmniej jeden adres e-mail.")
        if len(valid) > MAX_INVITATION_EMAILS:
            raise DjangoValidationError(
                f"Najwyżej {MAX_INVITATION_EMAILS} adresów na raz (podano {len(valid)}). "
                "Podziel listę na części."
            )
        return valid


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


#: Format pola ``<input type="date">`` – jedyny, jaki wysyła przeglądarka. Polskiego
#: ``DATE_INPUT_FORMATS`` tu nie użyjemy: nie ma w nim ``%Y-%m-%d``, więc „2027-06-04” z natywnego
#: pola daty nie przeszłoby walidacji.
DATE_INPUT_FORMAT = "%Y-%m-%d"


class DayField(forms.DateField):
    """Sam dzień, wpisywany natywnym ``<input type="date">`` – bez godziny i bez strefy.

    Strefy nie ma tu **świadomie**: to nie moment, tylko data z ogłoszenia („4 czerwca 2027”).
    Gdyby dzień wydarzenia jechał przez ``DateTimeField``, trzeba by mu dopisać godzinę, której
    organizator nie podał, a potem tę godzinę przeliczać – i zakres potrafiłby przeskoczyć o dzień.

    Widżet dostaje jawny ``format`` z tego samego powodu, co ``LocalDateTimeField``: bez niego
    Django wypisałoby wartość początkową po polsku („4 czerwca 2027”), czego pole ``date`` nie
    rozumie, i formularz edycji otwierałby się z pustym terminem wydarzenia.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", (DATE_INPUT_FORMAT,))
        kwargs.setdefault("widget", forms.DateInput(attrs={"type": "date"}, format=DATE_INPUT_FORMAT))
        super().__init__(**kwargs)


class StageForm(forms.ModelForm):
    """Oś czasu etapu w panelu koordynatora.

    Kolejność terminów jest sprawdzana przez ``Stage.full_clean()`` – ModelForm woła je w
    ``_post_clean``, więc komunikaty z ``Stage.clean()`` trafiają pod właściwe pola i nie ma
    drugiej kopii tej reguły w warstwie WWW. Reguły zależne od stanu bazy (zgłoszenia, zamknięcie
    etapu) zostają w serwisie ``update_stage`` – formularz ich nie zna.

    Dwie pary dat w jednym formularzu opisują dwie różne rzeczy i tak są podpisane: okno oddawania
    prac (``opens_at``/``deadline_at``) egzekwuje serwer, a „termin wydarzenia”
    (``event_starts_on``/``event_ends_on``) jest tym, co czyta publiczność o etapie stacjonarnym.
    Wpisanie dni pobytu **nie rusza** uploadu – i odwrotnie.

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
            "event_starts_on": DayField,
            "event_ends_on": DayField,
            "review_deadline_at": LocalDateTimeField,
            "appeal_window_opens_at": LocalDateTimeField,
            "appeal_window_closes_at": LocalDateTimeField,
        }
        labels = {
            "name": "Nazwa etapu",
            "format": "Forma etapu",
            "event_starts_on": "Termin wydarzenia (od / do)",
            # Drugie pole tej samej rubryki nie powtarza podpisu – to jeden termin w dwóch polach,
            # a dwa identyczne nagłówki czytałyby się jak dwa osobne terminy.
            "event_ends_on": "do",
        }
        help_texts = {
            "name": ("Puste pole = nazwa domyślna dla rodzaju etapu (Eliminacje / Wojewódzki / Finał)."),
            "format": (
                "Etap w formie rozmowy nie przyjmuje plików: zamiast zadań uczestnicy "
                "zakwalifikowani do etapu zapisują się na jeden z terminów wyznaczonych przez "
                "koordynatora. Formy nie zmienisz, gdy etap ma już oddane prace albo zapisy."
            ),
            "location": "Puste dla etapu zdalnego. Np. „Kraków, Wydział Fizyki UJ”.",
            "grace_seconds": ("Tolerancja po terminie oddania. Upload zamyka się dopiero po jej upływie."),
            "event_starts_on": (
                "Tylko etapy stacjonarne: dni pobytu pokazywane publicznie, np. 4–7 czerwca 2027. "
                "Okno oddawania prac powyżej pozostaje bez zmian."
            ),
            "event_ends_on": "Oba pola wypełnia się razem albo zostawia puste.",
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


class RegistrationSettingsForm(forms.ModelForm):
    """Okno rejestracji uczestników w panelu koordynatora.

    Oba terminy są opcjonalne i to jest sedno tego ekranu: puste otwarcie znaczy „od zaraz”, puste
    zamknięcie – „do odwołania”. Kolejność sprawdza ``Edition.full_clean()`` (ModelForm woła je
    w ``_post_clean``), więc komunikat staje pod polem „zamknięcie”, a nie w chmurce nad
    formularzem – i nie ma drugiej kopii tej reguły w warstwie WWW.
    """

    class Meta:
        model = Edition
        fields = REGISTRATION_EDITABLE_FIELDS
        field_classes = {
            "registration_opens_at": LocalDateTimeField,
            "registration_closes_at": LocalDateTimeField,
        }
        labels = {
            "registration_enabled": "Rejestracja włączona",
            "registration_opens_at": "Otwarcie rejestracji",
            "registration_closes_at": "Zamknięcie rejestracji",
        }
        help_texts = {
            "registration_enabled": (
                "Wyłącznik awaryjny. Odznaczenie zamyka rejestrację natychmiast, niezależnie od "
                "terminów poniżej – i nie kasuje ich, więc po ponownym włączeniu okno wraca."
            ),
            "registration_opens_at": (
                "Puste = rejestracja jest otwarta od zaraz. Data przed terminem jest zapowiedzią: "
                "strona główna i menu pokazują wtedy „Rejestracja rusza …”."
            ),
            "registration_closes_at": "Puste = rejestracja trwa do odwołania.",
        }

    def changed_values(self) -> dict:
        """Pola faktycznie zmienione – w rozdzielczości formularza (minuty), jak w ``StageForm``.

        Bez tego samo otwarcie i zapisanie formularza przepisywałoby terminy wpisane spoza panelu
        (admin, seed) z pominięciem sekund, a w audycie stawałby wpis o zmianie, której nie było.
        """
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name in REGISTRATION_EDITABLE_FIELDS
            and _to_minute(self.initial.get(name)) != _to_minute(value)
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
