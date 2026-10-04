"""Formularze nadzoru: ustawienia etapu (koordynator) i prośba ucznia o alternatywę."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ALTERNATIVE_NOTE_MAX_LENGTH, AlternativeReason, ProctoringConfig


class ProctoringConfigForm(forms.ModelForm):
    """Ustawienia nadzoru etapu. Ekran koordynatora – po polsku (zakres I18N-01, jak WEB-01)."""

    class Meta:
        model = ProctoringConfig
        fields = (
            "enabled",
            "require_screen_share",
            "require_microphone",
            "id_photo",
            "record",
            "on_unavailable",
            "instructions",
        )
        widgets = {"instructions": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "record": "Domyślnie wyłączone. Nagranie obrazu ucznia w domu to najdalej idące przetwarzanie – "
            "włącz tylko, gdy regulamin etapu tego wymaga; uczniowie zobaczą to w informacji przed zgodą.",
            "require_microphone": "Dźwięk z domu ucznia to dodatkowe dane – zostaw wyłączone, "
            "jeśli obraz wystarcza.",
            "on_unavailable": "„Pozwól” nie zatrzymuje zawodów przy awarii serwera nadzoru; "
            "sesja dostaje znacznik.",
        }

    def service_data(self) -> dict:
        return dict(self.cleaned_data)


class AlternativeForm(forms.Form):
    """„Nie mogę użyć kamery” – powód z listy i krótka uwaga (bez danych o zdrowiu)."""

    reason = forms.ChoiceField(
        label=_("Powód"),
        choices=[
            (AlternativeReason.NO_CAMERA, _("Nie mam kamery")),
            (AlternativeReason.CAMERA_BROKEN, _("Kamera nie działa")),
            (AlternativeReason.NO_BROWSER, _("Moja przeglądarka nie obsługuje nadzoru")),
            (AlternativeReason.ACCESSIBILITY, _("Potrzeby dostępności")),
            (AlternativeReason.OTHER, _("Inny powód")),
        ],
    )
    note = forms.CharField(
        label=_("Krótka uwaga (opcjonalnie)"),
        required=False,
        max_length=ALTERNATIVE_NOTE_MAX_LENGTH,
        help_text=_(
            "Nie wpisuj informacji o zdrowiu – organizator skontaktuje się z Tobą, jeśli będzie trzeba."
        ),
        widget=forms.Textarea(attrs={"rows": 2}),
    )
