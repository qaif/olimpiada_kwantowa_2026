"""Alias witryny konkursu: druga domena tej samej olimpiady w drugim języku treści (§ 1.6.2).

Migracja jest **wyłącznie schematem** i **nie wpisuje ani jednego wiersza**: Konkurs #1 prowadzi
jedno drzewo stron w jednym ``Locale`` i ma takie zostać. Pusta tabela znaczy tu dokładnie to samo,
co znaczyła nieobecność tabeli – gałąź aliasów w ``apps.tenancy.resolution`` nie wykonuje się dla
konkursu bez aliasu ani razu, a przy ``WAGTAIL_I18N_ENABLED=False`` nie powstaje w ogóle.

Odwracalność wychodzi z definicji: ``CreateModel`` cofa się ``DeleteModel``, a kasowana tabela jest
pusta wszędzie tam, gdzie nikt aliasu nie założył.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0003_prefixes"),
        # ``Locale`` jest modelem rdzenia Wagtaila; ta sama migracja stoi w zależnościach
        # ``tenancy.0001`` i ``cms.0003``, więc kolejność jest już rozstrzygnięta.
        ("wagtailcore", "0094_alter_page_locale"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompetitionSiteAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="utworzony")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="site_aliases",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
                (
                    "locale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="wagtailcore.locale",
                        verbose_name="język treści",
                    ),
                ),
                (
                    "site",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="competition_alias",
                        to="wagtailcore.site",
                        verbose_name="witryna",
                    ),
                ),
            ],
            options={
                "verbose_name": "alias witryny konkursu",
                "verbose_name_plural": "aliasy witryn konkursów",
                "ordering": ("competition", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="competitionsitealias",
            constraint=models.UniqueConstraint(
                fields=("competition", "locale"),
                name="tenancy_one_site_per_competition_language",
            ),
        ),
    ]
