"""``manage.py scope_cms_access`` — zawęża ``/cms/`` każdego koordynatora do jego konkursu.

Do tego wydania globalna grupa ``coordinator`` miała prawa Wagtaila na **korzeniu** drzewa stron
i kolekcji (``cms.0003``), więc koordynator drugiego konkursu edytował strony i media pierwszego.
Komenda przestawia instalację na stan docelowy (``apps/cms/permissions.py``, docstring modułu)
w jednej transakcji:

1. zakłada grupę ``superkoordynator`` z prawami do korzenia (``ensure_super_coordinator_group``)
   — wcześniej komenda ``superkoordynator --all-current-coordinators`` wpisała do niej obecnych
   koordynatorów, więc żaden z nich niczego nie traci,
2. zakłada każdemu konkursowi grupę ``cms:<slug>`` i kolekcję mediów,
3. przenosi obrazy i dokumenty z korzenia kolekcji do kolekcji konkursu — sama przy jednym
   konkursie, przy kilku wyłącznie wskazanemu (``--root-media-to``),
4. zabiera globalnej grupie ``coordinator`` uprawnienia ``/cms/`` (sama grupa zostaje — jest rolą),
5. wpisuje koordynatorów każdego konkursu do jego grupy (dalej robią to sygnały,
   ``apps/cms/signals.py``).

**Kontrola „przed i po” jest częścią komendy, a nie testu.** Dla każdego koordynatora bez
``is_superuser`` komenda liczy macierz możliwości (``cms_abilities``: każda strona, obraz,
dokument, komunikat i ustawienia witryny × czynność) przed zmianą i po niej, w tej samej
transakcji. W instalacji z **jednym** konkursem obie macierze mają być identyczne — inaczej
komenda wycofuje całość i wypisuje różnicę. W instalacji z kilkoma konkursami różnica jest celem
(koordynator traci cudze strony i media), więc jest wypisywana; **zyskać** nie może nikt w żadnym
układzie — to zawężenie, nie nadanie.

``--dry-run`` wykonuje wszystko i wycofuje transakcję: raport pokazuje to, co zrobiłaby baza.
Komenda jest idempotentna — drugi przebieg niczego nie zmienia i mówi o tym.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.models import Collection

from apps.accounts.models import User
from apps.accounts.super_coordinator import current_coordinators, is_super_coordinator
from apps.cms.permissions import (
    adopt_root_media,
    cms_abilities,
    cms_group_name,
    ensure_cms_group,
    ensure_super_coordinator_group,
    global_coordinator_cms_rows,
    strip_global_coordinator,
    sync_competition_cms_group,
)
from apps.tenancy.models import Competition


class ScopeMismatch(CommandError):
    """Macierz możliwości po zmianie nie zgadza się z oczekiwaną — transakcja jest wycofana."""


class Command(BaseCommand):
    help = (
        "Zawęża /cms/ koordynatorów do ich konkursów: grupy cms:<slug>, kolekcje konkursów, "
        "globalna grupa coordinator bez uprawnień /cms/. Najpierw: superkoordynator "
        "--all-current-coordinators. --dry-run tylko pokazuje."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Wykonaj i wycofaj — pokaż, co by się zmieniło, niczego nie zapisując.",
        )
        parser.add_argument(
            "--root-media-to",
            metavar="SLUG",
            help=(
                "Konkurs, do którego kolekcji trafią obrazy i dokumenty z korzenia kolekcji. "
                "Przy jednym konkursie domyślnie on; przy kilku bez tej opcji pliki zostają "
                "w korzeniu (widzi je tylko superkoordynator)."
            ),
        )

    def handle(self, *args, **options):
        competitions = list(Competition.objects.select_related("site").order_by("pk"))
        if not competitions:
            raise CommandError("W bazie nie ma żadnego konkursu — nie ma czego zawężać.")
        media_target = self._media_target(competitions, options["root_media_to"])
        dry_run = options["dry_run"]

        try:
            with transaction.atomic():
                self._scope(competitions, media_target)
                if dry_run:
                    transaction.set_rollback(True)
        except ScopeMismatch:
            self.stderr.write(self.style.ERROR("Wycofano — nic nie zapisano."))
            raise

        if dry_run:
            self.stdout.write(self.style.WARNING("Próba na sucho — nic nie zapisano."))
        else:
            self.stdout.write(self.style.SUCCESS("Gotowe: /cms/ jest zawężone do konkursów."))

    # --- kroki ------------------------------------------------------------------------------------
    def _media_target(self, competitions, slug):
        if slug:
            for competition in competitions:
                if competition.slug == slug.strip().lower():
                    return competition
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{slug}”.")
        return competitions[0] if len(competitions) == 1 else None

    def _scope(self, competitions, media_target) -> None:
        watched = self._watched_accounts()
        before = {user.pk: cms_abilities(user) for user in watched}

        ensure_super_coordinator_group()
        for competition in competitions:
            ensure_cms_group(competition)

        self._adopt_media(media_target)
        self._strip()
        for competition in competitions:
            added, removed = sync_competition_cms_group(competition)
            name = cms_group_name(competition)
            for email in added:
                self.stdout.write(f"{'dopisuję':<11}{email} → „{name}”")
            for email in removed:
                self.stdout.write(f"{'wypisuję':<11}{email} z „{name}” (nie koordynuje tego konkursu)")

        self._compare(watched, before, single=len(competitions) == 1)

    def _watched_accounts(self) -> list[User]:
        """Koordynatorzy, którym zawężenie może coś zmienić: aktywni i bez ``is_superuser``."""
        rows = [user for user in current_coordinators() if user.is_active and not user.is_superuser]
        supers = [user.email for user in rows if is_super_coordinator(user)]
        if supers:
            self.stdout.write(f"Superkoordynatorzy (zachowują dostęp do wszystkiego): {', '.join(supers)}")
        return rows

    def _adopt_media(self, media_target) -> None:
        from wagtail.documents import get_document_model
        from wagtail.images import get_image_model

        root = Collection.get_first_root_node()
        waiting = (
            get_image_model().objects.filter(collection=root).count()
            + get_document_model().objects.filter(collection=root).count()
        )
        if not waiting:
            return
        if media_target is None:
            self.stdout.write(
                self.style.WARNING(
                    f"W korzeniu kolekcji leży {waiting} plików, a konkursów jest kilka — zostają "
                    "tam (widzi je superkoordynator). Wskaż właściciela opcją --root-media-to."
                )
            )
            return
        moved = adopt_root_media(media_target)
        self.stdout.write(
            f"{'przenoszę':<11}{moved['images']} obrazów i {moved['documents']} dokumentów z korzenia "
            f"do kolekcji „{media_target.name}”"
        )

    def _strip(self) -> None:
        rows = global_coordinator_cms_rows()
        if not any(rows.values()):
            self.stdout.write("Grupa „coordinator” nie ma już uprawnień /cms/ — bez zmian.")
            return
        for permission in rows["permissions"]:
            self.stdout.write(
                f"{'zabieram':<11}„coordinator”: {permission.content_type.app_label}.{permission.codename}"
            )
        for row in rows["pages"]:
            self.stdout.write(
                f"{'zabieram':<11}„coordinator”: {row.permission.codename} na stronie „{row.page.title}”"
            )
        for row in rows["collections"]:
            self.stdout.write(
                f"{'zabieram':<11}„coordinator”: {row.permission.codename} w kolekcji „{row.collection.name}”"
            )
        strip_global_coordinator()

    def _compare(self, watched, before, *, single: bool) -> None:
        problems = []
        for user in watched:
            after = cms_abilities(User.objects.get(pk=user.pk))
            lost = before[user.pk] - after
            gained = after - before[user.pk]
            if gained:
                problems.append(
                    f"{user.email} zyskałby {len(gained)} pozycji, np. {sorted(gained, key=str)[:3]}"
                )
            if lost and single:
                problems.append(
                    f"{user.email} straciłby {len(lost)} pozycji, np. {sorted(lost, key=str)[:3]}"
                )
            elif lost:
                self.stdout.write(
                    f"{'zawężam':<11}{user.email}: traci {len(lost)} pozycji w innych konkursach"
                )
            else:
                self.stdout.write(f"{'bez zmian':<11}{user.email}: te same możliwości w /cms/")
        if problems:
            raise ScopeMismatch(
                "Macierz możliwości po zawężeniu różni się od oczekiwanej:\n" + "\n".join(problems)
            )
