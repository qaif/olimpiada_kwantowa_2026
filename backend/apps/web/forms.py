"""Formularze Django dla interfejsu WWW.

Formularze pilnują wyłącznie kształtu danych (typy, wymagalność, długości). Reguły domenowe –
siła hasła, unikalność e-maila, zgodność oceny ze skalą, okna czasowe – zostają w serwisach,
które te formularze wołają. Dublowanie ich tutaj rozjechałoby się z API przy pierwszej zmianie.
"""

from __future__ import annotations

import json

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from apps.appeals.models import MAX_TEXT_LENGTH, MIN_ARGUMENT_LENGTH, AppealStatus
from apps.results.models import Anonymization


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
    district = forms.CharField(label="Okręg", max_length=100)
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
    district = forms.CharField(label="Okręg (deklarowany)", max_length=100, required=False)


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

    district = forms.CharField(label="Okręg", max_length=100)


class InvitationForm(forms.Form):
    """Generowanie kodu zaproszenia. Kod jawny jest pokazywany dokładnie raz."""

    district = forms.CharField(label="Okręg (narzucony kodem)", max_length=100, required=False)
    valid_days = forms.IntegerField(label="Ważność (dni)", min_value=1, max_value=365, initial=14)
    max_uses = forms.IntegerField(label="Limit użyć", min_value=1, max_value=100, initial=1)
    is_appeals = forms.BooleanField(label="Komisja odwoławcza", required=False)
    requires_approval = forms.BooleanField(label="Wymaga zatwierdzenia (PENDING)", required=False)


class PublishResultsForm(forms.Form):
    """Publikacja wyników etapu wraz z wyborem trybu anonimizacji."""

    anonymization = forms.ChoiceField(label="Anonimizacja", choices=Anonymization.choices)
