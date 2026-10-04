"""Języki interfejsu per konkurs (I18N-01 § 1) – zbiór zamiast przełącznika „angielski tak/nie”.

Przepisanie danych zachowuje to, co każdy konkurs oferował do tej pory:

- witryna z włączonym ``cms.SiteSettings.english_interface_enabled`` oferowała **oba** języki
  instalacji, więc dostaje ``["pl", "en"]``,
- witryna z wyłączonym przełącznikiem (tak stoi Olimpiada Kwantowa) dostaje sam język domyślny
  konkursu: ``["pl"]``. Jedyny konkurs, któremu to cokolwiek zmienia, to konkurs z
  ``default_language="en"`` i wyłączonym przełącznikiem – do tej pory pokazywał się po polsku
  wbrew własnemu językowi domyślnemu, czyli w stanie, którego nikt nie zamawiał.

Kolumnę przełącznika kasuje dopiero ``cms.0031`` (zależna od tej migracji), więc tutaj jest ona
jeszcze do odczytu. Cofnięcie odtwarza przełącznik z obecności ``en`` w zbiorze – tyle, ile stary
kod umie wyrazić.
"""

from django.db import migrations, models


def languages_from_switch(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    SiteSettings = apps.get_model("cms", "SiteSettings")
    english_sites = set(
        SiteSettings.objects.filter(english_interface_enabled=True).values_list("site_id", flat=True)
    )
    for competition in Competition.objects.all():
        if competition.site_id in english_sites:
            languages = ["pl", "en"]
        else:
            languages = [competition.default_language or "pl"]
        competition.interface_languages = languages
        competition.save(update_fields=["interface_languages"])


def switch_from_languages(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    SiteSettings = apps.get_model("cms", "SiteSettings")
    for competition in Competition.objects.all():
        english = "en" in (competition.interface_languages or []) and len(competition.interface_languages) > 1
        SiteSettings.objects.filter(site_id=competition.site_id).update(english_interface_enabled=english)


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0010_path_prefix_routing_on_platform"),
        ("cms", "0030_hero_slider_images_intro_deadline"),
    ]

    operations = [
        migrations.AddField(
            model_name="competition",
            name="interface_languages",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Języki, które uczestnik może wybrać. Język domyślny jest zawsze wśród nich.",
                verbose_name="języki interfejsu",
            ),
        ),
        migrations.RunPython(languages_from_switch, switch_from_languages),
    ]
