"""Formularze narzędzi koordynatora: komunikaty, audyt, symulacja, jakość, dyplomy, zgody i dokumenty.

Osobny moduł od ``apps.web.forms`` z jednego powodu: tamten plik zbiera formularze, które mają
w serwisie po kilku czytelników (rejestracja, profil, oceny, etapy) i są częścią kontraktu
z API. Te obsługują po jednym ekranie każdy, nie mają odpowiednika w API i nigdy nie będą
wołane spoza ``apps.web.views.coordinator_reports``, ``…coordinator_messages``,
``…coordinator_quality``, ``…coordinator_consents``, ``…coordinator_documents``,
``…coordinator_pipeline``, ``…coordinator_categories``, ``…coordinator_regions``
i ``…coordinator_institutions`` – trzymanie ich razem z formularzem rejestracji utrudniałoby
czytanie obu.

Wspólna zasada: formularz sprawdza **kształt** danych (czy pole jest wypełnione, czy wartość
należy do zamkniętej listy, czy liczba jest dodatnia). Reguła domenowa – kto jest odbiorcą
komunikatu, czy próg da się zapisać – stoi w serwisach (``apps.accounts.messaging``,
``apps.results.simulation``) i formularz jej nie powiela.
"""

from __future__ import annotations

from django import forms

from apps.accounts.models import BroadcastGroup, ConsentDefinition, Region, RegistrationProfile
from apps.competitions.models import (
    Category,
    ManualQualification,
    QualificationMode,
    Stage,
    StageComponent,
    TieBreak,
    TransitionRule,
)
from apps.grading.models import ReviewerRole
from apps.results.models import CertificateKind
from apps.schools.custom import MIN_NAME_LENGTH, CustomInstitution
from apps.schools.models import InstitutionType
from apps.tenancy.documents import DocumentTemplate

from .forms import VOIVODESHIP_CHOICES

#: Górny limit długości komunikatu. Sto tysięcy znaków to kilkadziesiąt stron – nie jest to limit
#: redakcyjny, tylko zabezpieczenie przed wklejeniem przez pomyłkę całego dokumentu do pola treści
#: i rozesłaniem go tysiącom osób.
MAX_BODY_LENGTH = 100_000

#: Grupy, które bez wskazanego etapu nie mają sensu – ekran wymaga wtedy wyboru etapu.
STAGE_REQUIRED_GROUPS = frozenset({BroadcastGroup.STAGE_REGISTERED, BroadcastGroup.STAGE_QUALIFIED})


class BroadcastForm(forms.Form):
    """Komunikat do grupy odbiorców: grupa, doprecyzowanie grupy, temat i treść.

    Pola doprecyzowujące (etap, województwo, wklejona lista) są **opcjonalne na poziomie pola**,
    a wymagane dopiero wtedy, gdy wybrana grupa ich potrzebuje – sprawdza to ``clean``. Gdyby były
    wymagane zawsze, koordynator wysyłający list do całego komitetu musiałby wskazać etap,
    który nie ma z tym listem nic wspólnego.

    Treść jest czystym tekstem. Listy transakcyjne w tym serwisie są tekstowe (patrz
    ``apps.core.tasks.send_mail_task``), a komunikat organizatora nie ma powodu być wyjątkiem:
    HTML w poczcie masowej to dodatkowa droga do filtrów antyspamowych i do listu, który
    u połowy odbiorców wygląda inaczej niż u autora.
    """

    group = forms.ChoiceField(label="Grupa odbiorców", choices=BroadcastGroup.choices)
    stage = forms.ModelChoiceField(
        label="Etap",
        queryset=Stage.objects.none(),
        required=False,
        empty_label="— wybierz etap —",
    )
    district = forms.ChoiceField(label="Województwo", choices=VOIVODESHIP_CHOICES, required=False)
    addresses = forms.CharField(
        label="Lista adresów",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Adresy rozdzielone przecinkiem, średnikiem albo nową linią.",
    )
    subject = forms.CharField(label="Temat", max_length=200)
    body = forms.CharField(
        label="Treść",
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 12}),
    )

    def __init__(self, *args, stages=None, **kwargs):
        """``stages`` zawęża listę etapów do bieżącej edycji – innych i tak nie wolno wybrać.

        Queryset podawany z zewnątrz, a nie liczony w polu: formularz nie ma wiedzieć, która
        edycja jest bieżąca (to pytanie do ``apps.competitions.services``), a test chce móc podać
        własny zestaw etapów bez ustawiania globalnego stanu.
        """
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = stages if stages is not None else Stage.objects.none()

    def clean(self) -> dict:
        cleaned = super().clean()
        group = cleaned.get("group")
        if group in STAGE_REQUIRED_GROUPS and not cleaned.get("stage"):
            self.add_error("stage", "Ta grupa odbiorców wymaga wskazania etapu.")
        if group == BroadcastGroup.COMMITTEE_DISTRICT and not cleaned.get("district"):
            self.add_error("district", "Wybierz województwo komitetu.")
        if group == BroadcastGroup.CUSTOM and not (cleaned.get("addresses") or "").strip():
            self.add_error("addresses", "Wklej przynajmniej jeden adres.")
        return cleaned


class AuditFilterForm(forms.Form):
    """Filtr przeglądarki audytu. Wszystkie pola opcjonalne – pusty filtr znaczy „pokaż wszystko”.

    Formularz jest **wyłącznie** interfejsem: zawężenie zapytania robi ``apps.core.audit_browser``,
    który czyta surowe parametry adresu. Dzięki temu odnośnik z ręcznie dopisanym parametrem
    działa tak samo, jak wysłanie formularza, a strona nie przewraca się na wartości, której
    formularz by nie przyjął.

    Listy wyboru dostają zawartość z bazy (``choices`` podawane przy tworzeniu), bo mają
    wymieniać akcje i typy obiektów, które w tej instalacji naprawdę wystąpiły.

    Pól dat tu **nie ma** i to jest świadome. Parametry przedziału nazywają się w adresie ``from``
    i ``to`` – krótko, bo to ten adres ktoś wkleja do wiadomości, kiedy chce pokazać komuś wycinek
    audytu. ``from`` jest w Pythonie słowem kluczowym, więc pola o tej nazwie nie da się
    zadeklarować; nazwanie go inaczej znaczyłoby dłuższy parametr w adresie albo dodatkowe
    mapowanie nazw w obie strony. Oba pola są zwykłymi ``<input type="date">`` w szablonie,
    a czyta je (i wybacza literówki) ``apps.core.audit_browser.parse_date``.
    """

    actor = forms.CharField(label="Wykonawca (e-mail)", required=False, max_length=254)
    action = forms.ChoiceField(label="Akcja", required=False, choices=())
    target_type = forms.ChoiceField(label="Typ obiektu", required=False, choices=())

    def __init__(self, *args, actions=(), target_types=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [("", "— wszystkie —"), *[(name, name) for name in actions]]
        self.fields["target_type"].choices = [
            ("", "— wszystkie —"),
            *[(name, name) for name in target_types],
        ]


class SimulationForm(forms.Form):
    """Parametry symulacji progu: tryb i jego liczby.

    Pola liczbowe są opcjonalne, bo każdy tryb potrzebuje innej pary; komplet sprawdza dopiero
    ``apps.results.simulation.build_rule`` przez ``full_clean`` modelu progu. Dublowanie tej
    reguły tutaj znaczyłoby, że dołożenie kiedyś nowego trybu wymaga poprawki w dwóch miejscach,
    a rozjazd między nimi objawiłby się podglądem reguły, której nie da się zapisać.
    """

    mode = forms.ChoiceField(label="Tryb progu", choices=QualificationMode.choices)
    min_points = forms.IntegerField(label="Minimum punktów", required=False, min_value=0)
    top_n = forms.IntegerField(label="Liczba kwalifikowanych (N)", required=False, min_value=1)


class ManualQualificationForm(forms.Form):
    """Decyzja komitetu o kwalifikacji jednego wpisu: co i dlaczego.

    Oba pola są tu „miękkie” (``required=False``) z rozmysłem: pusta decyzja znaczy „zdejmij
    decyzję, niech rozstrzyga próg” i jest poprawnym żądaniem, a wymagalność uzasadnienia zależy
    od decyzji. Regułę „decyzja wymaga uzasadnienia o długości co najmniej N” trzyma serwis
    (``apps.results.manual``), bo to jest reguła domenowa, a nie kształt formularza – i musi
    obowiązywać także wejście, które kiedyś przyjdzie inną drogą.
    """

    decision = forms.ChoiceField(label="Decyzja", choices=ManualQualification.choices, required=False)
    reason = forms.CharField(
        label="Uzasadnienie",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class CertificateIssueForm(forms.Form):
    """Rodzaj wystawianego dokumentu.

    Rodzaju **nie** wyliczamy z punktów: o tym, kto jest laureatem, a kto finalistą, rozstrzyga
    komitet na posiedzeniu, a próg tytułu bywa inny niż próg kwalifikacji. Lista jest zamknięta
    wartościami z modelu, więc do bazy nie trafi tytuł, którego dokument nie umie złożyć.
    """

    kind = forms.ChoiceField(label="Rodzaj dokumentu", choices=CertificateKind.choices)


class SimilarityFilterForm(forms.Form):
    """Próg pokazywania par na ekranie podobieństw.

    Ułamek, a nie procent, bo taką wartość niesie model i taka jedzie w adresie – ekran, który
    przyjmowałby „85”, a zapisywał 0,85, wymagałby przeliczania w dwie strony przy każdym
    odnośniku. Procent pokazujemy dopiero w tabeli.
    """

    threshold = forms.FloatField(
        label="Próg podobieństwa",
        required=False,
        min_value=0,
        max_value=1,
        help_text="Ułamek z zakresu 0–1. Domyślnie 0,8.",
    )


class ConsentDefinitionForm(forms.ModelForm):
    """Treść jednej zgody konkursu: oświadczenie, odnośnik, komunikaty, kolejność, stan.

    **Czego w formularzu nie ma i dlaczego.** Rodzaj (``kind``) i nazwa pola (``field_name``) są
    tożsamością zgody – po nich poznaje ją model dowodowy, serwis rejestracji i panel uczestnika,
    więc ich zmiana z panelu rozjeżdżałaby wpisy sprzed niej z wpisami po niej. Wymagalność
    (``required``, ``required_for_minor``) jest decyzją o podstawie przetwarzania danych i nie
    zapada jednym kliknięciem w panelu (patrz ``apps.web.views.coordinator_consents``).

    **Wersja jest w formularzu, ale wyłączona.** Dwa powody, oba praktyczne: numer wersji ma być
    widoczny przy treści, której dotyczy (inaczej trzeba go szukać na liście), a ``disabled``
    sprawia, że wartość z POST-a jest ignorowana i do zapisu idzie ta z bazy. Zmiana wersji ma
    własny, dwukrokowy adres – jest zdarzeniem, a nie polem formularza.

    Sprawdzenie wzorca treści (``{link}`` i ``{organizer}``, nic więcej) robi ``clean()`` modelu
    i formularz go nie powiela: gdyby powielał, dwa miejsca musiałyby wiedzieć to samo o składni
    ``format_html`` – a to ona wywraca formularz rejestracji, gdy się pomylić.
    """

    version = forms.CharField(
        label="Wersja dokumentu",
        required=False,
        disabled=True,
        help_text="Wersję zmienia się osobno, z potwierdzeniem – patrz sekcja poniżej.",
    )

    class Meta:
        model = ConsentDefinition
        fields = (
            "text",
            "link_text",
            "document_slug",
            "help_text",
            "missing_message",
            "ordering",
            "is_active",
            "version",
        )
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "text": (
                "Miejsca do podstawienia: <code>{link}</code> – odnośnik do dokumentu, "
                "<code>{organizer}</code> – nazwa organizatora. Nawias klamrowy w treści "
                "wpisz podwójnie."
            ),
            "link_text": "Napis, którym podpisany jest odnośnik. Pusty = zgoda bez odnośnika.",
            "document_slug": "Slug strony w <code>/dokumenty/</code>. Pusty = zgoda bez dokumentu.",
            "missing_message": "Komunikat pokazywany, gdy zgoda jest wymagana, a nie zaznaczona.",
            "ordering": "Kolejność w formularzu rejestracji. Zgody wymagane mają stać przed dobrowolnymi.",
            "is_active": "Odznaczona zgoda znika z formularza. Wpisy dowodowe zostają nietknięte.",
        }


class ConsentVersionForm(forms.Form):
    """Nowa wersja dokumentu – pierwszy krok zmiany, ten, który jeszcze nic nie zapisuje.

    Jedno pole i żadnej reguły domenowej: o tym, czy wersję wolno zapisać i co przy tym trafia do
    audytu, rozstrzyga ``apps.accounts.consents.change_version``. Formularz pilnuje wyłącznie
    kształtu – wersja niepusta i mieszcząca się w kolumnie.
    """

    version = forms.CharField(
        label="Nowa wersja dokumentu",
        max_length=100,
        help_text="Napis identyfikujący dokument, np. „2.0 z 1 marca 2027”. Trafi do każdego "
        "nowego wpisu dowodowego.",
    )

    def clean_version(self) -> str:
        """Napis bez otoczki. Sama spacja nie jest wersją, a serwis i tak by ją odbił."""
        return (self.cleaned_data.get("version") or "").strip()


class DocumentTemplateForm(forms.ModelForm):
    """Nowa wersja tekstu dokumentu: numer wersji i cztery napisy z podstawieniami.

    **Formularz jest modelowy z jednego powodu** – żeby walidacja znaczników została tam, gdzie
    jest dzisiaj. ``DocumentTemplate.clean`` odrzuca ``{partcipant_code}`` komunikatem wyliczającym
    dozwolone znaczniki, i robi to tak samo dla panelu, dla komendy i dla importu (ta sama reguła,
    co przy ``ConsentDefinitionForm``). Formularz, który powtarzałby tę listę, byłby drugim
    miejscem wiedzącym to samo o składni ``str.format_map`` – a rozjazd między nimi objawiłby się
    klamrą na wydanym dyplomie.

    **Czego w formularzu nie ma.** Rodzaju (``kind``) i konkursu: rodzaj jest w adresie, a konkurs
    w kontekście żądania – gdyby były polami, dałoby się jednym POST-em przepisać tekst cudzego
    dokumentu. ``is_current`` też nie: o tym, która wersja obowiązuje, rozstrzyga
    ``apps.tenancy.documents.set_current_template`` w jednej transakcji z demotowaniem poprzedniej,
    a pole „obowiązująca” w formularzu pozwalałoby zostawić konkurs z dwiema albo z żadną.

    Wersja jest polem **wymaganym i nowym za każdym razem** – więz ``(konkurs, rodzaj, wersja)``
    pilnuje, żeby poprawka nie mogła się wślizgnąć pod numerem, który nosi już wydany dokument.
    """

    class Meta:
        model = DocumentTemplate
        fields = ("version", "title", "statement", "signature_line", "footer_note")
        widgets = {"statement": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "version": (
                "Napis identyfikujący tekst, np. „2.0 z 1 marca 2027”. Trafi do każdego dokumentu "
                "wystawionego od tej chwili i to po nim odtwarza się jego treść po latach."
            ),
            "title": "Nagłówek na papierze, np. „Dyplom laureata”.",
            "statement": "Zdanie pod nazwiskiem odbiorcy.",
            "signature_line": "Podpis pod dokumentem. Puste = dokument bez linii podpisu.",
            "footer_note": "Dopisek u dołu strony. Puste = bez dopisku.",
        }

    def clean_version(self) -> str:
        """Napis bez otoczki. Sama spacja nie jest wersją, a więz bazy i tak by ją odbił."""
        return (self.cleaned_data.get("version") or "").strip()


# =================================================================================================
# Edytor przebiegu zawodów (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2, ekrany T27)
# =================================================================================================
#
# Cztery formularze poniżej obsługują trzy ekrany za flagą ``process_editor`` (tor etapów, reguły
# przejścia kroku, komponenty etapu) i jeden za flagą ``categories``. Zasada modułu obowiązuje je
# tak samo: formularz sprawdza **kształt**, a reguła domenowa stoi w modelu (``clean()``) albo
# w widoku, który jedyny zna edycję i konkurs żądania.


class PipelineStepCreateForm(forms.Form):
    """Dopisanie kroku toru dla etapu, który jeszcze swojego kroku nie ma.

    Lista etapów jest podawana z zewnątrz (``stages``), a nie liczona w polu, z tego samego
    powodu, co w ``BroadcastForm``: formularz nie ma wiedzieć, która edycja jest bieżąca ani
    który etap ma już krok – to są dwa pytania do bazy o **konkretny** konkurs.

    ``off_pipeline`` jest tu, a nie dopiero przy edycji kroku, bo dla etapu treningowego to jest
    jedyna poprawna odpowiedź od pierwszej chwili: krok poza torem nie kwalifikuje i nie zajmuje
    miejsca w kolejce, a krok treningu wpisany na tor przesunąłby wszystkie następne etapy.
    """

    stage = forms.ModelChoiceField(
        label="Etap",
        queryset=Stage.objects.none(),
        empty_label="— wybierz etap —",
        help_text="Lista obejmuje wyłącznie etapy bieżącej edycji, które nie mają jeszcze kroku.",
    )
    off_pipeline = forms.BooleanField(
        label="Poza torem zawodów",
        required=False,
        help_text="Trening, warsztat, sesja próbna. Taki krok nie kwalifikuje i nie ma następnika.",
    )

    def __init__(self, *args, stages=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = stages if stages is not None else Stage.objects.none()


class PipelineStepForm(forms.Form):
    """Miejsce kroku w kolejce i jego udział w torze zawodów.

    Formularz jest **zwykły**, a nie modelowy, i to jest decyzja, a nie skrót. Pozycja kroku nie
    jest wartością pola, tylko **miejscem na liście**: więz ``(edycja, miejsce)`` jest unikalny,
    więc zapis „ustaw 2” bez przenumerowania reszty kończyłby się albo ``IntegrityError``, albo
    dwiema listami – tą w bazie i tą na ekranie. Przenumerowanie całej kolejki robi widok, bo
    tylko on zna edycję; formularz pilnuje wyłącznie, żeby miejsce było liczbą dodatnią.

    Miejsce większe niż długość kolejki nie jest błędem – znaczy „na koniec”. Odrzucanie go
    zmuszałoby koordynatora do liczenia kroków przed wpisaniem liczby.
    """

    position = forms.IntegerField(
        label="Miejsce w kolejce",
        min_value=1,
        help_text="1 = pierwszy etap zawodów. Większa liczba niż długość kolejki znaczy „na koniec”.",
    )
    off_pipeline = forms.BooleanField(
        label="Poza torem zawodów",
        required=False,
        help_text="Zaznaczony krok wypada z kolejki: nie kwalifikuje i nie jest niczyim następnikiem.",
    )


class TransitionRuleForm(forms.ModelForm):
    """Jedna reguła przejścia kroku: tryb, podział, kategoria i liczby trybu.

    Wszystkie trzy pola liczbowe są opcjonalne na poziomie formularza, bo każdy tryb potrzebuje
    innego kompletu – rozstrzyga o tym ``TransitionRule.clean()`` i to ta sama walidacja, która
    stoi przy zapisie i przy podglądzie (``apps.results.simulation.build_transition_rule``).
    Powtórzenie jej tutaj znaczyłoby, że dołożenie kiedyś nowego trybu wymaga poprawki w dwóch
    miejscach, a rozjazd między nimi objawiłby się podglądem reguły, której nie da się zapisać.

    Kroku (``step``) w formularzu nie ma: krok jest w adresie, a gdyby był polem, dałoby się
    jednym POST-em dopisać regułę do cudzego kroku. Lista kategorii jest podawana z zewnątrz,
    zawężona do konkursu żądania – z tego samego powodu.
    """

    class Meta:
        model = TransitionRule
        fields = ("mode", "group_by", "category", "min_points", "top_n", "percentile", "position")
        help_texts = {
            "mode": "Kilka reguł na kroku sumuje się: przechodzi ten, kto spełnia którąkolwiek.",
            "group_by": (
                "Podział pola przed zastosowaniem progu. „N w grupie” wymaga wskazania podziału; "
                "dzisiejsze „N na województwo” to ten tryb z podziałem po regionie."
            ),
            "category": "Puste = reguła dotyczy wszystkich startujących.",
            "percentile": "Procent z zakresu 1–100. Dotyczy wyłącznie trybu „najlepsze P procent”.",
            "position": "Kolejność na liście reguł kroku. Nie wpływa na wynik – reguły się sumują.",
        }

    def __init__(self, *args, categories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = categories if categories is not None else Category.objects.none()
        self.fields["category"].empty_label = "— wszystkie kategorie —"


class StageComponentForm(forms.ModelForm):
    """Jedna forma w etapie: skąd bierze punkty, z jaką wagą i czy jest obowiązkowa.

    Waga jest parą liczb, a nie ułamkiem dziesiętnym, i to jest ta sama decyzja, co w modelu
    (§ 1.2.6): ``1/3`` zapisane jako ``0,333…`` daje sumę zależną od kolejności dodawania, czyli
    tabelę wyników zmieniającą się przy przeliczeniu. Formularz pokazuje więc licznik i mianownik
    osobno – tak, jak leżą w bazie i jak liczy je ``Fraction``.

    Etapu w polach nie ma: etap jest w adresie. Reguły „mianownik dodatni” formularz nie powiela –
    stoi w ``StageComponent.clean()`` i w więzie bazy.
    """

    class Meta:
        model = StageComponent
        fields = ("kind", "name", "position", "weight_numerator", "weight_denominator", "required")
        help_texts = {
            "kind": "Źródło punktów komponentu. Etap bez ani jednego komponentu czyta formę etapu.",
            "name": "Puste = nazwą jest etykieta rodzaju. To ona staje w nagłówku kolumny wyników.",
            "position": "Kolejność kolumn w tabeli wyników.",
            "weight_numerator": "Licznik wagi. Waga 1/1 daje sumę identyczną z dzisiejszą.",
            "weight_denominator": "Mianownik wagi. Musi być dodatni.",
            "required": (
                "Wymagany komponent blokuje zamknięcie tabeli, dopóki brakuje ocen. "
                "Niewymagany liczy brak wyniku jako zero."
            ),
        }


class CategoryForm(forms.ModelForm):
    """Kategoria uczestników: kod, nazwa, kolejność, zakres klas i stan.

    Konkursu w polach nie ma – bierze się z żądania. Zakres klas jest **opcjonalny**: puste
    znaczy „kategorię wskazuje człowiek”, a nie „pasuje do wszystkich” (patrz
    ``Category.matches_grade``). Kolejność dwóch klas sprawdza ``Category.clean()``, więc
    komunikat staje pod polem „klasa do”, a nie w chmurce nad formularzem.
    """

    class Meta:
        model = Category
        fields = ("code", "name", "position", "grade_min", "grade_max", "is_active")
        help_texts = {
            "code": "Stały identyfikator do regulaminu i importu, np. „podstawowa”. Unikalny w konkursie.",
            "name": "Nazwa widoczna w tabeli wyników.",
            "position": "Kolejność kategorii na listach i w rankingach.",
            "grade_min": "Puste pola = brak reguły automatycznej; kategorię wskazuje wtedy człowiek.",
            "grade_max": "Wypełnia się razem z „klasa od” albo zostawia oba puste.",
            "is_active": "Odznaczona kategoria znika z list. Wpisy z lat poprzednich zostają nietknięte.",
        }


# --- wydanie J: wagi, remisy, drużyny, punkty z rozmowy, role recenzenckie (§ 1.2.3, 1.2.6–1.2.7) --


#: Górna granica licznika i mianownika wagi – tyle mieści ``PositiveSmallIntegerField``. Formularz
#: nazywa ją wprost, żeby przekroczenie kończyło się komunikatem pod polem, a nie błędem bazy.
MAX_WEIGHT_PART = 32767


class ProblemWeightForm(forms.Form):
    """Waga jednego zadania w sumie etapu – licznik i mianownik osobno.

    Ułamek zwykły, a nie liczba dziesiętna, i to jest decyzja modelu, nie formularza (§ 1.2.6 a):
    waga ``1/3`` zapisana jako ``0,333…`` daje sumę zależną od kolejności dodawania, czyli tabelę
    wyników zmieniającą się przy przeliczeniu.

    Formularz jest **jeden na zadanie** i dostaje własny prefiks (``p<pk>``): ekran skali pokazuje
    wszystkie zadania etapu naraz, a nazwy pól muszą je rozróżniać. Licznik wolno ustawić na zero
    (zadanie oceniane, ale nieliczone do sumy); mianownika – nie, bo to dzielenie przez zero
    w dniu ogłoszenia wyników.
    """

    weight_numerator = forms.IntegerField(label="Licznik", min_value=0, max_value=MAX_WEIGHT_PART)
    weight_denominator = forms.IntegerField(label="Mianownik", min_value=1, max_value=MAX_WEIGHT_PART)


class TieBreakForm(forms.ModelForm):
    """Jedno kryterium rozstrzygania remisów etapu: czym, w którą stronę i na którym miejscu.

    Etapu w polach nie ma – jest w adresie i wchodzi do instancji przed walidacją, żeby
    ``TieBreak.clean()`` mogło sprawdzić, czy wskazane zadanie i komponent należą do **tego**
    etapu. Bez tego reguła „zadanie z innego etapu” wypadłaby dopiero na tabeli wyników, jako
    liczba znikąd.

    Listy wyboru są zawężone do zadań i komponentów etapu, a nie do wszystkich w bazie: pole
    z zadaniami cudzego konkursu byłoby wyciekiem samą swoją zawartością.
    """

    class Meta:
        model = TieBreak
        fields = ("key", "problem", "component", "descending", "position")
        help_texts = {
            "key": (
                "Źródło liczby, którą rozstrzygamy remis. „Bez rozstrzygania” kończy listę – "
                "wiersze równe na wcześniejszych kryteriach dzielą miejsce."
            ),
            "problem": "Wymagane wyłącznie dla kryterium „wynik we wskazanym zadaniu”.",
            "component": "Wymagany wyłącznie dla kryterium „wynik we wskazanym komponencie”.",
            "descending": "Odznacz przy czasie oddania: tam „lepiej” znaczy wcześniej.",
            "position": "Kolejność stosowania kryteriów. Każde miejsce w etapie jest zajęte raz.",
        }

    def __init__(self, *args, stage, **kwargs):
        kwargs.setdefault("instance", TieBreak(stage=stage))
        super().__init__(*args, **kwargs)
        self.fields["problem"].queryset = stage.problems.order_by("number", "id")
        self.fields["problem"].empty_label = "— bez zadania —"
        self.fields["component"].queryset = stage.components.order_by("position", "id")
        self.fields["component"].empty_label = "— bez komponentu —"


class TeamForm(forms.Form):
    """Nowa drużyna w bieżącej edycji: nazwa, szkoła i adres opiekuna.

    Zwykły ``Form``, a nie ``ModelForm``, bo drużynę zakłada serwis (``create_team``): to on nadaje
    kod publiczny z prefiksem konkursu i ponawia przy kolizji, i to on pisze wpis audytowy. Formularz
    modelowy zapisywałby wiersz obok serwisu, czyli bez kodu i bez śladu.
    """

    name = forms.CharField(label="Nazwa drużyny", max_length=120)
    school = forms.CharField(label="Szkoła", max_length=255, required=False)
    supervisor_email = forms.EmailField(label="Opiekun (e-mail)", required=False)


class TeamMemberForm(forms.Form):
    """Dopisanie uczestnika do składu – **po kodzie publicznym**, a nie po nazwisku.

    Kod publiczny jest tu jedynym wejściem z rozmysłem: wyszukiwarka po nazwisku zamieniłaby ekran
    składu w drugą listę uczestników z danymi osobowymi, a kod jest tym, co koordynator ma
    w protokole i co uczestnik widzi na swoim pulpicie.
    """

    public_code = forms.CharField(label="Kod publiczny uczestnika", max_length=16)
    is_captain = forms.BooleanField(label="Kapitan", required=False)

    def clean_public_code(self) -> str:
        """Kod bez otoczki i wielkimi literami – tak, jak leży w bazie i jak stoi na wydruku."""
        return (self.cleaned_data.get("public_code") or "").strip().upper()


class TeamStageForm(forms.Form):
    """Wpis drużyny do etapu. Lista etapów przychodzi z zewnątrz – zawężona do edycji drużyny."""

    stage = forms.ModelChoiceField(
        label="Etap", queryset=Stage.objects.none(), empty_label="— wybierz etap —"
    )

    def __init__(self, *args, stages=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = stages if stages is not None else Stage.objects.none()


class InterviewScoreForm(forms.Form):
    """Punkty komisji z jednej rozmowy: wpis do etapu, liczba punktów i uwaga.

    Skali formularz **nie zna** i to jest reguła, a nie uproszczenie: dopuszczalne wartości
    rozstrzyga ``grading.services.allowed_scores`` przez ``record_interview_score``, czyli tą samą
    drogą, co przy ocenie pracy. Druga kopia listy w formularzu rozjechałaby się przy pierwszej
    zmianie skali etapu.
    """

    entry = forms.ModelChoiceField(label="Wpis", queryset=None)
    points = forms.IntegerField(label="Punkty")
    note = forms.CharField(label="Uwaga komisji", max_length=200, required=False)

    def __init__(self, *args, entries, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["entry"].queryset = entries


class ReviewerRoleForm(forms.ModelForm):
    """Nazwana rola recenzencka etapu: kod, nazwa, runda, liczba recenzentów i udział w zgodności.

    Etapu w polach nie ma – jest w adresie. Runda **zostaje** osobnym polem, bo rola jest etykietą
    nad rundą, a nie zamiast niej: maszyna stanów oceniania rozstrzyga po ``Review.round``
    (§ 1.2.7) i etap 2 tego nie zmienia.
    """

    class Meta:
        model = ReviewerRole
        fields = ("code", "name", "round", "count", "counts_towards_consensus", "position")
        help_texts = {
            "code": "Kod maszynowy roli, np. „first”, „arbiter”. Unikalny w etapie.",
            "name": "Nazwa, którą recenzent widzi w swojej kolejce, np. „pierwszy recenzent”.",
            "round": "Runda, w której ta rola pracuje. Przydział obsadza role rundy ślepej.",
            "count": "Ilu recenzentów obsadza tę rolę przy jednej pracy.",
            "counts_towards_consensus": (
                "Odznacz dla roli, która pisze własną opinię obok recenzji i nie liczy się do zgodności."
            ),
            "position": "Kolejność obsadzania ról przy przydziale.",
        }


def region_subtree_ids(region: Region) -> set[int]:
    """Identyfikatory regionu i wszystkiego, co pod nim wisi – kandydaci **wykluczeni** z listy nadrzędnych.

    Drzewo ma trzy poziomy (``RegionLevel``), więc pętla wykonuje się najwyżej trzy razy i nie ma
    tu ani rekurencji, ani zapytania rekurencyjnego. Powód istnienia: region wskazujący na własne
    dziecko jako nadrzędne tworzy cykl, a cykl w drzewie regionów zapętliłby **wyświetlanie**
    listy, a nie zapis – czyli awaria ujawniłaby się dopiero komuś innemu i na innym ekranie.
    """
    found = {region.pk}
    frontier = [region.pk]
    while frontier:
        children = list(Region.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
        frontier = [child for child in children if child not in found]
        found.update(frontier)
    return found


class RegionForm(forms.ModelForm):
    """Jeden region podziału terytorialnego konkursu (§ 1.4.2).

    Konkursu w polach nie ma – bierze się go z żądania i wchodzi do instancji przed zapisem.
    Pole ``competition`` w formularzu znaczyłoby listę wyboru z cudzymi konkursami, czyli wyciek
    samą swoją zawartością.

    Unikalność kodu **w konkursie** sprawdzamy tutaj, a nie zostawiamy więzowi bazy
    (``accounts_region_unique_code``): komunikat z bazy mówi o nazwie więzu i staje pod błędami
    ogólnymi, a organizator wpisujący drugi raz „mazowieckie” ma zobaczyć zdanie pod polem, które
    właśnie wypełnił.

    Lista nadrzędnych jest zawężona do regionów **tego** konkursu z pominięciem samego regionu
    i jego poddrzewa – patrz :func:`region_subtree_ids`.
    """

    class Meta:
        model = Region
        fields = ("code", "name", "level", "parent", "position", "is_active", "counts_for_conflict")
        help_texts = {
            "code": (
                "Kod maszynowy, np. „mazowieckie”. Trafia do eksportów, filtrów i wpisów audytu, "
                "więc zmieniaj go tylko wtedy, gdy naprawdę był pomyłką."
            ),
            "name": "Nazwa, którą widzi uczestnik na liście wyboru.",
            "level": "Kraj, region (województwo, stan, okręg) albo podregion (powiat, dystrykt).",
            "parent": "Region, pod którym ten stoi w drzewie. Puste znaczy korzeń.",
            "position": "Kolejność w obrębie jednego nadrzędnego.",
            "is_active": (
                "Odznaczony region znika z list wyboru, ale zostaje przy profilach, które go już mają."
            ),
            "counts_for_conflict": (
                "Odznacz dla regionu zbiorczego, np. „poza Polską”: dwoje uczestników spoza kraju "
                "nie jest ze sobą w konflikcie z tytułu miejsca zamieszkania."
            ),
        }

    def __init__(self, *args, competition, **kwargs):
        super().__init__(*args, **kwargs)
        self.competition = competition
        candidates = Region.objects.for_competition(competition).order_by("position", "name", "id")
        if self.instance.pk:
            candidates = candidates.exclude(pk__in=region_subtree_ids(self.instance))
        self.fields["parent"].queryset = candidates
        self.fields["parent"].empty_label = "— bez nadrzędnego —"

    def clean_code(self) -> str:
        """Kod bez otoczki i niezajęty w tym konkursie. W innym konkursie ten sam kod jest wolny."""
        code = (self.cleaned_data.get("code") or "").strip()
        taken = Region.objects.for_competition(self.competition).filter(code=code)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError("Region o tym kodzie już jest w tym konkursie.")
        return code


class CustomInstitutionForm(forms.ModelForm):
    """Jedna placówka ze słownika własnego organizatora (§ 1.3.3).

    Konkursu w polach nie ma – bierze się go z żądania i wchodzi do instancji przed zapisem.
    Pole ``competition`` w formularzu znaczyłoby listę wyboru z cudzymi konkursami, czyli wyciek
    samą swoją zawartością.

    Kolumn wyliczanych (``search_text``, ``city_search``) też nie ma i mieć nie może: liczy je
    ``CustomInstitution.save()`` tą samą funkcją, co dla wykazu publicznego. Pole do ręcznego
    wpisania postaci porównawczej byłoby drugą regułą normalizacji na jednym ekranie.

    Reguły są **te same**, co przy imporcie pliku (``apps.schools.custom.normalise_row``) i tak ma
    zostać: identyfikator unikalny w konkursie, kraj dwuliterowym kodem, nazwa co najmniej
    trzyznakowa. Powtórzenie jest tu świadome i wąskie – import czyta wiersz arkusza, a formularz
    pola HTML, ale **komunikat dla człowieka ma być ten sam**, bo człowiek jest ten sam.

    Pozostałe więzy (długości pól, kształt ``region_code``) sprawdza ``full_clean()`` modelu,
    wołane przez ``ModelForm`` – i dlatego nie ma ich tutaj drugi raz.
    """

    class Meta:
        model = CustomInstitution
        fields = (
            "name",
            "institution_type",
            "external_id",
            "country",
            "region_code",
            "city",
            "postal_code",
            "address",
            "is_active",
        )
        help_texts = {
            "name": "Nazwa, którą uczestnik zobaczy w podpowiedziach wyszukiwarki.",
            "institution_type": (
                "Rodzaj z listy wspólnej dla obu wykazów. Podpowiedzi dostaje uczestnik wyłącznie "
                "dla rodzajów dopuszczonych w profilu rejestracji."
            ),
            "external_id": (
                "Identyfikator z wykazu organizatora. Puste znaczy „nie prowadzę numeracji” – "
                "wtedy kolejny import rozpozna wiersz po nazwie i miejscowości."
            ),
            "country": "Dwuliterowy kod kraju (ISO 3166-1), na przykład „DE”. Puste znaczy Polska.",
            "region_code": "Kod regionu z podziału terytorialnego konkursu. Puste znaczy „bez regionu”.",
            "is_active": (
                "Odznaczona placówka znika z podpowiedzi, ale zostaje przy profilach, które ją już mają."
            ),
        }

    def __init__(self, *args, competition, **kwargs):
        super().__init__(*args, **kwargs)
        self.competition = competition

    def clean_name(self) -> str:
        """Nazwa bez otoczki i nie krótsza niż w imporcie – ``MIN_NAME_LENGTH`` jest jedno."""
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) < MIN_NAME_LENGTH:
            raise forms.ValidationError("Podaj nazwę placówki – co najmniej trzy znaki.")
        return name

    def clean_external_id(self) -> str:
        """Identyfikator niezajęty **w tym konkursie**. Pusty jest poprawny i wolno go powtórzyć.

        Sprawdzamy tutaj, a nie zostawiamy więzowi bazy (``schools_custominstitution_unique_
        external_id``): komunikat z bazy mówi o nazwie więzu i staje pod błędami ogólnymi,
        a organizator wpisujący drugi raz „A7” ma zobaczyć zdanie pod polem, które właśnie
        wypełnił. Więz bazy zostaje jako ostatnia linia obrony przed importem i komendą.
        """
        external_id = (self.cleaned_data.get("external_id") or "").strip()
        if not external_id:
            return ""
        taken = CustomInstitution.objects.for_competition(self.competition).filter(external_id=external_id)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError("Placówka o tym identyfikatorze już jest w tym konkursie.")
        return external_id

    def clean_country(self) -> str:
        """Kraj złożony do dwóch wielkich liter – ta sama reguła i ten sam komunikat, co w imporcie."""
        country = (self.cleaned_data.get("country") or "").strip().upper()
        if country and (len(country) != 2 or not country.isascii() or not country.isalpha()):
            raise forms.ValidationError("Kraj podaj dwuliterowym kodem (ISO 3166-1), na przykład „DE”.")
        return country


class CustomInstitutionImportForm(forms.Form):
    """Wgranie wykazu placówek z pliku CSV: plik, wygaszanie nieobecnych, źródło, potwierdzenie.

    Formularz sprawdza **kształt** – że plik w ogóle jest i że nazywa się jak CSV. Co jest w środku,
    rozstrzyga ``apps.schools.custom.import_custom_institutions``: rozmiar, kodowanie, nagłówek
    i każdy wiersz z osobna. Drugiej kopii tych reguł tu nie ma, bo ten sam plik wchodzi także
    komendą i z testu, a dwie kopie rozjechałyby się przy pierwszej zmianie formatu.

    ``confirm`` jest polem ukrytym, a nie drugim adresem, i to jest jedyne odstępstwo od reguły
    „czynność ma własny adres”: oba kroki wgrywają **ten sam plik na ten sam adres** i różnią się
    wyłącznie tym, czy zapisujemy. Drugi adres znaczyłby dwa formularze wieloczęściowe różniące
    się jedną wartością – i ryzyko, że podgląd pójdzie pod adres zapisu.
    """

    file = forms.FileField(
        label="Plik CSV",
        help_text="Pierwszy wiersz to nagłówek. Separator: średnik albo przecinek, kodowanie UTF-8.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv"}),
    )
    deactivate_missing = forms.BooleanField(
        label="Wygaś placówki nieobecne w pliku",
        required=False,
        help_text=(
            "Zaznacz, jeżeli plik jest całym wykazem. Przy uzupełnieniu zostaw odznaczone – "
            "inaczej wszystko, czego w pliku nie ma, zniknie z podpowiedzi. Usunięcia nie ma nigdy."
        ),
    )
    source_label = forms.CharField(
        label="Źródło",
        required=False,
        max_length=120,
        help_text="Napis w kolumnie „źródło”. Puste znaczy nazwa pliku i dzisiejsza data.",
    )
    #: Krok drugi. ``BooleanField`` w ukrytym polu, bo wartość przychodzi z naszego szablonu,
    #: a nie od człowieka: „False” i „True” są tu jedynymi napisami, jakie mogą przyjść.
    confirm = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def clean_file(self):
        """Plik z rozszerzeniem ``.csv``. Arkusz XLSX odbijamy tutaj, a nie komunikatem o kodowaniu.

        Parser czyta CSV i nic poza tym; wgrany ``.xlsx`` doszedłby do niego jako archiwum ZIP
        i odpadł na dekodowaniu, czyli zdaniem „zapisz plik jako CSV w kodowaniu UTF-8” – prawdziwym,
        ale wypowiedzianym o dwa kroki za późno i nie o tym, co się stało.
        """
        upload = self.cleaned_data["file"]
        if not (getattr(upload, "name", "") or "").lower().endswith(".csv"):
            raise forms.ValidationError(
                "Wykaz wgrywa się plikiem CSV. W arkuszu wybierz „Zapisz jako” → „CSV UTF-8”."
            )
        return upload


class RegistrationProfileForm(forms.ModelForm):
    """Profil rejestracji konkursu: co formularz ``/register/`` pyta i co dopuszcza (§ 1.3.4).

    Konkursu w polach nie ma – wiersz należy do konkursu z żądania (``OneToOne``), a lista wyboru
    z cudzymi konkursami byłaby wyciekiem samą swoją zawartością.

    ``allowed_institution_types`` jest w modelu ``JSONField``-em z listą napisów, a na ekranie
    **zestawem kratek**: organizator zaznacza rodzaje, a nie wpisuje JSON-a. Pusty zestaw jest
    wartością poprawną i znaczy „wyłącznie szkoła ponadpodstawowa”, czyli dzisiejszy formularz –
    mówi to podpowiedź pod polem, bo pusty zbiór łatwo przeczytać jako „żadna”.

    Reguł domenowych ten formularz nie powiela: przedziału klas i listy znanych rodzajów pilnuje
    ``RegistrationProfile.clean()``, wołane przez ``full_clean()`` z ``ModelForm._post_clean``.
    Dzięki temu ten sam warunek obowiązuje ekran, komendę i migrację.
    """

    allowed_institution_types = forms.MultipleChoiceField(
        label="Dozwolone placówki",
        required=False,
        choices=InstitutionType.choices,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Nic niezaznaczonego znaczy „szkoła ponadpodstawowa”, czyli dzisiejszy formularz. "
            "Lista wyboru rodzaju pojawia się w rejestracji dopiero przy dwóch zaznaczonych."
        ),
    )

    class Meta:
        model = RegistrationProfile
        fields = (
            "allowed_institution_types",
            "allow_custom_directory",
            "allow_free_text_school",
            "allow_foreign",
            "require_grade",
            "grade_min",
            "grade_max",
            "require_phone",
            "require_region",
            "require_birth_year",
            "participant_picks_category",
        )
        help_texts = {
            "allow_custom_directory": (
                "Czy wyszukiwarka pyta też o słownik organizatora. Działa razem z flagą "
                "„custom_school_directory”: bez niej wykaz własny nie jest czytany ani razu."
            ),
            "allow_free_text_school": (
                "Furtka „mojej szkoły nie ma na liście”. Odznaczenie zamyka rejestrację dla szkół "
                "spoza wykazów – zostaw zaznaczone, jeżeli nie wiesz, że chcesz inaczej."
            ),
            "allow_foreign": "Pole „kraj” w formularzu rejestracji. Puste pole znaczy Polska.",
            "require_grade": "Odznaczenie zostawia listę klas nieobowiązkową.",
            "grade_min": "Puste znaczy dzisiejszą granicę.",
            "grade_max": "Puste znaczy dzisiejszą granicę.",
            "require_birth_year": (
                "Dziś nie ma czego wyłączyć: od rocznika zależy reguła zgody opiekuna, "
                "czyli podstawa prawna zapisu. Pole czeka na osobne wydanie."
            ),
            "participant_picks_category": (
                "Wybór kategorii w rejestracji. Działa razem z flagą „categories”."
            ),
        }

    def clean_allowed_institution_types(self) -> list[str]:
        """Zaznaczone kratki jako lista napisów – w kolejności deklaracji ``InstitutionType``.

        Kolejność ustalamy tutaj, a nie zostawiamy tej, w której przyszły pola: ``JSONField``
        zapamiętałby kolejność klikania, a wtedy dwa konkursy z tym samym zestawem rodzajów
        miałyby w bazie dwa różne wiersze. Odczyt (``RegistrationProfile.institution_types``)
        i tak porządkuje listę, więc chodzi o to, żeby **zapis** nie kłamał w eksporcie.
        """
        chosen = set(self.cleaned_data.get("allowed_institution_types") or ())
        return [value for value in InstitutionType.values if value in chosen]
