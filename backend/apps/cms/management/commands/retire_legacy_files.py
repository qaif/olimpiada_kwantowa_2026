"""``manage.py retire_legacy_files`` – zdejmuje pliki wycofane przez organizatora, bez seedu.

``seed_legacy_content`` już dziś odpina i kasuje pliki wymienione w ``LegacyPage.retired_pdf_titles``
(patrz jego ``_retire_attachments``), ale robi to jako efekt uboczny **pełnego** przebiegu – a ten
na produkcji nie chodzi po każdym wdrożeniu: nadpisałby treść wszystkich stron plikami z repozytorium
i zostawił nową rewizję bez autora (patrz docstring ``seed_legacy_content``, akapit o ochronie stron
zredagowanych w ``/cms/``). Wycofanie pliku samo w sobie nie ma nic wspólnego z treścią strony – to
ta sama operacja co tamta, tylko wywołana **bez** ``page.save()`` i ``page.save_revision()``: strona
zostaje z dokładnie tym samym ``latest_revision_id``, tą samą liczbą rewizji i tą samą treścią, nawet
jeśli redakcja zdążyła ją zmienić w ``/cms/`` od czasu, gdy plik przyszedł z repozytorium.

Stronę szukamy **dokładnie tak, jak szuka jej seed** (``site_tree.take_document_page``): najpierw
w sekcji ``/dokumenty/`` (stan docelowy), a gdy jej tam nie ma, pod stroną główną (układ sprzed
wydzielenia sekcji). W odróżnieniu od tamtej funkcji **nic nie przenosimy i niczego nie zakładamy**
– ta komenda ma prawo tylko sprzątać pliki w bibliotece, nie ruszać drzewa stron. Strona, której nie
ma nigdzie, jest pomijana z komunikatem, a nie błędem: to znaczy, że instalacja nigdy jej nie miała
albo ktoś skasował ją w ``/cms/`` – w obu przypadkach nie ma czego odpinać.

Idempotentna: drugi przebieg nie znajduje już dokumentu o wycofanym tytule i wypisuje „nic do
usunięcia”. ``--dry-run`` tylko liczy, ile plików zostałoby usuniętych, i niczego nie zmienia.

Cały przebieg stoi w jednej transakcji (``@transaction.atomic``): gdyby ``retire_documents``
podniosło ``ProtectedError`` (dokument o tym samym tytule wciąż wisi przy **innej** stronie – patrz
jego docstring), żaden ze wcześniej odpiętych załączników nie zostaje odpięty na trwałe – baza
wraca do stanu sprzed uruchomienia komendy, a nie do stanu „pół sprzątnięte”.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.documents import get_document_model

from apps.cms.attachments import retire_documents
from apps.cms.management.commands.seed_legacy_content import PAGES, LegacyPage
from apps.cms.models import (
    ContentPage,
    ContentPageAttachment,
    DocumentIndexPage,
    DocumentPage,
    DocumentPageAttachment,
)
from apps.cms.site_tree import INDEX_SLUG, home_page


def _find_page(home, spec: LegacyPage):
    """Strona wpisu ``spec`` – w sekcji dokumentów, a gdy jej tam nie ma, pod stroną główną.

    Sama kolejność wyszukiwania, co w ``site_tree.take_document_page``, ale bez przenoszenia:
    ta komenda czyta drzewo, nie zmienia go. Zwraca ``None``, gdy strony nie ma nigdzie.
    """
    model = DocumentPage if spec.document else ContentPage
    index = DocumentIndexPage.objects.child_of(home).filter(slug=INDEX_SLUG).first()
    if index is not None:
        page = model.objects.child_of(index).filter(slug=spec.slug).first()
        if page is not None:
            return page
    return model.objects.child_of(home).filter(slug=spec.slug).first()


class Command(BaseCommand):
    help = (
        "Zdejmuje z biblioteki Wagtaila pliki wycofane przez organizatora "
        "(LegacyPage.retired_pdf_titles), bez ponownego seedowania treści stron. Idempotentna."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Tylko policz, co zostałoby usunięte – nic nie zmieniaj.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        home = home_page()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        dry_run = bool(options.get("dry_run"))

        specs = [spec for spec in PAGES if spec.retired_pdf_titles]
        if not specs:
            self.stdout.write("brak stron z wycofanymi plikami do sprawdzenia")
            return

        for spec in specs:
            page = _find_page(home, spec)
            address = f"/dokumenty/{spec.slug}/" if spec.document else f"/{spec.slug}/"
            if page is None:
                self.stdout.write(f"{address}: strony nie ma w drzewie – pomijam")
                continue

            if dry_run:
                count = get_document_model().objects.filter(title__in=spec.retired_pdf_titles).count()
                if count:
                    self.stdout.write(f"{address}: {count} plik(ów) do usunięcia (--dry-run)")
                else:
                    self.stdout.write(f"{address}: nic do usunięcia (--dry-run)")
                continue

            model = DocumentPageAttachment if spec.document else ContentPageAttachment
            removed = retire_documents(page, model, spec.retired_pdf_titles)
            if removed:
                self.stdout.write(f"{address}: usunięto {removed} plik(ów)")
            else:
                self.stdout.write(f"{address}: nic do usunięcia")
