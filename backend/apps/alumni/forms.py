"""Formularze sieci absolwentów. Walidują kształt; reguły (rola, flaga, zakresy) są w serwisach."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.countries import COUNTRIES

from .models import (
    MAX_BIO_LENGTH,
    MAX_INVITATION_BODY,
    MAX_MENTOR_CAPACITY,
    MAX_NOTE_LENGTH,
    MAX_REASON_LENGTH,
    MIN_MENTOR_CAPACITY,
    Interest,
    InvitationKind,
    Level,
)
from .validators import clean_event_url, clean_github, clean_linkedin


class JoinForm(forms.Form):
    consent = forms.BooleanField(required=True, label=_("Wyrażam zgodę opisaną wyżej."))


class ProfileForm(forms.Form):
    show_full_name = forms.BooleanField(
        required=False,
        label=_("Pokazuj moje pełne imię i nazwisko"),
        help_text=_("Działa tylko wtedy, gdy masz w konkursie zgodę na publikację imienia i nazwiska."),
    )
    university = forms.CharField(required=False, max_length=120, label=_("Uczelnia albo miejsce pracy"))
    field_of_study = forms.CharField(required=False, max_length=120, label=_("Kierunek albo stanowisko"))
    city = forms.CharField(required=False, max_length=120, label=_("Miasto"))
    country = forms.ChoiceField(required=False, label=_("Kraj"), choices=[("", "—"), *COUNTRIES])
    bio = forms.CharField(
        required=False,
        max_length=MAX_BIO_LENGTH,
        label=_("Kilka słów o sobie"),
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": MAX_BIO_LENGTH}),
    )
    interests = forms.MultipleChoiceField(
        required=False,
        label=_("Zainteresowania"),
        choices=Interest.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    linkedin_url = forms.CharField(
        required=False, max_length=300, label="LinkedIn", help_text="https://www.linkedin.com/in/…"
    )
    github_url = forms.CharField(
        required=False, max_length=300, label="GitHub", help_text="https://github.com/…"
    )
    mentor_available = forms.BooleanField(required=False, label=_("Chcę być mentorem"))
    mentor_topics = forms.MultipleChoiceField(
        required=False,
        label=_("W czym mogę pomóc"),
        choices=Interest.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    mentor_capacity = forms.IntegerField(
        min_value=MIN_MENTOR_CAPACITY,
        max_value=MAX_MENTOR_CAPACITY,
        initial=2,
        label=_("Ilu uczestników naraz"),
    )
    listed = forms.BooleanField(
        required=False, label=_("Pokazuj mój profil w katalogu dla zalogowanych uczestników")
    )
    public = forms.BooleanField(
        required=False,
        label=_("Pokazuj mój profil na publicznej ścianie absolwentów"),
        help_text=_("Na ścianie widać tylko podpis, osiągnięcia, uczelnię i kierunek."),
    )
    invitations = forms.BooleanField(
        required=False, label=_("Chcę dostawać zaproszenia od organizatora (warsztaty, webinary, jury)")
    )

    def clean_linkedin_url(self):
        return clean_linkedin(self.cleaned_data.get("linkedin_url", ""))

    def clean_github_url(self):
        return clean_github(self.cleaned_data.get("github_url", ""))


class RequestForm(forms.Form):
    topic = forms.ChoiceField(required=False, label=_("Temat"), choices=[("", "—"), *Interest.choices])
    note = forms.CharField(
        required=False,
        max_length=MAX_NOTE_LENGTH,
        label=_("Krótko: w czym potrzebujesz pomocy?"),
        help_text=_("Notatkę przeczyta mentor i organizator. Nie wpisuj tu danych kontaktowych."),
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": MAX_NOTE_LENGTH}),
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(
        max_length=MAX_REASON_LENGTH,
        label=_("Co się stało?"),
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": MAX_REASON_LENGTH}),
    )


class EndForm(forms.Form):
    note = forms.CharField(
        max_length=MAX_REASON_LENGTH,
        label=_("Notatka (powód zakończenia)"),
        widget=forms.Textarea(attrs={"rows": 2, "maxlength": MAX_REASON_LENGTH}),
    )


class SettingsForm(forms.Form):
    eligibility = forms.ChoiceField(label=_("Kto może dołączyć"), choices=Level.choices)
    mentoring_enabled = forms.BooleanField(required=False, label=_("Mentoring włączony"))
    public_wall = forms.BooleanField(required=False, label=_("Publiczna ściana absolwentów"))


class InvitationForm(forms.Form):
    kind = forms.ChoiceField(label=_("Rodzaj"), choices=InvitationKind.choices)
    title = forms.CharField(max_length=150, label=_("Tytuł (temat listu)"))
    body = forms.CharField(
        max_length=MAX_INVITATION_BODY,
        label=_("Treść"),
        widget=forms.Textarea(attrs={"rows": 8, "maxlength": MAX_INVITATION_BODY}),
    )
    url = forms.CharField(required=False, max_length=500, label=_("Adres wydarzenia (https://…)"))
    editions = forms.MultipleChoiceField(
        required=False, label=_("Edycje"), widget=forms.CheckboxSelectMultiple
    )
    min_level = forms.ChoiceField(
        required=False, label=_("Najniższe osiągnięcie"), choices=[("", _("dowolne")), *Level.choices]
    )
    interests = forms.MultipleChoiceField(
        required=False,
        label=_("Zainteresowania (dowolne z)"),
        choices=Interest.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    mentors_only = forms.BooleanField(required=False, label=_("Tylko mentorzy"))

    def __init__(self, *args, editions=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editions"].choices = [(str(edition.pk), edition.year_label) for edition in editions]

    def clean_url(self):
        return clean_event_url(self.cleaned_data.get("url", ""))

    def filters(self) -> dict:
        data = self.cleaned_data
        return {
            "editions": data.get("editions") or [],
            "min_level": data.get("min_level") or "",
            "interests": data.get("interests") or [],
            "mentors_only": data.get("mentors_only") or False,
        }
