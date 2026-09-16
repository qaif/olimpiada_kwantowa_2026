"""Formularze narzędzi koordynatora: komunikaty, filtr audytu, symulacja progu, jakość i dyplomy.

Osobny moduł od ``apps.web.forms`` z jednego powodu: tamten plik zbiera formularze, które mają
w serwisie po kilku czytelników (rejestracja, profil, oceny, etapy) i są częścią kontraktu
z API. Te obsługują po jednym ekranie każdy, nie mają odpowiednika w API i nigdy nie będą
wołane spoza ``apps.web.views.coordinator_reports``, ``…coordinator_messages``
i ``…coordinator_quality`` – trzymanie ich razem z formularzem rejestracji utrudniałoby
czytanie obu.

Wspólna zasada: formularz sprawdza **kształt** danych (czy pole jest wypełnione, czy wartość
należy do zamkniętej listy, czy liczba jest dodatnia). Reguła domenowa – kto jest odbiorcą
komunikatu, czy próg da się zapisać – stoi w serwisach (``apps.accounts.messaging``,
``apps.results.simulation``) i formularz jej nie powiela.
"""

from __future__ import annotations

from django import forms

from apps.accounts.models import BroadcastGroup
from apps.competitions.models import ManualQualification, QualificationMode, Stage
from apps.results.models import CertificateKind

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
