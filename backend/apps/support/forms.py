"""Formularze zgłoszeń: nowe zgłoszenie (z kontem i bez) oraz odpowiedź w wątku.

Formularz nowego zgłoszenia jest **jeden** dla obu przypadków, a różnicę robi ``anonymous``:
osoba bez konta dostaje dodatkowo pole adresu e-mail i blok antyspamowy (CAPTCHA, pułapka,
próg czasu) – dokładnie ten sam, co formularz rejestracji. Dwa osobne formularze rozjechałyby
się przy pierwszej zmianie listy kategorii, a to ona decyduje o podziale kolejki koordynatora.

Dlaczego blok antyspamowy dokłada się dynamicznie, a nie przez dwie klasy: ``CaptchaFormMixin``
tworzy swoje pola w ``__init__``, więc klasa dziedzicząca po nim ma je **zawsze**. Zgłoszenie od
zalogowanego uczestnika nie ma po co przechodzić przez obrazek – konto jest już tym kosztem,
którego CAPTCHA broni, a ktoś, kto właśnie nie może wysłać pracy, nie ma przepisywać działania
arytmetycznego.
"""

from __future__ import annotations

from django import forms

from apps.web.captcha import CaptchaFormMixin

from .models import MAX_BODY_LENGTH, SupportCategory

#: Klasa CSS markera pola obowiązkowego – ta sama, co w formularzach rejestracji.
REQUIRED_CSS_CLASS = "required"


class SupportTicketForm(forms.Form):
    """Nowe zgłoszenie. Pole ``page_url`` jest ukryte i wypełnia je szablon adresem strony.

    ``page_url`` przychodzi od klienta i tak jest traktowane: ląduje w kontekście zgłoszenia jako
    **napis**, nigdy jako adres do przekierowania ani do zbudowania odnośnika. Jest po to, żeby
    organizator wiedział, z której strony przyszła sprawa – „nie działa przycisk” bez adresu jest
    zagadką, a nie zgłoszeniem.
    """

    required_css_class = REQUIRED_CSS_CLASS

    category = forms.ChoiceField(
        label="Czego dotyczy sprawa",
        choices=SupportCategory.choices,
        initial=SupportCategory.OTHER,
    )
    subject = forms.CharField(label="Temat", max_length=200)
    body = forms.CharField(
        label="Opisz, co się dzieje",
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=(
            "Napisz, co próbowałeś zrobić i co się stało. Jeśli widziałeś komunikat błędu – "
            "przepisz go. Adres strony i przeglądarkę dołączamy automatycznie."
        ),
    )
    page_url = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, anonymous: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.anonymous = anonymous
        if anonymous:
            # Adres zwrotny stoi **przed** tematem: to pierwsza rzecz, której brak unieważnia
            # całe zgłoszenie (nie ma dokąd odpowiedzieć), a nie kolejne pole opisu sprawy.
            self.fields["email"] = forms.EmailField(
                label="Twój adres e-mail",
                max_length=254,
                help_text="Na ten adres odpowie organizator. Zgłoszenie nie zakłada konta.",
            )
            self.order_fields(["email", "category", "subject", "body"])


class AnonymousSupportTicketForm(CaptchaFormMixin, SupportTicketForm):
    """Zgłoszenie **bez konta** – z blokiem antyspamowym rejestracji.

    Ten sam zestaw warstw, co przy zakładaniu konta (CAPTCHA, pułapka ``website``, minimalny czas
    wypełniania), bo to ten sam problem: publiczny formularz, który wysyła list. Bez niego adres
    organizatora byłby wysyłaczem spamu dla każdego skryptu, który go znajdzie.

    ``CaptchaFormMixin`` stoi pierwszy na liście baz – jego ``clean()`` zdejmuje pola pomocnicze
    z ``cleaned_data`` po tym, jak reszta łańcucha zrobi swoje.
    """

    def __init__(self, *args, **kwargs):
        kwargs["anonymous"] = True
        super().__init__(*args, **kwargs)


class SupportReplyForm(forms.Form):
    """Dopisek do wątku – jedno pole, bo to jedno zdanie do już otwartej sprawy."""

    required_css_class = REQUIRED_CSS_CLASS

    body = forms.CharField(
        label="Twoja odpowiedź",
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 5}),
    )


class CoordinatorReplyForm(SupportReplyForm):
    """Odpowiedź organizatora razem z ewentualnym zamknięciem sprawy.

    Zamknięcie jest polem wyboru przy odpowiedzi, a nie osobnym przyciskiem obok: koordynator
    rozstrzyga o jednym i drugim w tej samej chwili („odpisuję i uważam sprawę za załatwioną”),
    a dwa osobne kliknięcia kończyłyby się sprawami odpowiedzianymi i nigdy niezamkniętymi.
    """

    close = forms.BooleanField(label="Zamknij zgłoszenie po wysłaniu odpowiedzi", required=False)
