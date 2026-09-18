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
from django.utils import timezone
from django.utils.translation import gettext_lazy

from apps.accounts.consents import (
    CONSENT_FIELD_NAMES,
    ConsentKind,
    consent_set,
    is_minor,
    organizer_name,
)
from apps.accounts.consents import (
    label as consent_label,
)
from apps.accounts.models import (
    DIRECTORY_INSTITUTION_TYPES,
    GRADE_CHOICES,
    CommitteeStatus,
    RegistrationProfile,
    User,
    Voivodeship,
)
from apps.accounts.services import (
    MAX_INVITATION_EMAILS,
    MAX_INVITATION_NOTE_LENGTH,
    custom_directory_enabled,
    parse_email_list,
    registration_profile,
)
from apps.appeals.models import MAX_TEXT_LENGTH, MIN_ARGUMENT_LENGTH, AppealStatus
from apps.cms.models import Announcement
from apps.competitions.events import EVENT_EDITABLE_FIELDS
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
    EditionEvent,
    Problem,
    Stage,
)
from apps.competitions.services import EDITION_EDITABLE_FIELDS, STAGE_EDITABLE_FIELDS
from apps.competitions.video import DEFAULT_VIDEO_BASE_URL, VideoProvider
from apps.core.api import DomainError
from apps.grading.rubric import criteria_for, format_criteria_lines, parse_criteria_lines
from apps.grading.snippets import format_snippet_lines, parse_snippet_lines, problem_snippets
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

    Atrybutu ``size`` tu **nie ma i być nie może**: szerokość wszystkich pól tekstowych ustala
    jedna reguła arkusza (``width: 100%``). Organizator zgłosił, że „okno do wprowadzenia numeru
    telefonu ma wyraźnie mniejszy rozmiar” – przyczyną był brak selektora ``input[type="tel"]``
    w tamtej regule, więc kontrolka zostawała przy domyślnych dwudziestu znakach przeglądarki.
    """
    return forms.CharField(
        label="Telefon",
        max_length=32,
        required=required,
        help_text="Do kontaktu w sprawach organizacyjnych, np. +48 600 000 000.",
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
    )


#: Podpowiedź pod rocznikiem w obu formularzach rejestracji uczestnika. Zgłoszenie organizatora:
#: „rok urodzenia nie jest powiązany z obowiązkowością oświadczenia o niepełnoletniości”.
#: Powiązanie istniało od początku po stronie serwera (``ConsentFieldsMixin.clean``), ale nie było
#: **widoczne**: uczestnik wpisywał rocznik i nic się nie działo, a odmowę poznawał dopiero po
#: wysłaniu formularza. Zdanie mówi wprost, czego się spodziewać; sam blok zgody odsłania
#: ``static/js/register-age.js``.
BIRTH_YEAR_MINOR_HINT = (
    "Osoby niepełnoletnie potrzebują zgody opiekuna – pole poniżej pojawi się automatycznie."
)


def birth_year_field(*, help_text: str = "") -> forms.IntegerField:
    """Rocznik uczestnika. Daty dziennej nie zbieramy – zasada minimalizacji.

    ``data-age="birth-year"`` jest punktem zaczepienia dla ``static/js/register-age.js``: skrypt
    szuka pola po atrybucie, a nie po ``id_birth_year``, bo ten sam blok renderuje się w dwóch
    szablonach i przy dołożeniu prefiksu formularza identyfikator by się zmienił. Atrybut stoi tu,
    a nie w szablonie, z tego samego powodu, co ``data-picker`` przy bloku „szkoła”.

    ``help_text`` jest parametrem, bo podpowiedź o zgodzie opiekuna ma sens wyłącznie tam, gdzie
    obok stoi blok zgód (rejestracja). W edycji profilu zgód nie ma – obiecywałaby pole, które się
    nie pojawi.
    """
    return forms.IntegerField(
        label="Rok urodzenia",
        min_value=1900,
        max_value=2100,
        help_text=help_text,
        widget=forms.NumberInput(attrs={"data-age": "birth-year"}),
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
SCHOOL_FIELD_NAMES = ("school_id", "school_city", "school_query", "school_custom", "school")

#: Pola dokładane przez ``RegistrationProfile`` (§ 1.3.4). Nie ma ich w ``SCHOOL_FIELD_NAMES``
#: i mieć nie może: tamta krotka opisuje blok renderowany **ręcznie** przez
#: ``web/_school_picker.html``, a te trzy idą zwykłą pętlą po polach – czyli tak samo jak
#: województwo i klasa. Nazwy są nazwami argumentów serwisu rejestracji, więc
#: ``**form.cleaned_data`` trafia tam, gdzie ma trafić, bez ani jednego mapowania po drodze.
INSTITUTION_TYPE_FIELD = "institution_type"
COUNTRY_FIELD = "country"
INSTITUTION_NAME_FIELD = "institution_name"

#: Druga kolumna dowiązania placówki (``Participant.custom_institution_ref``, § 1.3.3). Pole jest
#: **ukryte** i renderuje je ręcznie ``web/_school_picker.html``, tak samo jak ``school_id`` – więc
#: wchodzi do bloku „szkoła” (``SchoolChoiceMixin.school_field_names``), a nie do zwykłej pętli po
#: polach. W ``SCHOOL_FIELD_NAMES`` go nie ma i być nie może: tamta krotka opisuje blok
#: **dzisiejszy** i wyznacza miejsca wstawiania pól profilu (``extended_participant_field_order``),
#: a to pole powstaje warunkowo i dla Konkursu #1 nie powstaje nigdy.
CUSTOM_INSTITUTION_FIELD = "custom_institution_id"


def extended_participant_field_order(order, added) -> list[str]:
    """``PARTICIPANT_FIELD_ORDER`` rozszerzone o pola profilu – **tylko** o te, które powstały.

    Miejsca są dwa i oba wynikają z kolejności pytań, a nie z wygody:

    - **rodzaj placówki stoi przed blokiem szkoły**, bo to on rozstrzyga, czego w tym bloku
      szukać – tak samo jak województwo stoi przed nim od zawsze (patrz komentarz przy
      ``PARTICIPANT_FIELD_ORDER``),
    - **kraj i nazwa placówki stoją za blokiem**, bo są odpowiedzią na wypadek, w którym w wykazie
      nic nie ma.

    Pusta lista ``added`` znaczy, że nie ma czego wstawiać – i wtedy ta funkcja nie jest w ogóle
    wołana. Kolejność bez profilu zostaje więc **tą samą krotką**, którą deklaruje moduł.
    """
    result: list[str] = []
    for name in order:
        if name == SCHOOL_FIELD_NAMES[0] and INSTITUTION_TYPE_FIELD in added:
            result.append(INSTITUTION_TYPE_FIELD)
        result.append(name)
        if name == SCHOOL_FIELD_NAMES[-1]:
            result.extend(field for field in (COUNTRY_FIELD, INSTITUTION_NAME_FIELD) if field in added)
    # Pola, dla których w kolejności nie znalazło się miejsce (formularz bez bloku szkoły w
    # ``field_order``), lądują na końcu – widoczne, a nie zgubione.
    result.extend(field for field in added if field not in result)
    return result


#: Klasa doklejana przez Django do etykiety (i do akapitu ``as_p``) każdego pola wymaganego.
#: Gwiazdkę dorysowuje arkusz (``label.required::after`` w static/css/app.css) – w HTML-u nie ma
#: jej ani razu, bo czytnik ekranu i tak dostaje wymagalność z atrybutu ``required`` na kontrolce,
#: a przeczytana na głos „gwiazdka” po każdej etykiecie byłaby szumem. Stała, a nie napis wpisany
#: w czterech klasach: formularze rejestracji mają wyglądać tak samo, a nie prawie tak samo.
REQUIRED_CSS_CLASS = "required"


class SchoolChoiceMixin(forms.Form):
    """Wybór szkoły ze słownika SIO albo – świadomą decyzją – wpisanie jej ręcznie.

    Pięć pól zamiast jednego, bo jedno pole tekstowe nie potrafi odróżnić „nie znalazłem swojej
    szkoły” od „nie chciało mi się szukać”:

    - ``school_city`` – **krok pierwszy**: miejscowość. Do serwisu **nie trafia**; jest wyłącznie
      zakresem wyszukiwania. Doszedł po uwadze organizatora („warto dodać pole miasta i wówczas
      dać pełną listę szkół”): dwadzieścia podpowiedzi na całą Polskę nie jest listą, z której da
      się wybrać swoją szkołę, a po zawężeniu do miasta lista bywa kompletna i można ją przewinąć,
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

    **Profil rejestracji** (``RegistrationProfile``, § 1.3.4) rozstrzyga, o co ten konkurs pyta:
    czy blok szkoły w ogóle się pokazuje, jakie rodzaje placówek są do wyboru, czy wolno wpisać
    nazwę ręcznie i w jakim przedziale jest klasa. Odczyt jest **jeden na formularz** i jest
    w ``__init__`` – tam, gdzie powstają pola. Profil domyślny (czyli Konkurs #1 i każdy konkurs
    bez flagi ``institution_types``) wychodzi z ``__init__`` **przed** dołożeniem czegokolwiek:
    ani jedno pole, ani jedna etykieta i ani jeden komunikat nie zmieniają się wtedy o znak
    (§ 5.3, ``test_registration_form_html_unchanged``).

    **Słownik własny organizatora** (§ 1.3.3) dokłada szóste pole, ukryte:
    ``custom_institution_id`` – wynik wyboru wiersza z drugiego wykazu, czyli druga kolumna
    dowiązania (``Participant.custom_institution_ref``). Warunek jest osobny od profilu
    (:meth:`_apply_custom_directory`), bo słownik ma własną flagę i własne pole konfiguracji;
    ``clean()`` sprowadza wtedy blok do **trzech** kluczy serwisu: ``school``, ``school_id``
    i ``custom_institution_id``.
    """

    #: Czy ten formularz słucha profilu rejestracji. ``False`` znaczy „dzisiejsze reguły zawsze”
    #: i ma dokładnie jednego adresata – edycję własnego profilu uczestnika, której zapis
    #: (``apps.accounts.profile``) sprawdza dziś wyłącznie dzisiejsze reguły. Pole widoczne na
    #: ekranie, którego zapis by je pominął, byłoby polem udającym, że coś robi.
    profile_driven = True

    school_id = forms.IntegerField(
        required=False, min_value=1, widget=forms.HiddenInput(attrs={"data-picker": "school-id"})
    )
    school_city = forms.CharField(
        label="Miejscowość",
        required=False,
        max_length=120,
        help_text=(
            "Wpisz pierwsze litery i wybierz miejscowość z podpowiedzi. "
            "Pole „Szkoła” pokaże wtedy pełną listę szkół z tej miejscowości."
        ),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "role": "combobox",
                "aria-expanded": "false",
                "aria-autocomplete": "list",
                "aria-controls": "city-suggestions",
                "data-picker": "city",
            }
        ),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registration_profile = registration_profile() if self.profile_driven else RegistrationProfile()
        self._apply_custom_directory()
        if self.registration_profile.is_default():
            # Dzisiejszy formularz. Wyjście **przed** czymkolwiek, a nie „pętla, która nic nie
            # zmienia”: to jest miejsce, w którym widać, że Konkurs #1 nie płaci za etap 2 ani
            # jednym polem, ani jednym zapytaniem.
            return
        self._apply_registration_profile(self.registration_profile)

    def _apply_custom_directory(self) -> None:
        """Dokłada ukryte pole drugiego wykazu – **tylko** gdy konkurs ze słownika własnego korzysta.

        Warunek stoi **przed** wyjściem dla profilu domyślnego i to nie jest niedopatrzenie:
        słownik własny ma własną flagę (``custom_school_directory``) i własne pole profilu, więc
        konkurs może z niego korzystać, nie zmieniając ani jednego pytania formularza – a wtedy
        ``is_default()`` jest prawdą, choć pole jest potrzebne. Odwrotnie być nie może:
        ``custom_directory_enabled`` przy wyłączonej fladze odpowiada **bez zapytania do bazy**,
        więc ``/register/`` Konkursu #1 nie płaci za ten warunek ani jednym zapytaniem, a pole
        w jego formularzu nie powstaje nigdy (§ 5.6, ``test_registration_form_html_unchanged``).

        ``profile_driven = False`` (edycja własnego profilu uczestnika) pola nie dostaje: zapis
        tamtego ekranu (``apps.accounts.profile``) drugiego wykazu nie obsługuje, a kontrolka,
        której nikt nie odbiera, obiecywałaby wybór bez skutku.
        """
        if not self.profile_driven or not custom_directory_enabled():
            return
        self.fields[CUSTOM_INSTITUTION_FIELD] = forms.IntegerField(
            required=False,
            # Punkt zaczepienia dla ``static/js/school-picker.js`` (T25): skrypt wpisuje tu
            # identyfikator wiersza wybranego ze słownika organizatora. Bez skryptu pole zostaje
            # puste i formularz działa jak dziś – wyborem jest wtedy wykaz publiczny albo wolny
            # tekst, czyli warunek „strona bez JavaScriptu działa” (§ 0.4).
            widget=forms.HiddenInput(attrs={"data-picker": "custom-institution-id"}),
        )

    def _apply_registration_profile(self, profile) -> None:
        """Dokłada i zdejmuje pola według profilu. Woła się **raz**, po zbudowaniu ``self.fields``.

        Trzy zmiany kształtu, każda warunkowa:

        - ``institution_type`` – tylko wtedy, gdy dopuszczonych rodzajów placówek jest **więcej
          niż jeden**. Lista wyboru z jedną pozycją nie jest pytaniem, tylko ozdobą,
        - blok szkoły znika w całości, gdy żaden dopuszczony rodzaj nie ma wierszy w wykazie
          (sam ``NONE``, ``FOREIGN``, ``OTHER``). Szablon renderuje ten blok w miejscu pola
          ``school_id``, więc brak pola znaczy brak bloku – bez ani jednej zmiany w szablonie,
          który należy do T25,
        - ``school_custom`` i ``school`` znikają, gdy konkurs nie dopuszcza szkoły spoza wykazu:
          kratka odsłaniająca pole, którego wpisanie i tak kończy się odmową, byłaby zaproszeniem
          do błędu.

        Do tego trzy zmiany wymagalności i zakresu (klasa, telefon, województwo) – bez dokładania
        pól, bo te pytania stoją w formularzu od zawsze.
        """
        from apps.schools.models import InstitutionType

        allowed = profile.institution_types()
        added: list[str] = []
        if len(allowed) > 1:
            self.fields[INSTITUTION_TYPE_FIELD] = forms.ChoiceField(
                label="Rodzaj placówki",
                choices=[(value, label) for value, label in InstitutionType.choices if value in allowed],
                # Atrybut ``data-picker`` jest umową z ``static/js/school-picker.js`` (T25):
                # skrypt czyta z niego, jakiego rodzaju placówek szukać w podpowiedziach. Bez
                # skryptu pole jest zwykłą listą wyboru i formularz działa tak samo, tylko
                # wolniej – to jest warunek z § 0.4.
                widget=forms.Select(attrs={"data-picker": "institution-type"}),
            )
            added.append(INSTITUTION_TYPE_FIELD)
        if not profile.directory_types():
            # ``self.school_field_names``, a nie sama stała: blok znika **w całości**, razem
            # z ukrytym polem drugiego wykazu. Kontrolka bez bloku, który ją renderuje, byłaby
            # polem niewidocznym na żadnym ekranie i nie do wypełnienia przez nikogo.
            for name in self.school_field_names:
                self.fields.pop(name, None)
        elif not profile.allow_free_text_school:
            self.fields.pop("school_custom", None)
            self.fields.pop("school", None)
        if profile.allow_foreign:
            self.fields[COUNTRY_FIELD] = forms.CharField(
                label="Kraj",
                required=False,
                max_length=2,
                help_text="Dwuliterowy kod kraju (ISO 3166-1), na przykład „DE”. Puste znaczy Polska.",
            )
            added.append(COUNTRY_FIELD)
        if set(allowed) - set(DIRECTORY_INSTITUTION_TYPES) - {InstitutionType.NONE}:
            self.fields[INSTITUTION_NAME_FIELD] = forms.CharField(
                label="Nazwa placówki",
                required=False,
                max_length=255,
                help_text="Wypełnij, jeżeli Twojej placówki nie ma w wykazie.",
            )
            added.append(INSTITUTION_NAME_FIELD)
        self._apply_profile_requirements(profile)
        if added:
            # ``field_order`` na **instancji**, a nie na klasie: porządek zależy od profilu
            # konkursu, a klasa jest jedna dla całej instalacji. ``ConsentFieldsMixin`` woła
            # ``order_fields(self.field_order)`` po nas i dzięki temu widzi już rozszerzoną
            # kolejność – zgody zostają na końcu, tam gdzie były.
            self.field_order = extended_participant_field_order(self.field_order, added)
            self.order_fields(self.field_order)

    def _apply_profile_requirements(self, profile) -> None:
        """Wymagalność i zakres pól, które w formularzu stoją od zawsze."""
        grade = self.fields.get("grade")
        if grade is not None:
            low, high = profile.grade_range()
            grade.choices = [
                ("", "— wybierz klasę —"),
                *((str(value), str(value)) for value in range(low, high + 1)),
            ]
            grade.required = profile.require_grade
        phone = self.fields.get("phone")
        if phone is not None:
            phone.required = profile.require_phone
        district = self.fields.get("district")
        if district is not None:
            district.required = profile.require_region

    @property
    def school_field_names(self) -> tuple[str, ...]:
        """Nazwy pól bloku „szkoła” – szablon pomija je w zwykłej pętli po polach formularza.

        Ukryte pole drugiego wykazu dochodzi **tylko wtedy, gdy formularz je ma**: renderuje je
        ``web/_school_picker.html`` razem z ``school_id``, więc bez tego dopisku ta sama kontrolka
        wyszłaby w HTML-u dwa razy – raz w bloku, raz w zwykłej pętli po polach. Formularz bez
        słownika własnego (czyli Konkurs #1) dostaje stąd **tę samą krotkę**, co przed etapem 2.
        """
        if CUSTOM_INSTITUTION_FIELD in self.fields:
            return (*SCHOOL_FIELD_NAMES, CUSTOM_INSTITUTION_FIELD)
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

        Profil rejestracji dokłada **rozgałęzienie na samej górze**, a nie warunki wewnątrz reguł:
        placówka spoza wykazu (``FOREIGN``, ``NONE``, ``OTHER``) nie przechodzi przez ani jedną
        linijkę powyższego akapitu, bo nie ma tam czego wybierać. Dzisiejsza gałąź zostaje
        nietknięta co do komunikatu i co do kolejności warunków.
        """
        cleaned = super().clean()
        custom = cleaned.get("school_custom")
        query = (cleaned.get("school_query") or "").strip()
        # Pola pomocnicze nie mają prawa dojechać do serwisu – widoki wołają go
        # ``**form.cleaned_data``, więc każdy nadmiarowy klucz byłby TypeError. Miejscowość jest
        # wśród nich: opisuje **sposób szukania**, a nie szkołę. Miasto szkoły wybranej ze
        # słownika przepisuje ``_resolve_school`` z rejestru, a przy szkole spoza wykazu wolny
        # tekst jest jedyną prawdą, jaką mamy – wpisana obok miejscowość nie ma jak jej uzupełnić,
        # bo nikt nie zapewni, że dotyczy tej samej placówki.
        cleaned.pop("school_query", None)
        cleaned.pop("school_custom", None)
        cleaned.pop("school_city", None)
        free_text = (cleaned.get("school") or "").strip()
        cleaned["school"] = free_text
        chosen = self._chosen_institution_type(cleaned)
        if cleaned.get(CUSTOM_INSTITUTION_FIELD):
            return self._clean_custom_institution(cleaned, chosen)
        if chosen is not None and chosen not in DIRECTORY_INSTITUTION_TYPES:
            return self._clean_institution_outside_the_directory(cleaned, chosen)
        if not self.registration_profile.allow_free_text_school and not cleaned.get("school_id"):
            # Konkurs bez furtki na wolny tekst mówi to jednym zdaniem, pod polem wyszukiwarki –
            # dzisiejsze komunikaty odsyłałyby do kratki, której w tym formularzu nie ma.
            self.add_error("school_query", "Wybierz placówkę z listy.")
            return cleaned
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

    def _chosen_institution_type(self, cleaned) -> str | None:
        """Rodzaj placówki wybrany w tym formularzu albo ``None``, gdy nie ma czego wybierać.

        ``None`` – a nie ``"SECONDARY"`` – bo to jest odpowiedź „konkurs o rodzaj nie pyta”,
        i dopiero ona pozwala zostawić dzisiejszą gałąź ``clean()`` bez ani jednego warunku
        o profilu. Przy jednym dopuszczonym rodzaju pola w formularzu nie ma, więc wybór jest
        znany bez pytania.
        """
        allowed = self.registration_profile.institution_types()
        chosen = cleaned.get(INSTITUTION_TYPE_FIELD) or (allowed[0] if len(allowed) == 1 else None)
        return chosen if chosen in allowed else None

    def _clean_custom_institution(self, cleaned, chosen: str | None):
        """Placówka wybrana ze **słownika organizatora** (§ 1.3.3) – gałąź pierwsza, jak w serwisie.

        Kolejność warunków jest tu przepisana z ``accounts.services._resolve_institution`` i to
        nie jest podobieństwo, tylko wymaganie: wybór z wykazu jest wyborem z wykazu **niezależnie
        od rodzaju placówki**, bo uczelnia partnerska organizatora i jego ośrodek zagraniczny stoją
        w tej samej tabeli. Gdyby formularz rozstrzygał inaczej niż serwis, uczestnik dostawałby
        odmowę za wybór, który serwis by przyjął – albo odwrotnie.

        Zerujemy obie drogi wykazu publicznego: nazwę przepisze serwis z wybranego wiersza, a wybór
        kliknięty wcześniej w drugiej liście nie ma prawa przeżyć zmiany decyzji. Kraj przy
        placówce „poza Polską” zostaje wymagany – tak samo jak w gałęzi wolnego tekstu i tym samym
        zdaniem, bo odmowa serwisu (``COUNTRY_REQUIRED``) jest ta sama.

        Identyfikatora **nie sprawdzamy w bazie**: czy wiersz istnieje, jest aktywny i ma
        dopuszczony rodzaj, rozstrzyga ``_resolve_custom_institution`` – jednym zapytaniem
        i w jednym miejscu dla formularza, API i importu grupowego.
        """
        from apps.schools.models import InstitutionType

        cleaned["school"] = ""
        cleaned["school_id"] = None
        if INSTITUTION_NAME_FIELD in cleaned:
            cleaned[INSTITUTION_NAME_FIELD] = ""
        if (
            chosen == InstitutionType.FOREIGN
            and COUNTRY_FIELD in self.fields
            and not (cleaned.get(COUNTRY_FIELD) or "").strip()
        ):
            self.add_error(COUNTRY_FIELD, "Podaj kraj.")
        return cleaned

    def _clean_institution_outside_the_directory(self, cleaned, chosen: str):
        """Placówka spoza wykazu: nazwa wolnym tekstem, kraj przy placówce poza Polską.

        Do serwisu idzie ``institution_name``, a nie ``school``: kopiowanie jednego w drugie jest
        **regułą domenową** i ma jedno miejsce (``accounts.services._resolve_institution``), tak
        samo jak dziś przepisanie nazwy z rejestru. Formularz zeruje ``school`` i ``school_id``,
        żeby wybór klikniętym wcześniej wierszem wykazu nie przeżył zmiany rodzaju placówki.
        """
        from apps.schools.models import InstitutionType

        cleaned["school"] = ""
        cleaned["school_id"] = None
        name = (cleaned.get(INSTITUTION_NAME_FIELD) or "").strip()
        cleaned[INSTITUTION_NAME_FIELD] = name
        if chosen == InstitutionType.NONE:
            cleaned[INSTITUTION_NAME_FIELD] = ""
            return cleaned
        if not name and INSTITUTION_NAME_FIELD in self.fields:
            self.add_error(INSTITUTION_NAME_FIELD, "Podaj nazwę placówki.")
        if chosen == InstitutionType.FOREIGN and COUNTRY_FIELD in self.fields:
            if not (cleaned.get(COUNTRY_FIELD) or "").strip():
                self.add_error(COUNTRY_FIELD, "Podaj kraj.")
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

    **Skąd bierze się zestaw zgód.** Z :func:`apps.accounts.consents.consent_set`, czyli z tego
    samego wejścia, co API i panel uczestnika – nie ze stałej. Przy wyłączonej fladze
    ``per_competition_consents`` zestaw jest dokładnie tą stałą i nie kosztuje ani jednego
    zapytania; przy włączonej pochodzi z ``ConsentDefinition`` **tego** konkursu. Odczyt jest
    jeden na formularz i jest tutaj, bo to tutaj powstają pola: gdyby zestaw czytało osobno
    ``__init__``, osobno ``consent_field_names`` i osobno ``clean``, trzy części jednego
    formularza mogłyby zobaczyć trzy różne zestawy.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Nazwę organizatora czytamy **raz na formularz** i podajemy ją każdej etykiecie: inaczej
        # ten sam napis szedłby z bazy tyle razy, ile jest zgód.
        organizer = organizer_name()
        self._consents = consent_set()
        for consent in self._consents:
            self.fields[consent.field_name] = forms.BooleanField(
                label=consent_label(consent, organizer=organizer),
                required=False,
                help_text=consent.help_text,
                # Bez dwukropka: etykietą jest całe zdanie oświadczenia zakończone kropką,
                # a domyślny ``label_suffix`` dokleiłby do niej „:”.
                label_suffix="",
            )
        # Pola dołożone po ``super().__init__`` stają na końcu ``self.fields`` niezależnie od
        # ``field_order`` – tam je zresztą chcemy mieć, ale kolejność ma wynikać z deklaracji,
        # a nie z tego, w którym momencie powstało pole. Zgoda o nazwie spoza ``FIELD_ORDER``
        # (konkurs z własnym zestawem) ląduje na końcu, czyli tam, gdzie stoi cały blok zgód.
        self.order_fields(self.field_order)

    @property
    def consent_field_names(self) -> tuple[str, ...]:
        """Nazwy pól zgód – szablon renderuje je w osobnym ``<fieldset>``, poza zwykłą pętlą.

        Liczone z zestawu, z którego powstały pola, a nie ze stałej ``CONSENT_FIELD_NAMES``:
        szablon ma wypisać dokładnie te pola, które w tym formularzu są.
        """
        return tuple(consent.field_name for consent in self._consents)

    def clean(self):
        """Zgoda opiekuna wymagana dla niepełnoletniego – błąd pod polem, nie nad formularzem.

        Rocznik bywa niepoprawny (pole nie przeszło walidacji) – wtedy milczymy i zostawiamy
        rozstrzygnięcie serwisowi: dopisywanie drugiego błędu do formularza, w którym pierwszy
        jest oczywisty, tylko zaciemnia, co poprawić.

        Zgody opiekuna szukamy w zestawie **tego** formularza, a nie w indeksie ``BY_KIND``:
        konkurs, który tej zgody nie zbiera, nie ma pod czym postawić błędu i milczy. Regułę
        „kto jest niepełnoletni” trzyma nadal ``consents.is_minor`` – to jest kod, nie dane.
        """
        cleaned = super().clean()
        birth_year = cleaned.get("birth_year")
        guardian = next((item for item in self._consents if item.kind == ConsentKind.GUARDIAN), None)
        if (
            guardian is not None
            and birth_year
            and is_minor(birth_year)
            and not cleaned.get(guardian.field_name)
        ):
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
    birth_year = birth_year_field(help_text=BIRTH_YEAR_MINOR_HINT)

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
    birth_year = birth_year_field(help_text=BIRTH_YEAR_MINOR_HINT)


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
    # Adres opiekuna szkolnego stoi na końcu, bo jest jedynym polem tego formularza, które nie
    # opisuje uczestnika, tylko **nadaje komuś wgląd** w jego przebieg w zawodach.
    "supervisor_email",
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
        # Miejscowość odtwarzamy wyłącznie ze słownika: przy szkole wpisanej ręcznie nie wiemy,
        # w jakim mieście ona jest (pytamy o nazwę, nie o adres), a podstawienie czegokolwiek
        # zawęziłoby wyszukiwarkę do miasta, którego uczestnik nigdy nie wskazał. Wstawiamy
        # **gminę**, a nie miejscowość z wykazu: to ona jest tym, czego oczekuje parametr ``city``
        # wyszukiwarki, a „Wrocław-Krzyki” w tym polu otworzyłoby edycję profilu z pustą listą.
        "school_city": (participant.school_ref.city_parent or participant.school_ref.city)
        if from_registry
        else "",
        "school_query": participant.school,
        "school_custom": not from_registry,
        "school": "" if from_registry else participant.school,
        "supervisor_email": participant.supervisor_email,
    }


class ParticipantProfileForm(SchoolChoiceMixin):
    """Edycja własnych danych uczestnika (``/me/profile/``).

    Zakres jest dokładnie taki, jak w rejestracji **minus** poświadczenia i minus zgody: zgody są
    oświadczeniami z własną historią (``ConsentRecord``) i nie zmienia się ich zapisem formularza
    danych. ``public_code`` nie jest edytowalny nigdzie – to identyfikator w ogłoszonych tabelach.

    Profilu rejestracji ten ekran **nie słucha** (``profile_driven = False``) i jest to decyzja
    na jedno wydanie: zapis idzie przez ``apps.accounts.profile._participant_values``, który
    sprawdza dzisiejsze reguły (klasa 1–5, szkoła z wykazu albo wolny tekst) i o rodzaju placówki
    nie wie. Pole „Rodzaj placówki” na ekranie, którego zapis by je pominął, byłoby polem
    udającym, że coś robi – a to jest gorsze od jego braku. Zmiana placówki przez uczestnika
    konkursu z własnym profilem rejestracji wchodzi razem z tamtym serwisem.
    """

    required_css_class = REQUIRED_CSS_CLASS
    profile_driven = False
    field_order = [name for name in PARTICIPANT_PROFILE_FIELD_ORDER]

    first_name = forms.CharField(label="Imię", max_length=150)
    last_name = forms.CharField(label="Nazwisko", max_length=150)
    phone = phone_field()
    district = voivodeship_field("Województwo")
    grade = grade_field()
    birth_year = birth_year_field()
    # Opcjonalne i odwracalne jednym wyczyszczeniem pola: to uczestnik decyduje, czy nauczyciel
    # ma widzieć jego postęp, i tylko on może tę decyzję cofnąć (patrz ``apps.accounts.supervisors``).
    supervisor_email = forms.EmailField(
        label="Adres e-mail opiekuna szkolnego",
        max_length=254,
        required=False,
        help_text=(
            "Opcjonalnie. Opiekun z kontem w serwisie zobaczy Twój kod, imię, nazwisko i to, "
            "na jakim etapie procedury są Twoje prace – nigdy punktów przed ogłoszeniem wyników "
            "ani samych prac. Puste pole znaczy „nie mam opiekuna”."
        ),
    )

    def __init__(self, *args, **kwargs):
        """Zdejmuje pole opiekuna, gdy organizator w ogóle nie oferuje tej roli.

        Przełącznik ``cms.SiteSettings.supervisor_registration_enabled`` jest domyślnie wyłączony
        i wtedy nikt nie ma jak założyć konta opiekuna – pole byłoby pytaniem o adres, którego
        nikt nie użyje, a dodatkowo obietnicą funkcji, której w serwisie nie ma.

        Usuwamy je **razem z wartością**, a nie tylko ukrywamy w szablonie: pole schowane
        atrybutem dalej przyjmuje POST, więc ukrycie byłoby pozorne. Adres już zapisany w profilu
        zostaje w bazie nietknięty – decyzja ucznia sprzed wyłączenia przełącznika nie jest
        czymś, co formularz danych ma prawo cofnąć bez jego wiedzy.
        """
        super().__init__(*args, **kwargs)
        from apps.accounts.supervisors import registration_enabled

        if not registration_enabled():
            self.fields.pop("supervisor_email", None)

    def clean(self):
        """Bez pola nie ma klucza – a serwis profilu zmienia wyłącznie to, co dostał.

        ``update_participant`` czyta ``fields`` po nazwach (``apps.accounts.profile``), więc brak
        klucza znaczy „nie ruszaj tej wartości”. Nie podstawiamy tu pustego napisu: skasowałby
        adres, który uczeń kiedyś świadomie wpisał.
        """
        cleaned = super().clean()
        if "supervisor_email" not in self.fields:
            cleaned.pop("supervisor_email", None)
        return cleaned


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
    """Upload rozwiązania. Formaty, rozmiar i magic bytes sprawdza walidator z ``apps.submissions``.

    ``confirmed`` jest listą kontrolną sprowadzoną do jednego zdania i jest **wymagane po stronie
    serwera**, a nie tylko atrybutem ``required`` w HTML. Powód jest prozaiczny: najczęstsza
    reklamacja po etapie brzmi „wysłałem nie ten plik” albo „skan wyszedł nieczytelny”, a jedyną
    chwilą, w której da się to tanio zatrzymać, jest kliknięcie „Wyślij”. Numer zadania wstawia
    szablon (etykieta jest per karta), więc formularz trzyma samo pole i komunikat odmowy.
    """

    file = forms.FileField(label="Plik rozwiązania")
    confirmed = forms.BooleanField(
        label="Potwierdzam, że to rozwiązanie właściwego zadania i plik jest czytelny",
        error_messages={
            "required": gettext_lazy(
                "Zaznacz potwierdzenie, że wysyłasz rozwiązanie właściwego zadania i że plik jest czytelny."
            )
        },
    )


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


# --- konta w panelu koordynatora ---------------------------------------------------------------
#
# Trzy formularze, a nie jeden, bo opisują trzy różne obiekty: konto, profil uczestnika i profil
# członka komitetu. Konto bez profilu roli nie ma dwóch ostatnich wcale, a pole ``district`` znaczy
# w każdym z profili co innego (województwo uczestnika decyduje o etapie wojewódzkim, województwo
# recenzenta – o konflikcie interesów). Na jednej stronie stoją z prefiksami, więc nazwy pól się
# nie zderzają, a widok przekazuje do serwisu trzy osobne słowniki.


class CoordinatorAccountForm(forms.Form):
    """Dane samego konta zmieniane przez koordynatora: nazwisko, adres e-mail, blokada logowania.

    Imię i nazwisko są **nieobowiązkowe**, choć rejestracja ich wymaga: konta zakładane komendą
    CLI i część kont z dostawcy zewnętrznego mają te pola puste, a ekran naprawczy nie może żądać
    uzupełnienia danych, po które organizator akurat nie dzwoni.

    ``is_active`` jest tu wyłącznikiem logowania, a nie znacznikiem aktywacji adresu: konto
    zablokowane zostaje w bazie razem ze swoją dokumentacją, więc zablokowanie jest odwracalne –
    inaczej niż usunięcie. Potwierdzenie adresu e-mail (``email_verified_at``) to osobna sprawa
    i ten formularz go nie rusza.
    """

    required_css_class = REQUIRED_CSS_CLASS

    first_name = forms.CharField(label="Imię", max_length=150, required=False)
    last_name = forms.CharField(label="Nazwisko", max_length=150, required=False)
    email = forms.EmailField(
        label="Adres e-mail",
        max_length=254,
        help_text=(
            "Zmiana wchodzi od razu, bez listu potwierdzającego – od tej chwili to jest login tego konta."
        ),
    )
    is_active = forms.BooleanField(
        label="Konto aktywne",
        required=False,
        help_text="Odznaczenie blokuje logowanie. Dane i prace zostają – to nie jest usunięcie konta.",
    )


class CoordinatorParticipantForm(SchoolChoiceMixin):
    """Dane profilu uczestnika w panelu koordynatora – ten sam zakres, co ``/me/profile/``.

    Imienia i nazwiska tu nie ma, bo są polami **konta**, a nie profilu (patrz formularz wyżej);
    ``public_code`` nie jest edytowalny nigdzie – to identyfikator w ogłoszonych tabelach wyników.
    Szkołę wybiera się tą samą wyszukiwarką SIO, co przy rejestracji, żeby uczniowie jednej szkoły
    mieli w bazie jeden napis (od tego zależy próg k-anonimowości przy publikacji).
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = ["phone", "district", *SCHOOL_FIELD_NAMES, "grade", "birth_year"]

    phone = phone_field()
    district = voivodeship_field("Województwo")
    grade = grade_field()
    birth_year = birth_year_field()


class CoordinatorCommitteeForm(forms.Form):
    """Profil członka komitetu w panelu koordynatora: status, komisja odwoławcza, województwo.

    Województwo jest nieobowiązkowe (pozycja „— wybierz województwo —” znaczy „brak”, czyli praca
    z całego kraju) – dokładnie jak w sekcji „Województwa członków komitetu” na pulpicie.
    Status ma zamkniętą listę z modelu: ustawienie ``ACTIVE`` przechodzi w serwisie tą samą drogą,
    co przycisk „Zatwierdź”, więc konto dostaje komplet grup, a nie sam napis w kolumnie.
    """

    required_css_class = REQUIRED_CSS_CLASS

    status = forms.ChoiceField(label="Status", choices=CommitteeStatus.choices)
    district = voivodeship_field("Województwo", required=False)
    is_appeals_committee = forms.BooleanField(label="Komisja odwoławcza", required=False)


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

#: Górna granica pojedynczej oceny w formularzach skali. Ta sama, co przy wpisywaniu punktów.
MAX_SCALE_VALUE = 1000

#: Podpisy formatów rozwiązań w formularzu zadania. Domyślnie wystarcza samo rozszerzenie, ale
#: „.jpg” nie mówi koordynatorowi, **po co** ten format istnieje – a jest nim zdjęcie kartki
#: zrobione telefonem przez uczestnika bez skanera.
FORMAT_LABELS = {"jpg": "JPEG (zdjęcie rozwiązania)"}

#: Podpowiedź pod polem skali. Jedna dla etapu i dla zadania – to ten sam zapis.
SCALE_HELP_TEXT = (
    "Po jednej pozycji w wierszu, w postaci „wartość;opis”, wartości rosnąco i koniecznie z zerem, "
    "np. „0;brak istotnego postępu”. Opis widzi recenzent przy wyborze oceny."
)


def parse_scale_lines(text: str) -> list[dict]:
    """Zamienia tekst „wartość;opis” (po jednej pozycji w wierszu) na listę pozycji skali.

    Textarea zamiast formsetu, bo skala ma kilka pozycji i wpisuje się ją raz na edycję – formset
    kosztowałby JavaScript (dodawanie i usuwanie wierszy), a strict CSP nie ma tu miejsca na wyspę
    skryptu dla czynności, którą da się zrobić jednym polem tekstowym.

    Numer wiersza wchodzi do komunikatu, bo przy ośmiu pozycjach „zła wartość” bez wskazania,
    której, jest zagadką. Sensu skali (zero, kolejność, unikalność, zgodność z maksimum) ta funkcja
    **nie** sprawdza – to reguła domenowa i stoi w ``competitions.models.validate_scoring_values``,
    wołanej przez ``full_clean()`` przy zapisie.
    """
    values: list[dict] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        head, separator, label = line.partition(";")
        if not separator:
            raise forms.ValidationError(
                f"Wiersz {number}: brakuje średnika. Zapis to „wartość;opis”, np. „2;istotny postęp”."
            )
        try:
            value = int(head.strip())
        except ValueError as exc:
            raise forms.ValidationError(
                f"Wiersz {number}: „{head.strip()}” nie jest liczbą całkowitą."
            ) from exc
        if not 0 <= value <= MAX_SCALE_VALUE:
            raise forms.ValidationError(f"Wiersz {number}: wartość musi mieścić się w 0–{MAX_SCALE_VALUE}.")
        if not label.strip():
            raise forms.ValidationError(f"Wiersz {number}: brakuje opisu po średniku.")
        values.append({"value": value, "label": label.strip()})
    if not values:
        raise forms.ValidationError("Skala musi mieć co najmniej jedną pozycję.")
    return values


def format_scale_lines(values) -> str:
    """Odwrotność ``parse_scale_lines`` – skala z bazy w postaci, w jakiej wraca do formularza."""
    return "\n".join(
        f"{item['value']};{item.get('label', '')}"
        for item in values or []
        if isinstance(item, dict) and "value" in item
    )


class ScoringScaleForm(forms.Form):
    """Skala punktacji etapu: wartości jako tekst i maksimum osobnym polem.

    Maksimum jest polem, a nie liczbą wyprowadzoną z ostatniego wiersza, bo to **osobna** deklaracja
    i jej niezgodność ze skalą jest błędem, o którym koordynator ma się dowiedzieć (``full_clean``
    modelu). Wyliczanie jej po cichu zamieniałoby literówkę w wartości na milczącą zmianę maksimum.
    """

    values = forms.CharField(
        label="Wartości skali",
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=SCALE_HELP_TEXT,
    )
    max_value = forms.IntegerField(
        label="Maksimum punktów",
        min_value=0,
        max_value=MAX_SCALE_VALUE,
        help_text="Musi być równe największej wartości skali.",
    )

    def clean_values(self):
        return parse_scale_lines(self.cleaned_data["values"])


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


def _relax_video_fields(form: forms.ModelForm) -> None:
    """Pola pokoju wideo są w formularzu etapu **opcjonalne** – i to jest decyzja, nie niedopatrzenie.

    Dotyczą wyłącznie etapu w formie rozmowy, a takich jest w edycji najwyżej jeden. Wymaganie ich
    od każdego etapu znaczyłoby, że koordynator przesuwający deadline eliminacji musi po drodze
    odpowiedzieć na pytanie o dostawcę wideo – i że każdy klient wysyłający ten formularz bez tych
    pól (skrypt, test, starsza zakładka) dostaje 400 zamiast zapisu.
    """
    for name in ("video_provider", "video_base_url"):
        if name in form.fields:
            form.fields[name].required = False


def _clean_video_provider(form: forms.ModelForm) -> str:
    """Puste pole dostawcy znaczy „bez wideo”, a nie pustą wartość w kolumnie z zamkniętą listą."""
    return form.cleaned_data.get("video_provider") or VideoProvider.NONE


def _clean_video_base_url(form: forms.ModelForm) -> str:
    """Puste pole adresu serwera znaczy „publiczna instancja Jitsi”, czyli wartość domyślną.

    Normalizacja jest tu potrzebna także po to, żeby formularz wysłany **bez** tego pola nie
    wyglądał jak zmiana: ``changed_values`` porównuje z wartością początkową, a pusty napis
    różniłby się od domyślnego adresu i lądował w audycie jako edycja, której nikt nie zrobił
    (a w etapie zamkniętym – jako odmowa zapisu).
    """
    return form.cleaned_data.get("video_base_url") or DEFAULT_VIDEO_BASE_URL


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
            "review_deadline_days": "Dni na jedną recenzję",
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
            "review_deadline_days": (
                "Ile dni ma recenzent od chwili przydziału pracy. Termin jest osobisty – prace "
                "przydzielane później dostają go liczonego od swojego dnia, nie od zamknięcia "
                "etapu. Nigdy nie wypada po terminie recenzji całego etapu (powyżej). Zmiana "
                "dotyczy przydziałów przyszłych; terminy już przyznane zostają bez zmian."
            ),
            "video_provider": (
                "Dotyczy wyłącznie etapu w formie rozmowy. Adres pokoju powstaje wtedy sam przy "
                "zapisie uczestnika; ręczny link wpisany przy terminie zawsze go przebija."
            ),
            "video_base_url": (
                "Korzeń adresu pokoju. Puste = publiczna instancja Jitsi (https://meet.jit.si/)."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _relax_video_fields(self)

    def clean_video_provider(self):
        return _clean_video_provider(self)

    def clean_video_base_url(self):
        return _clean_video_base_url(self)

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
    """Ramy czasowe edycji w panelu koordynatora: okno rejestracji i okres retencji danych.

    Oba terminy są opcjonalne i to jest sedno tego ekranu: puste otwarcie znaczy „od zaraz”, puste
    zamknięcie – „do odwołania”. Kolejność sprawdza ``Edition.full_clean()`` (ModelForm woła je
    w ``_post_clean``), więc komunikat staje pod polem „zamknięcie”, a nie w chmurce nad
    formularzem – i nie ma drugiej kopii tej reguły w warstwie WWW.

    Retencja stoi tu, a nie na osobnym ekranie, bo odpowiada na drugą połowę tego samego pytania:
    od kiedy wolno zbierać dane uczestników i do kiedy wolno je trzymać. Samo zapisanie liczby
    niczego nie kasuje – anonimizuje zadanie okresowe (``apps.accounts.retention``), a koordynator
    widzi jego plan na ``/coordinator/retention/``.
    """

    class Meta:
        model = Edition
        fields = EDITION_EDITABLE_FIELDS
        field_classes = {
            "registration_opens_at": LocalDateTimeField,
            "registration_closes_at": LocalDateTimeField,
        }
        labels = {
            "registration_enabled": "Rejestracja włączona",
            "registration_opens_at": "Otwarcie rejestracji",
            "registration_closes_at": "Zamknięcie rejestracji",
            "data_retention_months": "Retencja danych (miesiące)",
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
            "data_retention_months": (
                "Liczone od ostatniego deadline'u etapu tej edycji. Po tym czasie konta "
                "uczestników, którzy nie startują w późniejszej edycji, są anonimizowane. "
                "Zero wyłącza anonimizację – plan widać na stronie „Retencja danych”."
            ),
        }

    def changed_values(self) -> dict:
        """Pola faktycznie zmienione – w rozdzielczości formularza (minuty), jak w ``StageForm``.

        Bez tego samo otwarcie i zapisanie formularza przepisywałoby terminy wpisane spoza panelu
        (admin, seed) z pominięciem sekund, a w audycie stawałby wpis o zmianie, której nie było.
        """
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name in EDITION_EDITABLE_FIELDS and _to_minute(self.initial.get(name)) != _to_minute(value)
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


class EditionEventForm(forms.ModelForm):
    """Wydarzenie edycji na linii czasu w nagłówku – dodawane i zmieniane przez koordynatora.

    Formularz jest celowo ubogi: nazwa, dwie daty, dopisek, odnośnik i wyłącznik. Wszystko, co
    system **egzekwuje** (okno uploadu, terminy recenzji, okno reklamacji), zostaje w ``StageForm``
    – tutaj wpisuje się kalendarz, o którym serwis tylko informuje, i dlatego nie ma tu ani jednej
    godziny. Gala zaczyna się „12 czerwca”, a nie „12 czerwca o 17:00 czasu polskiego”; godzina
    jest treścią zaproszenia, a nie punktem na osi.

    Kolejność dat pilnuje ``EditionEvent.full_clean()`` (ModelForm woła je w ``_post_clean``), więc
    komunikat staje pod polem „koniec”, a nie w chmurce nad formularzem – i nie ma drugiej kopii
    tej reguły w warstwie WWW. Postać odnośnika sprawdza serwis (``apps.competitions.events``):
    to reguła bezpieczeństwa, a nie formatowania pola, więc musi obowiązywać każdego wywołującego.
    """

    class Meta:
        model = EditionEvent
        fields = EVENT_EDITABLE_FIELDS
        field_classes = {"starts_on": DayField, "ends_on": DayField}
        labels = {
            "title": "Nazwa wydarzenia",
            "starts_on": "Termin (od / do)",
            # Drugie pole tej samej rubryki nie powtarza podpisu – to jeden termin w dwóch polach,
            # tak samo jak „Termin wydarzenia” w formularzu etapu.
            "ends_on": "do",
            "note": "Dopisek",
            "url": "Odnośnik",
            "show_on_timeline": "Pokazuj na linii czasu",
        }
        help_texts = {
            "title": "Krótko – ta nazwa staje nad paskiem w nagłówku, np. „Gala finałowa”.",
            "starts_on": "Dzień, w którym wydarzenie się zaczyna.",
            "ends_on": "Puste = wydarzenie jednodniowe.",
            "note": "Miejsce albo tryb, np. „online”, „Kraków, ICE”. Widoczny w liście pod paskiem.",
            "url": (
                "Adres strony wydarzenia: ścieżka w tym serwisie („/warsztaty/”) albo pełny adres "
                "„https://…”. Puste = nazwa nie będzie odnośnikiem."
            ),
            "show_on_timeline": (
                "Odznacz, żeby przygotować termin, zanim go ogłosisz. Wydarzenie zostaje na tej "
                "liście, ale nie pokazuje się w nagłówku serwisu."
            ),
        }

    def changed_values(self) -> dict:
        """Pola, które koordynator faktycznie zmienił – żeby audyt nie notował pustych zapisów.

        Bez tego samo otwarcie formularza i kliknięcie „Zapisz” zostawiałoby w historii wpis
        o zmianie sześciu pól, z których żadne się nie zmieniło. Porównanie jest proste (daty
        z pola ``date`` nie mają rozdzielczości do uzgodnienia, inaczej niż terminy etapu).
        """
        return {
            name: value
            for name, value in self.cleaned_data.items()
            if name in EVENT_EDITABLE_FIELDS and self.initial.get(name) != value
        }


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
        choices=[(value, FORMAT_LABELS.get(value, f".{value}")) for value in SUPPORTED_FILE_FORMATS],
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
    # Nadpisanie skali etapu. Oba pola są opcjonalne i działają **razem**: puste znaczy „punktuj
    # tak, jak cały etap”. Pola są poza ``Meta.fields`` świadomie – w modelu skala jest listą JSON,
    # a tutaj tekstem, więc ``construct_instance`` nie ma czego przepisywać; wartość wchodzi do
    # zadania przez serwis ``update_problem``, tą samą drogą co reszta pól.
    scoring_values = forms.CharField(
        label="Skala punktacji tego zadania",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=f"Puste = zadanie punktuje skala etapu. {SCALE_HELP_TEXT}",
    )
    max_points = forms.IntegerField(
        label="Maksimum punktów tego zadania",
        required=False,
        min_value=0,
        max_value=MAX_SCALE_VALUE,
        help_text="Wypełnij razem ze skalą zadania; musi być równe jej największej wartości.",
    )
    # Materiały dla oceniających. Wzorcówka jest plikiem (jak treść zadania), rubryka – tekstem
    # (jak skala), bo kryteria poprawia się w trakcie oceniania i zmiana ma działać od razu.
    model_solution_pdf = forms.FileField(
        label="Rozwiązanie wzorcowe (PDF)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        help_text=(
            f"Widzą je wyłącznie recenzenci i koordynator – nigdy uczestnicy. "
            f"Plik PDF, maksymalnie {MAX_STATEMENT_MB} MB. Nowy plik zastępuje poprzedni."
        ),
    )
    rubric = forms.CharField(
        label="Rubryka oceniania",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=(
            "Puste = zadanie bez rubryki (recenzent wybiera ocenę wprost ze skali). Po jednym "
            "kryterium w wierszu, w postaci „punkty;tytuł;opis”, np. „2;Poprawność rachunków;"
            "liczy się wynik i jednostki”. Opis jest opcjonalny. Suma punktów z kryteriów musi "
            "dać wartość ze skali tego zadania – inaczej recenzent dostanie błąd przy wysyłce."
        ),
    )
    # Szablony komentarzy: ta sama konwencja zapisu, co rubryka (textarea, jedna pozycja w wierszu),
    # bo oba pola stoją na tym samym formularzu i koordynator nie ma powodu uczyć się dwóch składni.
    # Pole jest poza ``Meta.fields`` – w bazie to osobny model (``grading.CommentSnippet``).
    comment_snippets = forms.CharField(
        label="Szablony komentarzy dla recenzentów",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=(
            "Gotowe zdania, które recenzent wstawia jednym kliknięciem do komentarza dla "
            "uczestnika. Po jednym szablonie w wierszu, w postaci „tytuł;treść”, np. „Brak "
            "jednostek;Wynik jest poprawny, ale nie podałeś jednostek.”. Szablony są podpowiedzią, "
            "nie automatem – recenzent poprawia wstawiony tekst. Własnych szablonów recenzentów "
            "to pole nie rusza."
        ),
    )

    class Meta:
        model = Problem
        fields = (
            "number",
            "title",
            # Wersja angielska tytułu i treści stoi zaraz przy polskiej, a nie w osobnej sekcji:
            # to ten sam dokument w drugim języku i wypełnia się go w tej samej chwili. Oba pola
            # są opcjonalne – bez nich angielski interfejs wydaje wersję polską (``Problem``).
            "title_en",
            "statement_pdf",
            "statement_pdf_en",
            "allowed_formats",
            "max_file_mb",
            "reviewer_notes",
        )
        labels = {"reviewer_notes": "Uwagi dla recenzentów"}
        help_texts = {
            "reviewer_notes": (
                "Czego nie widać we wzorcówce, a rozstrzyga o punktach. Tekst widoczny wyłącznie "
                "w panelu recenzenta."
            ),
            "title_en": "Opcjonalnie. Bez tłumaczenia angielski interfejs pokazuje tytuł polski.",
            "statement_pdf_en": (
                "Opcjonalnie. Bez tłumaczenia angielski interfejs wydaje polski plik treści."
            ),
        }
        widgets = {"reviewer_notes": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, stage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage = stage if stage is not None else getattr(self.instance, "stage", None)
        # Skala zadania nie jest w ``Meta.fields``, więc ``ModelForm`` nie wypełni jej z instancji.
        if self.instance.pk and not self.is_bound:
            self.initial.setdefault("scoring_values", format_scale_lines(self.instance.scoring_values))
            self.initial.setdefault("max_points", self.instance.max_points)
            # Rubryka też stoi poza ``Meta.fields``: w bazie jest osobnym modelem
            # (``grading.RubricCriterion``), a tutaj jednym polem tekstowym.
            self.initial.setdefault("rubric", format_criteria_lines(criteria_for(self.instance)))
            self.initial.setdefault("comment_snippets", format_snippet_lines(problem_snippets(self.instance)))
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

    def clean_statement_pdf_en(self):
        """Angielska treść przechodzi dokładnie tę samą kontrolę, co polska – to ten sam dokument."""
        upload = self.cleaned_data.get("statement_pdf_en")
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

    def clean_model_solution_pdf(self):
        """Wzorcówka przechodzi dokładnie tę samą kontrolę, co treść zadania – to też PDF."""
        upload = self.cleaned_data.get("model_solution_pdf")
        if not isinstance(upload, UploadedFile):
            return upload
        if upload.size > MAX_STATEMENT_MB * MEGABYTE:
            raise forms.ValidationError(
                f"Plik ma {upload.size} B – limit rozwiązania wzorcowego to {MAX_STATEMENT_MB} MB."
            )
        if upload.size == 0:
            raise forms.ValidationError("Plik jest pusty.")
        try:
            validate_pdf(upload)
        except DomainError as exc:
            raise forms.ValidationError(str(exc.detail)) from exc
        return upload

    def clean_rubric(self):
        """Tekst rubryki → lista kryteriów. Pusty tekst to pusta lista, czyli „zadanie bez rubryki”.

        Reguła zapisu jest w ``apps.grading.rubric``; tutaj wyłącznie tłumaczenie jej wyjątku na
        błąd pod polem formularza – żeby literówka w jednym wierszu nie wyglądała jak awaria.
        """
        text = (self.cleaned_data.get("rubric") or "").strip()
        if not text:
            return []
        try:
            return parse_criteria_lines(text)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_comment_snippets(self):
        """Tekst szablonów → lista pozycji. Pusty tekst to pusta lista, czyli „zadanie bez szablonów”.

        Reguła zapisu jest w ``apps.grading.snippets``; tutaj wyłącznie tłumaczenie jej wyjątku na
        błąd pod polem – tak samo jak przy rubryce.
        """
        text = (self.cleaned_data.get("comment_snippets") or "").strip()
        if not text:
            return []
        try:
            return parse_snippet_lines(text)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_scoring_values(self):
        """Pusty tekst to ``None`` („dziedzicz po etapie”), a nie pusta skala."""
        text = (self.cleaned_data.get("scoring_values") or "").strip()
        return parse_scale_lines(text) if text else None

    def clean(self):
        """Skala zadania i jego maksimum są jedną deklaracją – wypełnia się je razem albo wcale."""
        cleaned = super().clean()
        values = cleaned.get("scoring_values")
        max_points = cleaned.get("max_points")
        if values and max_points is None:
            self.add_error("max_points", "Podaj maksimum punktów dla skali tego zadania.")
        if not values and max_points is not None:
            self.add_error("scoring_values", "Podaj skalę zadania albo wyczyść maksimum punktów.")
        return cleaned

    def uploaded_statement(self):
        """Nowy plik treści albo ``None``. Serwis rozpoznaje po tym, czy podmieniać treść."""
        upload = self.cleaned_data.get("statement_pdf")
        return upload if isinstance(upload, UploadedFile) else None

    def uploaded_statement_en(self):
        """Nowa treść angielska albo ``None`` – ta sama umowa z serwisem, co przy treści polskiej."""
        upload = self.cleaned_data.get("statement_pdf_en")
        return upload if isinstance(upload, UploadedFile) else None

    def uploaded_model_solution(self):
        """Nowa wzorcówka albo ``None`` – ta sama umowa z serwisem, co przy treści zadania."""
        upload = self.cleaned_data.get("model_solution_pdf")
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


class AnnouncementForm(forms.ModelForm):
    """Komunikat organizatora pokazywany w banerze na każdej stronie serwisu.

    ``ModelForm``, a nie zwykły formularz z sześcioma polami: reguła okna czasowego (początek
    przed końcem) jest wyrażalna w modelu i pilnuje jej ``Announcement.full_clean()`` – ten sam
    warunek, co constraint w bazie. Powtórzona w warstwie WWW rozjechałaby się z nim przy
    pierwszej zmianie, a skutek byłby widoczny dopiero przy zapisie, który baza odrzuca.

    Terminy są opcjonalne z dwóch różnych powodów: pusty koniec znaczy „do wyłączenia”, a początek
    domyślnie jest **teraz** (``Announcement.starts_at``), czyli komunikat zaczyna obowiązywać
    w chwili zapisania. Komunikat pisze się w pośpiechu – domyślne „od zaraz” jest tym, czego
    organizator chce w dziewięciu przypadkach na dziesięć.
    """

    required_css_class = REQUIRED_CSS_CLASS

    class Meta:
        model = Announcement
        fields = (
            "text",
            "level",
            "link_url",
            "link_label",
            "starts_at",
            "ends_at",
            "is_active",
            "dismissible",
        )
        field_classes = {"starts_at": LocalDateTimeField, "ends_at": LocalDateTimeField}
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "text": "Treść komunikatu",
            "level": "Waga",
            "link_url": "Adres odnośnika",
            "link_label": "Etykieta odnośnika",
            "starts_at": "Od",
            "ends_at": "Do",
            "is_active": "Włączony",
            "dismissible": "Czytelnik może zamknąć",
        }
        help_texts = {
            "text": (
                "Zwykły tekst, najwyżej 500 znaków. Znaczniki HTML nie zadziałają – odnośnik "
                "wpisuje się w dwa pola poniżej."
            ),
            "level": "„Awaria” jest czytana przez czytniki ekranu od razu, bez przewijania strony.",
            "ends_at": "Puste = komunikat wisi do wyłączenia.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # „Od” nie jest w formularzu obowiązkowe, choć w modelu jest (``default=timezone.now``):
        # organizator ogłaszający awarię nie ma przepisywać bieżącej godziny z zegarka. Wartość
        # domyślną wstawia ``clean_starts_at`` niżej. Ustawienie ``blank=True`` na modelu dałoby
        # to samo w formularzu, ale zdjęłoby walidację także z zapisów spoza panelu.
        self.fields["starts_at"].required = False

    def clean_starts_at(self):
        """Puste „od” znaczy „od zaraz”, a nie brak wartości."""
        return self.cleaned_data.get("starts_at") or timezone.now()
