"""``manage.py djcms_routes`` – pliki kontraktu tras aplikacji dla Caddy'ego i djcms (DJ-02 § 6).

Tryby:

- bez flag: wypisuje manifest na standardowe wyjście (``--format text|json|env``, domyślnie
  ``text`` – do czytania przez człowieka),
- ``--write``: zapisuje ``backend/djcms_contract/app_routes.json`` (dla djcms) i
  ``backend/djcms_contract/app_routes.env`` (dla ``scripts/render_caddyfile.sh``). Pliki są
  commitowane – obraz djcms i generator Caddyfile'a czytają je bez uruchamiania ``web``,
- ``--check``: kod 1 i różnica, gdy pliki nie odpowiadają urlconfowi. Uruchamiane w CI
  i przez ``djcms_cutover.sh --check``: nowy adres aplikacji bez regeneracji plików oznaczałby,
  że po przełączeniu Caddy wysyła go do djcms.

Komenda nie dotyka bazy (``requires_system_checks = []``, żadnych zapytań), więc działa w CI
bez Postgresa – tak samo jak ``makemigrations --check``.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.app_routes import build_manifest, caddy_regexes, contract_env, contract_json

JSON_NAME = "app_routes.json"
ENV_NAME = "app_routes.env"


def default_contract_dir() -> Path:
    """``backend/djcms_contract`` – obok ``manage.py``, czyli w obrazie ``web`` pod ``/app/``."""
    return Path(settings.BASE_DIR) / "djcms_contract"


def rendered_files(manifest=None) -> dict[str, str]:
    """Treść obu plików kontraktu, dokładnie tak, jak ``--write`` je zapisuje."""
    manifest = manifest or build_manifest()
    return {
        JSON_NAME: json.dumps(contract_json(manifest), ensure_ascii=False, indent=2) + "\n",
        ENV_NAME: contract_env(manifest),
    }


class Command(BaseCommand):
    help = "Manifest adresów aplikacji web dla Caddy'ego i django CMS: podgląd, --write, --check."

    # Kontrole systemowe (w tym ``cms.W011``, która sama buduje manifest) nie są tu potrzebne:
    # komenda **jest** kontrolą, a jej wynik ma nie zależeć od stanu reszty konfiguracji.
    requires_system_checks: list[str] = []

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--write", action="store_true", help="Zapisz pliki kontraktu.")
        mode.add_argument(
            "--check", action="store_true", help="Kod 1 i różnica, gdy pliki kontraktu są nieaktualne."
        )
        parser.add_argument(
            "--format",
            choices=("text", "json", "env"),
            default="text",
            help="Format wypisania bez --write/--check (domyślnie text).",
        )
        parser.add_argument(
            "--dir",
            dest="contract_dir",
            default=None,
            help="Katalog plików kontraktu (domyślnie backend/djcms_contract).",
        )

    def handle(self, *args, **options) -> None:
        contract_dir = Path(options["contract_dir"]) if options["contract_dir"] else default_contract_dir()
        manifest = build_manifest()
        files = rendered_files(manifest)

        if options["write"]:
            contract_dir.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                # ``newline="\n"``: plik jest kontraktem bajtowym – ten sam na Windowsie i w CI.
                (contract_dir / name).write_text(content, encoding="utf-8", newline="\n")
                self.stdout.write(f"zapisano {contract_dir / name}")
            return

        if options["check"]:
            self._check(contract_dir, files)
            return

        if options["format"] == "json":
            self.stdout.write(files[JSON_NAME], ending="")
        elif options["format"] == "env":
            self.stdout.write(files[ENV_NAME], ending="")
        else:
            self._text(manifest)

    def _check(self, contract_dir: Path, files: dict[str, str]) -> None:
        stale: list[str] = []
        for name, expected in files.items():
            path = contract_dir / name
            try:
                # Odczyt w trybie tekstowym zamienia ``\r\n`` na ``\n`` – kopia robocza z innym
                # zakończeniem linii nie jest „nieaktualnym kontraktem”.
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = ""
            if current == expected:
                continue
            stale.append(name)
            diff = difflib.unified_diff(
                current.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{path} (w repozytorium)",
                tofile=f"{path} (z urlconfu)",
            )
            self.stdout.write("".join(diff), ending="")
        if stale:
            raise CommandError(
                "Pliki kontraktu tras są nieaktualne wobec urlconfu: "
                + ", ".join(stale)
                + ". Uruchom `python manage.py djcms_routes --write` i dołącz zmianę do commita.",
                returncode=1,
            )
        self.stdout.write(self.style.SUCCESS(f"Kontrakt tras aktualny ({contract_dir})."))

    def _text(self, manifest) -> None:
        app_re, app_re_prefixed = caddy_regexes(manifest)
        self.stdout.write("Pierwsze segmenty aplikacji: " + ", ".join(manifest.first_segments))
        self.stdout.write("Adresy aplikacji pod stronami: " + (", ".join(manifest.nested_paths) or "–"))
        self.stdout.write("Pliki w korzeniu (wyrażenia): " + (", ".join(manifest.root_regexes) or "–"))
        self.stdout.write("Prefiksy prywatne (robots.txt): " + ", ".join(manifest.private_prefixes))
        self.stdout.write(f"APP_RE={app_re}")
        self.stdout.write(f"APP_RE_PREFIXED={app_re_prefixed}")
