"""Formularze edytora testów online (panel koordynatora).

Formularze opisują **kształt danych** i nic ponadto: reguły zależne od stanu zawodów (czy do testu
są już podejścia, czy etap ma formę testu) mieszkają w ``services``. Podział jest ten sam, co
w ``apps.web.forms``, i z tego samego powodu: błąd o kształcie ma stanąć pod polem, zanim żądanie
dojdzie do serwisu, a błąd o stanie ma wrócić komunikatem, bo nie da się go naprawić w formularzu.

Jedna decyzja wymaga uzasadnienia, bo wygląda na uproszczenie, a jest wyborem:

**Warianty odpowiedzi wpisuje się w pole tekstowe, po jednym w wierszu, z gwiazdką przy
poprawnym** – zamiast zestawu pól dokładanych przyciskiem „dodaj wariant”. Powody, w kolejności
wagi: (1) dokładanie pól wymaga JavaScriptu, a panel ma działać bez niego – inaczej pytanie
z pięcioma wariantami byłoby nie do ułożenia na komputerze z zablokowanymi skryptami w szkolnej
pracowni; (2) to jest dokładnie ta sama składnia, co w imporcie z pliku (``apps.quiz.imports``),
więc koordynator uczy się jej raz i może wkleić fragment przygotowanego arkusza wprost w pole;
(3) zmiana kolejności wariantów albo poprawka literówki w trzech naraz to edycja tekstu, a nie
przeklikiwanie pięciu pól.
"""

from __future__ import annotations

from django import forms

from apps.web.forms import LocalDateTimeField

from .grading import ALL_OR_NOTHING, PROPORTIONAL
from .models import (
    DEFAULT_DURATION_MINUTES,
    NegativeFloor,
    QuestionKind,
    Quiz,
    QuizQuestion,
    ShowResultsAfter,
)

#: Znak, którym w polu wariantów oznacza się odpowiedź poprawną. Ten sam, co w imporcie CSV –
#: jeden znak do zapamiętania zamiast dwóch konwencji na dwóch ekranach.
CORRECT_MARK = "*"


class QuizSettingsForm(forms.ModelForm):
    """Ustawienia testu jednego etapu.

    Terminy są opcjonalne i **puste znaczy „jak etap”** – to jest reguła z ``Quiz.window``,
    powtórzona tutaj wyłącznie w ``help_text``, żeby koordynator nie musiał jej odgadywać
    z pustego pola. Kopiowania dat etapu do tych pól na starcie celowo nie ma: skopiowana data
    przestałaby podążać za zmianą terminu etapu, a nikt by tego nie zauważył.
    """

    opens_at = LocalDateTimeField(
        label="Otwarcie testu",
        required=False,
        help_text="Puste = test otwiera się razem z etapem.",
    )
    closes_at = LocalDateTimeField(
        label="Zamknięcie testu",
        required=False,
        help_text="Puste = test zamyka się razem z terminem etapu.",
    )

    class Meta:
        model = Quiz
        fields = (
            "title",
            "instructions",
            "duration_minutes",
            "opens_at",
            "closes_at",
            "attempts_allowed",
            "shuffle_questions",
            "shuffle_options",
            "questions_per_attempt",
            "show_results_after",
            "negative_floor",
        )
        labels = {
            "title": "Tytuł testu",
            "instructions": "Instrukcja dla uczestnika",
            "duration_minutes": "Czas trwania (minuty)",
            "attempts_allowed": "Liczba podejść",
            "shuffle_questions": "Losowa kolejność pytań",
            "shuffle_options": "Losowa kolejność wariantów odpowiedzi",
            "questions_per_attempt": "Pytań losowanych z każdej puli",
            "show_results_after": "Kiedy pokazać wynik uczestnikowi",
            "negative_floor": "Podłoga punktów ujemnych",
        }
        help_texts = {
            "instructions": "Widoczna na stronie startowej, przed rozpoczęciem podejścia.",
            "duration_minutes": "Licznik jednego podejścia. Zamknięcie testu i tak kończy je wcześniej.",
            "questions_per_attempt": "Puste = wszystkie pytania testu. Pule nadajesz przy pytaniach.",
        }
        widgets = {
            "instructions": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duration_minutes"].initial = DEFAULT_DURATION_MINUTES
        self.fields["show_results_after"].initial = ShowResultsAfter.AFTER_CLOSE
        self.fields["negative_floor"].initial = NegativeFloor.QUESTION


class QuestionForm(forms.Form):
    """Jedno pytanie razem z kluczem odpowiedzi – cztery rodzaje w jednym formularzu.

    Jeden formularz, a nie cztery: rodzaj pytania bywa zmieniany **w trakcie** układania („to
    jednak powinna być liczba, nie wybór”), a cztery osobne ekrany znaczyłyby przepisywanie treści
    pytania przy każdej takiej zmianie. Pola nieistotne dla wybranego rodzaju są ignorowane przy
    zapisie (``clean`` czyści je do ``None``), a szablon je chowa – ale **tylko wizualnie**,
    bo o tym, co trafia do bazy, rozstrzyga ``grading.validate_question_settings``, a nie CSS.
    """

    kind = forms.ChoiceField(label="Rodzaj pytania", choices=QuestionKind.choices)
    pool = forms.CharField(
        label="Pula",
        required=False,
        max_length=40,
        help_text="Temat, z którego losowany jest zestaw. Puste = pula domyślna.",
    )
    order = forms.IntegerField(label="Kolejność", min_value=0, max_value=999, initial=0)
    text = forms.CharField(label="Treść pytania", widget=forms.Textarea(attrs={"rows": 4}))
    image = forms.ImageField(label="Ilustracja", required=False)
    clear_image = forms.BooleanField(label="Usuń ilustrację", required=False)
    points = forms.DecimalField(label="Punkty", min_value=0.01, max_value=100, decimal_places=2, initial=1)
    negative_points = forms.DecimalField(
        label="Punkty ujemne za błędną odpowiedź",
        min_value=0,
        max_value=100,
        decimal_places=2,
        initial=0,
        help_text="Odejmowane za błędną odpowiedź. Zero = bez kary. Brak odpowiedzi nie jest karany.",
    )
    options = forms.CharField(
        label="Warianty odpowiedzi",
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=f"Po jednym w wierszu. Gwiazdka „{CORRECT_MARK}” na początku oznacza wariant poprawny.",
    )
    partial_credit = forms.ChoiceField(
        label="Ocena częściowa (wielokrotny wybór)",
        required=False,
        choices=(
            (ALL_OR_NOTHING, "wszystko albo nic"),
            (PROPORTIONAL, "proporcjonalnie do trafionych wariantów"),
        ),
        initial=ALL_OR_NOTHING,
    )
    accepted = forms.CharField(
        label="Uznawane odpowiedzi",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Po jednej w wierszu. Wystarczy zgodność z którąkolwiek.",
    )
    fold_case = forms.BooleanField(label="Ignoruj wielkość liter", required=False, initial=True)
    fold_whitespace = forms.BooleanField(label="Ignoruj nadmiarowe spacje", required=False, initial=True)
    fold_diacritics = forms.BooleanField(label="Ignoruj polskie znaki", required=False, initial=True)
    answer = forms.CharField(label="Poprawna wartość", required=False, max_length=40)
    tolerance_abs = forms.CharField(
        label="Tolerancja bezwzględna",
        required=False,
        max_length=40,
        help_text="Np. 0,05 – odpowiedź uznana, gdy różni się najwyżej o tyle.",
    )
    tolerance_rel = forms.CharField(
        label="Tolerancja względna",
        required=False,
        max_length=40,
        help_text="Ułamek, np. 0,01 = 1%. Działa **obok** bezwzględnej: wystarczy zmieścić się w jednej.",
    )
    unit = forms.CharField(
        label="Jednostka",
        required=False,
        max_length=20,
        help_text="Podpis obok pola odpowiedzi. Jednostki nie sprawdzamy – uczestnik jej nie wpisuje.",
    )

    def clean(self):
        """Sprawdzenie kompletności klucza odpowiedzi **dla wybranego rodzaju** pytania.

        Walidacja jest tutaj, a nie w ``clean_<pole>``, bo żadne z tych pól nie jest wymagane
        samo z siebie – wymagane robi je dopiero wybór rodzaju. Sam klucz składa i normalizuje
        ``grading.validate_question_settings`` przy zapisie; tutaj sprawdzamy wyłącznie to, co
        pozwala postawić komunikat **pod właściwym polem**, zamiast wracać ogólnym błędem.
        """
        data = super().clean()
        kind = data.get("kind")
        if kind in QuestionKind.choice_kinds():
            lines = parse_option_lines(data.get("options") or "")
            if len(lines) < 2:
                self.add_error("options", "Podaj co najmniej dwa warianty – po jednym w wierszu.")
            elif not any(item["is_correct"] for item in lines):
                self.add_error(
                    "options", f"Oznacz poprawny wariant gwiazdką „{CORRECT_MARK}” na początku wiersza."
                )
            elif kind == QuestionKind.SINGLE_CHOICE and sum(item["is_correct"] for item in lines) > 1:
                self.add_error("options", "Pytanie jednokrotnego wyboru ma dokładnie jeden poprawny wariant.")
        elif kind == QuestionKind.SHORT_TEXT:
            if not [line for line in (data.get("accepted") or "").splitlines() if line.strip()]:
                self.add_error("accepted", "Podaj co najmniej jedną uznawaną odpowiedź.")
        elif kind == QuestionKind.NUMERIC and not (data.get("answer") or "").strip():
            self.add_error("answer", "Podaj poprawną wartość liczbową.")
        return data

    def question_fields(self) -> dict:
        """Pola modelu ``QuizQuestion`` – bez klucza odpowiedzi, który idzie osobno."""
        return {
            name: self.cleaned_data[name]
            for name in ("pool", "order", "kind", "text", "points", "negative_points")
        }

    def option_rows(self) -> list[dict]:
        """Warianty w kształcie, jakiego oczekuje ``services.save_question``."""
        return parse_option_lines(self.cleaned_data.get("options") or "")

    def settings(self) -> dict:
        """Surowy klucz odpowiedzi dla wybranego rodzaju. Normalizuje go dopiero ``grading``."""
        kind = self.cleaned_data.get("kind")
        if kind == QuestionKind.MULTIPLE_CHOICE:
            return {"partial_credit": self.cleaned_data.get("partial_credit") or ALL_OR_NOTHING}
        if kind == QuestionKind.SHORT_TEXT:
            return {
                "accepted": [
                    line.strip()
                    for line in (self.cleaned_data.get("accepted") or "").splitlines()
                    if line.strip()
                ],
                "fold_case": self.cleaned_data.get("fold_case", False),
                "fold_whitespace": self.cleaned_data.get("fold_whitespace", False),
                "fold_diacritics": self.cleaned_data.get("fold_diacritics", False),
            }
        if kind == QuestionKind.NUMERIC:
            return {
                "answer": self.cleaned_data.get("answer"),
                "tolerance_abs": self.cleaned_data.get("tolerance_abs") or "0",
                "tolerance_rel": self.cleaned_data.get("tolerance_rel") or "0",
                "unit": self.cleaned_data.get("unit") or "",
            }
        return {}


def parse_option_lines(text: str) -> list[dict]:
    """Wiersze pola „Warianty odpowiedzi” na listę ``{"text": …, "is_correct": …}``.

    Funkcja jest wspólna dla walidacji i dla zapisu, żeby „co uznaliśmy za poprawne przy
    sprawdzaniu formularza” i „co zapisaliśmy” nie mogły się rozjechać o jedną spację.
    """
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_correct = stripped.startswith(CORRECT_MARK)
        rows.append({"text": stripped.lstrip(CORRECT_MARK).strip(), "is_correct": is_correct})
    return [row for row in rows if row["text"]]


def format_option_lines(question: QuizQuestion) -> str:
    """Odwrotność ``parse_option_lines`` – wariant zapisany w bazie z powrotem do pola formularza."""
    return "\n".join(
        f"{CORRECT_MARK}{option.text}" if option.is_correct else option.text
        for option in question.options.all()
    )


def initial_from_question(question: QuizQuestion) -> dict:
    """Stan formularza dla pytania już zapisanego – klucz odpowiedzi rozpakowany z ``settings``.

    Odwzorowanie jest w jedną stronę świadomie „szerokie”: wypełniamy pola **wszystkich** rodzajów
    tym, co dla danego pytania istnieje. Dzięki temu zmiana rodzaju w formularzu nie gubi tego, co
    koordynator wpisał wcześniej – a gubiłaby, gdyby początkowe wartości były przycięte do rodzaju
    zapisanego w bazie.
    """
    settings = question.settings if isinstance(question.settings, dict) else {}
    return {
        "kind": question.kind,
        "pool": question.pool,
        "order": question.order,
        "text": question.text,
        "points": question.points,
        "negative_points": question.negative_points,
        "options": format_option_lines(question),
        "partial_credit": settings.get("partial_credit", ALL_OR_NOTHING),
        "accepted": "\n".join(settings.get("accepted", [])),
        "fold_case": settings.get("fold_case", True),
        "fold_whitespace": settings.get("fold_whitespace", True),
        "fold_diacritics": settings.get("fold_diacritics", True),
        "answer": settings.get("answer", ""),
        "tolerance_abs": settings.get("tolerance_abs", ""),
        "tolerance_rel": settings.get("tolerance_rel", ""),
        "unit": settings.get("unit", ""),
    }


class QuestionImportForm(forms.Form):
    """Wczytanie pytań z pliku albo z wklejonego tekstu.

    Dwie drogi wejścia, bo tak wygląda praca: plik przychodzi od autora pytań, a wklejenie jest
    szybsze przy poprawianiu trzech pytań naraz. Plik wygrywa, gdy podano oba – to on jest
    czynnością wykonaną świadomie (wybór z dysku), a tekst bywa resztką po poprzedniej próbie.

    Formatu **nie zgadujemy** z rozszerzenia: plik ``.txt`` bywa i jednym, i drugim, a pomyłka
    kosztowałaby czterdziestoma pytaniami wczytanymi jako jedno.
    """

    fmt = forms.ChoiceField(
        label="Format", choices=(("markdown", "Markdown"), ("csv", "CSV")), initial="markdown"
    )
    upload = forms.FileField(label="Plik z pytaniami", required=False)
    text = forms.CharField(
        label="albo wklej treść",
        required=False,
        widget=forms.Textarea(attrs={"rows": 12}),
    )
    replace = forms.BooleanField(
        label="Zastąp dotychczasowe pytania",
        required=False,
        help_text="Bez zaznaczenia wczytane pytania dopisują się do istniejących.",
    )

    #: Górna granica wczytywanego pliku. Plik z pytaniami to kilkadziesiąt kilobajtów tekstu;
    #: wszystko powyżej jest pomyłką przy wyborze pliku, a nie testem.
    MAX_UPLOAD_BYTES = 512 * 1024

    def clean(self):
        data = super().clean()
        upload = data.get("upload")
        if upload is not None:
            if upload.size > self.MAX_UPLOAD_BYTES:
                self.add_error("upload", "Plik jest za duży (maks. 512 kB).")
                return data
            try:
                data["text"] = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                # Najczęstsza przyczyna: arkusz zapisany „jako CSV” w domyślnym kodowaniu Windows.
                # Komunikat mówi, co zrobić, a nie czym jest UTF-8.
                self.add_error(
                    "upload",
                    "Nie udało się odczytać pliku. Zapisz go w kodowaniu UTF-8 "
                    "(w arkuszu: „CSV UTF-8”) i spróbuj ponownie.",
                )
                return data
        if not (data.get("text") or "").strip():
            self.add_error("text", "Wskaż plik albo wklej treść pytań.")
        return data
