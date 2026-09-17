"""``manage.py create_competition`` — nowy konkurs: witryna, drzewo stron, ustawienia, konkurs.

Komenda jest **jedynym** wejściem do zakładania konkursu. Powód jest ten sam, dla którego katalog
szablonów jest osobnym modułem: konkurs to cztery wiersze w trzech aplikacjach (``wagtailcore.Site``,
``wagtailcore.Page``, ``cms.SiteSettings``, ``tenancy.Competition``), a każdy z nich wpisany ręcznie
w ``/admin/`` jest okazją do pominięcia jednego z pozostałych. Konkurs bez witryny nie ma drzewa
stron; witryna bez konkursu oddaje pod swoim adresem treść konkursu domyślnego.

Czego komenda **nie** robi i dlaczego:

- **nie uruchamia seedów treści.** ``seed_cms``, ``seed_regulamin``, ``seed_legacy_content``
  i ``seed_partners`` wpisują akapity Olimpiady Kwantowej. Nowy konkurs dostaje puste strony
  o właściwych adresach — treść należy do jego redakcji. Wyjątkiem są komendy wymienione
  w ``safe_seeds`` szablonu: globalne i idempotentne (dziś: sam ``seed_schools``).
- **nie zakłada edycji ani etapów.** ``competitions.Edition`` dostaje klucz obcy do konkursu
  dopiero w wydaniu B (zadanie T3, ``docs/UNIWERSALNY-ETAP-1.md`` § 4.1). Do tego czasu edycja
  założona tutaj należałaby do Konkursu #1 — czyli byłaby edycją cudzego konkursu. Szablon opisuje
  etapy (``stages``), a komenda wypisuje je jako listę kontrolną dla koordynatora.
- **nie dotyka ``.env`` ani ``deploy/Caddyfile``.** Oba pliki należą do administratora serwera,
  a komenda chodzi w kontenerze aplikacji, który ich nie widzi (i nie ma prawa widzieć). Zamiast
  tego wypisuje dokładne linijki do wklejenia — patrz ``Command._report``.

Dodanie domeny wymaga zgodnej zmiany w **trzech** miejscach (§ 2.5): ``EXTRA_DOMAINS`` (Caddy),
``DJANGO_ALLOWED_HOSTS`` i ``DJANGO_CSRF_TRUSTED_ORIGINS``. Rozjazd wyłapuje ``manage.py
check_domains``, wołany na końcu ``scripts/deploy.sh``.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.models import Page, Site

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.templates_catalog import TEMPLATE_CHOICES, TEMPLATE_PUSTY, TEMPLATES

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
                template=template,
            )
            if dry_run:
                # Wycofanie **po** wykonaniu całości, a nie pominięcie zapisów: próba na sucho ma
                # sprawdzić to, co sprawdzi baza (unikalność, więzy, walidacja modeli), a nie to,
                # co pamiętamy o bazie. Cena — chwilowe wiersze w transakcji — jest żadna.
                transaction.set_rollback(True)

        if not dry_run:
            self._run_safe_seeds(template)

        self._report(competition, template_name, template, dry_run=dry_run)

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

    def _run_safe_seeds(self, template: dict) -> None:
        """Komendy z ``safe_seeds`` szablonu — wyłącznie globalne i idempotentne (patrz katalog)."""
        for command_name in template["safe_seeds"]:
            self.stdout.write(f"Uruchamiam {command_name} (dane globalne, przebieg idempotentny)…")
            call_command(command_name)

    # --- podsumowanie -------------------------------------------------------------------------
    def _report(self, competition: Competition, template_name: str, template: dict, *, dry_run: bool) -> None:
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
        if template["stages"]:
            stages = ", ".join(f"{kind} ({title})" for kind, title, _ in template["stages"])
            write(f"  5. Etapy do założenia w panelu koordynatora: {stages}")
        write("  6. Sprawdzenie spójności domen: manage.py check_domains")


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
