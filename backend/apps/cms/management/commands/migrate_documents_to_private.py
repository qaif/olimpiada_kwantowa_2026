"""Przeniesienie plików dokumentów Wagtaila z publicznego storage do prywatnego (audyt 10.10.2026, W2).

Migracje ``cms.0031``–``0034`` przepinają **wiersze** na ``cms.Document``, którego plik leży
w prywatnym storage (``apps/cms/documents.py``). Same pliki zostają tam, gdzie zapisał je domyślny
model Wagtaila – w ``default`` (produkcyjnie anonimowo czytelny bucket ``public-media``) pod
``documents/<oryginalna nazwa>``. To polecenie je przenosi:

1. kopiuje obiekt do prywatnego storage pod nowym kluczem ``<uuid4>/<nazwa>``
   (``document_upload_to`` – ta sama reguła, co przy wgraniu w ``/cms/``),
2. sprawdza, że kopia ma ten sam rozmiar,
3. zapisuje nowy klucz w wierszu (``UPDATE``, bez sygnałów zapisu – plik się nie zmienił,
   więc nie ma czego przeliczać ani indeksować),
4. na końcu kasuje obiekty z publicznego storage (``--keep-public`` zostawia je na miejscu).

Kasowanie jest ostatnie i zbiorcze: przerwany przebieg zostawia co najwyżej kopię w obu miejscach,
nigdy dokumentu bez pliku.

**Idempotentne.** Dokument, którego plik jest już w prywatnym storage, jest pomijany; drugi
przebieg nie robi nic. Plik, którego nie ma w żadnym storage, jest wypisywany jako brakujący –
polecenie nie zgaduje, gdzie mógłby być.

``--dry-run`` wypisuje plan bez zapisu do storage i bazy.

Uruchomić **raz, zaraz po** ``migrate``: do tego czasu dokumenty z wierszami przepiętymi na
``cms.Document`` szukają pliku w prywatnym storage i go nie znajdują (``/documents/<id>/…`` → błąd).
"""

from __future__ import annotations

import posixpath

from django.core.files import File
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from wagtail.documents import get_document_model

from apps.cms.documents import document_upload_to

#: Alias storage, do którego zapisywał domyślny model dokumentu Wagtaila.
PUBLIC_STORAGE_ALIAS = "default"


class Command(BaseCommand):
    help = (
        "Przenosi pliki dokumentów Wagtaila z publicznego storage (public-media) do prywatnego "
        "pod nieodgadywalny klucz. Idempotentne; --dry-run pokazuje plan."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tylko plan – bez zapisu.")
        parser.add_argument(
            "--keep-public",
            action="store_true",
            help="Nie kasuj obiektów z publicznego storage po skopiowaniu (np. na czas weryfikacji).",
        )

    def handle(self, *args, dry_run: bool = False, keep_public: bool = False, **options):
        Document = get_document_model()
        private = Document._meta.get_field("file").storage
        public = storages[PUBLIC_STORAGE_ALIAS]
        if type(private) is type(public) and private.deconstruct() == public.deconstruct():
            # Ochrona przed konfiguracją, w której „prywatny” storage jest tym samym, co publiczny:
            # przeniesienie skasowałoby wtedy właśnie skopiowany plik.
            raise CommandError(
                "Storage dokumentów jest tym samym storage, co 'default' – nie ma dokąd przenosić."
            )

        moved = already = missing = 0
        to_delete: list[str] = []
        for document in Document.objects.order_by("pk").iterator():
            name = document.file.name
            if not name:
                continue
            if private.exists(name):
                already += 1
                continue
            if not public.exists(name):
                missing += 1
                self.stderr.write(f"Brak pliku dokumentu #{document.pk} ({name!r}) w obu storage.")
                continue
            target = document_upload_to(document, posixpath.basename(name.replace("\\", "/")))
            if dry_run:
                self.stdout.write(f"#{document.pk}: {name} -> (prywatny) {target}")
                moved += 1
                continue
            with public.open(name, "rb") as source:
                saved = private.save(target, File(source, name=posixpath.basename(target)))
            if private.size(saved) != public.size(name):
                private.delete(saved)
                raise CommandError(
                    f"Kopia dokumentu #{document.pk} ma inny rozmiar niż oryginał – przerwano, "
                    "oryginał bez zmian."
                )
            Document.objects.filter(pk=document.pk).update(file=saved)
            to_delete.append(name)
            moved += 1
            self.stdout.write(f"#{document.pk}: {name} -> (prywatny) {saved}")

        if not keep_public:
            for name in to_delete:
                public.delete(name)

        verb = "Do przeniesienia" if dry_run else "Przeniesiono"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: {moved}, już prywatne: {already}, brakujące: {missing}"
                + ("" if dry_run or keep_public else f", skasowane z publicznego: {len(to_delete)}")
            )
        )
