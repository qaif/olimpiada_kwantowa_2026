"""``manage.py replace_page_text`` – podmiana jednego sformułowania na stronie zredagowanej w /cms/.

Po co, skoro są seedy: seed jest narzędziem **importującym** – nadpisuje całą stronę treścią
z repozytorium. Strona, którą redakcja prowadzi w /cms/ (na produkcji m.in. „Skład komitetów”:
listy osób dopisywane przez organizatora), nie może przez niego przejść, bo poprawka jednego
zdania skasowałaby wszystko, co dopisano od importu. Ta komenda zmienia **wyłącznie wskazany
napis** i zostawia resztę strony taką, jaka jest.

Zmiana idzie drogą redakcyjną: nowa rewizja + publikacja. Poprawienie samej kolumny w bazie
zostawiłoby ostatnią rewizję ze starym brzmieniem, a pierwsza edycja w /cms/ po cichu by je
przywróciła (edytor otwiera rewizję, nie wiersz strony).

Trzy bezpieczniki:

- **bez ``--apply`` nic nie zapisuje** – wypisuje, ile wystąpień znalazła i w których polach,
- **strona z nieopublikowanymi zmianami jest odrzucana**: publikacja rewizji wypchnęłaby razem
  z poprawką czyjś szkic,
- **idempotencja**: wystąpienia, które są już częścią nowego brzmienia, nie liczą się jako stare.
  Dzięki temu „X” → „X, Y” uruchomione drugi raz nie daje „X, Y, Y”.

Napis jest szukany w surowej postaci pól ``StreamField`` i ``RichTextField`` (HTML), więc musi
mieścić się w jednym ciągu znaków bez znaczników w środku.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page

#: Znak spoza jakiejkolwiek treści – zasłania na czas liczenia wystąpienia nowego brzmienia.
_MASK = "\x00"


def replace_outside(text: str, old: str, new: str) -> tuple[str, int]:
    """Podmienia ``old`` na ``new`` z pominięciem miejsc, w których ``new`` już stoi."""
    masked = text.replace(new, _MASK) if old in new else text
    count = masked.count(old)
    return masked.replace(old, new).replace(_MASK, new), count


def _json_text(value: str) -> str:
    """Napis w postaci, w jakiej stoi wewnątrz JSON-a (bez otaczających cudzysłowów)."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


class Command(BaseCommand):
    help = "Podmienia jedno sformułowanie w treści strony CMS (nowa rewizja + publikacja)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="Slug strony.")
        parser.add_argument("--old", required=True, help="Dotychczasowe brzmienie.")
        parser.add_argument("--new", required=True, help="Nowe brzmienie.")
        parser.add_argument("--apply", action="store_true", help="Zapisz zmianę (domyślnie: tylko raport).")

    @transaction.atomic
    def handle(self, *args, **options):
        old, new = options["old"], options["new"]
        if not old or old == new:
            raise CommandError("--old musi być niepuste i różne od --new.")
        pages = list(Page.objects.filter(slug=options["slug"]).specific())
        if len(pages) != 1:
            raise CommandError(
                f"Slug „{options['slug']}” wskazuje {len(pages)} stron – potrzebna dokładnie jedna."
            )
        page = pages[0]
        if page.has_unpublished_changes:
            raise CommandError(
                f"Strona {page.url} ma nieopublikowane zmiany – opublikuj albo odrzuć szkic w /cms/ "
                "i uruchom ponownie."
            )

        total = 0
        for field in page._meta.get_fields():
            if isinstance(field, StreamField):
                raw = json.dumps(list(getattr(page, field.name).raw_data), ensure_ascii=False)
                # Napis w JSON-ie stoi po ucieczce znaków – szukamy go w tej samej postaci.
                changed, count = replace_outside(raw, _json_text(old), _json_text(new))
                if count:
                    # Napis JSON, nie lista: ``StreamField.to_python`` sam odtwarza z niego bloki
                    # razem z ich identyfikatorami, czyli dokładnie to, co leży w bazie.
                    setattr(page, field.name, changed)
            elif isinstance(field, RichTextField):
                changed, count = replace_outside(getattr(page, field.name) or "", old, new)
                if count:
                    setattr(page, field.name, changed)
            else:
                continue
            if count:
                self.stdout.write(f"{page.url} · pole „{field.name}”: {count} wystąpień")
                total += count

        if not total:
            self.stdout.write("Nic do zmiany: dotychczasowego brzmienia nie ma (albo strona ma już nowe).")
            return
        if not options["apply"]:
            self.stdout.write(f"Raport: {total} wystąpień do podmiany. Dodaj --apply, żeby zapisać.")
            transaction.set_rollback(True)
            return
        page.save_revision(log_action=True).publish()
        self.stdout.write(self.style.SUCCESS(f"replace_page_text: {total} podmian na {page.url}"))
