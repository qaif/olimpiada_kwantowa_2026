"""Pełna data urodzenia uczestnika.

Kolumna dochodzi **nullowalna i bez backfillu**, i to jest cała migracja. Wierszy sprzed tej
zmiany nie da się uzupełnić: znają sam rocznik, a dzień urodzin zgadnięty z rocznika byłby daną
wymyśloną, nie uzupełnioną – a od niej zależałaby potem reguła „zgoda opiekuna dla małoletniego”.
Puste pole znaczy więc „nie wiemy dokładnie” i reguła wieku spada wtedy na starą, rocznikową
(``apps.accounts.consents.is_minor``).

``birth_year`` zostaje kolumną ``NOT NULL`` i staje się kolumną **wyliczaną**: gdy data jest
wypełniona, rocznik ma być jej rokiem. Więzu bazodanowego tu nie ma, bo „rok wyciągnięty z daty”
nie zapisuje się przenośnie w ``CheckConstraint``. Niezmiennika pilnuje ``Participant.save``
i test ``apps/accounts/tests/test_birth_date.py``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0030_terms_version_20_september")]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="birth_date",
            field=models.DateField(blank=True, null=True, verbose_name="data urodzenia"),
        ),
        migrations.AlterField(
            model_name="registrationprofile",
            name="require_birth_year",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Wyłączona znaczy, że data urodzenia jest nieobowiązkowa. Uczestnik bez "
                    "podanej daty jest traktowany jak osoba niepełnoletnia, więc zgoda opiekuna "
                    "będzie wymagana od każdego."
                ),
                verbose_name="data urodzenia wymagana",
            ),
        ),
    ]
