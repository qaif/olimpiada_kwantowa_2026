# REG-UX-01 § 3.1: automatyczny zapis nowych kont do etapu treningowego (pole na etapie + więz).
# Pisana ręcznie (bez bazy przy tworzeniu); dodanie pola z wartością domyślną i więzi na etapach –
# tabela ma kilka wierszy na edycję, więc operacja jest natychmiastowa.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0034_video_provider_livekit"),
    ]

    operations = [
        migrations.AddField(
            model_name="stage",
            name="auto_register_new_participants",
            field=models.BooleanField(default=False, verbose_name="automatyczny zapis nowych kont"),
        ),
        migrations.AddConstraint(
            model_name="stage",
            constraint=models.CheckConstraint(
                condition=models.Q(("auto_register_new_participants", False))
                | models.Q(("kind", "TRAINING")),
                name="competitions_stage_auto_register_training",
            ),
        ),
    ]
