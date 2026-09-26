"""``manage.py verify_cutover [--competition SLUG …]`` – kontrola witryn przed przełączeniem (DJ-02 § 10.3).

Dla każdego aktywnego konkursu z rejestru (albo wskazanych ``--competition``):

1. liczba opublikowanych stron witryny = liczba stron paczki eksportu (pobranej teraz z API,
   ``GET c/<slug>/export`` – ta sama paczka, którą importował ``import_cms_bundle --from-api``),
2. każda ścieżka stron paczki i każda z ``linked_paths`` (odnośniki aplikacji – S16) odpowiada 200
   przez klienta testowego Django: host konkursu (dla konkursu pod prefiksem – host gospodarza
   i ``/<prefiks>/…``) i nagłówki „jak od Caddy'ego” w trybie ``primary``
   (``apps.pages.mode.proxy_request_meta``) – w procesie, bez sieci do djcms,
3. witryna ma stronę główną (``is_home``),
4. żadna opublikowana strona nie stoi pod adresem aplikacji głównej (S5),
5. ``/sitemap.xml`` i ``/robots.txt`` odpowiadają 200,
6. przekierowania z paczki są zapisane (``dj_seo.Redirect`` witryny).

Wynik: tabela (konkurs, strony djcms/paczka, adresy 200/wszystkie, przekierowania zapisane/w paczce,
wynik) i lista powodów porażki. Kod 0 – wszystko zielone, 1 – cokolwiek nie (skrypt przełączenia
``djcms_cutover.sh`` wtedy nie przełącza).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.importer import services
from apps.importer.verification import (
    SiteCheck,
    app_path_collisions,
    bundle_paths,
    missing_linked_paths,
    normalise_page_path,
    published_paths,
)

#: Kody odpowiedzi uznawane za „strona działa” (bez przekierowań – adres z paczki ma być stroną).
OK_STATUS = 200


def _proxy_meta() -> tuple[dict[str, str], str | None]:
    """Nagłówki żądania w trybie ``primary`` jak od Caddy'ego; drugi element – powód porażki."""
    try:
        from apps.pages.mode import proxy_request_meta
    except ImportError:
        return {"HTTP_X_DJCMS_MODE": "primary"}, None
    try:
        return proxy_request_meta("primary"), None
    except Exception as exc:  # noqa: BLE001 - brak zaufanego proxy to wynik kontroli, nie awaria komendy
        return {"HTTP_X_DJCMS_MODE": "primary"}, f"tryb primary niedostępny: {exc}"


def request_target(competition) -> tuple[str, str] | None:
    """``(host, prefiks)`` żądań do witryny konkursu – tak, jak dojdą do djcms przez Caddy'ego."""
    from apps.sites.models import CompetitionHost, CompetitionSite, RoutingMode

    if competition.routing_mode == RoutingMode.PATH:
        gateway = (
            CompetitionHost.objects.filter(competition__is_active=True, competition__hosts_path_prefixes=True)
            .exclude(competition=competition)
            .order_by("host")
            .first()
        )
        if gateway is None:
            return None
        return gateway.host, f"/{competition.path_prefix}"
    host = competition.hosts.order_by("host").first()
    if host is not None:
        return host.host, ""
    if competition.is_default and CompetitionSite.objects.filter(is_default=True).exists():
        return "localhost", ""
    return None


class Command(BaseCommand):
    help = "Sprawdza witryny konkursów przed przełączeniem serwisu publicznego na djcms (kod 0/1)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            action="append",
            default=[],
            metavar="SLUG",
            help="Tylko ten konkurs (powtarzalne).",
        )

    def handle(self, *args, **options):
        from apps.sites.models import CompetitionSite

        competitions = CompetitionSite.objects.filter(is_active=True).select_related("site").order_by("slug")
        if options["competition"]:
            competitions = competitions.filter(slug__in=options["competition"])
            missing = set(options["competition"]) - set(competitions.values_list("slug", flat=True))
            if missing:
                raise CommandError(
                    f"Konkursów {', '.join(sorted(missing))} nie ma wśród aktywnych w rejestrze."
                )
        competitions = list(competitions)
        if not competitions:
            raise CommandError("Brak aktywnych konkursów w rejestrze – najpierw manage.py sync_competitions.")
        meta, meta_problem = _proxy_meta()
        checks = [self._check(competition, meta, meta_problem) for competition in competitions]
        self._table(checks)
        failed = [check for check in checks if not check.ok]
        for check in failed:
            for reason in check.failures:
                self.stdout.write(f"  ✗ {check.slug}: {reason}")
        if failed:
            raise CommandError(f"verify_cutover: {len(failed)} z {len(checks)} witryn nie przeszło kontroli.")
        self.stdout.write(
            self.style.SUCCESS(f"verify_cutover: {len(checks)} witryn gotowych do przełączenia.")
        )

    # --- jedna witryna -----------------------------------------------------------------------------

    def _check(self, competition, meta: dict, meta_problem: str | None) -> SiteCheck:
        from cms.models import Page

        check = SiteCheck(slug=competition.slug)
        if meta_problem:
            check.failures.append(meta_problem)
        published = published_paths(competition.site)
        check.pages = len(published)
        if not Page.objects.filter(site=competition.site, is_home=True).exists():
            check.failures.append("brak strony głównej (is_home)")
        check.failures.extend(
            f"S5 – strona pod adresem aplikacji {line}" for line in app_path_collisions(competition.site)
        )
        check.failures.extend(
            f"S16 – aplikacja linkuje do {path}, a strony nie ma"
            for path in missing_linked_paths(competition)
        )

        paths: list[str] = []
        bundle = self._bundle(competition, check)
        if bundle is not None:
            by_id = bundle_paths(bundle)
            check.bundle_pages = len(by_id)
            if check.pages != check.bundle_pages:
                check.failures.append(f"stron opublikowanych {check.pages}, w paczce {check.bundle_pages}")
            paths.extend(by_id.values())
            self._redirects(competition, bundle, by_id, published, check)
        paths.extend(
            normalise_page_path(path) for path in competition.linked_paths or [] if isinstance(path, str)
        )
        self._requests(competition, sorted(set(paths)), meta, check)
        return check

    def _bundle(self, competition, check: SiteCheck):
        from apps.live.client import MainApi, MainApiError

        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile(dir=settings.MEDIA_ROOT) as spool:
                MainApi().download_export(
                    spool, limit_bytes=services.MAX_UNCOMPRESSED_BYTES, competition=competition.slug
                )
                spool.seek(0)
                # Wyłącznie manifest – obrazów do porównania nie potrzeba (walidacja i tak je sprawdza).
                return services.open_bundle(spool)
        except MainApiError as exc:
            check.failures.append(f"paczka eksportu niedostępna ({exc})")
        except services.BundleError as exc:
            check.failures.append(f"paczka eksportu odrzucona ({exc})")
        return None

    def _redirects(self, competition, bundle, by_id: dict, published: dict, check: SiteCheck) -> None:
        from cms.models import Page

        pages = Page.objects.in_bulk([page_id for page_id in published.values()])
        pages_by_id = {
            wagtail_id: pages[published[path]] for wagtail_id, path in by_id.items() if path in published
        }
        specs = services.resolve_redirects(bundle, pages_by_id, services.ImportReport())
        check.bundle_redirects = len(specs)
        if not specs:
            return
        model = services.redirect_model()
        if model is None:
            check.failures.append(f"przekierowań w paczce {len(specs)}, a brak modelu dj_seo.Redirect")
            return
        saved = set(
            model.objects.filter(
                site=competition.site, old_path__in=[spec.old_path for spec in specs]
            ).values_list("old_path", flat=True)
        )
        check.redirects = len(saved)
        for spec in specs:
            if spec.old_path not in saved:
                check.failures.append(f"przekierowanie {spec.old_path} → {spec.new_path} nie jest zapisane")

    def _requests(self, competition, paths: list[str], meta: dict, check: SiteCheck) -> None:
        from django.test import Client
        from django.test.utils import override_settings

        target = request_target(competition)
        if target is None:
            check.failures.append("konkurs bez hosta (i bez gospodarza prefiksu) – nie ma pod czym sprawdzić")
            return
        host, prefix = target
        client = Client(raise_request_exception=False, HTTP_HOST=host, **meta)
        urls = [(f"{prefix}{path}", path) for path in paths]
        urls += [(f"{prefix}/sitemap.xml", "sitemap.xml"), (f"{prefix}/robots.txt", "robots.txt")]
        # Host konkursu jest w ``ALLOWED_HOSTS`` produkcji (te same zmienne co backend); tu dokładamy
        # go jawnie, bo kontrola działa w procesie i nie może zależeć od konfiguracji hostów devu.
        with override_settings(ALLOWED_HOSTS=[*settings.ALLOWED_HOSTS, host]):
            for url, label in urls:
                status = client.get(url).status_code
                if label.endswith(".xml") or label.endswith(".txt"):
                    if status != OK_STATUS:
                        check.failures.append(f"{url} → {status}")
                    continue
                check.paths_total += 1
                if status == OK_STATUS:
                    check.paths_ok += 1
                else:
                    check.failures.append(f"{host}{url} → {status}")

    def _table(self, checks: list[SiteCheck]) -> None:
        header = f"{'konkurs':<20} {'strony':>11} {'adresy 200':>11} {'przekier.':>10}  wynik"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for check in checks:
            self.stdout.write(
                f"{check.slug:<20} {f'{check.pages}/{check.bundle_pages}':>11} "
                f"{f'{check.paths_ok}/{check.paths_total}':>11} "
                f"{f'{check.redirects}/{check.bundle_redirects}':>10}  {'OK' if check.ok else 'BŁĄD'}"
            )
