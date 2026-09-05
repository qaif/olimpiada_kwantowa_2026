"""Wartości początkowe ``cms.SiteSettings`` dla domyślnej witryny.

Dane organizatora są w czterech miejscach starej strony identyczne (nagłówek, „O Olimpiadzie”,
„Kontakt”, stopka) i pochodzą z ``docs/import/stara-strona-inwentarz.md`` (sekcja 4.1). Wpisujemy
je raz, tutaj, żeby świeża baza miała komplet jeszcze przed pierwszym wejściem redaktora
do ``/cms/`` – potem zmienia je redakcja, a nie deploy.

Migracja **nie nadpisuje** istniejącego wiersza: na bazie, gdzie ktoś zdążył poprawić numer
telefonu, powtórzone ``migrate`` cofnęłoby tę poprawkę.
"""

from django.db import migrations

DEFAULTS = {
    "site_name": "Olimpiada Kwantowa",
    "tagline": "Przyszłość ma naturę kwantową.",
    "organizer_name": "Fundacja Quantum AI",
    "organizer_address": "ul. Sanocka 9/103, 02-110 Warszawa",
    "organizer_registry": "KRS 0000808359 · NIP 7010955891 · REGON 384899425",
    "contact_email": "contact@qaif.org",
    "contact_phone": "+48 507 982 292",
    "contact_url": "https://www.qaif.org/",
}


def create_defaults(apps, schema_editor):
    Site = apps.get_model("wagtailcore", "Site")
    SiteSettings = apps.get_model("cms", "SiteSettings")
    site = Site.objects.filter(is_default_site=True).first() or Site.objects.order_by("pk").first()
    if site is None:  # pragma: no cover - wagtailcore zawsze tworzy witrynę w 0002_initial_data
        return
    SiteSettings.objects.get_or_create(site=site, defaults=DEFAULTS)


def remove_defaults(apps, schema_editor):
    """Wycofanie: kasujemy wyłącznie wiersz o wartościach dokładnie takich, jakie tu wpisano."""
    SiteSettings = apps.get_model("cms", "SiteSettings")
    SiteSettings.objects.filter(**DEFAULTS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0005_contentpage_sitesettings_home_steps"),
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [migrations.RunPython(create_defaults, remove_defaults)]
