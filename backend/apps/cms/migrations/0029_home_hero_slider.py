"""Slider w nagłówku strony głównej: plakaty redakcji i aktualności.

Poza schematem migracja wstawia **jeden** plakat – „Rozpoczęliśmy rejestrację!” – na stronę główną
domyślnej witryny (Konkurs #1), o ile jej slider jest pusty. Plakat trafia i do wiersza strony,
i do jej najnowszej rewizji: edytor w ``/cms/`` otwiera rewizję, a nie wiersz, więc bez tego
pierwsze „Opublikuj” po wdrożeniu po cichu zdjęłoby plakat. Strony główne innych konkursów
zostają z pustym sliderem – ich redakcja wstawi własne plansze.
"""

import json
import uuid

import wagtail.fields
from django.db import migrations, models

REGISTRATION_POSTER = {
    "kicker": "Olimpiada Kwantowa",
    "title": "Rozpoczęliśmy rejestrację!",
    "text": "Załóż konto uczestnika i dołącz do zawodów. Zadania, terminy i wyniki – wszystko w jednym serwisie.",
    "stamp": "zapisy otwarte",
    "button_label": "Zarejestruj się",
    "button_url": "/register/",
    "theme": "czerwony",
}


def _default_home(apps):
    Site = apps.get_model("wagtailcore", "Site")
    HomePage = apps.get_model("cms", "HomePage")
    site = Site.objects.filter(is_default_site=True).first()
    if site is None:
        return None
    return HomePage.objects.filter(pk=site.root_page_id).first()


def add_registration_poster(apps, schema_editor):
    home = _default_home(apps)
    if home is None or list(home.hero_slides.raw_data):
        return
    stream = [{"type": "poster", "value": dict(REGISTRATION_POSTER), "id": str(uuid.uuid4())}]
    home.hero_slides = stream
    home.save(update_fields=["hero_slides"])

    Revision = apps.get_model("wagtailcore", "Revision")
    revision = Revision.objects.filter(pk=home.latest_revision_id).first()
    if revision is None or not isinstance(revision.content, dict):
        return
    content = dict(revision.content)
    # Rewizja trzyma StreamField w tej samej postaci co inne pola strumieniowe tej strony: jako
    # tekst JSON albo jako listę – zależnie od wersji Wagtaila, która ją zapisała.
    sample = content.get("about_body")
    content["hero_slides"] = json.dumps(stream) if isinstance(sample, str) else stream
    content["hero_show_news"] = True
    revision.content = content
    revision.save(update_fields=["content"])


class Migration(migrations.Migration):

    dependencies = [
        ('cms', '0028_editing_freeze'),
        ('wagtailcore', '0075_populate_latest_revision_and_revision_object_str'),
    ]

    operations = [
        migrations.AddField(
            model_name='homepage',
            name='hero_show_news',
            field=models.BooleanField(default=True, help_text='Trzy najnowsze aktualności jako kolejne plansze slidera.', verbose_name='aktualności w sliderze'),
        ),
        migrations.AddField(
            model_name='homepage',
            name='hero_slides',
            field=wagtail.fields.StreamField([('poster', 7)], blank=True, block_lookup={0: ('wagtail.blocks.CharBlock', (), {'help_text': 'Np. „Olimpiada Kwantowa 2026/2027”.', 'label': 'nadtytuł', 'max_length': 60, 'required': False}), 1: ('wagtail.blocks.CharBlock', (), {'help_text': 'Krótko – 2–4 słowa, np. „Rozpoczęliśmy rejestrację!”.', 'label': 'hasło plakatu', 'max_length': 80}), 2: ('wagtail.blocks.TextBlock', (), {'label': 'tekst', 'max_length': 240, 'required': False}), 3: ('wagtail.blocks.CharBlock', (), {'help_text': 'Krótki napis w kółku, np. „udział bezpłatny” albo data.', 'label': 'pieczątka', 'max_length': 30, 'required': False}), 4: ('wagtail.blocks.CharBlock', (), {'label': 'napis na przycisku', 'max_length': 40, 'required': False}), 5: ('wagtail.blocks.CharBlock', (), {'help_text': 'Adres w serwisie (np. /register/) albo pełny adres https://…', 'label': 'adres przycisku', 'max_length': 300, 'required': False}), 6: ('wagtail.blocks.ChoiceBlock', [], {'choices': [('czerwony', 'czerwony plakat na granacie'), ('granatowy', 'granatowy plakat z czerwonym akcentem'), ('papier', 'jasny papier z czerwonym nadrukiem')], 'label': 'kolorystyka'}), 7: ('wagtail.blocks.StructBlock', [[('kicker', 0), ('title', 1), ('text', 2), ('stamp', 3), ('button_label', 4), ('button_url', 5), ('theme', 6)]], {})}, verbose_name='plakaty w sliderze'),
        ),
        migrations.RunPython(add_registration_poster, migrations.RunPython.noop),
    ]
