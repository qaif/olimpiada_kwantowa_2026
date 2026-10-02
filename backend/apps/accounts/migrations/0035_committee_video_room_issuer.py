"""Uprawnienie członka komisji do zakładania pokoi wideo (v0.39.0, ``CommitteeMember.video_room_issuer``).

``AddField`` z domyślnym ``False``: żaden istniejący członek komisji nie dostaje uprawnienia sam –
nadaje je koordynator na ekranie „Pokoje wideo”. W PostgreSQL ≥ 11 kolumna z niezmienną wartością
domyślną nie przepisuje tabeli.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0034_clear_anonymised_profile_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="committeemember",
            name="video_room_issuer",
            field=models.BooleanField(default=False, verbose_name="może tworzyć pokoje wideo"),
        ),
    ]
