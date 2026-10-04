"""Wgrywa grafiki marki konkursu (logotyp nagłówka, favikona, obraz udostępniania) i je wskazuje.

Ta sama zmiana da się zrobić ręcznie: wgrać pliki w ``/cms/`` → Obrazy i wskazać je
w „Ustawieniach konkursu”. Komenda jest dla operatora, który wdraża gotowy komplet (np. logo IQO,
4.10.2026): pliki idą do kolekcji **tego** konkursu (``apps.cms.permissions.ensure_collection``),
więc widzi je jego redakcja, a nie redakcje pozostałych konkursów.

Pliki można podać ścieżkami albo jednym archiwum ZIP na standardowym wejściu (``--zip -``) –
kontener ``web`` ma system plików tylko do odczytu, więc na produkcji to jest wygodniejsza droga::

    docker compose exec -T web python manage.py competition_brand_images iqo --zip - < marka.zip

W archiwum liczą się nazwy ``site_logo.png``, ``favicon.png`` i ``social_image.png`` (każda
opcjonalna). Wpis audytu ``competition.brand_images_changed`` z listą zmienionych pól.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from django.core.files.images import ImageFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.images import get_image_model

FIELDS = ("site_logo", "favicon", "social_image")
TITLES = {"site_logo": "logotyp serwisu", "favicon": "favikona", "social_image": "obraz udostępniania"}
MAX_BYTES = 5 * 1024 * 1024


class Command(BaseCommand):
    help = "Wgrywa logotyp nagłówka, favikonę i obraz udostępniania konkursu i wskazuje je w konkursie."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Identyfikator konkursu, np. iqo.")
        for name in FIELDS:
            parser.add_argument(
                f"--{name.replace('_', '-')}", dest=name, help=f"Plik PNG/JPG: {TITLES[name]}."
            )
        parser.add_argument(
            "--zip",
            dest="zip",
            help="Archiwum ZIP (site_logo.png, favicon.png, social_image.png); „-” = stdin.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Sprawdź pliki i wycofaj transakcję.")

    def handle(self, *args, slug, **options):
        from apps.cms.permissions import ensure_collection
        from apps.core.models import audit
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=slug).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu „{slug}”.")
        files = self._files(options)
        if not files:
            raise CommandError(
                "Nie podano żadnego pliku (--site-logo, --favicon, --social-image albo --zip)."
            )
        with transaction.atomic():
            collection = ensure_collection(competition)
            changed = []
            for name, (filename, content) in files.items():
                if len(content) > MAX_BYTES:
                    raise CommandError(f"{filename}: plik większy niż 5 MB.")
                image = get_image_model()(
                    title=f"{competition.short_name or competition.name} – {TITLES[name]}",
                    collection=collection,
                    file=ImageFile(io.BytesIO(content), name=filename),
                )
                image.full_clean(exclude=["file_hash"])
                image.save()
                setattr(competition, name, image)
                changed.append(name)
                self.stdout.write(f"{name}: {filename} → obraz #{image.pk} ({image.width}×{image.height})")
            competition.save(update_fields=changed)
            audit(None, "competition.brand_images_changed", competition, {"fields": changed})
            if options["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("[próba] wycofano – baza bez zmian."))
                return
        self.stdout.write(self.style.SUCCESS(f"Konkurs {slug}: ustawiono {', '.join(changed)}."))

    def _files(self, options) -> dict[str, tuple[str, bytes]]:
        found: dict[str, tuple[str, bytes]] = {}
        if options.get("zip"):
            raw = sys.stdin.buffer.read() if options["zip"] == "-" else Path(options["zip"]).read_bytes()
            try:
                archive = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile as exc:
                raise CommandError("To nie jest archiwum ZIP.") from exc
            names = {Path(n).name: n for n in archive.namelist() if not n.endswith("/")}
            for field in FIELDS:
                for ext in (".png", ".jpg", ".jpeg"):
                    if f"{field}{ext}" in names:
                        found[field] = (f"{field}{ext}", archive.read(names[f"{field}{ext}"]))
                        break
        for field in FIELDS:
            path = options.get(field)
            if path:
                found[field] = (Path(path).name, Path(path).read_bytes())
        return found
