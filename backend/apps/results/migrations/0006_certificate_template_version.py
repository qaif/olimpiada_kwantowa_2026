"""Wersja tekstu dokumentu zapamiętana przy wystawieniu (§ 1.1.3).

Migracja jest **wyłącznie schematem**, bez ``RunPython`` – i to jest decyzja, a nie przeoczenie.
Dokumenty wystawione przed tym wydaniem mają zostać z pustym polem, bo puste pole znaczy „układ
wbudowany”, czyli dzisiejsze stałe ``apps/results/certificates.py``. Wpisanie im wersji szablonu
z ``tenancy.0005`` byłoby powiedzeniem, że powstały z wiersza w bazie – a powstały ze stałej,
i to ona ma je odtwarzać także wtedy, gdy ktoś kiedyś poprawi tekst szablonu w panelu. Tak mówi
§ 1.1.3 wprost: „Puste pole (dokumenty sprzed etapu 2) znaczy »układ wbudowany«, czyli dzisiejszy”.

Numery, kody i pieczęcie dokumentów już wystawionych zostają nietknięte.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0005_certificatetemplate_competition"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificate",
            name="template_version",
            field=models.CharField(blank=True, max_length=100, verbose_name="wersja szablonu tekstu"),
        ),
    ]
