"""Schemat wydania D dla śladu audytowego: wpis dostaje konkurs, którego dotyczy.

Dotąd zakres audytu wychodził **z obiektu**, o którym wpis mówi
(``apps.web.scoping.audit_scope``): po jednym podzapytaniu na rodzaj obiektu obecny w tabeli,
w jednym zdaniu SQL, i to wyłącznie jako **wykluczenie** – dało się nim stwierdzić, że wpis należy
do sąsiada, ale nie dało się stwierdzić, że należy do nas. Wpis o obiekcie skasowanym albo
platformowym zostawał więc widoczny dla każdego konkursu.

Kolumna zamyka to od strony zapisu: konkurs jest ustalany w chwili zdarzenia
(``apps.core.models.audit_competition``), więc skasowanie obiektu niczego już nie gubi, a odczyt
kosztuje jeden indeks zamiast kilkunastu podzapytań (``docs/UNIWERSALNY-ETAP-1.md`` § 3.9).

``null=True`` **na stałe**: wpisy o obiektach platformowych (``accounts.user``,
``wagtailcore.site``, alert infrastruktury) nie mają konkursu i to jest odpowiedź poprawna, a nie
stan do domknięcia. Dlatego ta tabela nie dostanie migracji ``NOT NULL`` z § 4.4.

``SET_NULL``: ślad audytowy nie może zniknąć razem z konkursem ani zablokować jego usunięcia
(§ 3.2). Backfill zastanych wierszy jest osobno, w ``0003``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        # ``0002``, a nie ``0001``: backfill w ``0003`` liczy na to, że Konkurs #1 jest już w bazie.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Puste = zdarzenie dotyczy obiektu platformy, a nie konkursu.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_entries",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
