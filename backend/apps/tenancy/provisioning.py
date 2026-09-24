"""Zakładanie konkursu jako **czynność**, a nie jako komenda powłoki.

Do tej pory cała wiedza „co znaczy założyć konkurs” stała w ``handle()`` komendy
``manage.py create_competition``. Było to dobre miejsce dokładnie tak długo, jak długo konkurs
zakładał administrator z powłoki. Odkąd robi to także koordynator z panelu
(``/coordinator/competitions/new/``), miejsce przestało wystarczać: widok WWW, który woła komendę,
dostaje w zamian **napis** (to, co komenda wypisała), a potrzebuje wierszy — konkursu, edycji,
etapów i liczb zestawów startowych, żeby pokazać podgląd przed zapisem i adres po zapisie.

Dlatego czynność mieszka tutaj, a komenda jest jej **cienką obwolutą**: przyjmuje argumenty
z wiersza poleceń, woła :func:`create_competition_from_template` i składa z wyniku swój wydruk.
Kontrakt komendy (nazwy argumentów, treść odmów, kształt wydruku) zostaje bez zmian i jest
sprawdzany tymi samymi testami, co przedtem — ten plik nie jest zmianą zachowania, tylko zmianą
miejsca, w którym ono stoi.

Powody istnienia jednej drogi zakładania konkursu zostają te same, co w docstringu komendy: konkurs
to cztery wiersze w trzech aplikacjach (``wagtailcore.Site``, ``wagtailcore.Page``,
``cms.SiteSettings``, ``tenancy.Competition``) plus edycja z etapami i cztery zestawy startowe,
a każdy z nich wpisany ręcznie w ``/admin/`` jest okazją do pominięcia jednego z pozostałych.

Czego ta funkcja **nie** robi i dlaczego:

- **nie uruchamia seedów treści.** ``seed_cms``, ``seed_regulamin``, ``seed_legacy_content``
  i ``seed_partners`` wpisują akapity Olimpiady Kwantowej. Nowy konkurs dostaje puste strony
  o właściwych adresach — treść należy do jego redakcji. Wyjątkiem są komendy wymienione
  w ``safe_seeds`` szablonu: globalne i idempotentne (dziś: sam ``seed_schools``) — i te chodzą
  **wyłącznie** na wyraźne życzenie wołającego (``run_safe_seeds=True``). Domyślne ``False`` jest
  wartością dla panelu: wykaz SIO/RSPO to kilka tysięcy wierszy wpisywanych **poza** transakcją,
  czyli robota, której nie wolno wykonać w środku żądania HTTP (a na działającej instalacji wykaz
  i tak już stoi w bazie, bo jest wspólny dla wszystkich konkursów).
- **nie wpisuje terminów zawodów.** Edycję i etapy zakłada, ale ich oś czasu jest **wartością
  początkową**: pierwszy etap otwiera się pierwszego dnia następnego miesiąca, a kolejne odkładają
  się od niego odstępami z szablonu. Reguły wokół terminu oddania są te same, co
  w ``seed_edition_kwantowa`` (recenzje +14 dni, okno reklamacji +2/+9 dni względem terminu
  recenzji) — skopiowane są **reguły**, nie daty.
- **nie zakłada kont.** ``coordinator`` jest **istniejącym** kontem; wołający, który ma sam adres
  e-mail, przepuszcza go przez :func:`coordinator_from_email`, a nieznany adres jest tam błędem,
  a nie zaproszeniem do założenia konta bez wiedzy jego właściciela.
- **nie dotyka ``.env`` ani ``deploy/Caddyfile``.** Oba pliki należą do administratora serwera,
  a kod chodzi w kontenerze aplikacji, który ich nie widzi (i nie ma prawa widzieć). Zamiast tego
  wynik niesie :attr:`ProvisioningResult.env_lines` — linijki do wklejenia, albo pustą krotkę,
  gdy wklejać nie ma czego (tryb prefiksu ścieżki i konkurs pod subdomeną platformy).

Błędem tej warstwy jest :class:`ProvisioningError` — zwykły wyjątek, a nie ``CommandError``:
funkcję woła zarówno komenda (która zamienia go na ``CommandError`` i kończy proces), jak i widok
WWW (który zamienia go na komunikat w formularzu). Wyjątek komendy w widoku byłby zależnością
interfejsu WWW od modułu wiersza poleceń.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from wagtail.models import Page, Site

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.resolution import is_platform_subdomain
from apps.tenancy.templates_catalog import TEMPLATES

#: Strefa organizatora. Terminy liczymy w niej, a nie w UTC: „ostatni dzień o 23:59” ma być tą
#: godziną w Warszawie także wtedy, gdy serwer stoi gdzie indziej (do bazy i tak trafia UTC).
WARSAW = ZoneInfo("Europe/Warsaw")

#: Odstępy wokół terminu oddania — **te same reguły**, co w ``seed_edition_kwantowa``, i ta sama
#: kolejność wynikania: recenzje po terminie oddania, okno reklamacji po terminie recenzji.
#: Skopiowana jest reguła, a nie data: daty tamtej komendy są harmonogramem Konkursu #1.
REVIEW_AFTER_DEADLINE = timedelta(days=14)
APPEAL_OPENS_AFTER_REVIEW = timedelta(days=2)
APPEAL_CLOSES_AFTER_REVIEW = timedelta(days=9)

#: Godziny brzegowe etapu: otwarcie o północy, termin oddania o 23:59 czasu polskiego.
OPENS_TIME = (0, 0)
DEADLINE_TIME = (23, 59)

#: Okno reklamacji etapu treningowego — jeden dzień po dacie-wartowniku, dokładnie tak, jak
#: ustawia je ``seed_training_problems``. Model wymaga niepustego okna także tam, gdzie nikt
#: z niego nie skorzysta, bo trening nikogo nie kwalifikuje.
TRAINING_APPEAL_WINDOW = timedelta(days=1)

#: Wzorzec oznaczenia pierwszej edycji nowego konkursu — ten sam kształt, co ``EDITION_LABEL``
#: w ``seed_edition_kwantowa`` („I edycja 2026/2027”). Rocznik liczy się z dnia uruchomienia,
#: a nie ze stałej: konkurs założony we wrześniu i konkurs założony w maju mają dostać rocznik,
#: w którym naprawdę się odbędą.
EDITION_LABEL_PATTERN = "I edycja {school_year}"

#: Miesiąc, od którego liczy się nowy rok szkolny. Wrzesień, bo tak liczy go organizator
#: (I edycja 2026/2027 otwiera eliminacje 1 września 2026).
SCHOOL_YEAR_FIRST_MONTH = 9

#: Port witryny zakładanej tą drogą. Taki sam, jaki wpisuje ``cms.0002_initial_tree``
#: Konkursowi #1: ruch publiczny idzie przez Caddy'ego, który kończy TLS i puka do aplikacji po
#: HTTP, więc numer portu w ``wagtailcore.Site`` jest wartością dopasowania nagłówka ``Host``,
#: a nie adresem, pod którym cokolwiek nasłuchuje.
DEFAULT_SITE_PORT = 80

#: Pola ``cms.SiteSettings`` z wartościami domyślnymi Olimpiady Kwantowej, które nowy konkurs musi
#: dostać **puste**. To nie jest kosmetyka: domyślny ``facebook_url`` wskazuje profil Olimpiady
#: Kwantowej, a ``registration_note`` ogłasza jej datę startu — nowy konkurs pokazałby jedno
#: i drugie w stopce pod cudzą nazwą, w dniu, w którym powstał.
BLANKED_SITE_SETTINGS: tuple[str, ...] = (
    "tagline",
    "organizer_address",
    "organizer_registry",
    "contact_phone",
    "contact_url",
    "facebook_url",
    "linkedin_url",
    "instagram_url",
    "x_url",
    "tiktok_url",
    "youtube_url",
    "registration_note",
    "ga_measurement_id",
)

#: Wersja pierwszego zestawu szablonów tekstu dokumentów nowego konkursu. **Nie**
#: ``apps.tenancy.documents.INITIAL_VERSION`` („1.0 (stan z v0.23.0)”): tamta nazywa stan
#: Konkursu #1 w chwili migracji ``tenancy.0005`` i w konkursie założonym dwa lata później byłaby
#: nazwą wydania, którego jego koordynator nigdy nie widział. Tu chodzi o pierwszą wersję **tego**
#: konkursu, a ta nazywa się po prostu „1.0”.
INITIAL_DOCUMENT_VERSION = "1.0"

#: Zdania główne dokumentów nowego konkursu: ``DOCUMENT_STATEMENTS``
#: (``apps/results/certificates.py``) z marką Olimpiady Kwantowej zamienioną na znaczniki
#: ``{competition_genitive}`` i ``{competition_locative}``.
#:
#: Dlaczego nie kopia tamtych napisów co do znaku — mimo że tak robi migracja ``tenancy.0005``:
#: tamte zdania mówią „Olimpiady Kwantowej” w dopełniaczu, a migracja daje je **wyłącznie**
#: konkursowi o najniższym ``pk`` i pisze wprost, że konkurs założony później dostaje swoje teksty
#: z panelu. Wpisanie ich nowemu konkursowi byłoby wpisaniem cudzej marki do konfiguracji drugiego
#: organizatora (§ 0.1). Znacznik w tym samym miejscu zdania daje ten sam dokument, tylko z nazwą
#: tego konkursu — a odmianę niesie ``apps/tenancy/branding.py``, czyli jedno źródło.
#:
#: Klucze są wartościami ``apps.tenancy.documents.DocumentKind`` zapisanymi wprost, żeby ten moduł
#: nie musiał importować modeli w czasie wczytywania.
INITIAL_DOCUMENT_STATEMENTS: dict[str, str] = {
    "LAUREAT": "uzyskał(a) tytuł laureata {competition_genitive}",
    "FINALISTA": "uzyskał(a) tytuł finalisty {competition_genitive}",
    "UCZESTNIK": "brał(a) udział w {competition_locative}",
    "OPIEKUN": "sprawował(a) opiekę nad uczestnikami {competition_genitive}",
    "WARSZTATY": "uczestniczył(a) w warsztatach online {competition_genitive}",
}

#: Linia podpisu — ``SIGNATURE_LINE`` z tą samą zamianą i z tego samego powodu.
INITIAL_SIGNATURE_LINE = "Przewodniczący Komitetu Sterującego {competition_genitive}"


class ProvisioningError(Exception):
    """Odmowa założenia konkursu wypowiedziana zdaniem dla człowieka.

    Zwykły wyjątek, a nie ``CommandError`` i nie ``ValidationError``: wołający decyduje, czym ta
    odmowa jest w jego świecie — komenda kończy nią proces, widok WWW wpisuje ją do formularza.
    Treść komunikatu jest częścią kontraktu komendy (testy dopasowują go wzorcem), więc zmienia
    się ją razem z nimi.
    """


@dataclass(frozen=True)
class ProvisioningResult:
    """Co powstało (albo powstałoby przy ``dry_run``) — wiersze, liczby i linijki do ``.env``.

    Wynik niesie **obiekty**, a nie napisy, bo o to właśnie chodziło w wydzieleniu tej funkcji:
    komenda złoży z nich swój wydruk, a panel — podgląd przed zapisem i stronę po zapisie. Przy
    ``dry_run`` transakcja jest już wycofana, więc obiekty mają ``pk`` bez wiersza w bazie:
    czytelnik ma prawo do ich **pól**, ale nie do relacji, których jeszcze nie wczytano.
    """

    competition: Competition
    edition: object
    stages: list
    #: Liczby zestawów startowych: ``consents``, ``documents``, ``regions``, ``pipeline`` oraz
    #: nazwa grupy redakcyjnej pod kluczem ``cms_group``.
    seeded: dict
    #: Nazwa szablonu z ``templates_catalog.TEMPLATES`` i sam szablon — wołający pokazuje etykietę
    #: i listę dokumentów, a drugi odczyt katalogu u siebie byłby drugim miejscem na pomyłkę.
    template_name: str
    template: dict
    coordinator: object | None
    #: Linijki do ``.env`` administratora serwera; pusta krotka znaczy „nie ma czego wklejać”.
    env_lines: tuple[str, ...]
    dry_run: bool
    #: Konkurs platformy (witryny domyślnej), pod którego hostem odpowiada konkurs w trybie
    #: prefiksu ścieżki, albo ``None`` dla konkursu z własną domeną. Wołający wypisuje go, bo tej
    #: czynności ubocznej administrator ma się dowiedzieć z wydruku, a nie z ``/admin/``.
    platform: Competition | None = None
    #: Czy to założenie **włączyło** konkursowi platformy ``path_prefix_routing`` (wcześniej było
    #: wyłączone). ``False`` przy konkursie z domeną i przy bramce otwartej już wcześniej.
    opened_platform: bool = False


def coordinator_from_email(email: str):
    """Konto o tym adresie albo ``None`` dla pustego adresu. Nieznany adres = odmowa.

    Zakładanie konta „przy okazji” dałoby osobę z rolą koordynatora, która o tym nie wie, nie ma
    hasła i nie potwierdziła adresu — czyli konto obsługiwane wyłącznie przez reset hasła na
    adres, którego nikt nie zweryfikował.
    """
    email = (email or "").strip().lower()
    if not email:
        return None

    from apps.accounts.models import User

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        raise ProvisioningError(
            f"Nie ma konta o adresie „{email}”. Komenda nie zakłada kont — załóż je "
            f"rejestracją albo komendą bootstrap_coordinator i uruchom ponownie."
        )
    return user


def create_competition_from_template(
    *,
    slug: str,
    name: str,
    domain: str,
    template: str,
    organizer: str = "",
    contact_email: str = "",
    short_name: str = "",
    accent: str = "",
    path_prefix: str = "",
    edition_label: str = "",
    coordinator=None,
    public_code_prefix: str = "",
    certificate_prefix: str = "",
    dry_run: bool = False,
    run_safe_seeds: bool = False,
) -> ProvisioningResult:
    """Zakłada konkurs w komplecie: witrynę, drzewo stron, ustawienia, edycję, etapy i zestawy.

    ``template`` jest **nazwą** z ``apps.tenancy.templates_catalog.TEMPLATES`` (a nie słownikiem):
    wołający ma podać to, co widzi w interfejsie — nazwę wybraną z listy — a rozwinięcie jej
    w wartości należy do tej funkcji, żeby nie dało się przekazać szablonu spoza katalogu.

    ``dry_run`` wykonuje **całość** i wycofuje transakcję: próba na sucho ma sprawdzić to, co
    sprawdzi baza (unikalność, więzy, walidacja modeli), a nie to, co pamiętamy o bazie. Cena —
    chwilowe wiersze w transakcji — jest żadna, a zysk polega na tym, że podgląd w panelu pokazuje
    liczby policzone przez ten sam kod, który za chwilę je zapisze.

    ``run_safe_seeds`` jest domyślnie **wyłączone** i to jest wartość dla żądania HTTP — patrz
    docstring modułu. Seedy chodzą **poza** transakcją (są globalne i idempotentne), więc przy
    ``dry_run`` nie chodzą nigdy: ich wierszy nie ma czego wycofywać.
    """
    slug = (slug or "").strip().lower()
    name = (name or "").strip()
    hostname, port = split_domain(domain)
    if template not in TEMPLATES:
        raise ProvisioningError(f"Nie ma szablonu o nazwie „{template}”.")
    template_name = template
    template_spec = TEMPLATES[template_name]

    if Competition.objects.filter(slug=slug).exists():
        raise ProvisioningError(
            f"Konkurs o identyfikatorze „{slug}” już istnieje. Identyfikator jest trwały "
            f"(wchodzi do adresów i do eksportów), więc komenda go nie nadpisuje."
        )

    # Domena jest sprawdzana w dwóch tabelach, bo w dwóch może być zajęta: witryna rozstrzyga
    # żądanie (``Site.find_for_request``), a ``primary_domain`` buduje linki poza żądaniem.
    # Druga witryna pod tym samym hostem znaczyłaby, że o treści pod adresem decyduje kolejność
    # wierszy w bazie.
    if Site.objects.filter(hostname__iexact=hostname, port=port).exists():
        raise ProvisioningError(f"Witryna dla hosta „{hostname}:{port}” już istnieje.")
    if Competition.objects.filter(primary_domain__iexact=hostname).exists():
        raise ProvisioningError(f"Domena „{hostname}” należy już do innego konkursu.")

    today = timezone.localtime(timezone.now(), WARSAW).date()
    label = (edition_label or "").strip() or EDITION_LABEL_PATTERN.format(school_year=school_year(today))
    short = (short_name or name).strip()

    with transaction.atomic():
        competition = _create(
            slug=slug,
            name=template_spec["name_pattern"].format(name=name),
            short_name=template_spec["short_name_pattern"].format(name=short),
            hostname=hostname,
            port=port,
            path_prefix=(path_prefix or "").strip().lower(),
            organizer=(organizer or name).strip(),
            contact_email=(contact_email or "").strip(),
            accent=(accent or template_spec["accent_colour"]).strip(),
            public_code_prefix=(public_code_prefix or "").strip() or default_public_code_prefix(slug),
            certificate_prefix=(certificate_prefix or "").strip() or default_certificate_prefix(slug),
            template=template_spec,
        )
        edition, stages = _create_edition(competition, template_spec, label=label, base_day=base_day(today))
        if coordinator is not None:
            _grant_coordinator(coordinator, competition)
        seeded = _seed_configuration(competition, coordinator)
        seeded["pipeline"] = len(_seed_pipeline(edition))
        platform, opened_platform = _open_platform_for_prefix(competition)
        if dry_run:
            # Wycofanie **po** wykonaniu całości, a nie pominięcie zapisów – patrz docstring.
            transaction.set_rollback(True)

    if run_safe_seeds and not dry_run:
        _run_safe_seeds(template_spec)

    return ProvisioningResult(
        competition=competition,
        edition=edition,
        stages=stages,
        seeded=seeded,
        template_name=template_name,
        template=template_spec,
        coordinator=coordinator,
        env_lines=env_lines_for(competition),
        dry_run=dry_run,
        platform=platform,
        opened_platform=opened_platform,
    )


# --- zakładanie ---------------------------------------------------------------------------------


def _create(
    *,
    slug: str,
    name: str,
    short_name: str,
    hostname: str,
    port: int,
    path_prefix: str,
    organizer: str,
    contact_email: str,
    accent: str,
    public_code_prefix: str,
    certificate_prefix: str,
    template: dict,
) -> Competition:
    from apps.cms.models import HomePage, SiteSettings

    root = Page.get_first_root_node()
    if root is None:  # pragma: no cover - korzeń zakłada wagtailcore.0002_initial_data
        raise ProvisioningError("Brak korzenia drzewa stron Wagtaila – baza nie jest zmigrowana.")

    # Strony powstają **bez rewizji**, dokładnie tak, jak drzewo Konkursu #1 z migracji
    # ``cms.0002_initial_tree``: są od razu opublikowane, a pierwszą rewizją będzie pierwsza
    # poprawka redaktora. Publikowanie rewizji tuż po ``add_child`` byłoby zapisem stanu
    # sprzed dołożenia dzieci (rewizja niesie też ``path`` i ``numchild``) — czyli ryzykiem
    # rozjazdu drzewa w zamian za pustą pozycję w historii.
    #
    # ``HomePage.max_count == 1`` dotyczy **panelu** (``Page.can_create_at``), a nie zapisu:
    # Wagtail pilnuje w ten sposób, żeby redaktor nie założył drugiej strony głównej ręcznie.
    # Drugi konkurs to drugie drzewo z własną stroną główną i to jest stan zamierzony — dlatego
    # zakłada ją ta funkcja, a nie człowiek w ``/cms/``.
    home = HomePage(
        title=name,
        slug=slug,
        hero_title=name,
        # Sekcje strony głównej zostają puste: treść należy do redakcji nowego konkursu,
        # a domyślne „O Olimpiadzie” byłoby nagłówkiem nad pustką.
        about_title="",
        show_timeline=True,
    )
    root.add_child(instance=home)

    site = Site.objects.create(
        hostname=hostname,
        port=port,
        site_name=name,
        root_page=home,
        # Witryna domyślna jest **jedna** i jest nią Olimpiada Kwantowa: to ona odpowiada na
        # żądania z nieznanym hostem (także deweloperskie ``localhost``). Odebranie jej tej
        # roli zmieniłoby zachowanie działającej produkcji — § 0.
        is_default_site=False,
    )

    for model_label, page_slug, title in template["pages"]:
        model = django_apps.get_model(model_label)
        page = model(title=title, slug=page_slug, show_in_menus=True)
        home.add_child(instance=page)

    settings_row = SiteSettings.for_site(site)
    settings_row.site_name = name
    settings_row.organizer_name = organizer
    settings_row.contact_email = contact_email
    settings_row.organizer_logo = None
    for field in BLANKED_SITE_SETTINGS:
        setattr(settings_row, field, "")
    settings_row.save()

    competition = Competition(
        site=site,
        slug=slug,
        name=name,
        short_name=short_name,
        tagline="",
        accent_colour=accent,
        organizer_name=organizer,
        contact_email=contact_email,
        # Nadawca listów zostaje pusty = „weź ``DEFAULT_FROM_EMAIL`` instalacji”. To jest
        # rekomendacja, a nie brak: relay w compose podpisuje DKIM-em jedną domenę, więc
        # nadawca w domenie organizatora przeszedłby, ale trafiłby do spamu (§ 2.6).
        from_email="",
        email_subject_prefix=template["email_subject_prefix_pattern"].format(short_name=short_name),
        routing_mode=RoutingMode.PATH if path_prefix else RoutingMode.DOMAIN,
        primary_domain=hostname,
        path_prefix=path_prefix,
        feature_flags=dict(template["feature_flags"]),
        # Własne przedrostki od pierwszego dnia: ``OLM-``/``OK`` należą do Konkursu #1, a kod
        # publiczny jest identyfikatorem w tabelach wyników **jednego** konkursu (etap 1 § 3.3).
        public_code_prefix=public_code_prefix,
        certificate_prefix=certificate_prefix,
    )
    try:
        # ``full_clean`` zamiast samego ``save``: reguły spójności adresowania (domena zgodna
        # z witryną, prefiks spoza listy slugów zarezerwowanych) stoją w ``Competition.clean``
        # właśnie po to, żeby obowiązywały tak samo komendę, jak formularz w panelu.
        competition.full_clean()
    except ValidationError as exc:
        raise ProvisioningError(_flatten(exc)) from exc
    competition.save()
    return competition


def _open_platform_for_prefix(competition: Competition) -> tuple[Competition | None, bool]:
    """Konkurs platformy z otwartą bramką ``path_prefix_routing`` – dla konkursu w trybie ``PATH``.

    Konkurs pod prefiksem ścieżki odpowiada wyłącznie pod hostem, którego konkurs ma włączone
    ``path_prefix_routing`` (``apps.tenancy.resolution.hosts_path_prefixes``). Założenie konkursu
    z ``--path-prefix`` **jest** decyzją „ten konkurs stoi pod adresem platformy”, więc otwieramy
    bramkę konkursowi witryny domyślnej – tej, która odpowiada pod domeną platformy – w tej samej
    transakcji. Bez tego nowy konkurs powstawałby z adresem, który od pierwszej chwili daje 404,
    a administrator dowiadywałby się o drugim kroku dopiero z pierwszego zgłoszenia.

    Zapis idzie przez ``update()`` na jednym polu: nie woła ``full_clean`` ani sygnałów zapisu
    konkursu platformy, a przede wszystkim nie nadpisuje pozostałych przełączników tego konkursu.
    Brak konkursu platformy jest odmową: prefiks ścieżki nie miałby wtedy pod czym odpowiadać.
    """
    from apps.tenancy.resolution import PATH_PREFIX_FLAG

    if competition.routing_mode != RoutingMode.PATH:
        return None, False
    platform = (
        Competition.objects.filter(site__is_default_site=True, is_active=True)
        .exclude(pk=competition.pk)
        .first()
    )
    if platform is None:
        raise ProvisioningError(
            "Tryb prefiksu ścieżki wymaga konkursu platformy (aktywny konkurs witryny domyślnej) – "
            "pod jego domeną odpowiada konkurs z prefiksem. Załóż konkurs z własną domeną (--domain) "
            "albo najpierw konkurs platformy."
        )
    if platform.has_feature(PATH_PREFIX_FLAG):
        return platform, False
    flags = {**(platform.feature_flags or {}), PATH_PREFIX_FLAG: True}
    Competition.objects.filter(pk=platform.pk).update(feature_flags=flags)
    platform.feature_flags = flags
    return platform, True


# --- edycja i etapy -----------------------------------------------------------------------------


def _create_edition(competition: Competition, template: dict, *, label: str, base_day: date):
    """Pierwsza edycja konkursu (bieżąca) i etapy z szablonu. Zwraca ``(edycja, etapy)``.

    Etapy powstają przez ``apps.competitions.services.create_stage``, a nie przez
    ``Stage.objects.create``: to tam stoi wiedza o obiektach zależnych (``ScoringScale``,
    ``QualificationRule``), bez których etap jest wierszem, którego panel nie umie obsłużyć.
    Powtórzenie tej wiedzy tutaj znaczyłoby, że nowy konkurs dostaje etapy o jeden obiekt
    uboższe niż etapy zakładane w panelu — i że po zmianie skali domyślnej rozjeżdżają się
    obie drogi.

    Edycja jest **bieżąca** od razu, bo konkurs bez bieżącej edycji nie pokazuje harmonogramu
    ani nie przyjmuje rejestracji; nie ma tu czego przełączać, bo innej edycji jeszcze nie ma.
    """
    from apps.competitions.models import Edition

    edition = Edition(competition=competition, year_label=label, is_current=True)
    try:
        # ``full_clean`` z tego samego powodu, co przy konkursie: unikalność oznaczenia edycji
        # i więzy „jedna bieżąca” mają się zgłosić czytelnym błędem, a nie ``IntegrityError``
        # wypadającym w środku transakcji.
        edition.full_clean()
    except ValidationError as exc:
        raise ProvisioningError(_flatten(exc)) from exc
    edition.save()

    stages = [
        _create_stage(edition, spec, timeline)
        for spec, timeline in stage_timelines(template["stages"], base_day)
    ]
    return edition, stages


def _create_stage(edition, spec: dict, timeline: dict):
    from apps.competitions.services import create_stage

    try:
        return create_stage(
            edition=edition,
            kind=spec["kind"],
            name=spec["name"],
            format=spec["format"],
            **timeline,
        )
    except ValidationError as exc:
        raise ProvisioningError(f"Etap {spec['kind']}: " + _flatten(exc)) from exc


# --- rola koordynatora --------------------------------------------------------------------------


def _grant_coordinator(user, competition: Competition) -> None:
    """Rola koordynatora tego konkursu przez ``apps.accounts.services.grant_role``.

    Serwis zapisuje **oba**: wiersz ``accounts.Membership`` (rola w konkursie) i przynależność
    do grupy Django ``coordinator`` (uprawnienie do ``/cms/``, § 3.8). Wpisanie tu samego
    członkostwa dałoby koordynatora bez panelu redakcyjnego — czyli osobę, która nie może
    wpisać regulaminu własnego konkursu.
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import grant_role

    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)


# --- konfiguracja startowa ----------------------------------------------------------------------


def _seed_configuration(competition: Competition, coordinator) -> dict:
    """Cztery zestawy startowe etapu 2; zwraca liczby do podsumowania.

    Wszystkie cztery są tą samą robotą co migracje danych etapu 2 (``accounts.0024``,
    ``accounts.0026``, ``tenancy.0005``) i powstały z jednego braku: migracja wpisuje wiersze
    konkursom stojącym w bazie **w chwili jej wykonania**, a konkurs założony później nie ma
    jak przez nią przejść. Bez tego kroku nowy konkurs po włączeniu odpowiedniej flagi miałby
    pusty formularz zgód, pustą listę regionów i dokumenty bez tekstu — czyli konfigurację,
    której nikt nie zamawiał i której nie widać, dopóki flaga jest wyłączona.

    Krok stoi **w transakcji**, razem z konkursem, edycją i rolą: nowy konkurs ma powstać
    w komplecie albo wcale, a ``dry_run`` ma pokazać także to, co tutaj odrzuciłaby baza.
    """
    return {
        "cms_group": _ensure_cms_group(competition, coordinator),
        "consents": len(_seed_consents(competition)),
        "documents": len(_seed_documents(competition)),
        "regions": len(_seed_regions(competition)),
    }


def _ensure_cms_group(competition: Competition, coordinator) -> str:
    """Grupa redakcyjna ``cms:<slug>`` konkursu; zwraca jej nazwę (§ 1.1.5, T17).

    Grupa powstaje **zawsze**, także przy wyłączonej fladze ``scoped_cms_permissions``: jest
    wtedy pustym naczyniem, które nikomu niczego nie daje i nikomu niczego nie odbiera, a jej
    brak znaczyłby, że dzień włączenia flagi jest dniem, w którym trzeba pamiętać o komendzie
    ``scope_cms_access``. Do grupy **dopisujemy** koordynatora tylko przy włączonej fladze
    i zawsze **oprócz** grupy globalnej ``coordinator``, którą nadał ``grant_role``: § 1.1.5
    mówi wprost, że zawężenie dokłada, a nigdy nie odbiera.
    """
    from apps.cms.permissions import ensure_cms_group

    group = ensure_cms_group(competition)
    if coordinator is not None and competition.has_feature("scoped_cms_permissions"):
        # ``add`` jest idempotentne — powtórzone wywołanie nie mnoży przynależności.
        coordinator.groups.add(group)
    return group.name


def _seed_pipeline(edition) -> list:
    """Kroki toru z etapów szablonu – odpowiednik migracji ``competitions.0024`` dla nowej edycji.

    Bez tego ekran „Przebieg edycji” (za flagą ``process_editor``) pokazywałby etapy i zero
    kroków, a reguły przejścia byłyby nieosiągalne do pierwszego ręcznego „Dopisz krok”.
    """
    from apps.competitions.pipeline import ensure_pipeline

    return ensure_pipeline(edition)


def _seed_consents(competition: Competition) -> list:
    """Zestaw startowy zgód (``accounts.0024`` dla nowego konkursu, T10)."""
    from apps.accounts.consents import definitions_from_defaults

    return definitions_from_defaults(competition)


def _seed_documents(competition: Competition) -> list:
    """Teksty pięciu dokumentów w wersji ``1.0`` (``tenancy.0005`` dla nowego konkursu, T12).

    Tytuły idą **wprost** ze stałej składu (``DOCUMENT_TITLES``), bo marki nie zawierają;
    zdanie główne i linia podpisu — z :data:`INITIAL_DOCUMENT_STATEMENTS`
    i :data:`INITIAL_SIGNATURE_LINE`, czyli z tych samych zdań ze znacznikiem w miejscu nazwy.

    Zapis idzie przez ``set_current_template``, a nie przez ``DocumentTemplate.objects.create``:
    tam stoi walidacja znaczników i wpis audytowy, a szablon wpisany obok tej funkcji byłby
    pierwszym w bazie, którego nikt nie sprawdził.
    """
    from apps.results.certificates import DOCUMENT_TITLES
    from apps.tenancy.documents import set_current_template

    return [
        set_current_template(
            competition,
            kind,
            version=INITIAL_DOCUMENT_VERSION,
            title=DOCUMENT_TITLES[kind],
            statement=statement,
            signature_line=INITIAL_SIGNATURE_LINE,
        )
        for kind, statement in INITIAL_DOCUMENT_STATEMENTS.items()
    ]


def _seed_regions(competition: Competition) -> list:
    """Zestaw startowy regionów: kraj, 16 województw, „poza Polską” (``accounts.0026``, T18)."""
    from apps.accounts.regions import default_regions_for

    return default_regions_for(competition)


def _run_safe_seeds(template: dict) -> None:
    """Komendy z ``safe_seeds`` szablonu — wyłącznie globalne i idempotentne (patrz katalog).

    Chodzą **poza** transakcją konkursu, bo dotyczą danych wspólnych dla całej instalacji (wykaz
    SIO/RSPO), i dlatego nigdy przy ``dry_run``: ich wierszy nie ma czego wycofywać.
    """
    for command_name in template["safe_seeds"]:
        call_command(command_name)


# --- wartości wyliczane -------------------------------------------------------------------------


def env_lines_for(competition: Competition) -> tuple[str, ...]:
    """Linijki do ``.env`` administratora serwera. Pusto znaczy „nie ma czego wklejać”.

    Trzy konfiguracje, które muszą znać nową domenę (Caddy, ``ALLOWED_HOSTS``,
    ``CSRF_TRUSTED_ORIGINS``), biorą się z **jednej** zmiennej ``EXTRA_DOMAINS`` — i to ona jest
    treścią tej listy. Dwa przypadki mają listę pustą, bo nowa domena nie istnieje:

    - tryb prefiksu ścieżki: konkurs odpowiada pod domeną platformy,
    - subdomena platformy przy włączonym ``PLATFORM_SUBDOMAINS``: hosty ``*.SITE_DOMAIN`` są już
      wpuszczone wildcardem, a certyfikat Caddy pobiera na żądanie (``/internal/tls-allowed``).
    """
    if competition.routing_mode == RoutingMode.PATH:
        return ()
    domain = competition.primary_domain or competition.site.hostname
    if is_platform_subdomain(domain):
        return ()
    return (f"EXTRA_DOMAINS=… {domain}",)


def school_year(today: date) -> str:
    """``date(2026, 9, 17)`` → ``"2026/2027"``; ``date(2026, 5, 1)`` → ``"2025/2026"``.

    Rocznik liczony od września, bo tak liczy go organizator i tak podpisana jest I edycja
    Olimpiady Kwantowej. Konkurs założony w maju należy do rocznika, który właśnie się kończy —
    dopisanie mu następnego znaczyłoby edycję z etykietą o rok do przodu.
    """
    start = today.year if today.month >= SCHOOL_YEAR_FIRST_MONTH else today.year - 1
    return f"{start}/{start + 1}"


def base_day(today: date) -> date:
    """Dzień, od którego odkładają się terminy etapów: **pierwszy dzień następnego miesiąca**.

    Konwencja jest jawna i celowo gruba. „Dziś” dałoby etap otwarty w chwili założenia konkursu,
    czyli serwis przyjmujący prace, zanim istnieje choćby regulamin; „za rok” dałoby harmonogram,
    którego nikt nie poprawi, bo nic go nie uwiera. Pierwszy dzień następnego miesiąca jest
    rozpoznawalny na pierwszy rzut oka jako **wartość początkowa**, a nie data ustalona przez
    organizatora — i zostawia na poprawienie jej od dwóch do pięciu tygodni.
    """
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, 1)


def _moment(day: date, time: tuple[int, int]) -> datetime:
    return datetime(day.year, day.month, day.day, time[0], time[1], tzinfo=WARSAW)


def stage_timelines(stages, base: date):
    """``(opis etapu z szablonu, komplet terminów)`` dla kolejnych etapów, w kolejności szablonu.

    Jedno miejsce, w którym odstępy z katalogu zamieniają się w daty — dokładnie z tego samego
    powodu, dla którego ``seed_edition_kwantowa`` ma ``_timeline``: reguła zapisana dwa razy daje
    po zmianie dwa różne wyniki, a różnicy nie widać w bazie. Widać ją dopiero wtedy, gdy
    uczestnik nie może złożyć reklamacji.

    Etap bez terminu (``length_days is None``, czyli trening) dostaje datę-wartownika
    ``TRAINING_DEADLINE`` i **nie przesuwa** kursora: piaskownica stoi obok zawodów, a nie w ich
    kolejce, więc jej „termin” z 2099 roku nie może wypchnąć następnego etapu poza kalendarz.
    """
    from apps.competitions.models import TRAINING_DEADLINE

    cursor = base
    for spec in stages:
        if spec["length_days"] is None:
            yield (
                spec,
                {
                    "opens_at": _moment(base, OPENS_TIME),
                    "deadline_at": TRAINING_DEADLINE,
                    "review_deadline_at": TRAINING_DEADLINE,
                    "appeal_window_opens_at": TRAINING_DEADLINE,
                    "appeal_window_closes_at": TRAINING_DEADLINE + TRAINING_APPEAL_WINDOW,
                },
            )
            continue

        opens_day = cursor + timedelta(days=spec["opens_after_days"])
        deadline_day = opens_day + timedelta(days=spec["length_days"])
        deadline_at = _moment(deadline_day, DEADLINE_TIME)
        review_deadline_at = deadline_at + REVIEW_AFTER_DEADLINE
        yield (
            spec,
            {
                "opens_at": _moment(opens_day, OPENS_TIME),
                "deadline_at": deadline_at,
                "review_deadline_at": review_deadline_at,
                "appeal_window_opens_at": review_deadline_at + APPEAL_OPENS_AFTER_REVIEW,
                "appeal_window_closes_at": review_deadline_at + APPEAL_CLOSES_AFTER_REVIEW,
            },
        )
        cursor = deadline_day


def default_public_code_prefix(slug: str) -> str:
    """``fizyczna`` → ``FIZ-``: trzy pierwsze litery sluga wielkimi literami (znaki spoza a–z pominięte)."""
    letters = "".join(ch for ch in slug if ch.isalpha())[:3].upper() or "KON"
    return f"{letters}-"


def default_certificate_prefix(slug: str) -> str:
    """``fizyczna`` → ``FI``: dwie pierwsze litery sluga wielkimi literami."""
    return "".join(ch for ch in slug if ch.isalpha())[:2].upper() or "KO"


def split_domain(value: str) -> tuple[str, int]:
    """``"example.org"`` → ``("example.org", 80)``; ``"web:8000"`` → ``("web", 8000)``.

    Port bywa potrzebny wyłącznie na instalacji deweloperskiej (``localhost:8000``): Wagtail
    dopasowuje witrynę po parze host+port, więc bez tego rozdziału konkurs założony lokalnie nie
    odpowiadałby pod własnym adresem. W produkcji port jest zawsze domyślny — ruch kończy Caddy.
    """
    raw = (value or "").strip().lower().rstrip("/")
    # Schemat bywa wklejany odruchowo razem z adresem; ucięcie go jest tańsze niż błąd, bo
    # ``https://`` w ``Site.hostname`` nie dopasowałby się do niczego i objawiłby się dopiero
    # cudzą stroną główną pod nową domeną.
    for scheme in ("https://", "http://"):
        if raw.startswith(scheme):
            raw = raw[len(scheme) :]
    if not raw:
        raise ProvisioningError("Podaj domenę konkursu (--domain).")
    hostname, _, port_text = raw.partition(":")
    if not port_text:
        return hostname, DEFAULT_SITE_PORT
    if not port_text.isdigit():
        raise ProvisioningError(f"Nieczytelny numer portu w „{value}”.")
    return hostname, int(port_text)


def _flatten(exc: ValidationError) -> str:
    """``ValidationError`` jako jedno zdanie „pole: komunikat; pole: komunikat”.

    Kształt jest kontraktem komendy (testy szukają w nim nazwy pola, np. ``path_prefix``), więc
    składamy go w jednym miejscu, a nie w każdym ``except`` z osobna.
    """
    return "; ".join(f"{key}: {' '.join(values)}" for key, values in exc.message_dict.items())
