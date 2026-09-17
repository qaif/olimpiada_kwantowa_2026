"""``manage.py seed_partners`` – logotypy partnerów i organizatora w bibliotece i na stronach.

Źródłem jest ``apps/cms/fixtures/partners/``: pliki przekazane przez organizatora plus manifest
``partners.json``, który mówi, czyj jest który plik i pod jakim adresem stoi instytucja.

Dlaczego to **osobna** komenda, a nie kolejna sekcja ``seed_legacy_content``:

- ``seed_legacy_content`` jest narzędziem importującym i opisuje docelowy stan treści – przy każdym
  przebiegu nadpisuje ją tą samą treścią z plików. Lista partnerów jest czymś innym: rośnie
  w ``/cms/`` razem z podpisywanymi umowami, a poziom współpracy i opis są decyzją redakcji.
  Trzymanie jej w tamtej komendzie oznaczałoby, że każdy import treści kasuje partnerów dopisanych
  po ostatnim wdrożeniu (przed tą zmianą ``_seed_partners`` robiło dokładnie to),
- obie komendy dotykają tej samej strony, więc kolejność uruchomienia nie może mieć znaczenia:
  ``seed_legacy_content`` nie rusza już listy, jeśli są w niej wpisy, a ``seed_partners`` dopisuje
  się do tego, co zastanie.

Idempotencja jest **po nazwie partnera**, nie po pozycji w liście: wpis o tej samej nazwie zostaje
zaktualizowany (logotyp, adres), a nie zduplikowany. Aktualizujemy wyłącznie pola, które są
tożsamością wpisu – logotyp i adres; ``level`` i ``description`` ustawiamy tylko przy zakładaniu
wpisu, bo to redakcja, a nie manifest, decyduje, w której grupie stoi partner i co o nim napisać.
Wpisy dodane w ``/cms/``, których w manifeście nie ma, zostają nietknięte.

**Pola ``file`` i ``url`` są opcjonalne**, i to nie jest wygoda, tylko warunek bezpieczeństwa.
Część partnerów organizator dopisał wprost w ``/cms/`` na produkcji: ich logotypy leżą w bibliotece
Wagtaila, a plików źródłowych nie mamy. Manifest wymienia ich z nazwy (żeby świeża instalacja miała
tę samą listę), ale bez pliku i bez adresu – a komenda **nie rusza** wtedy ani logotypu, ani adresu.
Gdyby ruszała, jeden przebieg na produkcji skasowałby znak i odnośnik, których nie da się odtworzyć
z repozytorium. Wpis bez logotypu pokazuje na stronie kółko z inicjałami.

Czego komenda **nie** umie: usunąć partnera. Wpis skasowany w ``/cms/`` wróci przy najbliższym
przebiegu, bo manifest opisuje stan docelowy, a nie różnicę. Zakończenie współpracy jest więc
zmianą w repozytorium (skreślenie wpisu z ``partners.json``), a nie samym kliknięciem w panelu –
i to jest właściwa kolejność: lista partnerów wywieszona na stronie ma mieć ślad w historii.

Logotyp organizatora (``qaif.png``) nie jest partnerem: trafia do ``SiteSettings.organizer_logo``,
czyli tam, skąd stopka bierze pozostałe dane organizatora. Dzięki temu jest jedno miejsce, w którym
redakcja podmienia znak fundacji, i żadna strona nie ma go zaszytego w szablonie.

Normalizacja plików (CMYK → RGB, ograniczenie szerokości, wybór PNG/JPEG) należy do
``apps.cms.images`` – tam też stoi uzasadnienie.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.cms.blocks import PARTNER_LEVELS
from apps.cms.images import PARTNERS_DIR, ensure_image
from apps.cms.models import HomePage, PartnersPage, SiteSettings

MANIFEST = PARTNERS_DIR / "partners.json"

#: Klucze poziomów współpracy dopuszczone w manifeście – ta sama lista, co w ``/cms/``.
LEVEL_KEYS = frozenset(key for key, _ in PARTNER_LEVELS)


def _read_manifest(path: Path) -> dict:
    """Manifest po walidacji. Błąd w pliku ma zatrzymać komendę, a nie wgrać połowę logotypów."""
    if not path.exists():
        raise CommandError(f"Brak manifestu logotypów: {path}.")
    data = json.loads(path.read_text(encoding="utf-8"))

    partners = data.get("partners") or []
    if not partners:
        raise CommandError(f"Manifest {path} nie wymienia ani jednego partnera.")
    for entry in partners:
        for field in ("name", "level"):
            if not entry.get(field):
                raise CommandError(f"Wpis {entry!r} w manifeście nie ma pola „{field}”.")
        if entry["level"] not in LEVEL_KEYS:
            raise CommandError(
                f"Poziom „{entry['level']}” (partner {entry['name']}) nie występuje w PARTNER_LEVELS."
            )
        # ``file`` jest opcjonalny (patrz ``_comment`` w manifeście): partner dopisany przez
        # organizatora wprost w ``/cms/`` ma logotyp w bibliotece Wagtaila i nie ma go w repozytorium.
        # Wpisana nazwa pliku musi natomiast istnieć – literówka w manifeście ma zatrzymać komendę,
        # a nie po cichu zrobić z partnera wpisu bez znaku.
        if entry.get("file"):
            source = PARTNERS_DIR / entry["file"]
            if not source.exists():
                raise CommandError(f"Brak pliku logotypu {source}.")

    organizer = data.get("organizer") or {}
    if organizer and not (PARTNERS_DIR / organizer.get("file", "")).exists():
        raise CommandError(f"Brak pliku logotypu organizatora {organizer.get('file')!r}.")
    return data


class Command(BaseCommand):
    help = "Wgrywa logotypy partnerów i organizatora oraz uzupełnia stronę /partnerzy/. Idempotentne."

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        page = PartnersPage.objects.child_of(home).first()
        if page is None:
            raise CommandError("Brak strony /partnerzy/ – uruchom najpierw `manage.py seed_legacy_content`.")

        manifest = _read_manifest(MANIFEST)
        created, updated = self._seed_partners(page, manifest["partners"])
        organizer = self._seed_organizer_logo(home, manifest.get("organizer") or {})

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_partners: {len(page.partners)} partnerów na stronie "
                f"({created} nowych, {updated} zaktualizowanych obrazów), organizator: {organizer}"
            )
        )

    # --- partnerzy ------------------------------------------------------------------------

    def _seed_partners(self, page: PartnersPage, entries: list[dict]) -> tuple[int, int]:
        """Dopisuje albo aktualizuje wpisy partnerów. Zwraca ``(nowe obrazy, podmienione obrazy)``.

        Strumień przepisujemy w całości (``StreamField`` nie ma edycji wpisu w miejscu), ale
        zachowujemy kolejność i wartości wpisów, które już były – zmieniają się wyłącznie logotyp
        i adres tych partnerów, których wymienia manifest.
        """
        by_name = {block.value["name"]: dict(block.value) for block in page.partners}
        order = [block.value["name"] for block in page.partners]
        created = updated = 0

        for entry in entries:
            values = by_name.get(entry["name"])
            if values is None:
                values = {
                    "name": entry["name"],
                    "level": entry["level"],
                    "description": entry.get("description", ""),
                }
                by_name[entry["name"]] = values
                order.append(entry["name"])

            if entry.get("file"):
                image, action = ensure_image(
                    entry["name"],
                    PARTNERS_DIR / entry["file"],
                    description=entry["name"],
                )
                created += action == "created"
                updated += action == "updated"
                values["logo"] = image
                self.stdout.write(f"partner {entry['name']}: logotyp #{image.pk} {action}")
            else:
                # Manifest nie zna pliku – logotypu **nie ruszamy**. Wpis nowy dostanie kartę
                # z inicjałami, a wpis istniejący zachowa obraz wgrany w ``/cms/``. Wyzerowanie
                # go tutaj skasowałoby na produkcji znak, którego nie ma w repozytorium, czyli
                # nie dałoby się go przywrócić komendą.
                self.stdout.write(f"partner {entry['name']}: bez logotypu w manifeście")

            # Brak klucza ``url`` znaczy „manifest nie wie”, a nie „adresu nie ma”: pusty napis
            # nadpisałby adres wpisany przez redakcję. Jawne ``"url": ""`` nadal czyści pole.
            if "url" in entry:
                values["url"] = entry["url"]

        page.partners = [("partner", by_name[name]) for name in order]
        page.save()
        page.save_revision().publish()
        return created, updated

    # --- organizator ----------------------------------------------------------------------

    def _seed_organizer_logo(self, home: HomePage, organizer: dict) -> str:
        if not organizer:
            return "bez zmian (manifest nie podaje logotypu)"
        image, action = ensure_image(
            organizer["title"],
            PARTNERS_DIR / organizer["file"],
            description=organizer["title"],
        )
        settings = SiteSettings.for_site(home.get_site())
        if settings.organizer_logo_id != image.pk:
            settings.organizer_logo = image
            settings.save(update_fields=["organizer_logo"])
        return f"{organizer['title']} #{image.pk} {action}"
