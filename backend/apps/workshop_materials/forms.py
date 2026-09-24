"""Formularze materiału na ekranie koordynatora (``/coordinator/workshops/materials/``).

Jeden formularz na trzy drogi zapisu, bo pola opisowe (warsztat, tytuł, opis, publikacja) są
wspólne, a różni się wyłącznie to, skąd bierze się treść:

- **odnośnik** – zwykły POST formularza; adres jest polem formularza,
- **film i plik** – treść nie przechodzi przez serwer (``apps.workshop_materials.storage``). Skrypt
  ``static/js/workshop-material-upload.js`` wysyła ten sam formularz **bez pliku**, za to z nazwą
  i rozmiarem pliku (``filename``, ``size``), na adres „rozpocznij wgrywanie”. Serwer sprawdza go
  tym samym formularzem (``UploadStartForm``) i dopiero wtedy zakłada wgrywanie,
- **edycja** – wyłącznie pola opisowe i warsztat; plik podmienia się, dodając nowy materiał.
  Podmiana treści pod tym samym wierszem oznaczałaby, że materiał opublikowany zmienia się
  u widzów bez ponownego sprawdzenia – a tak nowy plik przechodzi całą drogę od początku.

Konkursu nie ma w polach i mieć nie może – materiał należy do konkursu **z żądania** (ta sama zasada,
co przy plakatach). Warsztat jest listą wyboru z harmonogramu tego konkursu: klucz spoza listy
odpada na walidacji, więc nie da się przypiąć materiału do warsztatu, którego nie ma.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django import forms

from . import formats
from .models import MaterialKind, MaterialStatus, WorkshopMaterial
from .services import row_label

REQUIRED_CSS_CLASS = "required"

#: Podpowiedź dla okna wyboru pliku – **nie** walidacja (tę robi serwer po bajtach).
VIDEO_ACCEPT = "video/mp4,video/webm,.mp4,.webm"
FILE_ACCEPT = ",".join(f".{ext}" for ext in sorted(formats.FILE_FORMATS)) + ",.jpeg"


def workshop_choices(rows: list[dict], *, current: WorkshopMaterial | None = None) -> list[tuple[str, str]]:
    """Lista wyboru warsztatu: wiersze harmonogramu, a przy edycji „osieroconego” – także jego klucz.

    Bez tej drugiej pozycji formularz edycji materiału, którego warsztat zniknął z harmonogramu,
    nie dałby się zapisać bez przepinania – a koordynator mógł chcieć poprawić tylko literówkę.
    """
    choices = [(row["key"], row_label(row["topic"], row["date_value"])) for row in rows]
    if (
        current is not None
        and current.workshop_key
        and current.workshop_key not in {row["key"] for row in rows}
    ):
        label = row_label(current.workshop_topic or current.workshop_key, current.workshop_date)
        choices.append((current.workshop_key, f"{label} (nie ma go już w harmonogramie)"))
    return choices


class MaterialForm(forms.ModelForm):
    """Pola wspólne: warsztat, tytuł, opis, adres (odnośnik) i publikacja."""

    required_css_class = REQUIRED_CSS_CLASS

    workshop = forms.ChoiceField(label="Warsztat")

    class Meta:
        model = WorkshopMaterial
        fields = ("title", "description", "url", "is_published")
        labels = {
            "title": "Tytuł",
            "description": "Opis (opcjonalnie)",
            "url": "Adres odnośnika",
            "is_published": "Opublikowany – widoczny po zalogowaniu na stronie /warsztaty/materialy/",
        }
        help_texts = {
            "title": "Np. „Nagranie zajęć” albo „Slajdy z wykładu”.",
            "description": "Kilka zdań: co jest w materiale, od której minuty zaczyna się zadanie itp.",
            "url": (
                "Tylko dla rodzaju „odnośnik”, np. nagranie niepubliczne w serwisie wideo. Adres musi "
                "zaczynać się od https://. Uwaga: odnośnik zna każdy, kto go dostanie – serwis wymaga "
                "logowania tylko do tego, żeby go zobaczyć."
            ),
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, rows: list[dict], **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = rows
        self.fields["workshop"].choices = workshop_choices(
            rows, current=self.instance if self.instance.pk else None
        )
        if self.instance.pk:
            self.fields["workshop"].initial = self.instance.workshop_key
            if self.instance.kind != MaterialKind.LINK:
                del self.fields["url"]

    def selected_row(self) -> dict:
        """Wiersz harmonogramu wybrany w formularzu albo – przy „osieroconym” – migawka materiału."""
        key = self.cleaned_data["workshop"]
        row = next((row for row in self.rows if row["key"] == key), None)
        if row is not None:
            return row
        return {
            "key": self.instance.workshop_key,
            "topic": self.instance.workshop_topic,
            "date_value": self.instance.workshop_date,
        }

    def clean_url(self):
        url = (self.cleaned_data.get("url") or "").strip()
        if url and urlsplit(url).scheme != "https":
            raise forms.ValidationError("Adres musi zaczynać się od https://.", code="insecure")
        # Edycja odnośnika: pole w modelu jest opcjonalne (film i plik go nie mają), więc bez tej
        # reguły dałoby się zapisać odnośnik bez adresu – czyli przycisk prowadzący donikąd.
        if not url and self.instance.pk and self.instance.kind == MaterialKind.LINK:
            raise forms.ValidationError("Podaj adres odnośnika.", code="required")
        return url

    def clean_is_published(self):
        # Odrzucony materiał (zagrożenie w pliku) nie ma już treści – publikacja z formularza
        # edycji byłaby tym samym, czego nie pozwala przycisk „Opublikuj” na liście.
        published = self.cleaned_data.get("is_published", False)
        if published and self.instance.status == MaterialStatus.REJECTED:
            raise forms.ValidationError("Odrzuconego materiału nie da się opublikować.", code="rejected")
        return published


class NewMaterialForm(MaterialForm):
    """Nowy materiał – z wyborem rodzaju. Odnośnik zapisuje się tym formularzem od razu."""

    kind = forms.ChoiceField(
        label="Rodzaj",
        choices=MaterialKind.choices,
        initial=MaterialKind.VIDEO,
        widget=forms.RadioSelect,
    )
    #: Pole pliku istnieje wyłącznie dla przeglądarki (skrypt czyta z niego plik i wysyła go
    #: częściami do magazynu). Serwer nigdy go nie dostaje – formularz jest wysyłany bez niego.
    file = forms.FileField(
        label="Plik",
        required=False,
        widget=forms.FileInput(attrs={"accept": f"{VIDEO_ACCEPT},{FILE_ACCEPT}", "data-upload-file": ""}),
    )

    field_order = ("workshop", "kind", "title", "description", "file", "url", "is_published")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limity z ustawień (``WORKSHOP_VIDEO_MAX_MB``, ``WORKSHOP_FILE_MAX_MB``) czytane przy każdym
        # formularzu, a nie przy imporcie modułu – podpowiedź ma mówić to samo, co sprawdzi serwer.
        video_mb = formats.video_max_bytes() // formats.MEGABYTE
        file_mb = formats.file_max_bytes() // formats.MEGABYTE
        self.fields["file"].help_text = (
            f"Film: MP4 (H.264 + AAC) albo WebM, najwyżej {video_mb} MB. "
            f"Plik: {formats.allowed_file_extensions()}, najwyżej {file_mb} MB. "
            "Format sprawdzamy po treści pliku, nie po rozszerzeniu."
        )

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        if kind == MaterialKind.LINK and not cleaned.get("url"):
            self.add_error("url", "Podaj adres odnośnika.")
        if kind in (MaterialKind.VIDEO, MaterialKind.FILE) and cleaned.get("url"):
            self.add_error("url", "Adres podaje się wyłącznie dla rodzaju „odnośnik”.")
        return cleaned


class UploadStartForm(NewMaterialForm):
    """„Rozpocznij wgrywanie” – ten sam formularz plus deklaracja pliku od skryptu przeglądarki."""

    filename = forms.CharField(max_length=255)
    size = forms.IntegerField(min_value=1)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == MaterialKind.LINK:
            raise forms.ValidationError("Odnośnik zapisuje się zwykłym formularzem, bez wgrywania.")
        return cleaned
