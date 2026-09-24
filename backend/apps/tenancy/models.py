"""Konkurs jako jednostka wielodostępności: jeden wiersz ``Competition`` na jedną ``wagtailcore.Site``.

Dlaczego wariant „multi-site + wiersz właściciela”, a nie schemat na dzierżawcę: Wagtail już jest
wielowitrynowy i już z tego korzystamy (``cms.SiteSettings`` dziedziczy po ``BaseSiteSetting``,
``apps.cms.context_processors`` woła ``Site.find_for_request``), słownik szkół ma być wspólny,
a konto osoby ma zostać **jedno** – uczeń bywa uczestnikiem dwóch olimpiad, nauczyciel opiekunem
w trzech. Pełne uzasadnienie i nazwane granice tego wyboru: ``docs/UNIWERSALNY-ETAP-1.md`` § 1.2.

Podział odpowiedzialności z ``cms.SiteSettings`` (żeby nie powstały dwa źródła prawdy):

- **tutaj** stoi to, co jest faktem o konkursie i jego organizatorze i czego potrzebuje kod
  **poza żądaniem HTTP** – poczta, zadania Celery, komendy, migracje: nazwa (z odmianą), dane
  podmiotu, nadawca listów, domena, przełączniki,
- w ``SiteSettings`` zostaje to, co jest **prezentacją serwisu** i należy do redaktora: hasło pod
  logotypem, znak w stopce, adresy profili społecznościowych, identyfikator GA4.

Pola dublujące się dziś w obu miejscach (``site_name``, ``organizer_name``, ``contact_email``)
zostają w ``SiteSettings`` bez zmian – produkcja ma je wypełnione i tak mają zostać.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.utils import timezone

#: Dozwolony zapis koloru akcentu: sześć cyfr szesnastkowych z krzyżykiem. Skrót trzyznakowy
#: (``#abc``) jest odrzucany celowo – wartość idzie wprost do zmiennej CSS na ``<html>``, a jeden
#: zapis zamiast dwóch znaczy, że porównanie kolorów w panelu jest porównaniem napisów.
HEX_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Ile adresów wolno wskazać w przekazywaniu rozwiązań. Pięć, bo to jest skrzynka **komitetu**,
#: a nie lista wysyłkowa: każdy oddany plik idzie na każdy z tych adresów, więc dziesiąty adres
#: znaczy dziesięć kopii jednego załącznika w kolejce ``mail`` i dziesięć kopii u odbiorców.
#: Organizator, który potrzebuje więcej, wpisuje jeden adres grupowy po swojej stronie – tam ma
#: nad nim kontrolę (może kogoś wypisać), a tu miałby tylko dłuższe pole.
MAX_FORWARD_EMAILS = 5

#: Czym wolno rozdzielić adresy w jednym polu tekstowym. Przecinek, średnik i nowa linia, bo
#: dokładnie tak ludzie wklejają adresy ze swojej książki adresowej – wymuszanie jednego znaku
#: byłoby regułą, której nikt nie przeczyta przed wklejeniem.
FORWARD_EMAIL_SEPARATORS = re.compile(r"[,;\s]+")


def split_forward_emails(value: str | None) -> list[str]:
    """Rozbija zawartość pola na listę adresów, bez pustych i bez powtórzeń.

    **Jedyne** miejsce, w którym z napisu powstaje lista: czyta stąd walidator, formularz panelu
    i wysyłka (``apps.submissions.forwarding``). Dwie implementacje znaczyłyby, że ekran przyjmuje
    coś, czego wysyłka nie rozumie – a objawem byłby list, który po prostu nie dochodzi.

    Powtórzenia zjadamy zachowując kolejność wpisania: ten sam adres dwa razy nie jest błędem
    organizatora (bywa skutkiem wklejenia), ale byłby dwiema kopiami tego samego załącznika.
    """
    seen: dict[str, None] = {}
    for item in FORWARD_EMAIL_SEPARATORS.split(value or ""):
        cleaned = item.strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def validate_submission_forward_emails(value: str) -> None:
    """Lista adresów przekazywania rozwiązań: poprawne adresy i nie więcej niż :data:`MAX_FORWARD_EMAILS`.

    Walidator modelu, a nie formularza: to samo pole wypełnia komenda zakładająca konkurs i import
    konfiguracji, a adres z literówką znaczy tu pracę uczestnika wysyłaną w próżnię – i to bez
    żadnego objawu widocznego dla organizatora, bo odbicie od MTA dochodzi do nadawcy instalacji.

    Puste pole jest poprawne i znaczy „funkcja wyłączona” – patrz ``Competition.forward_emails``.
    """
    addresses = split_forward_emails(value)
    if len(addresses) > MAX_FORWARD_EMAILS:
        raise ValidationError(
            "Adresów może być najwyżej %(limit)s; wpisano %(count)s.",
            code="too_many_forward_emails",
            params={"limit": MAX_FORWARD_EMAILS, "count": len(addresses)},
        )
    wrong = []
    for address in addresses:
        try:
            validate_email(address)
        except ValidationError:
            wrong.append(address)
    if wrong:
        raise ValidationError(
            "To nie są poprawne adresy e-mail: %(addresses)s.",
            code="invalid_forward_email",
            params={"addresses": ", ".join(wrong)},
        )


def validate_hex_colour(value: str) -> None:
    """Kolor akcentu musi być pełnym zapisem szesnastkowym (``#1f6feb``).

    Walidator, a nie ``choices``: paleta należy do organizatora, a nie do nas. Walidator, a nie
    ``CharField`` bez sprawdzenia: wartość trafia do arkusza stylów jako zmienna CSS, więc napis
    spoza tego wzorca byłby albo cichym brakiem koloru, albo wstrzyknięciem do stylu.
    """
    if value and not HEX_COLOUR_RE.match(value):
        raise ValidationError(
            "Kolor akcentu podaje się w zapisie szesnastkowym, np. #1f6feb.",
            code="invalid_hex_colour",
        )


class RoutingMode(models.TextChoices):
    """Skąd bierze się konkurs w adresie: z domeny czy z pierwszego segmentu ścieżki."""

    DOMAIN = "DOMAIN", "własna domena"
    PATH = "PATH", "prefiks ścieżki na domenie platformy"


#: Katalog przełączników i ich wartości domyślne. Każdy z nich ma domyślnie stan **dzisiejszy**:
#: konkurs bez ani jednego wpisu w ``feature_flags`` zachowuje się dokładnie tak, jak serwis
#: zachowywał się przed wprowadzeniem wielokonkursowości (``docs/UNIWERSALNY-ETAP-1.md`` § 0.5).
#:
#: Katalog jest tu, a nie w ustawieniach, bo to jest wiedza o **modelu**: co wolno wpisać do
#: ``feature_flags`` i co znaczy brak wpisu. Flaga spoza katalogu jest błędem wołającego, a nie
#: wyłączoną funkcją – ``has_feature`` powie o tym wprost, zamiast po cichu oddać ``False``.
FEATURE_DEFAULTS: dict[str, bool] = {
    # Rozstrzyganie konkursu po pierwszym segmencie ścieżki (§ 2.3). Wyłączone: Konkurs #1 ma
    # własną domenę, a tryb prefiksu nie rozdziela ciasteczek sesji.
    "path_prefix_routing": False,
    # Autoryzacja po ``accounts.Membership`` zamiast po globalnej grupie Django. Przełącza się na
    # ``True`` dopiero po backfillu członkostw (T2) – do tego czasu regułą są grupy.
    "memberships_enforced": False,
    # Ekran „Ustawienia konkursu” w panelu koordynatora (T5).
    "competition_settings_page": False,
    # Zgody z modelu bazy zamiast ze stałej ``apps.accounts.consents.CONSENTS`` (etap 2).
    "per_competition_consents": False,
    # Rola opiekuna szkolnego – dziś zawsze obecna, więc domyślnie włączona.
    "supervisor_role": True,
    # Procedura odwoławcza – jw.
    "appeals": True,
    # Dyplomy i zaświadczenia – jw.
    "certificates": True,
    # --- etap 2: konfiguracja, proces, rejestracja (``docs/UNIWERSALNY-ETAP-2.md`` § 0.6) -------
    # Katalog dostaje **komplet** flag etapu 2 naraz, także te, których pierwszy czytelnik powstanie
    # dopiero za kilka wydań. Flaga bez czytelnika jest nieszkodliwa (``has_feature`` podnosi
    # ``KeyError`` wyłącznie dla nazwy **spoza** katalogu), a katalog dopisywany po kawałku byłby
    # plikiem, który zmienia dziesięć równoległych zadań – czyli jedynym miejscem gwarantowanego
    # konfliktu. ``per_competition_consents`` stoi wyżej, bo do katalogu weszła już w etapie 1.
    # Każda z nich jest domyślnie **wyłączona**, czyli Konkurs #1 zachowuje się dokładnie jak dziś.
    #
    # Tematy listów, podpisy i nazwa kalendarza z konkursu zamiast z literału (§ 1.1.1, § 1.1.4).
    # Czyta ją wyłącznie ``apps.tenancy.branding``.
    "competition_branding_in_mail": False,
    # Teksty dyplomów i zaświadczeń z ``tenancy.DocumentTemplate`` zamiast ze stałych
    # ``apps.results.certificates`` (§ 1.1.3). Skład PDF-a zostaje bez zmian – zmienia się
    # wyłącznie źródło czterech napisów.
    "document_templates": False,
    # Uprawnienia do ``/cms/`` liczone per konkurs: grupa ``cms:<slug>``, uprawnienia stron na
    # korzeniu witryny konkursu i własna kolekcja mediów (§ 1.1.5). Wyłączona znaczy „jak dziś”:
    # globalna grupa ``coordinator`` z migracji ``cms.0003``.
    "scoped_cms_permissions": False,
    # Edytor przebiegu zawodów: dowolna liczba etapów z ``PipelineStep``, komponenty etapu
    # i reguły przejścia jako dane zamiast stałej ``STAGE_ORDER`` (§ 1.2).
    "process_editor": False,
    # Kategorie uczestników z własnymi progami i osobnymi rankingami (§ 1.2.4).
    "categories": False,
    # Zgłoszenia i punktacja drużynowa: ``Team``, ``TeamMember``, właściciel wpisu inny niż
    # uczestnik (§ 1.2.3).
    "team_entries": False,
    # Wagi zadań, przesunięcie skali, punkty ujemne i jawny porządek rozstrzygania remisów
    # (§ 1.2.6). Wyłączona zostawia dzisiejszą skalę 0-2-5-6 i sumę bez wag.
    "weighted_scoring": False,
    # Nazwane role recenzenckie i przydział prac według nich (§ 1.2.7).
    "reviewer_roles": False,
    # Placówki inne niż szkoła ponadpodstawowa we wspólnym wykazie (§ 1.3.2).
    "institution_types": False,
    # Własny słownik placówek organizatora wgrywany z CSV (§ 1.3.3). Wyłączona znaczy, że do
    # ``CustomInstitution`` nie idzie ani jedno zapytanie – wyszukiwarka pyta sam wykaz SIO.
    "custom_school_directory": False,
    # Podział terytorialny z drzewa ``Region`` zamiast z zamkniętej listy ``Voivodeship`` (§ 1.4).
    # Kolumny województw zostają wypełniane i czytane, dopóki flaga jest wyłączona.
    "custom_regions": False,
    # Wpisowe: cennik, zwolnienia, status płatności i dokumenty rozliczeniowe (§ 1.5.1).
    # Konkurs #1 jest bezpłatny i ma taki zostać.
    "fees": False,
    # Logistyka etapu stacjonarnego: miejsca, formularze przyjazdu, nocleg, listy obecności
    # (§ 1.5.2).
    "onsite_logistics": False,
    # Wielojęzyczność **treści** w drzewie stron (``WAGTAIL_I18N_ENABLED``, § 1.6). Wyłączona
    # znaczy jeden język i adresy bez prefiksu: ``/`` zostaje ``/``, a nie ``/pl/``.
    "content_translations": False,
    # --- konkursy w subdomenach platformy --------------------------------------------------------
    # Ekran „Nowy konkurs” w panelu koordynatora (``/coordinator/competitions/new/``): koordynator
    # zakłada z panelu kolejny konkurs, a ten staje pod adresem ``<slug>.<SITE_DOMAIN>``. Wyłączona
    # znaczy, że adresu nie ma (404) i że w menu nie przybywa ani jedna pozycja — czyli dokładnie
    # dzisiejszy panel Konkursu #1.
    #
    # Ekran wymaga **dwóch** zgód naraz i to nie jest nadmiarowa ostrożność: ta flaga jest decyzją
    # o **konkursie** („temu organizatorowi wolno zakładać kolejne”), a ustawienie instalacji
    # ``PLATFORM_SUBDOMAINS`` (``config/settings/base.py``) — stwierdzeniem o **serwerze** („jest
    # rekord wieloznaczny w DNS-ie i Caddy umie pobrać certyfikat na żądanie”). Sama flaga bez
    # ustawienia dałaby konkurs założony pod adresem, który nie odpowiada.
    "competition_creation": False,
    # --- forum uczestników -----------------------------------------------------------------------
    # Forum moderowane przez koordynatora (prośba organizatora z 21.09.2026). Wyłączona znaczy, że
    # adresów ``/forum/…`` i ``/coordinator/forum/`` **nie ma** (404) i że w żadnym menu nie
    # przybywa ani jedna pozycja — czyli dokładnie dzisiejszy serwis.
    #
    # Domyślnie wyłączona mimo tego, że ekran jest zamówiony wprost przez organizatora Konkursu #1
    # (a nie jest zdolnością systemu wielokonkursowego, jak piętnaście flag wyżej). Powód nie jest
    # techniczny: forum to miejsce, w którym **osoby niepełnoletnie piszą publicznie**, a jego
    # otwarcie wymaga dyżuru moderacyjnego po stronie organizatora. Włączenie tego przez wdrożenie
    # znaczyłoby otwarcie takiego miejsca w chwili, w której nikt go jeszcze nie pilnuje — dlatego
    # otwiera je świadomy wpis w panelu, a nie data wydania.
    "participant_forum": False,
    # --- materiały z warsztatów -------------------------------------------------------------------
    # Nagrania, pliki i odnośniki z warsztatów dla zalogowanych (prośba organizatora z 24.09.2026,
    # ``apps.workshop_materials``). Wyłączona znaczy, że adresów ``/warsztaty/materialy/…``
    # i ``/coordinator/workshops/materials/…`` **nie ma** (404), a strona „Warsztaty” i menu wyglądają
    # co do bajtu jak dziś. Domyślnie wyłączona, bo włączenie ma sens dopiero po dopisaniu uprawnień
    # wgrywania wieloczęściowego do polityki MinIO (``deploy/minio/policy-submissions.json``) i po
    # sprawdzeniu miejsca na dysku serwera (``docs/OPERACJE.md``) – czyli po kroku operatora,
    # którego wdrożenie samo nie wykona.
    "workshop_materials": False,
}


class Competition(models.Model):
    """Jeden konkurs wiedzy: marka, organizator, adresowanie i przełączniki funkcji.

    Konkurs jest **właścicielem** danych zawodów (edycje, uczestnicy, prace, wyniki) i zarazem
    tożsamością, którą widzi uczestnik: nazwą w temacie listu, domeną w linku i danymi
    administratora danych w klauzuli informacyjnej.

    Konkurs #1 („Olimpiada Kwantowa”) nie powstaje z seedów, tylko z **danych już stojących
    w produkcyjnej bazie** – patrz migracja ``0002_competition_from_site``.
    """

    # --- tożsamość ---------------------------------------------------------------------------
    #: ``OneToOne`` z ``PROTECT``: skasowanie witryny w ``/cms/`` nie może osierocić konkursu
    #: razem z jego edycjami, pracami i wynikami. Relacja jest jeden do jednego, bo drzewo stron
    #: konkursu to dokładnie drzewo jego witryny – dwie witryny dla jednego konkursu znaczyłyby
    #: dwie odpowiedzi na pytanie „jaka jest strona główna tego konkursu”.
    site = models.OneToOneField(
        "wagtailcore.Site",
        on_delete=models.PROTECT,
        related_name="competition",
        verbose_name="witryna",
    )
    slug = models.SlugField("identyfikator", max_length=50, unique=True)
    is_active = models.BooleanField("aktywny", default=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    # --- marka -------------------------------------------------------------------------------
    name = models.CharField("nazwa", max_length=200)
    short_name = models.CharField("nazwa skrócona", max_length=60, blank=True)
    #: Odmiana nazwy własnej. Dwa pola są tańsze niż reguły fleksyjne i uczciwsze niż mianownik
    #: w każdym zdaniu: listy piszą „komitet **Olimpiady Kwantowej**” i „udział w **Olimpiadzie
    #: Kwantowej**”. Ten sam problem jest już opisany w ``apps/accounts/consents.py`` przy nazwie
    #: organizatora („odmieniać cudzej nazwy własnej w kodzie nie będziemy”).
    #: Puste pole znaczy „użyj ``name``” – patrz ``genitive`` i ``locative``.
    genitive_name = models.CharField("nazwa w dopełniaczu", max_length=200, blank=True)
    locative_name = models.CharField("nazwa w miejscowniku", max_length=200, blank=True)
    tagline = models.CharField("hasło", max_length=200, blank=True)
    #: Kolor akcentu jako wartość, nie jako arkusz stylów: szablon wystawia go jako zmienną CSS
    #: na ``<html>``. Generowanie arkusza per konkurs unieważniałoby manifest WhiteNoise
    #: (``CompressedManifestStaticFilesStorage``) i wymagałoby budowania statyków przy każdym
    #: nowym konkursie – czyli wdrożenia zamiast wpisu w panelu.
    accent_colour = models.CharField(
        "kolor akcentu", max_length=7, blank=True, validators=[validate_hex_colour]
    )
    #: Logotyp i favikona przez bibliotekę obrazów Wagtaila, bo redaktor i tak wgrywa tam grafiki.
    #: ``SET_NULL``: skasowanie obrazu ma zdjąć znak, a nie wywrócić konkurs; ``related_name="+"``,
    #: bo od obrazu nikt nie pyta o konkurs.
    logo = models.ForeignKey(
        "wagtailimages.Image",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="logotyp",
    )
    favicon = models.ForeignKey(
        "wagtailimages.Image",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="favikona",
    )

    # --- organizator (podmiot prawny) ---------------------------------------------------------
    organizer_name = models.CharField("organizator", max_length=200)
    organizer_address = models.CharField("adres", max_length=200, blank=True)
    organizer_registry = models.CharField("dane rejestrowe", max_length=200, blank=True)
    organizer_url = models.URLField("strona organizatora", blank=True)
    contact_email = models.EmailField("e-mail kontaktowy", blank=True)
    contact_phone = models.CharField("telefon", max_length=40, blank=True)
    #: Adres inspektora ochrony danych **organizatora**: to on jest administratorem danych swoich
    #: uczestników, a operator platformy procesorem (``docs/UNIWERSALNY-ETAP-1.md`` § 8, D5).
    dpo_email = models.EmailField("inspektor ochrony danych", blank=True)

    # --- poczta --------------------------------------------------------------------------------
    #: Puste pola znaczą „weź ustawienie instalacji” (``DEFAULT_FROM_EMAIL``,
    #: ``EMAIL_SUBJECT_PREFIX``). Uwaga operacyjna: relay w compose podpisuje DKIM-em jedną
    #: domenę, więc nadawca w obcej domenie przejdzie, ale trafi do spamu – rekomendowany
    #: układ to nadawca w domenie platformy i ``Reply-To`` na ``contact_email`` (§ 8, D6).
    from_email = models.EmailField("nadawca listów", blank=True)
    email_subject_prefix = models.CharField("prefiks tematu", max_length=60, blank=True)
    #: Adresy, na które serwis przekazuje **każde** przyjęte rozwiązanie (prośba organizatora
    #: z 20.09.2026). Puste pole znaczy „funkcja wyłączona” i to jest stan domyślny każdego
    #: konkursu – przekazywanie wynosi prace uczestników poza serwis, więc nie może się włączyć
    #: przez wdrożenie, tylko przez świadomy wpis w panelu.
    #:
    #: Pole **tekstowe z listą**, a nie ``EmailField`` ani osobna tabela: komitet ma jedną, dwie,
    #: czasem trzy skrzynki i zmienia je raz na edycję. Tabela dawałaby ekran z dodawaniem
    #: i kasowaniem wierszy dla trzech wartości, a pojedynczy ``EmailField`` zmuszałby organizatora
    #: do zakładania aliasu u swojego dostawcy poczty, żeby wpisać dwa adresy.
    #:
    #: Konkurs jest właścicielem tego ustawienia, a nie edycja: skrzynka komitetu przeżywa rocznik,
    #: a odpowiedź na pytanie „dokąd idą prace tego organizatora” nie może zależeć od tego, którą
    #: edycję akurat wybrano.
    submission_forward_emails = models.TextField(
        "przekazywanie rozwiązań",
        blank=True,
        validators=[validate_submission_forward_emails],
        help_text=(
            "Adresy rozdzielone przecinkiem albo nową linią (najwyżej pięć). Puste pole wyłącza "
            "przekazywanie."
        ),
    )

    # --- adresowanie ---------------------------------------------------------------------------
    routing_mode = models.CharField(
        "tryb adresowania",
        max_length=8,
        choices=RoutingMode.choices,
        default=RoutingMode.DOMAIN,
    )
    #: Dubluje ``site.hostname`` **celowo**: witrynę zmienia redaktor w ``/cms/``, a my
    #: potrzebujemy domeny tam, gdzie żądania nie ma – w liście z zadania Celery, w komendzie
    #: i przy porównaniu z ``ALLOWED_HOSTS``. Zgodności pilnują ``clean()`` i sygnał
    #: ``post_save`` na ``Site`` (``apps/tenancy/signals.py``).
    primary_domain = models.CharField("domena główna", max_length=255, blank=True)
    path_prefix = models.SlugField("prefiks ścieżki", max_length=40, blank=True)

    # --- identyfikatory drukowane ----------------------------------------------------------------
    #: Prefiks kodu publicznego uczestnika (``OLM-XXXXXX``). Był stałą modułu
    #: (``apps.accounts.models.PUBLIC_CODE_PREFIX``), a kod publiczny jest identyfikatorem
    #: w tabelach wyników **jednego** konkursu – dwie olimpiady pod jednym prefiksem dawałyby
    #: w liście „OLM-ABC234” bez informacji, czyj to wynik. Konkurs #1 ma tu dokładnie ``OLM-``,
    #: więc żaden istniejący kod się nie zmienia (``docs/UNIWERSALNY-ETAP-1.md`` § 3.3).
    #: Kody już nadane zostają nietknięte: pole opisuje **nowe** kody, a nie zastane.
    public_code_prefix = models.CharField("prefiks kodu uczestnika", max_length=8, default="OLM-")
    #: Prefiks numeru dyplomu (``OK/2026/0001``). Był stałą ``CERTIFICATE_NUMBER_PREFIX``
    #: w ``apps.results.models``. Unikalność samego numeru zostaje **globalna**, bo prefiks jest
    #: jego częścią, a strona weryfikacji dyplomu jest publiczna i działa bez wskazania konkursu.
    certificate_prefix = models.CharField("prefiks numeru dyplomu", max_length=8, default="OK")

    # --- zachowanie ------------------------------------------------------------------------------
    default_language = models.CharField("język domyślny", max_length=8, default="pl")
    #: Strefa czasowa jest per konkurs, bo ``WARSAW`` w ``apps/competitions/models.py`` jest dziś
    #: stałą modułu. Etap 1 **nie zmienia** obliczeń czasu – pole jest wypełniane i pokazywane,
    #: a użycie go w prezentacji terminów to etap 2.
    time_zone = models.CharField("strefa czasowa", max_length=64, default="Europe/Warsaw")
    feature_flags = models.JSONField("przełączniki", default=dict, blank=True)

    class Meta:
        verbose_name = "konkurs"
        verbose_name_plural = "konkursy"
        ordering = ("name", "id")

    def __str__(self) -> str:
        return self.short_name or self.name

    # --- odczyt ------------------------------------------------------------------------------
    @property
    def genitive(self) -> str:
        """Nazwa w dopełniaczu, z odwrotem na mianownik. Puste pole = „nie odmieniamy”."""
        return self.genitive_name or self.name

    @property
    def locative(self) -> str:
        """Nazwa w miejscowniku, z odwrotem na mianownik."""
        return self.locative_name or self.name

    @property
    def forward_emails(self) -> list[str]:
        """Adresy przekazywania rozwiązań – **jedyne** wejście do kolumny tekstowej.

        Pusta lista znaczy „przekazywanie wyłączone”, i to jest ta sama odpowiedź, co dla pola
        wypełnionego samymi spacjami. Gdyby wołający rozbijał napis sam, każde miejsce miałoby
        własne zdanie o tym, co jest wpisem pustym – a jedno z nich prędzej czy później wysłałoby
        list na adres ``""``.
        """
        return split_forward_emails(self.submission_forward_emails)

    def has_feature(self, name: str) -> bool:
        """Czy funkcja ``name`` jest w tym konkursie włączona.

        **Jedyne** wejście do ``feature_flags``. Zapis ``competition.feature_flags.get(...)``
        rozsiany po widokach znaczyłby, że domyślna wartość flagi jest zapisana w tylu miejscach,
        ile jest odczytów – a wtedy pierwsza zmiana domyślnej wartości byłaby zmianą niepełną.

        Nieznana nazwa podnosi ``KeyError``, zamiast oddać ``False``: literówka w nazwie flagi
        wyglądałaby wtedy jak funkcja wyłączona przez organizatora i nikt by jej nie znalazł.
        """
        if name not in FEATURE_DEFAULTS:
            raise KeyError(f"Nieznany przełącznik konkursu: {name!r}.")
        value = (self.feature_flags or {}).get(name, FEATURE_DEFAULTS[name])
        return bool(value)

    # --- walidacja ---------------------------------------------------------------------------
    def clean(self) -> None:
        """Spójność adresowania: domena zgodna z witryną, prefiks obowiązkowy tylko w trybie ``PATH``.

        Walidacja jest tu, a nie w formularzu panelu, bo te reguły obowiązują tak samo komendę
        zakładającą konkurs, migrację i import – formularz jest tylko jednym z wołających.
        """
        super().clean()
        errors: dict[str, str] = {}

        if not self.primary_domain and self.site_id:
            # Nie jest to „poprawka danych wpisanych przez człowieka”, tylko wypełnienie
            # wartości, której jedynym sensownym źródłem jest witryna konkursu.
            self.primary_domain = self.site.hostname

        if self.routing_mode == RoutingMode.PATH and not self.path_prefix:
            errors["path_prefix"] = "Tryb prefiksu ścieżki wymaga podania prefiksu."
        if self.routing_mode == RoutingMode.DOMAIN and not self.primary_domain:
            errors["primary_domain"] = "Tryb własnej domeny wymaga podania domeny."

        if self.path_prefix:
            # Import lokalny: ``apps.cms`` importuje modele Wagtaila, a ten moduł jest ładowany
            # przy starcie aplikacji wcześniej. Lista slugów zarezerwowanych jest jedna dla całej
            # instalacji – prefiks ścieżki i slug strony drugiego poziomu konkurują o ten sam
            # pierwszy segment adresu.
            from apps.cms.models import RESERVED_SLUGS, taken_first_segments

            if self.path_prefix in RESERVED_SLUGS:
                errors["path_prefix"] = (
                    "Ten prefiks należy do adresów aplikacji i przechwyciłby je dla konkursu."
                )
            elif self.path_prefix in taken_first_segments():
                # Druga strona reguły z § 2.3: prefiks nie może przechwycić istniejącej strony
                # drugiego poziomu domyślnej witryny (np. „zadania” Konkursu #1).
                errors["path_prefix"] = (
                    "Ten prefiks jest już adresem strony w serwisie i przechwyciłby ją dla konkursu."
                )

        if errors:
            raise ValidationError(errors)


# Wpisowe (cennik konkursu, rejestr należności, dokument rozliczeniowy) mieszka razem z resztą
# swojej logiki w ``apps.tenancy.fees`` – modele i czynności w jednym pliku, bo czyta się je
# wyłącznie razem (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.1). Django rejestruje modele wtedy, gdy
# importuje ``models`` aplikacji, więc bez tej linijki ``makemigrations`` nie zobaczyłby tabel.
# Import stoi na **końcu** pliku i jest bezpieczny: ``fees`` sięga do ``Competition``, ``Edition``,
# ``Category`` i ``Participant`` wyłącznie przez nazwy („tenancy.Competition”, …), a jedyny jego
# import z domeny zawodów (``apps.competitions.scoping``) nie dotyka modeli.
from .fees import (  # noqa: E402,F401  (import dla rejestracji modeli)
    FeeSchedule,
    FeeStatus,
    ParticipantFee,
)
