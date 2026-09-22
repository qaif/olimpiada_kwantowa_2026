"""``ConsentRecord`` dostaje drugiego możliwego właściciela: opiekuna szkolnego.

Rejestracja opiekuna (``/register/supervisor/``) zbiera od 22.09.2026 te same dwie zgody, co
uczestnik – regulamin i RODO (``apps.accounts.supervisors.register_supervisor``) – i potrzebuje
tego samego dowodu, co uczestnik: wiersza ``ConsentRecord`` z wersją dokumentu, czasem i drogą.

Nowa kolumna (``supervisor``), a nie osobny model ``SupervisorConsentRecord``: to jest ten sam
kształt dowodu (rodzaj, wersja, czas, droga), a druga tabela oznaczałaby drugą definicję „co to
znaczy zgoda” do utrzymania w zgodzie z pierwszą. ``participant`` staje się **opcjonalny** z tego
samego powodu – wiersz niesie teraz dokładnie jedną z dwóch relacji, nigdy obie i nigdy żadnej
(``accounts_consentrecord_exactly_one_owner``). Istniejące wiersze mają ``participant`` wypełniony
i ograniczenie ich nie rusza.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0031_participant_birth_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentrecord",
            name="supervisor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="consents",
                to="accounts.schoolsupervisor",
                verbose_name="opiekun szkolny",
            ),
        ),
        migrations.AlterField(
            model_name="consentrecord",
            name="participant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="consents",
                to="accounts.participant",
                verbose_name="uczestnik",
            ),
        ),
        migrations.AddIndex(
            model_name="consentrecord",
            index=models.Index(fields=["supervisor", "kind"], name="accounts_consent_sup_idx"),
        ),
        migrations.AddConstraint(
            model_name="consentrecord",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("participant__isnull", False), ("supervisor__isnull", True))
                    | models.Q(("participant__isnull", True), ("supervisor__isnull", False))
                ),
                name="accounts_consentrecord_exactly_one_owner",
            ),
        ),
    ]
