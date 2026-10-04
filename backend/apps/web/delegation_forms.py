"""Formularze delegacji krajowych (DEL-01): koordynator, opiekun drużyny i przyjęcie zaproszenia.

Osobny moduł od ``apps.web.forms`` z tego samego powodu, co ``supervisor_forms``: to jest komplet
formularzy jednej funkcji, czytany razem z jej widokami.

Dwa języki w jednym pliku i to jest celowe. Formularze **koordynatora** są po polsku bez gettext
(ekrany panelu nie są tłumaczone – I18N-01 § 0). Formularze **opiekuna drużyny** i ekranu
zaproszenia czyta osoba z dowolnego kraju olimpiady międzynarodowej, więc każda etykieta idzie
przez ``gettext_lazy`` (msgid po polsku, przekład w dziesięciu katalogach ``locale/``).

Formularze pilnują kształtu danych; reguły (limit, kraj, okno rejestracji, zgodność adresu,
komplet zgód) rozstrzyga ``apps.accounts.delegation_services``.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from apps.accounts.consents import ConsentKind, consent_set, organizer_name
from apps.accounts.consents import label as consent_label
from apps.accounts.delegations import DelegationStatus
from apps.accounts.names import validate_person_name
from apps.accounts.services import registration_profile
from apps.tenancy.models import MAX_DELEGATION_SIZE
from apps.web.forms import (
    PASSWORD_CONFIRM_FIELD,
    REQUIRED_CSS_CLASS,
    _clean_birth_date_method,
    birth_date_field,
    clean_password_pair,
    password_field,
)

#: Zgody opiekuna drużyny – te same, co ``delegation_services.LEADER_CONSENT_KINDS``.
LEADER_CONSENT_KINDS = (ConsentKind.TERMS, ConsentKind.PRIVACY)


# --- koordynator --------------------------------------------------------------------------------


class LeaderInviteForm(forms.Form):
    """„Zaproś opiekuna”: adres i kraj. Delegację kraju zakłada serwis, gdy jej jeszcze nie ma."""

    required_css_class = REQUIRED_CSS_CLASS

    email = forms.EmailField(label="Adres e-mail opiekuna", max_length=254)
    country = forms.ChoiceField(label="Kraj", choices=())

    def __init__(self, *args, countries=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].choices = [("", "— wybierz kraj —"), *countries]


class DelegationEditForm(forms.Form):
    """Limit, stan i notatka jednej delegacji."""

    required_css_class = REQUIRED_CSS_CLASS

    max_students = forms.IntegerField(label="Limit uczniów", min_value=1, max_value=MAX_DELEGATION_SIZE)
    status = forms.ChoiceField(
        label="Stan",
        choices=DelegationStatus.choices,
        help_text=(
            "Zamknięta delegacja zamraża listę uczniów: opiekun ją widzi, ale nie dodaje, "
            "nie poprawia i nie usuwa."
        ),
    )
    note = forms.CharField(
        label="Notatka koordynatora",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Widzi ją wyłącznie zespół organizatora – opiekun drużyny nie.",
    )


# --- przyjęcie zaproszenia --------------------------------------------------------------------


class LeaderConsentMixin(forms.Form):
    """Regulamin i RODO jako pola z etykietą z dokumentem – tak samo jak u opiekuna szkolnego.

    Pola powstają w ``__init__``, bo etykieta niesie odnośnik do dokumentu i nazwę organizatora
    czytane z bazy. Komplet sprawdza serwis (``accept_leader_invitation``) – formularz nie ma
    własnej kopii reguły „co jest wymagane”.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organizer = organizer_name()
        self._consents = [consent for consent in consent_set() if consent.kind in LEADER_CONSENT_KINDS]
        for consent in self._consents:
            self.fields[consent.field_name] = forms.BooleanField(
                label=consent_label(consent, organizer=organizer), required=False, label_suffix=""
            )

    @property
    def consent_field_names(self) -> tuple[str, ...]:
        return tuple(consent.field_name for consent in self._consents)

    def given(self) -> dict[str, bool]:
        """Zgody w postaci rodzajów (``TERMS``, ``PRIVACY``) – tak mówi serwis."""
        return {consent.kind: bool(self.cleaned_data.get(consent.field_name)) for consent in self._consents}


class LeaderSignupForm(LeaderConsentMixin):
    """Nowe konto opiekuna z zaproszenia. Adresu nie ma w formularzu – pochodzi z zaproszenia."""

    required_css_class = REQUIRED_CSS_CLASS
    field_order = ["first_name", "last_name", "password", PASSWORD_CONFIRM_FIELD]

    first_name = forms.CharField(
        label=gettext_lazy("Imię"), max_length=150, validators=[validate_person_name]
    )
    last_name = forms.CharField(
        label=gettext_lazy("Nazwisko"), max_length=150, validators=[validate_person_name]
    )
    password = password_field()
    password2 = password_field(gettext_lazy("Powtórz hasło"))

    def __init__(self, *args, email: str = "", **kwargs):
        self._email = email
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        # Adres z zaproszenia do sprawdzenia podobieństwa hasła – pola e-mail w formularzu nie ma.
        cleaned["email"] = self._email
        cleaned = clean_password_pair(self, cleaned)
        cleaned.pop("email", None)
        return cleaned


class LeaderConfirmForm(LeaderConsentMixin):
    """Zalogowany adresat zaproszenia: same zgody – konto już ma."""


# --- opiekun drużyny: uczeń ----------------------------------------------------------------------


def _grade_choices(profile) -> list[tuple[str, str]]:
    low, high = profile.grade_range()
    return [
        ("", gettext_lazy("— wybierz klasę —")),
        *((str(number), str(number)) for number in range(low, high + 1)),
    ]


class StudentForm(forms.Form):
    """Uczeń zgłaszany przez opiekuna drużyny: dane, których uczeń nie musi przepisywać.

    Hasła, telefonu i zgód tu nie ma: składa je uczeń sam pod linkiem z listu. Klasa i data
    urodzenia są wymagane wtedy, gdy wymaga ich profil rejestracji konkursu – ta sama reguła,
    co przy samodzielnej rejestracji.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = ["first_name", "last_name", "email", "birth_date", "school", "grade", "guardian_email"]

    first_name = forms.CharField(
        label=gettext_lazy("Imię"), max_length=150, validators=[validate_person_name]
    )
    last_name = forms.CharField(
        label=gettext_lazy("Nazwisko"), max_length=150, validators=[validate_person_name]
    )
    email = forms.EmailField(
        label=gettext_lazy("Adres e-mail ucznia"),
        max_length=254,
        help_text=gettext_lazy(
            "Na ten adres uczeń dostanie link do uruchomienia konta. Adres jest jego loginem."
        ),
    )
    school = forms.CharField(label=gettext_lazy("Szkoła"), max_length=255, min_length=3)
    grade = forms.TypedChoiceField(label=gettext_lazy("Klasa"), choices=(), coerce=int, empty_value=None)
    guardian_email = forms.EmailField(
        label=gettext_lazy("Adres e-mail rodzica lub opiekuna prawnego"),
        max_length=254,
        required=False,
        help_text=gettext_lazy(
            "Nieobowiązkowe. Uczeń niepełnoletni poprosi z tego adresu o zgodę rodzica po uruchomieniu konta."
        ),
    )

    clean_birth_date = _clean_birth_date_method

    def __init__(self, *args, competition=None, **kwargs):
        super().__init__(*args, **kwargs)
        profile = registration_profile(competition)
        self.fields["birth_date"] = birth_date_field(required=profile.require_birth_year)
        self.fields["grade"].choices = _grade_choices(profile)
        self.fields["grade"].required = profile.require_grade
        self.order_fields(self.field_order)
