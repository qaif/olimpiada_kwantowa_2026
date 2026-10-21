# Napisana ręcznie (UX-PANEL-03): rodzaje powiadomień panelu komisji. Wyłącznie ``choices`` – bez zmian w bazie.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("chat_organizer", "Wiadomość od organizatora"),
                    ("chat_peer", "Wiadomość od uczestnika"),
                    ("announcement", "Ogłoszenie organizatora"),
                    ("student_status_accepted", "Zaświadczenie zaakceptowane"),
                    ("student_status_rejected", "Zaświadczenie odrzucone"),
                    ("forum_reply", "Odpowiedź na forum"),
                    ("forum_decision", "Decyzja moderatora forum"),
                    ("results_published", "Ogłoszone wyniki"),
                    ("appeal_decided", "Decyzja w sprawie reklamacji"),
                    ("review_assigned", "Nowe prace do oceny"),
                    ("review_tiebreak", "Praca w rundzie rozjemczej"),
                    ("review_deadline", "Termin oceniania"),
                    ("appeal_pending", "Reklamacja do rozpatrzenia"),
                ],
                max_length=32,
                verbose_name="rodzaj",
            ),
        ),
    ]
