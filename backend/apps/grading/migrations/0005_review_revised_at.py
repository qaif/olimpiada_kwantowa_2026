from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grading", "0004_coordinator_override_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="revised_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="poprawiona"),
        ),
    ]
