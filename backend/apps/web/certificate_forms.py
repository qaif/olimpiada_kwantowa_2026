"""Formularze szablonów dyplomów i obecności na warsztatach.

Osobny moduł od ``apps.web.forms`` z tego samego powodu, co ``supervisor_forms``: to jest komplet
formularzy jednej funkcji panelu, czyta się je razem z jej widokami, a wspólny plik formularzy
serwisu ma już kilkaset linii i rośnie przy każdej nowej funkcji niezależnie od pozostałych.
"""

from __future__ import annotations

import csv
import io

from django import forms

from apps.competitions.models import Edition
from apps.results.certificate_layout import default_certificate_layout
from apps.results.models import CertificateTemplate

#: Ile wierszy importu obecności przyjmujemy z jednego pliku. Warsztaty prowadzi się dla całej
#: edycji, więc kilka tysięcy par „kod, warsztat” jest realne; sto tysięcy to pomyłka w pliku.
MAX_IMPORT_ROWS = 20000


class CertificateTemplateForm(forms.ModelForm):
    """Dodanie i zmiana szablonu graficznego dokumentu.

    ``layout`` zostaje polem JSON w polu tekstowym, bez edytora graficznego. To jest świadomy
    wybór zakresu: edytor „przeciągnij napis po kartce” jest osobną funkcją wielkości tej całej
    strony, a problem organizatora brzmi „chcę wgrać kartę z drukarni i przesunąć nazwisko niżej”.
    Na to wystarcza słownik z podpowiedzianą wartością domyślną i podgląd PDF obok formularza.

    Walidacja kształtu układu jest tutaj, a nie w modelu: to formularz stoi między człowiekiem
    a bazą, a dane wpisane z panelu są jedyną drogą, którą do tej kolumny trafia cokolwiek.
    """

    class Meta:
        model = CertificateTemplate
        fields = (
            "name",
            "kind",
            "edition",
            "background",
            "logo",
            "signature_1_image",
            "signature_1_name",
            "signature_1_title",
            "signature_2_image",
            "signature_2_name",
            "signature_2_title",
            "signature_3_image",
            "signature_3_name",
            "signature_3_title",
            "layout",
            "is_active",
        )
        widgets = {"layout": forms.Textarea(attrs={"rows": 16, "spellcheck": "false"})}

    def __init__(self, *args, competition=None, **kwargs):
        """``competition`` zawęża listę edycji do **tego** konkursu.

        Lista wyboru jest tu bramką równie realną, co queryset widoku: ``ModelChoiceField``
        odrzuca wartość spoza swojego querysetu, więc szablon nie da się przypiąć do cudzej
        edycji nawet żądaniem złożonym ręcznie. Argument jest nazwany i domyślnie pusty, bo ten
        sam formularz bywa budowany bez żądania (``/admin/``, testy jednostkowe).
        """
        super().__init__(*args, **kwargs)
        # Puste znaczy „wszystkie rodzaje” i „wszystkie edycje” – etykieta musi to powiedzieć,
        # bo domyślne „---------” czyta się jak „nie wybrano” i wygląda na błąd formularza.
        self.fields["kind"].choices = [("", "wszystkie rodzaje")] + list(self.fields["kind"].choices)[1:]
        self.fields["edition"].empty_label = "wszystkie edycje"
        editions = Edition.objects.order_by("-created_at", "-id")
        if competition is not None:
            editions = editions.for_competition(competition).order_by("-created_at", "-id")
        self.fields["edition"].queryset = editions
        if not self.instance.pk and not self.initial.get("layout"):
            self.initial["layout"] = default_certificate_layout()

    def clean_layout(self):
        """Układ musi być słownikiem słowników – tyle, ile sprawdza skład, i ani reguły więcej.

        Pojedynczych kluczy nie zamykamy listą z premedytacją: ``apps.results.certificate_layout``
        scala szablon z wartościami domyślnymi blok po bloku, więc klucz nieznany jest po prostu
        ignorowany, a brakujący – uzupełniany. Odrzucanie ich tutaj znaczyłoby, że dodanie bloku
        w kodzie unieważnia szablony zapisane wcześniej.
        """
        layout = self.cleaned_data.get("layout")
        if layout in (None, ""):
            return default_certificate_layout()
        if not isinstance(layout, dict):
            raise forms.ValidationError('Układ musi być obiektem JSON, np. {"title": {"y": 190}}.')
        for name, settings in layout.items():
            if not isinstance(settings, dict):
                raise forms.ValidationError(
                    f'Blok „{name}” musi być obiektem z ustawieniami, np. {{"y": 190, "size": 28}}.'
                )
        return layout


class WorkshopAttendanceImportForm(forms.Form):
    """Import obecności z pliku CSV ``kod,warsztat``.

    Po co, skoro jest tabela z kratkami. Bo obecność na warsztatach online **powstaje gdzie
    indziej**: listę uczestników spotkania eksportuje platforma wideo, a nie panel olimpiady.
    Przepisywanie trzystu kratek ręcznie po każdych zajęciach jest tym rodzajem pracy, po której
    tabela obecności zostaje pusta, a zaświadczeń nikt nie wystawia.

    Plik jest **parą kolumn i niczym więcej**: kod uczestnika (ten z panelu, nie e-mail – e-mail
    jest daną osobową, która nie ma po co krążyć w plikach po dyskach szkół) oraz klucz warsztatu
    z tabeli obecności. Nagłówek jest opcjonalny i rozpoznawany po pierwszej komórce.
    """

    file = forms.FileField(label="Plik CSV (kod, warsztat)")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError(
                "Plik nie jest tekstem w UTF-8. Zapisz go z arkusza jako „CSV UTF-8”."
            ) from exc
        rows = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 2:
                continue
            code, key = row[0].strip(), row[1].strip()
            if not code or not key or code.lower() in {"kod", "code"}:
                continue
            rows.append((code.upper(), key))
            if len(rows) > MAX_IMPORT_ROWS:
                raise forms.ValidationError(
                    f"Plik ma więcej niż {MAX_IMPORT_ROWS} wierszy – to wygląda na pomyłkę."
                )
        if not rows:
            raise forms.ValidationError(
                "W pliku nie ma ani jednego wiersza „kod,warsztat”. Sprawdź, czy kolumny są w tej kolejności."
            )
        self.rows = rows
        return upload
