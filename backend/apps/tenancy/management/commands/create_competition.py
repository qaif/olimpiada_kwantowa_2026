"""``manage.py create_competition`` — nowy konkurs: witryna, strony, ustawienia, konkurs, edycja.

Komenda jest **jedynym** wejściem do zakładania konkursu. Powód jest ten sam, dla którego katalog
szablonów jest osobnym modułem: konkurs to cztery wiersze w trzech aplikacjach (``wagtailcore.Site``,
``wagtailcore.Page``, ``cms.SiteSettings``, ``tenancy.Competition``) plus edycja z etapami, a każdy
z nich wpisany ręcznie w ``/admin/`` jest okazją do pominięcia jednego z pozostałych. Konkurs bez
witryny nie ma drzewa stron; witryna bez konkursu oddaje pod swoim adresem treść konkursu domyślnego;
konkurs bez bieżącej edycji nie pokazuje harmonogramu i nie przyjmuje rejestracji.

Od etapu 2 komenda wpisuje nowemu konkursowi także **cztery zestawy startowe**, które konkursom
stojącym już w bazie dały migracje danych: definicje zgód (``accounts.0024``), teksty dokumentów
(``tenancy.0005``), regiony (``accounts.0026``) i grupę redakcyjną ``cms:<slug>`` (§ 1.1.5).
Migracja z definicji dotyczy wierszy istniejących w chwili jej wykonania, więc bez tego kroku
konkurs założony później miałby po włączeniu flagi pusty formularz zgód i pustą listę regionów —
czyli konfigurację, której brak widać dopiero w dniu, w którym zaczyna być potrzebna. Wszystko
dzieje się w tej samej transakcji, co reszta, i jest idempotentne.

Czego komenda **nie** robi i dlaczego:

- **nie uruchamia seedów treści.** ``seed_cms``, ``seed_regulamin``, ``seed_legacy_content``
  i ``seed_partners`` wpisują akapity Olimpiady Kwantowej. Nowy konkurs dostaje puste strony
  o właściwych adresach — treść należy do jego redakcji. Wyjątkiem są komendy wymienione
  w ``safe_seeds`` szablonu: globalne i idempotentne (dziś: sam ``seed_schools``).
- **nie wpisuje terminów zawodów.** Edycję i etapy zakłada (§ 4.5, kroki 5 i 6), ale ich oś czasu
  jest **wartością początkową**, a nie harmonogramem: pierwszy etap otwiera się pierwszego dnia
  następnego miesiąca, a kolejne odkładają się od niego odstępami z szablonu. Reguły wokół terminu
  oddania są te same, co w ``seed_edition_kwantowa`` (recenzje +14 dni, okno reklamacji +2/+9 dni
  względem terminu recenzji) — skopiowane są **reguły**, nie daty. Komplet terminów należy do
  koordynatora i poprawia się go w panelu.
- **nie zakłada kont.** ``--coordinator-email`` nadaje rolę **istniejącemu** kontu; nieznany adres
  jest błędem, a nie zaproszeniem do założenia konta bez wiedzy jego właściciela. Konto zakłada się
  drogą, którą serwis zna (rejestracja, zaproszenie do komitetu, ``bootstrap_coordinator``).
- **nie dotyka ``.env`` ani ``deploy/Caddyfile``.** Oba pliki należą do administratora serwera,
  a komenda chodzi w kontenerze aplikacji, który ich nie widzi (i nie ma prawa widzieć). Zamiast
  tego wypisuje dokładne linijki do wklejenia — patrz ``Command._report``.

Dodanie domeny wymaga zgodnej zmiany w **trzech** miejscach (§ 2.5): ``EXTRA_DOMAINS`` (Caddy),
``DJANGO_ALLOWED_HOSTS`` i ``DJANGO_CSRF_TRUSTED_ORIGINS``. Rozjazd wyłapuje ``manage.py
check_domains``, wołany na końcu ``scripts/deploy.sh``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from wagtail.models import Page, Site

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.templates_catalog import TEMPLATE_CHOICES, TEMPLATE_PUSTY, TEMPLATES

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
#: w ``seed_edition_kwantowa`` („I edycja 2026/2027”). Rocznik liczy się z dnia uruchomienia
#: komendy, a nie ze stałej: konkurs założony we wrześniu i konkurs założony w maju mają dostać
#: rocznik, w którym naprawdę się odbędą.
EDITION_LABEL_PATTERN = "I edycja {school_year}"

#: Miesiąc, od którego liczy się nowy rok szkolny. Wrzesień, bo tak liczy go organizator
#: (I edycja 2026/2027 otwiera eliminacje 1 września 2026).
SCHOOL_YEAR_FIRST_MONTH = 9

#: Port witryny zakładanej przez tę komendę. Taki sam, jaki wpisuje ``cms.0002_initial_tree``
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
#: nie musiał importować modeli w czasie wczytywania komendy.
INITIAL_DOCUMENT_STATEMENTS: dict[str, str] = {
    "LAUREAT": "uzyskał(a) tytuł laureata {competition_genitive}",
    "FINALISTA": "uzyskał(a) tytuł finalisty {competition_genitive}",
    "UCZESTNIK": "brał(a) udział w {competition_locative}",
    "OPIEKUN": "sprawował(a) opiekę nad uczestnikami {competition_genitive}",
    "WARSZTATY": "uczestniczył(a) w warsztatach online {competition_genitive}",
}

#: Linia podpisu — ``SIGNATURE_LINE`` z tą samą zamianą i z tego samego powodu.
INITIAL_SIGNATURE_LINE = "Przewodniczący Komitetu Głównego {competition_genitive}"


class Command(BaseCommand):
    help = (
        "Zakłada nowy konkurs: witrynę Wagtaila, drzewo stron, ustawienia serwisu i wiersz "
        "Competition z szablonu. Nie uruchamia seedów treści i nie dotyka .env."
    )

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="Identyfikator konkursu, np. fizyczna.")
        parser.add_argument("--name", required=True, help="Pełna nazwa, np. „Olimpiada Fizyczna”.")
        parser.add_argument(
            "--domain",
            required=True,
            help="Domena konkursu (dopuszczalne „host:port” dla instalacji deweloperskiej).",
        )
        parser.add_argument(
            "--from-template",
            choices=TEMPLATE_CHOICES,
            default=TEMPLATE_PUSTY,
            help="Szablon startowy (apps/tenancy/templates_catalog.py). Domyślnie: pusty.",
        )
        parser.add_argument(
            "--path-prefix",
            default="",
            help=(
                "Prefiks ścieżki zamiast własnej domeny (tryb PATH). Uwaga: ciasteczka są wtedy "
                "wspólne z konkursem na tej samej domenie — patrz README § 3."
            ),
        )
        parser.add_argument("--short-name", default="", help="Nazwa skrócona (domyślnie: --name).")
        parser.add_argument(
            "--organizer",
            default="",
            help="Organizator (podmiot prawny). Domyślnie powtarza --name, do poprawienia w panelu.",
        )
        parser.add_argument("--contact-email", default="", help="Adres kontaktowy organizatora.")
        parser.add_argument("--accent", default="", help="Kolor akcentu, np. #1f6feb.")
        parser.add_argument(
            "--public-code-prefix",
            default="",
            help=(
                "Przedrostek kodów publicznych uczestników (do 8 znaków). Domyślnie trzy pierwsze "
                "litery sluga wielkimi literami i myślnik, np. FIZ- – każdy konkurs ma mieć własny, "
                "żeby kody dwóch olimpiad nie wyglądały identycznie."
            ),
        )
        parser.add_argument(
            "--certificate-prefix",
            default="",
            help="Przedrostek numerów dyplomów (do 8 znaków). Domyślnie dwie pierwsze litery sluga.",
        )
        parser.add_argument(
            "--edition-label",
            default="",
            help=(
                "Oznaczenie pierwszej edycji, np. „I edycja 2026/2027”. Domyślnie bieżący rocznik "
                "szkolny liczony od września."
            ),
        )
        parser.add_argument(
            "--coordinator-email",
            default="",
            help=(
                "Adres istniejącego konta, które dostanie rolę koordynatora tego konkursu. "
                "Nieznany adres jest błędem — komenda nie zakłada kont."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Wykonaj wszystko i wycofaj transakcję — sprawdza dane, nie zmienia bazy.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help=(
                "Konkurs o tym identyfikatorze już jest: zakończ powodzeniem, nic nie zmieniając. "
                "Dla scripts/deploy.sh, które musi dać się uruchomić ponownie."
            ),
        )

    def handle(self, *args, **options):
        slug = (options["slug"] or "").strip().lower()
        name = (options["name"] or "").strip()
        hostname, port = _split_domain(options["domain"])
        template_name = options["from_template"]
        template = TEMPLATES[template_name]
        dry_run = options["dry_run"]

        existing = Competition.objects.filter(slug=slug).first()
        if existing is not None:
            if options["skip_existing"]:
                self.stdout.write(f"Konkurs „{slug}” już istnieje ({existing.name}) – pomijam.")
                return
            raise CommandError(
                f"Konkurs o identyfikatorze „{slug}” już istnieje. Identyfikator jest trwały "
                f"(wchodzi do adresów i do eksportów), więc komenda go nie nadpisuje."
            )

        # Domena jest sprawdzana w dwóch tabelach, bo w dwóch może być zajęta: witryna rozstrzyga
        # żądanie (``Site.find_for_request``), a ``primary_domain`` buduje linki poza żądaniem.
        # Druga witryna pod tym samym hostem znaczyłaby, że o treści pod adresem decyduje kolejność
        # wierszy w bazie.
        if Site.objects.filter(hostname__iexact=hostname, port=port).exists():
            raise CommandError(f"Witryna dla hosta „{hostname}:{port}” już istnieje.")
        if Competition.objects.filter(primary_domain__iexact=hostname).exists():
            raise CommandError(f"Domena „{hostname}” należy już do innego konkursu.")

        # Koordynator jest sprawdzany **przed** transakcją, razem z identyfikatorem i domeną:
        # literówka w adresie ma zatrzymać komendę tam, gdzie zatrzymują ją pozostałe odmowy —
        # zanim cokolwiek powstanie. Wyjątek w środku transakcji dałby ten sam skutek w bazie,
        # ale inny komunikat: „konkurs założony, rola nie” zamiast „nie zakładam niczego”.
        coordinator = self._coordinator(options["coordinator_email"])

        today = timezone.localtime(timezone.now(), WARSAW).date()
        edition_label = (options["edition_label"] or "").strip() or EDITION_LABEL_PATTERN.format(
            school_year=_school_year(today)
        )

        short_name = (options["short_name"] or name).strip()
        with transaction.atomic():
            competition = self._create(
                slug=slug,
                name=template["name_pattern"].format(name=name),
                short_name=template["short_name_pattern"].format(name=short_name),
                hostname=hostname,
                port=port,
                path_prefix=(options["path_prefix"] or "").strip().lower(),
                organizer=(options["organizer"] or name).strip(),
                contact_email=(options["contact_email"] or "").strip(),
                accent=(options["accent"] or template["accent_colour"]).strip(),
                public_code_prefix=(options["public_code_prefix"] or "").strip()
                or _default_public_code_prefix(slug),
                certificate_prefix=(options["certificate_prefix"] or "").strip()
                or _default_certificate_prefix(slug),
                template=template,
            )
            edition, stages = self._create_edition(
                competition, template, label=edition_label, base_day=_base_day(today)
            )
            if coordinator is not None:
                self._grant_coordinator(coordinator, competition)
            seeded = self._seed_configuration(competition, coordinator)
            seeded["pipeline"] = len(self._seed_pipeline(edition))
            if dry_run:
                # Wycofanie **po** wykonaniu całości, a nie pominięcie zapisów: próba na sucho ma
                # sprawdzić to, co sprawdzi baza (unikalność, więzy, walidacja modeli), a nie to,
                # co pamiętamy o bazie. Cena — chwilowe wiersze w transakcji — jest żadna.
                transaction.set_rollback(True)

        if not dry_run:
            self._run_safe_seeds(template)

        self._report(
            competition,
            template_name,
            template,
            edition=edition,
            stages=stages,
            coordinator=coordinator,
            seeded=seeded,
            dry_run=dry_run,
        )

    # --- zakładanie ---------------------------------------------------------------------------
    def _create(
        self,
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
            raise CommandError("Brak korzenia drzewa stron Wagtaila – baza nie jest zmigrowana.")

        # Strony powstają **bez rewizji**, dokładnie tak, jak drzewo Konkursu #1 z migracji
        # ``cms.0002_initial_tree``: są od razu opublikowane, a pierwszą rewizją będzie pierwsza
        # poprawka redaktora. Publikowanie rewizji tuż po ``add_child`` byłoby zapisem stanu
        # sprzed dołożenia dzieci (rewizja niesie też ``path`` i ``numchild``) — czyli ryzykiem
        # rozjazdu drzewa w zamian za pustą pozycję w historii.
        #
        # ``HomePage.max_count == 1`` dotyczy **panelu** (``Page.can_create_at``), a nie zapisu:
        # Wagtail pilnuje w ten sposób, żeby redaktor nie założył drugiej strony głównej ręcznie.
        # Drugi konkurs to drugie drzewo z własną stroną główną i to jest stan zamierzony — dlatego
        # zakłada ją komenda, a nie człowiek w ``/cms/``.
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
            raise CommandError("; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())) from exc
        competition.save()
        return competition

    # --- edycja i etapy -----------------------------------------------------------------------
    def _create_edition(self, competition: Competition, template: dict, *, label: str, base_day: date):
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
            # i więzy „jedna bieżąca” mają się zgłosić czytelnym błędem komendy, a nie
            # ``IntegrityError`` wypadającym w środku transakcji.
            edition.full_clean()
        except ValidationError as exc:
            raise CommandError("; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())) from exc
        edition.save()

        stages = [
            self._create_stage(edition, spec, timeline)
            for spec, timeline in _stage_timelines(template["stages"], base_day)
        ]
        return edition, stages

    def _create_stage(self, edition, spec: dict, timeline: dict):
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
            raise CommandError(
                f"Etap {spec['kind']}: "
                + "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())
            ) from exc

    # --- rola koordynatora --------------------------------------------------------------------
    def _coordinator(self, email: str):
        """Konto z ``--coordinator-email`` albo ``None``. Nieznany adres = odmowa, nie nowe konto.

        Zakładanie konta „przy okazji” dałoby osobę z rolą koordynatora, która o tym nie wie, nie
        ma hasła i nie potwierdziła adresu — czyli konto obsługiwane wyłącznie przez reset hasła
        na adres, którego nikt nie zweryfikował.
        """
        email = (email or "").strip().lower()
        if not email:
            return None

        from apps.accounts.models import User

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(
                f"Nie ma konta o adresie „{email}”. Komenda nie zakłada kont — załóż je "
                f"rejestracją albo komendą bootstrap_coordinator i uruchom ponownie."
            )
        return user

    def _grant_coordinator(self, user, competition: Competition) -> None:
        """Rola koordynatora tego konkursu przez ``apps.accounts.services.grant_role``.

        Serwis zapisuje **oba**: wiersz ``accounts.Membership`` (rola w konkursie) i przynależność
        do grupy Django ``coordinator`` (uprawnienie do ``/cms/``, § 3.8). Wpisanie tu samego
        członkostwa dałoby koordynatora bez panelu redakcyjnego — czyli osobę, która nie może
        wpisać regulaminu własnego konkursu.
        """
        from apps.accounts.models import CompetitionRole
        from apps.accounts.services import grant_role

        grant_role(user, CompetitionRole.COORDINATOR, competition=competition)

    # --- konfiguracja startowa ------------------------------------------------------------------
    def _seed_configuration(self, competition: Competition, coordinator) -> dict:
        """Cztery zestawy startowe etapu 2; zwraca liczby do podsumowania.

        Wszystkie cztery są tą samą robotą co migracje danych etapu 2 (``accounts.0024``,
        ``accounts.0026``, ``tenancy.0005``) i powstały z jednego braku: migracja wpisuje wiersze
        konkursom stojącym w bazie **w chwili jej wykonania**, a konkurs założony później nie ma
        jak przez nią przejść. Bez tego kroku nowy konkurs po włączeniu odpowiedniej flagi miałby
        pusty formularz zgód, pustą listę regionów i dokumenty bez tekstu — czyli konfigurację,
        której nikt nie zamawiał i której nie widać, dopóki flaga jest wyłączona.

        Krok stoi **w transakcji**, razem z konkursem, edycją i rolą: nowy konkurs ma powstać
        w komplecie albo wcale, a ``--dry-run`` ma pokazać także to, co tutaj odrzuciłaby baza.
        """
        return {
            "cms_group": self._ensure_cms_group(competition, coordinator),
            "consents": len(self._seed_consents(competition)),
            "documents": len(self._seed_documents(competition)),
            "regions": len(self._seed_regions(competition)),
        }

    def _ensure_cms_group(self, competition: Competition, coordinator) -> str:
        """Grupa redakcyjna ``cms:<slug>`` konkursu; zwraca jej nazwę (§ 1.1.5, T17).

        Grupa powstaje **zawsze**, także przy wyłączonej fladze ``scoped_cms_permissions``: jest
        wtedy pustym naczyniem, które nikomu niczego nie daje i nikomu niczego nie odbiera, a jej
        brak znaczyłby, że dzień włączenia flagi jest dniem, w którym trzeba pamiętać o komendzie
        ``scope_cms_access``. Do grupy **dopisujemy** koordynatora tylko przy włączonej fladze
        i zawsze **oprócz** grupy globalnej ``coordinator``, którą nadał ``grant_role``: § 1.1.5
        mówi wprost, że zawężenie dokłada, a nigdy nie odbiera. Odebranie globalnej grupy jest
        osobną, jawną komendą.
        """
        from apps.cms.permissions import ensure_cms_group

        group = ensure_cms_group(competition)
        if coordinator is not None and competition.has_feature("scoped_cms_permissions"):
            # ``add`` jest idempotentne — powtórzone wywołanie nie mnoży przynależności.
            coordinator.groups.add(group)
        return group.name

    def _seed_pipeline(self, edition) -> list:
        """Kroki toru z etapów szablonu – odpowiednik migracji ``competitions.0024`` dla nowej edycji.

        Bez tego ekran „Przebieg edycji” (za flagą ``process_editor``) pokazywałby etapy i zero
        kroków, a reguły przejścia byłyby nieosiągalne do pierwszego ręcznego „Dopisz krok”.
        """
        from apps.competitions.pipeline import ensure_pipeline

        return ensure_pipeline(edition)

    def _seed_consents(self, competition: Competition) -> list:
        """Zestaw startowy zgód (``accounts.0024`` dla nowego konkursu, T10)."""
        from apps.accounts.consents import definitions_from_defaults

        return definitions_from_defaults(competition)

    def _seed_documents(self, competition: Competition) -> list:
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

    def _seed_regions(self, competition: Competition) -> list:
        """Zestaw startowy regionów: kraj, 16 województw, „poza Polską” (``accounts.0026``, T18)."""
        from apps.accounts.regions import default_regions_for

        return default_regions_for(competition)

    def _run_safe_seeds(self, template: dict) -> None:
        """Komendy z ``safe_seeds`` szablonu — wyłącznie globalne i idempotentne (patrz katalog)."""
        for command_name in template["safe_seeds"]:
            self.stdout.write(f"Uruchamiam {command_name} (dane globalne, przebieg idempotentny)…")
            call_command(command_name)

    # --- podsumowanie -------------------------------------------------------------------------
    def _report(
        self,
        competition: Competition,
        template_name: str,
        template: dict,
        *,
        edition,
        stages: list,
        coordinator,
        seeded: dict,
        dry_run: bool,
    ) -> None:
        write = self.stdout.write
        if dry_run:
            write(self.style.WARNING("PRÓBA NA SUCHO – transakcja wycofana, baza bez zmian."))
        write(
            self.style.SUCCESS(
                f"Konkurs „{competition.name}” ({competition.slug}), szablon: {template['label']}."
            )
        )
        write(f"  witryna:  {competition.site.hostname}:{competition.site.port}")
        write(f"  strony:   /{', /'.join(slug for _, slug, _ in template['pages'])}")
        write(f"  edycja:   {edition.year_label} (bieżąca)")
        for stage in stages:
            # Terminy wypisujemy w strefie organizatora, bo w niej je liczyliśmy — w UTC wyglądałyby
            # jak przesunięte o godzinę i pierwszą reakcją koordynatora byłaby poprawka, która
            # niczego nie poprawia.
            opens = timezone.localtime(stage.opens_at, WARSAW).date().isoformat()
            deadline = timezone.localtime(stage.deadline_at, WARSAW).date().isoformat()
            write(f"  etap:     {stage.kind} {stage.name} — {opens} → {deadline} (wartość początkowa)")
        if coordinator is not None:
            write(f"  koordynator: {coordinator.email} (rola w tym konkursie + grupa „coordinator”)")
        # Zestawy startowe etapu 2. Liczby wypisujemy, a nie zawartość: komplet zgód i regionów
        # ogląda się na ekranach konfiguracji, a tutaj chodzi o jedno zdanie — „jest, nie musisz
        # o tym pamiętać”. Brakująca pozycja byłaby natomiast widoczna od razu.
        write(f"  zgody:    {seeded['consents']} definicje (zestaw domyślny, do poprawienia w panelu)")
        write(f"  szablony dokumentów: {seeded['documents']} (wersja {INITIAL_DOCUMENT_VERSION})")
        write(f"  regiony:  {seeded['regions']} (kraj, 16 województw, „poza Polską”)")
        write(f"  grupa /cms/: {seeded['cms_group']}")
        write(f"  przebieg: {seeded['pipeline']} kroków toru (edytor za flagą process_editor)")
        write(f"  kody:     {competition.public_code_prefix}… / dyplomy {competition.certificate_prefix}/…")

        write("")
        write("Następne kroki (żadnego z nich komenda nie wykonuje za administratora):")
        if competition.routing_mode == RoutingMode.PATH:
            write(
                f"  1. Nic w DNS-ie i nic w Caddym: konkurs odpowiada pod prefiksem "
                f"/{competition.path_prefix}/ na domenie platformy. Sesja i CSRF stoją wtedy na "
                f"jednym haszczu ciasteczek ze wszystkimi konkursami tej domeny (README § 3)."
            )
        else:
            domain = competition.primary_domain
            write(f"  1. DNS: rekord A/AAAA {domain} → adres serwera (oraz www.{domain}, jeśli ma działać).")
            write("  2. Na serwerze, w /opt/olimpiada/.env — jedna linijka, z której biorą się trzy:")
            write(f"       EXTRA_DOMAINS=… {domain}")
            write(
                f"     (Django dokłada stąd „{domain}” do ALLOWED_HOSTS i „https://{domain}” "
                f"do CSRF_TRUSTED_ORIGINS samo — config/settings/base.py.)"
            )
            write("  3. ./scripts/render_caddyfile.sh && docker compose up -d proxy web worker beat")
            write("     (Caddy pobierze certyfikat sam, gdy DNS już wskazuje serwer.)")
        write(
            f"  4. Dokumenty do wpisania w /cms/ przed otwarciem rejestracji: "
            f"{', '.join(template['documents']) or '—'}"
        )
        if stages:
            write(
                "  5. Terminy etapów w panelu koordynatora: powyższe są **wartością początkową** "
                "odłożoną od pierwszego dnia następnego miesiąca, a nie harmonogramem."
            )
        write(
            f"  6. Formaty plików do wpisania w zadaniach ({', '.join(template['upload_formats'])}). "
            f"Zgody przy rejestracji ({', '.join(template['consents'])}) są już wpisane jako "
            f"definicje konkursu — treść poprawia się na /coordinator/consents/ po włączeniu "
            f"przełącznika per_competition_consents."
        )
        write("  7. Sprawdzenie spójności domen: manage.py check_domains")


def _school_year(today: date) -> str:
    """``date(2026, 9, 17)`` → ``"2026/2027"``; ``date(2026, 5, 1)`` → ``"2025/2026"``.

    Rocznik liczony od września, bo tak liczy go organizator i tak podpisana jest I edycja
    Olimpiady Kwantowej. Konkurs założony w maju należy do rocznika, który właśnie się kończy —
    dopisanie mu następnego znaczyłoby edycję z etykietą o rok do przodu.
    """
    start = today.year if today.month >= SCHOOL_YEAR_FIRST_MONTH else today.year - 1
    return f"{start}/{start + 1}"


def _base_day(today: date) -> date:
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


def _stage_timelines(stages, base_day: date):
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

    cursor = base_day
    for spec in stages:
        if spec["length_days"] is None:
            yield (
                spec,
                {
                    "opens_at": _moment(base_day, OPENS_TIME),
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


def _default_public_code_prefix(slug: str) -> str:
    """``fizyczna`` → ``FIZ-``: trzy pierwsze litery sluga wielkimi literami (znaki spoza a–z pominięte)."""
    letters = "".join(ch for ch in slug if ch.isalpha())[:3].upper() or "KON"
    return f"{letters}-"


def _default_certificate_prefix(slug: str) -> str:
    """``fizyczna`` → ``FI``: dwie pierwsze litery sluga wielkimi literami."""
    return "".join(ch for ch in slug if ch.isalpha())[:2].upper() or "KO"


def _split_domain(value: str) -> tuple[str, int]:
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
        raise CommandError("Podaj domenę konkursu (--domain).")
    hostname, _, port_text = raw.partition(":")
    if not port_text:
        return hostname, DEFAULT_SITE_PORT
    if not port_text.isdigit():
        raise CommandError(f"Nieczytelny numer portu w „{value}”.")
    return hostname, int(port_text)
