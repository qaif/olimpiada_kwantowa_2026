"""Formularz wersji „Zasad Organizacji Zawodów” etapu w panelu koordynatora (ZOZ-01).

Treść przychodzi z jednej z dwóch dróg: wklejona w pole tekstowe albo wgrana plikiem ``.html``
(tak organizator dostaje transkrypcję dokumentu). Plik wygrywa – pole zostaje na poprawki drobne.
Oczyszczanie HTML-a robi serwis (``apps.competitions.stage_rules.sanitize_html``), nie formularz:
ta sama reguła obowiązuje w komendzie importu.
"""

from __future__ import annotations

from django import forms
from django.core.files.uploadedfile import UploadedFile

from apps.competitions.stage_rules import DEFAULT_TITLE, MAX_HTML_CHARS, MAX_PDF_MB, VERSION_RE

from .forms import DayField

MEGABYTE = 1024 * 1024


class StageRulesForm(forms.Form):
    version = forms.CharField(
        label="Wersja",
        max_length=20,
        help_text="Np. „1.0”. Numer jest częścią adresu wersji i nie zmienia się po zapisaniu.",
    )
    document_date = DayField(label="Data dokumentu")
    title = forms.CharField(label="Tytuł", max_length=200, initial=DEFAULT_TITLE)
    body_html = forms.CharField(
        label="Treść (HTML)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 18, "spellcheck": "false"}),
        help_text=(
            'Nagłówki paragrafów jako <h2 id="par-1">§ 1. …</h2>, ustępy jako lista <ol>. '
            "Skrypty, style i atrybuty zdarzeń są usuwane przy zapisie."
        ),
    )
    html_file = forms.FileField(
        label="…albo plik HTML",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".html,.htm,text/html"}),
        help_text="Plik w kodowaniu UTF-8. Zastępuje treść z pola wyżej.",
    )
    pdf = forms.FileField(
        label="Plik PDF do pobrania (opcjonalnie)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        help_text=f"Plik PDF, maksymalnie {MAX_PDF_MB} MB.",
    )
    remove_pdf = forms.BooleanField(label="Usuń dotychczasowy plik PDF", required=False)

    def __init__(self, *args, editing: bool = False, has_pdf: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            # Numer wersji jest w adresie opublikowanej strony – zmiana zerwałaby odnośniki.
            self.fields["version"].disabled = True
        if not has_pdf:
            del self.fields["remove_pdf"]

    def clean_version(self):
        value = (self.cleaned_data.get("version") or "").strip()
        if not VERSION_RE.match(value):
            raise forms.ValidationError(
                "Wersja: cyfry, litery, kropka lub myślnik (np. „1.0”), do 20 znaków."
            )
        return value

    def clean_html_file(self):
        upload = self.cleaned_data.get("html_file")
        if not isinstance(upload, UploadedFile):
            return None
        if upload.size > MAX_HTML_CHARS * 4:
            raise forms.ValidationError("Plik HTML jest za duży.")
        try:
            return upload.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError("Plik HTML musi być zapisany w kodowaniu UTF-8.") from exc

    def clean_pdf(self):
        upload = self.cleaned_data.get("pdf")
        if not isinstance(upload, UploadedFile):
            return None
        if upload.size == 0:
            raise forms.ValidationError("Plik jest pusty.")
        if upload.size > MAX_PDF_MB * MEGABYTE:
            raise forms.ValidationError(f"Limit pliku PDF to {MAX_PDF_MB} MB.")
        head = upload.read(5)
        upload.seek(0)
        if head != b"%PDF-":
            raise forms.ValidationError("Plik nie jest dokumentem PDF.")
        return upload

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("html_file") or cleaned.get("body_html") or ""
        if not source.strip():
            self.add_error("body_html", "Podaj treść dokumentu albo wgraj plik HTML.")
        cleaned["body"] = source
        return cleaned
