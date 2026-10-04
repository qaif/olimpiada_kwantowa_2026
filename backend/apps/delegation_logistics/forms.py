"""Formularze logistyki finału. Reguły (terminy, zgoda na dane o zdrowiu) rozstrzyga serwis.

Formularz członka ma **wszystkie grupy na jednej stronie**: opiekun wypełnia dane jednej osoby za
jednym podejściem, a grupy po terminie są polami ``disabled`` – Django ignoruje wtedy to, co przyszło
w żądaniu, i bierze wartość początkową, więc spreparowany POST i tak niczego nie zmieni (a gdyby –
serwis odmówi, ``LOGISTICS_GROUP_LOCKED``). Wszystkie pola są nieobowiązkowe: brak jest stanem
(„braki” na liście i w przypomnieniu), a nie błędem formularza – opiekun wpisuje to, co już wie.

Napisy formularza opiekuna i obsługi są tłumaczone (gettext, msgid po polsku); formularze ekranów
koordynatora są po polsku bez gettext (I18N-01 § 0, jak ekran „Delegacje”).
"""

from __future__ import annotations

from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import (
    Diet,
    FieldGroup,
    FinalEvent,
    Gender,
    GuestRole,
    RoomGender,
    TravelMode,
    TshirtSize,
)
from .services import GROUP_FIELDS, group_deadline, group_locked

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


def _choices(enum) -> list[tuple[str, str]]:
    return [("", "—"), *enum.choices]


def _iso_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def submitted(form) -> dict:
    """Dane z pól **niezablokowanych** – pole ``disabled`` oddaje wartość początkową, a nie zmianę."""
    return {name: value for name, value in form.cleaned_data.items() if not form.fields[name].disabled}


def _member_fields() -> dict[str, forms.Field]:
    return {
        # --- dokument podróży ---
        "passport_name": forms.CharField(
            label=_("Imię i nazwisko dokładnie jak w paszporcie"), max_length=150, required=False
        ),
        "nationality": forms.CharField(
            label=_("Obywatelstwo (dwuliterowy kod kraju, np. DE)"), max_length=2, required=False
        ),
        "date_of_birth": forms.DateField(label=_("Data urodzenia"), required=False, widget=DATE),
        "passport_number": forms.CharField(label=_("Numer paszportu"), max_length=30, required=False),
        "passport_expiry": forms.DateField(label=_("Paszport ważny do"), required=False, widget=DATE),
        # --- podróż ---
        "arrival_date": forms.DateField(label=_("Dzień przyjazdu"), required=False, widget=DATE),
        "arrival_time": forms.TimeField(
            label=_("Godzina przyjazdu (czas lokalny)"), required=False, widget=TIME
        ),
        "arrival_mode": forms.ChoiceField(
            label=_("Środek transportu"), choices=_choices(TravelMode), required=False
        ),
        "arrival_number": forms.CharField(label=_("Numer lotu / pociągu"), max_length=40, required=False),
        "arrival_place": forms.CharField(
            label=_("Lotnisko / dworzec przyjazdu"), max_length=120, required=False
        ),
        "departure_date": forms.DateField(label=_("Dzień wyjazdu"), required=False, widget=DATE),
        "departure_time": forms.TimeField(
            label=_("Godzina wyjazdu (czas lokalny)"), required=False, widget=TIME
        ),
        "departure_mode": forms.ChoiceField(
            label=_("Środek transportu (wyjazd)"), choices=_choices(TravelMode), required=False
        ),
        "departure_number": forms.CharField(
            label=_("Numer lotu / pociągu (wyjazd)"), max_length=40, required=False
        ),
        "departure_place": forms.CharField(
            label=_("Lotnisko / dworzec wyjazdu"), max_length=120, required=False
        ),
        # --- zakwaterowanie ---
        "needs_accommodation": forms.BooleanField(label=_("Potrzebuje noclegu"), required=False),
        "gender": forms.ChoiceField(
            label=_("Płeć (wyłącznie do przydziału pokoi)"), choices=_choices(Gender), required=False
        ),
        "roommate_preference": forms.CharField(
            label=_("Preferowany współlokator (imię i nazwisko)"), max_length=200, required=False
        ),
        "accommodation_notes": forms.CharField(
            label=_("Uwagi do zakwaterowania (bez informacji o zdrowiu)"),
            max_length=300,
            required=False,
            widget=forms.Textarea(attrs={"rows": 2}),
        ),
        # --- zdrowie ---
        "diet": forms.ChoiceField(label=_("Dieta"), choices=_choices(Diet), required=False),
        "diet_notes": forms.CharField(label=_("Uwagi do diety"), max_length=300, required=False),
        "allergies": forms.CharField(
            label=_("Alergie (pokarmowe i inne istotne)"),
            max_length=500,
            required=False,
            widget=forms.Textarea(attrs={"rows": 2}),
        ),
        "medical_notes": forms.CharField(
            label=_(
                "Informacje medyczne potrzebne na miejscu (np. stałe leki, postępowanie w nagłym wypadku)"
            ),
            max_length=1000,
            required=False,
            widget=forms.Textarea(attrs={"rows": 3}),
        ),
        # --- identyfikator i kontakt ---
        "tshirt_size": forms.ChoiceField(
            label=_("Rozmiar koszulki"), choices=_choices(TshirtSize), required=False
        ),
        "emergency_name": forms.CharField(
            label=_("Kontakt alarmowy – imię i nazwisko"), max_length=150, required=False
        ),
        "emergency_phone": forms.CharField(
            label=_("Kontakt alarmowy – telefon (z numerem kierunkowym kraju)"), max_length=40, required=False
        ),
    }


HEALTH_CONSENT_LABEL = _(
    "Potwierdzam, że ta osoba – a jeśli jest niepełnoletnia, jej rodzic lub opiekun prawny – wyraziła "
    "wyraźną zgodę na przetwarzanie podanych danych o diecie i zdrowiu przez organizatora wyłącznie "
    "w celu zapewnienia jej bezpieczeństwa i wyżywienia podczas finału. Dane zostaną usunięte po "
    "zakończeniu finału; zgodę można w każdej chwili wycofać."
)


class MemberForm(forms.Form):
    """Dane pobytu jednej osoby – grupy dopuszczone w konkursie, grupy po terminie zablokowane."""

    def __init__(self, *args, member, groups, event, as_officer: bool = False, **kwargs):
        initial = {}
        all_fields = _member_fields()
        for group in groups:
            for name in GROUP_FIELDS[group]:
                initial[name] = getattr(member, name)
        # Daty w polach szyfrowanych leżą jako napis ISO; u ucznia bez dokumentu podpowiadamy datę
        # urodzenia z profilu (``DelegationMember.birth_date``) – opiekun i tak ją już raz wpisał.
        initial["date_of_birth"] = member.birth_date
        initial["passport_expiry"] = _iso_date(member.passport_expiry)
        kwargs.setdefault("initial", initial)
        super().__init__(*args, **kwargs)
        self.member = member
        self.group_rows = []
        for group in groups:
            locked = not as_officer and group_locked(event, group)
            names = list(GROUP_FIELDS[group])
            for name in names:
                field = all_fields[name]
                field.disabled = locked
                self.fields[name] = field
            if group == FieldGroup.HEALTH and member.health_consent_at is None:
                self.fields["health_consent"] = forms.BooleanField(
                    label=HEALTH_CONSENT_LABEL, required=False, disabled=locked
                )
                names.append("health_consent")
            self.group_rows.append(
                {
                    "group": group,
                    "label": FieldGroup(group).label,
                    "deadline": group_deadline(event, group),
                    "locked": locked,
                    "fields": [self[name] for name in names],
                }
            )


class GuestForm(forms.Form):
    first_name = forms.CharField(label=_("Imię"), max_length=150)
    last_name = forms.CharField(label=_("Nazwisko"), max_length=150)
    email = forms.EmailField(label=_("Adres e-mail (opcjonalnie)"), required=False)
    role = forms.ChoiceField(label=_("Rola w delegacji"), choices=GuestRole.choices)


class PhotoForm(forms.Form):
    photo = forms.FileField(
        label=_("Zdjęcie do identyfikatora (JPG lub PNG, do 5 MB, twarz na wprost)"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )


# --- koordynator (po polsku, bez gettext) -------------------------------------------------------------


class EventForm(forms.ModelForm):
    class Meta:
        model = FinalEvent
        fields = [
            "name",
            "city",
            "venue",
            "starts_on",
            "ends_on",
            "retention_days",
            "letter_prefix",
            "deadline_identity",
            "deadline_travel",
            "deadline_accommodation",
            "deadline_health",
            "deadline_personal",
        ]
        widgets = {
            "starts_on": DATE,
            "ends_on": DATE,
            "deadline_identity": DATETIME,
            "deadline_travel": DATETIME,
            "deadline_accommodation": DATETIME,
            "deadline_health": DATETIME,
            "deadline_personal": DATETIME,
        }
        help_texts = {
            "retention_days": "Po tylu dniach od ostatniego dnia finału dane członków delegacji, zdjęcia "
            "i migawki listów zostaną usunięte automatycznie.",
            "letter_prefix": "Np. IQO – numer listu: IQO/2027/0001. Puste – identyfikator konkursu.",
        }

    def clean_retention_days(self):
        value = self.cleaned_data["retention_days"]
        if value is None or value < 1 or value > 365:
            raise forms.ValidationError("Podaj od 1 do 365 dni.")
        return value


class AccessForm(forms.Form):
    email = forms.EmailField(label="Adres e-mail konta")
    role = forms.ChoiceField(
        label="Przydział",
        choices=[
            ("OFFICER", "oficer logistyki (pełny wgląd – tylko koordynator)"),
            ("CHECKIN", "obsługa rejestracji (skanowanie identyfikatorów)"),
        ],
    )


class RoomForm(forms.Form):
    building = forms.CharField(label="Budynek / hotel", max_length=120, required=False)
    name = forms.CharField(label="Numer / nazwa pokoju", max_length=60)
    capacity = forms.IntegerField(label="Liczba miejsc", min_value=1, max_value=20)
    gender = forms.ChoiceField(label="Płeć pokoju", choices=RoomGender.choices)
    note = forms.CharField(label="Uwagi", max_length=200, required=False)


class CheckpointForm(forms.Form):
    name = forms.CharField(label="Nazwa punktu kontroli", max_length=120)
