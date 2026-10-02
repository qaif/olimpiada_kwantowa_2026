"""Pokoje wideo bez terminu rozmowy (v0.39.0, ``apps.competitions.video_rooms``).

Nowa tabela, bez danych do przeniesienia i bez dotykania istniejących – ``CREATE TABLE`` z trzema
indeksami unikalnymi (nazwa pokoju, dwa klucze linków-zaproszeń), czyli chwila także na produkcji.
Kolumny ``host_key``/``guest_key`` są poświadczeniem (docstring modelu): nie trafiają do audytu,
logów ani eksportów. Tokenów JWT tabela nie trzyma – te powstają przy każdym wejściu.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import apps.competitions.video_rooms


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0032_free_scores"),
        ("tenancy", "0010_path_prefix_routing_on_platform"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoRoom",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("label", models.CharField(max_length=80, verbose_name="etykieta")),
                ("room_name", models.CharField(max_length=128, unique=True, verbose_name="nazwa pokoju")),
                (
                    "host_key",
                    models.CharField(
                        default=apps.competitions.video_rooms.new_key,
                        max_length=64,
                        unique=True,
                        verbose_name="klucz linku gospodarza",
                    ),
                ),
                (
                    "guest_key",
                    models.CharField(
                        default=apps.competitions.video_rooms.new_key,
                        max_length=64,
                        unique=True,
                        verbose_name="klucz linku gościa",
                    ),
                ),
                (
                    "created_as",
                    models.CharField(
                        choices=[("coordinator", "koordynator"), ("committee", "członek komisji")],
                        default="coordinator",
                        max_length=16,
                        verbose_name="rola zakładającego",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="założony"),
                ),
                ("expires_at", models.DateTimeField(verbose_name="ważny do")),
                (
                    "committee_access",
                    models.BooleanField(default=False, verbose_name="dostępny dla członków komisji"),
                ),
                (
                    "committee_as_moderator",
                    models.BooleanField(default=False, verbose_name="komisja jako gospodarze"),
                ),
                ("closed_at", models.DateTimeField(blank=True, null=True, verbose_name="zamknięty")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="video_rooms",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="video_rooms_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="założył",
                    ),
                ),
            ],
            options={
                "verbose_name": "pokój wideo",
                "verbose_name_plural": "pokoje wideo",
                "ordering": ("-created_at", "-id"),
            },
        ),
    ]
