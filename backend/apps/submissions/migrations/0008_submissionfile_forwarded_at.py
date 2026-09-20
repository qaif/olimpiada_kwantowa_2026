"""Znacznik „ten plik został już przekazany organizatorowi” (prośba organizatora z 20.09.2026).

Kolumna wchodzi pusta i to jest zamierzone: pliki przyjęte przed wdrożeniem **nie** zostaną
przekazane wstecz. Przekazywanie wisi na werdykcie skanu, a ten dla starych plików zapadł dawno;
backfill wysłałby komitetowi cały dotychczasowy rocznik jednym ciągiem, czego nikt nie prosił.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("submissions", "0007_submission_competition_not_null")]

    operations = [
        migrations.AddField(
            model_name="submissionfile",
            name="forwarded_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="przekazany organizatorowi"),
        ),
    ]
