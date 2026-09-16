from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grading", "0005_review_revised_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="cancel_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("COORDINATOR", "koordynator odebrał pracę"),
                    ("SUPERSEDED", "nowa wersja rozwiązania"),
                    ("OVERRIDE", "korekta oceny przez koordynatora"),
                    ("MODERATION_RESOLVED", "rozjazd rozstrzygnięty"),
                    ("REVISED", "recenzent poprawił ocenę"),
                ],
                default="",
                max_length=24,
                verbose_name="powód anulowania",
            ),
        ),
    ]
