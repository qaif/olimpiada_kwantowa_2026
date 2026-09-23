"""Formularz plakatu na ekranie koordynatora (``/coordinator/posters/``).

Formularz sprawdza **kształt** (długości, obowiązkowość) i **treść plików** – format po bajtach,
rozmiar, dekodowalność podglądu (``apps.promo.validators``, ``apps.promo.previews``). Wszystko, co
dzieje się z plikami potem – miniatura, sprzątanie starych plików ze storage, kolejność nowego
plakatu – robi serwis (``apps.promo.services.save_material``), bo ta sama reguła ma obowiązywać
niezależnie od tego, skąd zapis przyszedł.

Konkursu nie ma w polach i mieć nie może: plakat należy do konkursu **z żądania** (tak samo jak
komunikat w ``apps.web.views.coordinator_announcements``), a pole w formularzu byłoby wartością,
którą da się podstawić w POST.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .models import PromoMaterial
from .previews import verify_image
from .validators import MAX_FILE_MB, MAX_PREVIEW_MB, validate_material_file, validate_preview_file

#: Klasa CSS markera pola obowiązkowego – ta sama, co w pozostałych formularzach serwisu.
REQUIRED_CSS_CLASS = "required"

#: Podpowiedź przeglądarce, co pokazać w oknie wyboru pliku. **Nie** jest walidacją – tę robi
#: ``validate_material_file`` po treści pliku.
MATERIAL_ACCEPT = "application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
PREVIEW_ACCEPT = "image/jpeg,image/png,.jpg,.jpeg,.png"


class PromoMaterialForm(forms.ModelForm):
    """Tytuł, opis, plik, opcjonalny podgląd i publikacja jednego plakatu."""

    required_css_class = REQUIRED_CSS_CLASS

    #: Pole pliku jest opisane ręcznie, a nie wzięte z modelu: na edycji jest **opcjonalne**
    #: (brak nowego pliku = zostaje dotychczasowy), a ``ClearableFileInput`` z modelu dokładałby
    #: kratkę „wyczyść”, która dla plakatu nie ma sensu – plakat bez pliku nie istnieje.
    file = forms.FileField(
        label="Plik plakatu",
        widget=forms.FileInput(attrs={"accept": MATERIAL_ACCEPT}),
        help_text=(
            f"PDF, JPG albo PNG, najwyżej {MAX_FILE_MB} MB. Format rozpoznajemy po treści pliku, "
            "nie po rozszerzeniu."
        ),
    )
    preview = forms.FileField(
        label="Własny podgląd (opcjonalnie)",
        required=False,
        widget=forms.FileInput(attrs={"accept": PREVIEW_ACCEPT}),
        help_text=(
            f"JPG albo PNG, najwyżej {MAX_PREVIEW_MB} MB. Dla plakatu JPG/PNG podgląd powstaje sam; "
            "przy PDF-ie bez podglądu karta pokaże ikonę dokumentu."
        ),
    )
    clear_preview = forms.BooleanField(
        label="Usuń własny podgląd",
        required=False,
        help_text="Dla plakatu JPG/PNG wróci podgląd zrobiony automatycznie z pliku.",
    )

    class Meta:
        model = PromoMaterial
        fields = ("title", "description", "is_published")
        labels = {
            "title": "Tytuł",
            "description": "Opis",
            "is_published": "Opublikowany – widoczny na stronie /plakaty/",
        }
        help_texts = {
            "title": "Np. „Plakat olimpiady 2026/2027”.",
            "description": "Jedna linia: format i przeznaczenie, np. „A4 pionowy” albo „A3 do gabloty”.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["file"].required = False
            self.fields["file"].help_text = (
                "Zostaw puste, żeby zachować obecny plik. Nowy plik zastępuje poprzedni – statystyki "
                "pobrań zostają przy plakacie."
            )
        if not (self.instance.pk and self.instance.preview and not self.instance.preview_is_generated):
            # Kratka „usuń własny podgląd” ma sens wyłącznie wtedy, gdy własny podgląd jest.
            del self.fields["clear_preview"]
        #: Format rozpoznany w ``clean_file`` – serwis zapisuje go razem z plikiem.
        self.detected_format: str | None = None

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if upload:
            self.detected_format = validate_material_file(upload)
        return upload

    def clean_preview(self):
        upload = self.cleaned_data.get("preview")
        if upload:
            validate_preview_file(upload)
            if not verify_image(upload):
                raise ValidationError(
                    "Nie udało się odczytać obrazu podglądu – zapisz go ponownie jako JPG albo PNG.",
                    code="invalid_image",
                )
        return upload
