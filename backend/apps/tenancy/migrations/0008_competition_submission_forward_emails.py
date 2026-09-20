"""Adresy, na które konkurs przekazuje przyjęte rozwiązania (prośba organizatora z 20.09.2026).

Migracja jest **wyłącznie schematem**: kolumna wchodzi pusta, a pusta znaczy „przekazywanie
wyłączone”. Wpisanie tu czyjegokolwiek adresu byłoby włączeniem wysyłki prac uczestników poza
serwis przez ``git pull``, a nie przez decyzję organizatora – a to jest decyzja o przetwarzaniu
danych osobowych (rejestr czynności, ``apps.accounts.processing_register``), nie o konfiguracji.

Konkurs #1 zachowuje się więc po wdrożeniu dokładnie tak, jak przed nim: ani jeden list więcej.
"""

import apps.tenancy.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0007_signature_line_steering_committee")]

    operations = [
        migrations.AddField(
            model_name="competition",
            name="submission_forward_emails",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Adresy rozdzielone przecinkiem albo nową linią (najwyżej pięć). Puste pole "
                    "wyłącza przekazywanie."
                ),
                validators=[apps.tenancy.models.validate_submission_forward_emails],
                verbose_name="przekazywanie rozwiązań",
            ),
        ),
    ]
