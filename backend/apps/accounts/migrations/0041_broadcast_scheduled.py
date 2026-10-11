"""Komunikaty z datą przyszłą (MSG-SCHED-01): termin, parametry wysyłki i nowe stany.

Dwie nowe kolumny z wartościami domyślnymi (``NULL`` i pusty słownik) – istniejące wiersze są
wysyłkami natychmiastowymi i zostają takie, jakie były. Zmiana stanów to wyłącznie lista wyboru.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0040_broadcast_group_no_student_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="messagebroadcast",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="termin wysyłki"),
        ),
        migrations.AddField(
            model_name="messagebroadcast",
            name="parameters",
            field=models.JSONField(blank=True, default=dict, verbose_name="parametry wysyłki"),
        ),
        migrations.AlterField(
            model_name="messagebroadcast",
            name="status",
            field=models.CharField(
                choices=[
                    ("QUEUED", "w kolejce"),
                    ("SENT", "przekazana do wysyłki"),
                    ("FAILED", "nieudana"),
                    ("SCHEDULED", "zaplanowana"),
                    ("CANCELLED", "anulowana"),
                    ("EXPIRED", "przeterminowana – nie wysłano"),
                    ("EMPTY", "bez odbiorców – nic nie wysłano"),
                ],
                default="QUEUED",
                max_length=16,
                verbose_name="stan",
            ),
        ),
    ]
