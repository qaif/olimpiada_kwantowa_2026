"""Formularze forum: nowy wątek, odpowiedź, poprawka, zgłoszenie wpisu i ekrany koordynatora.

Formularze są tu **cienkie** i tak ma zostać: sprawdzają kształt (długość, obowiązkowość,
zamkniętą listę wyborów), a o tym, czy wolno napisać wpis i w jakim stanie się on urodzi,
rozstrzyga ``apps.forum.services``. Dwie kopie tej reguły – w formularzu i w serwisie – znaczyłyby
dwa zdania o tym, kiedy forum jest zamknięte, i jedno z nich byłoby nieaktualne.

Kategorię w formularzu nowego wątku zawęża **wołający** (widok), podając queryset z
``ForumCategory.objects.for_competition``. Pole z pełną listą kategorii byłoby listą działów
wszystkich konkursów w instalacji – czyli wyciekiem widocznym w źródle strony.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy

from .models import (
    MAX_POST_LENGTH,
    MAX_REASON_LENGTH,
    MAX_TITLE_LENGTH,
    ModerationMode,
    NotificationFrequency,
)

#: Klasa CSS markera pola obowiązkowego – ta sama, co w pozostałych formularzach serwisu.
REQUIRED_CSS_CLASS = "required"


class PostForm(forms.Form):
    """Jedna wypowiedź. Używana przy odpowiedzi i przy poprawce własnego wpisu."""

    required_css_class = REQUIRED_CSS_CLASS

    body = forms.CharField(
        label="Treść",
        max_length=MAX_POST_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=(
            "Zwykły tekst. Odnośniki zamieniamy na klikalne same – znaczników HTML i załączników "
            "forum nie przyjmuje."
        ),
    )


class ThreadForm(PostForm):
    """Nowy wątek: temat, dział i pierwsza wypowiedź."""

    title = forms.CharField(label="Temat", max_length=MAX_TITLE_LENGTH)
    category = forms.ModelChoiceField(label="Dział", queryset=None, empty_label=None)

    def __init__(self, *args, categories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = categories
        self.order_fields(["category", "title", "body"])


class ReportForm(forms.Form):
    """„Zgłoś wpis” – jedno pole, bo zgłoszenie jest jednym zdaniem do moderatora."""

    required_css_class = REQUIRED_CSS_CLASS

    reason = forms.CharField(
        label="Dlaczego zgłaszasz ten wpis",
        max_length=MAX_REASON_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Napisz krótko, co jest nie tak. Zgłoszenie widzi wyłącznie organizator.",
    )


class ModerationNoteForm(forms.Form):
    """Uzasadnienie odrzucenia. Obowiązkowość rozstrzyga serwis – tu jest wyłącznie limit."""

    note = forms.CharField(label="Uzasadnienie", max_length=MAX_REASON_LENGTH, required=False)


class ForumSettingsForm(forms.Form):
    """Ustawienia forum konkursu: tryb moderacji i przełącznik „tylko do odczytu”."""

    required_css_class = REQUIRED_CSS_CLASS

    mode = forms.ChoiceField(
        label="Tryb moderacji",
        choices=ModerationMode.choices,
        widget=forms.RadioSelect,
        help_text=(
            "Przed publikacją: wpis widzą inni dopiero po Twoim zatwierdzeniu. Po publikacji: "
            "wpis jest widoczny od razu, a Ty możesz go ukryć. W czasie otwartego etapu "
            "obowiązuje tryb „przed publikacją” niezależnie od tego ustawienia."
        ),
    )
    is_read_only = forms.BooleanField(
        label="Forum tylko do odczytu",
        required=False,
        help_text="Rozmowy zostają do przeczytania, ale nikt nie doda nowego wpisu.",
    )


class NotificationSettingsForm(forms.Form):
    """Ustawienia powiadomień e-mail z forum na ekranie „Edycja danych”.

    W odróżnieniu od reszty formularzy forum etykiety **są tłumaczone**: stoją na ekranie konta,
    który ma wersję angielską (``apps.accounts.preferences`` – zakres tłumaczenia), a nie na
    ekranie forum. Pole listów o kolejce moderacji dostaje tylko koordynator – u pozostałych kont
    nie ma czego ustawiać, bo tych listów i tak nie dostają.
    """

    frequency = forms.ChoiceField(
        label=gettext_lazy("Listy o obserwowanych wątkach i decyzjach moderatora"),
        choices=(
            (
                NotificationFrequency.IMMEDIATE,
                gettext_lazy("na bieżąco (o jednym wątku najwyżej co kilka godzin)"),
            ),
            (NotificationFrequency.DAILY, gettext_lazy("raz dziennie – jedno podsumowanie")),
            (NotificationFrequency.NEVER, gettext_lazy("nigdy")),
        ),
        widget=forms.RadioSelect,
    )
    moderation_digest = forms.BooleanField(
        label=gettext_lazy("Listy o wpisach czekających na moderację"),
        required=False,
    )

    def __init__(self, *args, moderator: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if not moderator:
            del self.fields["moderation_digest"]


class CategoryForm(forms.Form):
    """Dział forum. Identyfikatora w adresie nie ma w formularzu – nadaje go serwis z nazwy."""

    required_css_class = REQUIRED_CSS_CLASS

    name = forms.CharField(label="Nazwa działu", max_length=120)
    description = forms.CharField(label="Opis", max_length=300, required=False)
    ordering = forms.IntegerField(label="Kolejność", min_value=0, max_value=9999, initial=100)
    is_open = forms.BooleanField(label="Otwarty na nowe wątki", required=False, initial=True)
