"""``manage.py seed_cms`` – przykładowe treści redakcyjne dla dema i dev-a.

Komenda jest **idempotentna**: rozpoznaje strony po slugu i aktualizuje je zamiast tworzyć duplikaty.
Nie dotyka ``seed_demo`` z ``apps.competitions`` (to osobna komenda i osobna domena) ani struktury
drzewa – tę tworzy migracja ``apps.cms.0002_initial_tree``. Uruchamiać wolno wyłącznie w dev/staging:
w produkcji treści pisze redaktor.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.cms.models import ArchiveEditionPage, ArchiveIndexPage, HomePage, NewsIndexPage, NewsPage
from apps.competitions.models import Edition

HERO_TEXT = (
    "<p>Serwis informacyjny olimpiady: terminy etapów, treści zadań po otwarciu etapu, "
    "archiwum poprzednich edycji i ogłoszone tabele wyników.</p>"
)

NEWS = (
    (
        "ruszyla-rejestracja",
        "Ruszyła rejestracja do eliminacji",
        "Konto zakłada się samodzielnie; do etapu okręgowego wchodzi się z kwalifikacji.",
    ),
    (
        "zasady-oceniania",
        "Zasady oceniania 0-2-5-6",
        "Każdą pracę ocenia niezależnie dwóch recenzentów; rozjazd rozstrzyga trzeci.",
    ),
)


class Command(BaseCommand):
    help = "Wypełnia część informacyjną przykładowymi treściami (dev/demo). Idempotentne."

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            self.stderr.write("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
            return

        if not home.hero_text:
            home.hero_text = HERO_TEXT
            home.save()
            self.stdout.write("strona główna: uzupełniono wprowadzenie")

        self._seed_news(home)
        self._seed_archive(home)
        self.stdout.write(self.style.SUCCESS("seed_cms: gotowe"))

    def _seed_news(self, home: HomePage) -> None:
        index = NewsIndexPage.objects.child_of(home).first()
        if index is None:
            return
        for slug, title, lead in NEWS:
            page = NewsPage.objects.child_of(index).filter(slug=slug).first()
            if page is not None:
                continue
            index.add_child(instance=NewsPage(title=title, slug=slug, lead=lead, date=timezone.localdate()))
            self.stdout.write(f"aktualność: {slug}")

    def _seed_archive(self, home: HomePage) -> None:
        """Po jednej stronie archiwum na edycję, która nie jest bieżąca."""
        index = ArchiveIndexPage.objects.child_of(home).first()
        if index is None:
            return
        for edition in Edition.objects.filter(is_current=False).order_by("pk"):
            if ArchiveEditionPage.objects.child_of(index).filter(edition=edition).exists():
                continue
            slug = f"edycja-{edition.pk}"
            if ArchiveEditionPage.objects.child_of(index).filter(slug=slug).exists():
                continue
            index.add_child(instance=ArchiveEditionPage(title=edition.year_label, slug=slug, edition=edition))
            self.stdout.write(f"archiwum: {slug}")
