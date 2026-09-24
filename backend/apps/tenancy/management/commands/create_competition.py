"""``manage.py create_competition`` — nowy konkurs: witryna, strony, ustawienia, konkurs, edycja.

Komenda jest **wierszem poleceń** do jednej czynności, a nie jej opisem: sama czynność mieszka
w ``apps/tenancy/provisioning.py`` i tam stoi jej uzasadnienie (dlaczego konkurs powstaje
w komplecie albo wcale, dlaczego bez seedów treści, dlaczego z zastępczymi terminami). Tutaj
zostaje to, czego wiersz poleceń potrzebuje, a czynność — nie:

- **argumenty** i ich nazwy (kontrakt ``scripts/deploy.sh``, ``docs/OPERACJE.md`` § 6 i kreatora
  ``/setup/``),
- ``--skip-existing``, czyli ergonomia wdrożenia, które musi dać się powtórzyć,
- ``--coordinator-email``: czynność przyjmuje **konto**, komenda przyjmuje adres i zamienia go na
  konto (nieznany adres jest błędem — kont komenda nie zakłada),
- ``safe_seeds`` szablonu (dziś: sam ``seed_schools``). Uruchamia je **komenda**, a nie czynność,
  i to jest decyzja: seedy chodzą poza transakcją i bywają kilkoma tysiącami wierszy, więc panel
  koordynatora nie ma prawa ich wywołać w środku żądania HTTP. Wiersz poleceń ma — i dlatego to
  tutaj stoi jedyne w tej ścieżce wywołanie ``call_command``,
- **wydruk**: podsumowanie i lista następnych kroków dla administratora.

Czego komenda nie robi: nie dotyka ``.env`` ani ``deploy/Caddyfile``. Oba pliki należą do
administratora serwera, a komenda chodzi w kontenerze aplikacji, który ich nie widzi (i nie ma
prawa widzieć). Zamiast tego wypisuje dokładne linijki do wklejenia — patrz ``Command._report``.

Dodanie domeny wymaga zgodnej zmiany w **trzech** miejscach (§ 2.5): ``EXTRA_DOMAINS`` (Caddy),
``DJANGO_ALLOWED_HOSTS`` i ``DJANGO_CSRF_TRUSTED_ORIGINS``. Rozjazd wyłapuje ``manage.py
check_domains``, wołany na końcu ``scripts/deploy.sh``. Konkurs w **subdomenie platformy**
(``PLATFORM_SUBDOMAINS``) jest wyjątkiem: wildcard obejmuje go we wszystkich trzech, więc
``ProvisioningResult.env_lines`` jest wtedy puste i wydruk o ``.env`` milczy.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.provisioning import (
    INITIAL_DOCUMENT_VERSION,
    WARSAW,
    ProvisioningError,
    coordinator_from_email,
    create_competition_from_template,
)
from apps.tenancy.templates_catalog import TEMPLATE_CHOICES, TEMPLATE_PUSTY


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
        dry_run = options["dry_run"]

        # ``--skip-existing`` rozstrzygamy **przed** czynnością, bo to jest ergonomia wdrożenia,
        # a nie reguła zakładania konkursu: powtórzony ``scripts/deploy.sh`` ma zakończyć się
        # zielono, a nie odmową. Sama odmowa („identyfikator jest trwały”) należy do czynności.
        existing = Competition.objects.filter(slug=slug).first()
        if existing is not None and options["skip_existing"]:
            self.stdout.write(f"Konkurs „{slug}” już istnieje ({existing.name}) – pomijam.")
            return

        try:
            # Koordynator jest sprawdzany **przed** czynnością, razem z identyfikatorem i domeną:
            # literówka w adresie ma zatrzymać komendę tam, gdzie zatrzymują ją pozostałe odmowy —
            # zanim cokolwiek powstanie.
            coordinator = coordinator_from_email(options["coordinator_email"])
            result = create_competition_from_template(
                slug=slug,
                name=options["name"],
                domain=options["domain"],
                template=options["from_template"],
                organizer=options["organizer"],
                contact_email=options["contact_email"],
                short_name=options["short_name"],
                accent=options["accent"],
                path_prefix=options["path_prefix"],
                edition_label=options["edition_label"],
                coordinator=coordinator,
                public_code_prefix=options["public_code_prefix"],
                certificate_prefix=options["certificate_prefix"],
                dry_run=dry_run,
                # Seedy uruchamia komenda, nie czynność – patrz docstring modułu.
                run_safe_seeds=False,
            )
        except ProvisioningError as exc:
            # Odmowa czynności jest odmową komendy, co do słowa: komunikaty są kontraktem (czytają
            # je ``scripts/deploy.sh``, kreator ``/setup/`` i testy), więc nie opakowujemy ich
            # w żaden własny wstęp.
            raise CommandError(str(exc)) from exc

        if not dry_run:
            self._run_safe_seeds(result.template)

        self._report(result)

    def _run_safe_seeds(self, template: dict) -> None:
        """Komendy z ``safe_seeds`` szablonu — wyłącznie globalne i idempotentne (patrz katalog).

        Jedyne wywołanie ``call_command`` na tej ścieżce i jedyny szew, przez który da się je
        podmienić: testy zakładania konkursu nie mają powodu uruchamiać importu kilku tysięcy
        wierszy wykazu SIO/RSPO, a mają powód sprawdzić **decyzję** komendy (co woła).
        """
        for command_name in template["safe_seeds"]:
            self.stdout.write(f"Uruchamiam {command_name} (dane globalne, przebieg idempotentny)…")
            call_command(command_name)

    # --- podsumowanie -------------------------------------------------------------------------
    def _report(self, result) -> None:
        competition = result.competition
        template = result.template
        seeded = result.seeded
        write = self.stdout.write
        if result.dry_run:
            write(self.style.WARNING("PRÓBA NA SUCHO – transakcja wycofana, baza bez zmian."))
        write(
            self.style.SUCCESS(
                f"Konkurs „{competition.name}” ({competition.slug}), szablon: {template['label']}."
            )
        )
        write(f"  witryna:  {competition.site.hostname}:{competition.site.port}")
        write(f"  strony:   /{', /'.join(slug for _, slug, _ in template['pages'])}")
        write(f"  edycja:   {result.edition.year_label} (bieżąca)")
        for stage in result.stages:
            # Terminy wypisujemy w strefie organizatora, bo w niej je liczyliśmy — w UTC wyglądałyby
            # jak przesunięte o godzinę i pierwszą reakcją koordynatora byłaby poprawka, która
            # niczego nie poprawia.
            opens = timezone.localtime(stage.opens_at, WARSAW).date().isoformat()
            deadline = timezone.localtime(stage.deadline_at, WARSAW).date().isoformat()
            write(f"  etap:     {stage.kind} {stage.name} — {opens} → {deadline} (wartość początkowa)")
        if result.coordinator is not None:
            write(f"  koordynator: {result.coordinator.email} (rola w tym konkursie + grupa „coordinator”)")
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
            if result.platform is not None:
                state = "włączono mu teraz" if result.opened_platform else "był już włączony"
                write(
                    f"     Konkurs platformy: {result.platform.slug} – przełącznik path_prefix_routing "
                    f"{state} (bez niego prefiks pod jego domeną daje 404). Własne drzewo stron CMS "
                    f"konkursu: /{competition.path_prefix}/ (strona główna „{competition.slug}”)."
                )
        elif not result.env_lines:
            # Subdomena platformy przy włączonym ``PLATFORM_SUBDOMAINS``: rekord wieloznaczny
            # w DNS-ie i wildcard w ustawieniach już ten adres obejmują, a certyfikat Caddy pobiera
            # przy pierwszym wejściu (``/internal/tls-allowed``). Powtórzenie tu instrukcji o .env
            # kazałoby administratorowi wpisać linijkę, która niczego nie zmienia.
            write(
                f"  1. Nic w .env: „{competition.primary_domain}” jest subdomeną platformy, więc "
                f"obejmuje ją rekord wieloznaczny w DNS-ie oraz wildcard w ALLOWED_HOSTS "
                f"i CSRF_TRUSTED_ORIGINS. Certyfikat Caddy pobierze przy pierwszym wejściu pod "
                f"ten adres (może to potrwać do minuty)."
            )
        else:
            domain = competition.primary_domain
            write(f"  1. DNS: rekord A/AAAA {domain} → adres serwera (oraz www.{domain}, jeśli ma działać).")
            write("  2. Na serwerze, w /opt/olimpiada/.env — jedna linijka, z której biorą się trzy:")
            for line in result.env_lines:
                write(f"       {line}")
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
        if result.stages:
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
