"""Formularz ekranu „Ustawienia konkursu” – marka, organizator, poczta i przełączniki.

Osobny moduł od ``apps.web.forms`` z tego samego powodu, co ``certificate_forms`` i
``supervisor_forms``: to jest komplet formularzy **jednej** funkcji panelu, czyta się go razem
z jego widokiem, a wspólny plik formularzy serwisu ma już blisko dwa tysiące linii.

**Czego tu nie ma i nie będzie.** Cztery pola modelu ``Competition`` należą do operatora
platformy, a nie do koordynatora konkursu (``docs/UNIWERSALNY-ETAP-1.md`` § 6 T5 i § 8, D7):

- ``slug`` – identyfikator konkursu w adresach komend i w eksportach; zmiana jest migracją
  danych, a nie wpisem w panelu,
- ``site`` – witryna Wagtaila razem z całym drzewem stron,
- ``routing_mode`` i ``path_prefix`` – sposób adresowania, którego zmiana wymaga zgodnej zmiany
  w Caddy, ``ALLOWED_HOSTS`` i ``CSRF_TRUSTED_ORIGINS``, czyli dostępu do serwera.

``primary_domain`` jest z tego samego powodu **tylko do odczytu**: ekran ją pokazuje (bo to
najczęstsze pytanie organizatora: „pod jakim adresem stoi mój konkurs”), ale nie przyjmuje.
"""

from __future__ import annotations

from django import forms

from apps.cms.blocks import PARTNER_LEVELS
from apps.cms.models import SPONSOR_SLIDER_MAX_SECONDS, SPONSOR_SLIDER_MIN_SECONDS
from apps.tenancy.models import FEATURE_DEFAULTS, MAX_FORWARD_EMAILS, Competition, split_forward_emails

#: Pola, które koordynator zmienia z panelu – w kolejności sekcji na ekranie. Krotka, a nie
#: ``exclude``: lista pól modelu rośnie w kolejnych etapach, a ``exclude`` wpuściłoby każde nowe
#: pole do formularza **po cichu**, łącznie z takim, które należy do operatora.
EDITABLE_FIELDS: tuple[str, ...] = (
    # marka
    "name",
    "short_name",
    "genitive_name",
    "locative_name",
    "tagline",
    "accent_colour",
    "logo",
    "favicon",
    # organizator
    "organizer_name",
    "organizer_address",
    "organizer_registry",
    "organizer_url",
    "contact_email",
    "contact_phone",
    "dpo_email",
    # poczta
    "from_email",
    "email_subject_prefix",
    # identyfikatory drukowane
    "public_code_prefix",
    "certificate_prefix",
    # zachowanie
    "default_language",
    "time_zone",
)
# ``submission_forward_emails`` świadomie **nie** stoi na tej liście, choć jest polem konkursu:
# przekazywanie prac ma własny ekran (``/coordinator/submission-forwarding/``), bo to jest decyzja
# o **przetwarzaniu danych osobowych uczestników** – wynosi ich pliki poza serwis – i nie może
# wpaść przypadkiem w jeden zapis razem z kolorem akcentu i prefiksem numeru dyplomu.

#: Przełączniki, które wolno przestawić z panelu, w kolejności wyświetlania. ``path_prefix_routing``
#: świadomie **nie** jest na tej liście: przestawia sposób adresowania, czyli to samo, czego
#: dotyczą wyłączone wyżej ``routing_mode`` i ``path_prefix``, i wymaga zmiany w Caddy.
EDITABLE_FLAGS: tuple[str, ...] = (
    "memberships_enforced",
    "competition_settings_page",
    "per_competition_consents",
    "supervisor_role",
    "appeals",
    "certificates",
)
# ``participant_forum`` świadomie **nie** stoi na tej liście, choć jest zwykłym przełącznikiem
# konkursu. Powód jest ten sam, co przy ``submission_forward_emails`` wyżej, tylko z drugiej
# strony: włączenie forum otwiera miejsce, w którym **osoby niepełnoletnie piszą publicznie**, więc
# jest zobowiązaniem organizatora do dyżuru moderacyjnego, a nie ustawieniem, które ma wpaść
# w jeden zapis razem z kolorem akcentu. Przestawia je operator platformy w ``/admin/``, po
# ustaleniu, kto i jak często zagląda do ``/coordinator/forum/`` (``docs/OPERACJE.md`` § 6.4).

#: Etykieta i wyjaśnienie każdego przełącznika. Wyjaśnienie mówi, **co się stanie**, a nie jak
#: flaga się nazywa w kodzie: ekran czyta koordynator, a nie autor migracji.
FLAG_LABELS: dict[str, tuple[str, str]] = {
    "memberships_enforced": (
        "Role z członkostw w konkursie",
        "<strong>Uwaga: ten przełącznik zmienia, kto ma dostęp do paneli.</strong> Włączony – "
        "o tym, kto jest uczestnikiem, recenzentem, członkiem komisji odwoławczej albo "
        "koordynatorem, rozstrzyga członkostwo <strong>w tym konkursie</strong>. Wyłączony – "
        "rozstrzyga globalna grupa konta, czyli rola nadana w <em>dowolnym</em> konkursie działa "
        "we wszystkich; panele tego konkursu otwierają się wtedy także przed osobami spoza niego. "
        "Wyłączenie jest przewidziane wyłącznie na czas migracji danych.",
    ),
    "competition_settings_page": (
        "Ekran „Ustawienia konkursu”",
        "Wyłączenie zamyka <strong>tę stronę</strong> i zdejmuje ją z menu. Ponowne włączenie "
        "wymaga wtedy operatora platformy.",
    ),
    "per_competition_consents": (
        "Zgody z bazy zamiast z kodu",
        "Docelowo pozwoli zapisać własne brzmienie zgód rejestracyjnych. Dopóki etap 2 tego nie "
        "dokończy, przełącznik nie zmienia niczego widocznego.",
    ),
    "supervisor_role": (
        "Rola opiekuna szkolnego",
        "Panel nauczyciela, import listy klasy i zaświadczenia dla opiekunów.",
    ),
    "appeals": (
        "Procedura odwoławcza",
        "Reklamacje uczestników i panel komisji odwoławczej.",
    ),
    "certificates": (
        "Dyplomy i zaświadczenia",
        "Wystawianie dokumentów, szablony graficzne i publiczna weryfikacja po kodzie.",
    ),
}

#: Prefiks pól przełączników w formularzu. Osobna przestrzeń nazw, bo ``feature_flags`` jest
#: jednym polem modelu (``JSONField``), a na ekranie ma być sześć pól wyboru – bez prefiksu
#: nazwa flagi mogłaby kiedyś zderzyć się z nazwą kolumny.
FLAG_PREFIX = "flag_"


class CompetitionSettingsForm(forms.ModelForm):
    """Marka, dane organizatora, poczta i przełączniki jednego konkursu.

    ``ModelForm``, bo walidacja należy do modelu i ma obowiązywać tak samo komendę zakładającą
    konkurs, migrację i ten ekran: zapis koloru akcentu sprawdza ``validate_hex_colour``,
    a spójność adresowania ``Competition.clean()`` (``ModelForm`` woła je w ``_post_clean``).
    Powtórzenie tych reguł tutaj dałoby drugie miejsce, w którym trzeba pamiętać o zmianie.

    Przełączniki są **osobnymi polami logicznymi**, a nie polem tekstowym z JSON-em: organizator
    ma zaznaczyć funkcję, a nie napisać słownik. Do modelu wracają jednym zapisem do
    ``feature_flags`` – patrz :meth:`feature_flags`.
    """

    class Meta:
        model = Competition
        fields = EDITABLE_FIELDS
        labels = {
            "name": "Nazwa konkursu",
            "short_name": "Nazwa skrócona",
            "genitive_name": "Nazwa w dopełniaczu",
            "locative_name": "Nazwa w miejscowniku",
            "tagline": "Hasło",
            "accent_colour": "Kolor akcentu",
            "logo": "Logotyp",
            "favicon": "Favikona",
            "organizer_name": "Organizator",
            "organizer_address": "Adres organizatora",
            "organizer_registry": "Dane rejestrowe",
            "organizer_url": "Strona organizatora",
            "contact_email": "E-mail kontaktowy",
            "contact_phone": "Telefon kontaktowy",
            "dpo_email": "Inspektor ochrony danych",
            "from_email": "Nadawca listów",
            "email_subject_prefix": "Prefiks tematu listów",
            "public_code_prefix": "Prefiks kodu uczestnika",
            "certificate_prefix": "Prefiks numeru dyplomu",
            "default_language": "Język domyślny",
            "time_zone": "Strefa czasowa",
        }
        help_texts = {
            "short_name": "Puste = używamy pełnej nazwy. Skrót stoi w wąskich miejscach interfejsu.",
            "genitive_name": (
                "„komitet <b>Olimpiady Kwantowej</b>”. Puste = nie odmieniamy i zostaje mianownik."
            ),
            "locative_name": "„udział w <b>Olimpiadzie Kwantowej</b>”. Puste = zostaje mianownik.",
            "accent_colour": "Zapis szesnastkowy z krzyżykiem, np. #1f6feb. Puste = kolor domyślny.",
            "logo": "Grafika z biblioteki obrazów (/cms/ → Obrazy). Najpierw wgraj plik tam.",
            "favicon": "Ikona zakładki. Kwadrat, najlepiej co najmniej 512 × 512.",
            "dpo_email": (
                "Adres inspektora ochrony danych organizatora. Stoi w klauzuli informacyjnej – "
                "administratorem danych uczestników jest organizator, nie operator platformy."
            ),
            "from_email": (
                "Puste = nadawca instalacji. Adres w obcej domenie przejdzie, ale bez rekordów "
                "SPF/DKIM tej domeny listy trafią do spamu."
            ),
            "email_subject_prefix": "Np. „[Olimpiada Kwantowa] ”. Puste = prefiks instalacji.",
            "public_code_prefix": (
                "Początek kodu, pod którym uczestnik występuje w tabelach wyników, np. „OLM-”. "
                "<b>Kody już nadane się nie zmienią</b> – prefiks obowiązuje od następnej "
                "rejestracji."
            ),
            "certificate_prefix": (
                "Początek numeru dyplomu, np. „OK” w „OK/2026/0001”. <b>Numery już wystawionych "
                "dokumentów się nie zmienią</b> – prefiks obowiązuje od następnego wystawienia."
            ),
            "time_zone": ("Pokazywana na ekranach; obliczanie terminów przestawi się na nią w etapie 2."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        flags = (self.instance.feature_flags or {}) if self.instance is not None else {}
        for name in EDITABLE_FLAGS:
            label, help_text = FLAG_LABELS[name]
            self.fields[f"{FLAG_PREFIX}{name}"] = forms.BooleanField(
                label=label,
                help_text=help_text,
                required=False,
                # Wartość początkowa z **modelu**, nie z literału: pusty ``feature_flags`` znaczy
                # „wszystko jak dotąd”, a co znaczy „jak dotąd”, wie ``FEATURE_DEFAULTS``.
                initial=bool(flags.get(name, FEATURE_DEFAULTS[name])),
            )
        # Biblioteka obrazów jako zwykła lista wyboru, a nie okno wyboru Wagtaila: panel działa
        # **bez JavaScriptu** i pod ścisłą polityką CSP (``apps.web.middleware``), a okno wyboru
        # jest komponentem panelu redakcyjnego razem z jego skryptami. Wgranie pliku zostaje
        # tam, gdzie było – w ``/cms/`` → „Obrazy”; tutaj się go tylko wskazuje.
        for name in ("logo", "favicon"):
            self.fields[name].queryset = self.fields[name].queryset.order_by("-created_at", "-id")
            self.fields[name].empty_label = "bez grafiki"
        # Prefiks tematu listów **nie jest przycinany**. Domyślne ``strip=True`` Django zjadałoby
        # spację na końcu, a to jest jedyny znak rozdzielający prefiks od tematu: Konkurs #1 ma
        # tam dokładnie „[Olimpiada Kwantowa] ”, więc samo otwarcie i zapisanie tego formularza
        # zmieniłoby temat **dziewięciu** listów na „[Olimpiada Kwantowa]Aktywuj konto…”. Część
        # odbiorców ma je posortowane regułami po temacie (§ 7.3, ``test_email_subjects_unchanged``).
        self.fields["email_subject_prefix"].strip = False

    @property
    def flag_fields(self) -> list:
        """Pola przełączników w kolejności ``EDITABLE_FLAGS`` – szablon renderuje je osobną sekcją."""
        return [self[f"{FLAG_PREFIX}{name}"] for name in EDITABLE_FLAGS]

    def feature_flags(self) -> dict:
        """Zawartość ``Competition.feature_flags`` po zapisie – **komplet** przełączników z ekranu.

        Zapisujemy wartości jawnie, także te równe domyślnym, i to jest świadome: pusty słownik
        znaczy „jak dotąd”, więc gdyby domyślna wartość kiedykolwiek się zmieniła, konkurs
        z pustym słownikiem zmieniłby zachowanie bez decyzji organizatora. Po pierwszym zapisie
        z tego ekranu stan konkursu jest zapisany, a nie dziedziczony.

        Przełączniki spoza ekranu (``path_prefix_routing``) przepisujemy bez zmian – należą do
        operatora i ten formularz nie ma prawa ich skasować przy okazji.
        """
        current = dict(self.instance.feature_flags or {})
        for name in EDITABLE_FLAGS:
            current[name] = bool(self.cleaned_data.get(f"{FLAG_PREFIX}{name}"))
        return current

    def _submitted(self, name: str):
        """Wartość pola w postaci porównywalnej z ``self.initial``.

        ``ModelChoiceField`` (logotyp, favikona) oddaje **obiekt**, a ``initial`` niesie klucz –
        porównanie bez tego sprowadzenia zgłaszałoby zmianę obrazu przy każdym zapisie.
        """
        value = self.cleaned_data.get(name)
        if name in ("logo", "favicon"):
            return getattr(value, "pk", None)
        return value

    def changed_fields(self) -> list[str]:
        """Nazwy pól, które **naprawdę** się zmieniły – treść wpisu audytowego.

        Nazwy, a nie pary „było → jest”: wpis audytowy czytają także osoby bez prawa do danych
        kontaktowych organizatora, a ``diff`` ma z założenia nie nosić treści (``apps.core.models``).
        Sama lista pól odpowiada na pytanie, które w tym miejscu pada: „co on tam zmienił”.

        Porównujemy z ``self.initial``, a nie z ``instance``: ``ModelForm._post_clean`` zdążył już
        wpisać nowe wartości do instancji, więc porównanie z nią zawsze dawałoby pustą listę.
        """
        changed = [name for name in EDITABLE_FIELDS if self.initial.get(name) != self._submitted(name)]
        before = self.instance.feature_flags if self.instance is not None else {}
        flags = self.feature_flags()
        changed += [
            f"{FLAG_PREFIX}{name}"
            for name in EDITABLE_FLAGS
            if bool((before or {}).get(name, FEATURE_DEFAULTS[name])) != flags[name]
        ]
        return changed


#: Ile adresów wolno wpisać na ekranie przekazywania. Reguła stoi w modelu
#: (``tenancy.MAX_FORWARD_EMAILS``); tutaj jest tylko nazwa, pod którą czyta ją szablon – żeby
#: liczba w zdaniu pomocy nie była literałem obok reguły, która ją naprawdę egzekwuje.
FORWARD_EMAIL_LIMIT = MAX_FORWARD_EMAILS


class SubmissionForwardingForm(forms.ModelForm):
    """Adresy, na które serwis przekazuje przyjęte rozwiązania (prośba organizatora z 20.09.2026).

    Jedno pole i jeden ekran, bo to jest jedna decyzja: „czy komitet dostaje prace także pocztą
    i na które skrzynki”. ``ModelForm``, a nie ``Form`` z ręcznym zapisem, żeby reguła poprawności
    adresów została tam, gdzie obowiązuje wszystkich – w walidatorze modelu
    (``apps.tenancy.models.validate_submission_forward_emails``, wołanym przez ``_post_clean``).
    Powtórzenie jej tutaj dawałoby ekran, który przyjmuje co innego niż komenda i import.

    Zapis **normalizuje** wpis do jednego adresu na wiersz. Nie jest to kosmetyka: pole bywa
    wklejane z książki adresowej („a@x.pl, b@x.pl”), a wpis audytowy i pytanie „czy coś się
    zmieniło” pracują na tej wartości – bez normalizacji ta sama lista wklejona dwoma sposobami
    wyglądałaby jak zmiana.
    """

    class Meta:
        model = Competition
        fields = ("submission_forward_emails",)
        labels = {"submission_forward_emails": "Adresy, na które trafiają rozwiązania"}
        widgets = {
            "submission_forward_emails": forms.Textarea(
                attrs={"rows": 5, "placeholder": "komitet@example.org"}
            )
        }

    def clean_submission_forward_emails(self) -> str:
        """Jeden adres na wiersz, bez powtórzeń i bez pustych – w kolejności wpisania.

        Metoda **nie waliduje**: poprawność adresów i ich liczbę sprawdza walidator modelu, który
        uruchamia się później (``ModelForm._post_clean`` → ``Model.full_clean``) i na wartości już
        znormalizowanej. Dublowanie sprawdzenia tutaj znaczyłoby dwa komunikaty o jednym błędzie.
        """
        return "\n".join(split_forward_emails(self.cleaned_data.get("submission_forward_emails", "")))

    def addresses(self) -> list[str]:
        """Adresy po walidacji – dla komunikatu na ekranie i dla licznika we wpisie audytowym."""
        return split_forward_emails(self.cleaned_data.get("submission_forward_emails", ""))

    def has_changed_addresses(self) -> bool:
        """Czy lista adresów jest inna niż przed otwarciem formularza – warunek wpisu audytowego.

        Porównujemy **zbiory adresów**, a nie napisy: zmiana kolejności albo separatora nie jest
        zdarzeniem, o którym ma zostać ślad, a zapis bez zmiany jest tu czynnością zwykłą (ekran
        bywa otwierany po to, żeby sprawdzić, dokąd idą prace).

        ``self.initial``, a nie ``self.instance``: ``_post_clean`` wpisał już nową wartość do
        instancji, więc porównanie z nią zawsze dawałoby „bez zmian”.
        """
        before = set(split_forward_emails(self.initial.get("submission_forward_emails", "")))
        return before != set(self.addresses())


class SponsorSliderForm(forms.Form):
    """Ekran „Slider sponsorów” (``/coordinator/sponsor-slider/``): włącznik, tempo, poziomy.

    Zwykły ``forms.Form``, nie ``ModelForm`` – trzy pola zapisują się na ``cms.SiteSettings``
    (per witryna, nie per konkurs jak reszta tego modułu), a widok sam decyduje, którą witrynę
    zapisuje (``SiteSettings.for_site(competition.site)``); formularz nie musi tego wiedzieć.

    Pole ``levels`` ma **te same** ``choices``, co ``PARTNER_LEVELS`` – nieznany klucz w POST-cie
    odpada tu, standardowym mechanizmem ``MultipleChoiceField`` („Wybierz poprawną wartość…”),
    zanim dotrze do walidatora modelu (``apps.cms.models.validate_sponsor_slider_levels``, który
    broni drugiej drogi zapisu – JSON-a wklejonego wprost w ``/cms/``).
    """

    enabled = forms.BooleanField(
        label="Pokazuj slider sponsorów w menu",
        required=False,
        help_text="Wyłączenie chowa pasek logotypów z menu na każdej stronie serwisu.",
    )
    seconds = forms.IntegerField(
        label="Co ile sekund pasek przesuwa się o jeden logotyp",
        min_value=SPONSOR_SLIDER_MIN_SECONDS,
        max_value=SPONSOR_SLIDER_MAX_SECONDS,
    )
    levels = forms.MultipleChoiceField(
        label="Poziomy partnerów w sliderze",
        required=False,
        choices=PARTNER_LEVELS,
        widget=forms.CheckboxSelectMultiple,
        help_text="Nic nie zaznaczone = wszystkie poziomy.",
    )

    def __init__(self, *args, level_counts: dict[str, int] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        counts = level_counts or {}
        # Liczba partnerów **z logotypem** na każdym poziomie – obok etykiety, żeby koordynator
        # widział od razu, czy zaznaczenie poziomu w ogóle coś pokaże.
        self.fields["levels"].choices = [
            (key, f"{label} ({counts.get(key, 0)})") for key, label in PARTNER_LEVELS
        ]
