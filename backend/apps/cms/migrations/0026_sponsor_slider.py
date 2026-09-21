"""Pasek rotujących logotypów w menu (uwaga organizatora z 21.09.2026, „jak na Olimpiadzie
Biologicznej”): włącznik, tempo przewijania i filtr poziomów współpracy – patrz
``apps.cms.sponsor_slider`` i ``apps.web.views.coordinator_sponsor_slider``.
"""

import apps.cms.models
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cms', '0025_registration_note_closing_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='sponsor_slider_enabled',
            field=models.BooleanField(default=True, help_text='Wyłączenie chowa pasek logotypów z menu na każdej stronie serwisu.', verbose_name='slider sponsorów włączony'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='sponsor_slider_levels',
            field=models.JSONField(blank=True, default=list, help_text='Poziomy współpracy pokazywane w sliderze (patrz „Partnerzy” w /cms/). Puste = pokazuj partnerów każdego poziomu.', validators=[apps.cms.models.validate_sponsor_slider_levels], verbose_name='slider sponsorów – poziomy'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='sponsor_slider_seconds',
            field=models.PositiveSmallIntegerField(default=5, help_text='Co ile sekund pasek przesuwa się o jeden logotyp.', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(120)], verbose_name='slider sponsorów – sekundy'),
        ),
    ]
