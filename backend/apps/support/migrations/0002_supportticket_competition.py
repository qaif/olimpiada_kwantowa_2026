"""Schemat wydania D dla korespondencji: zgłoszenie dostaje organizatora, do którego idzie.

``support.SupportTicket`` jest w tej aplikacji jedynym modelem, który potrzebuje własnej kolumny:
``SupportMessage`` dochodzi do konkursu przez swoje zgłoszenie (§ 3.4), a innych tabel tu nie ma.

Kolumna jest **nullowalna na stałe**, a nie przejściowo – i to jest jedyna różnica wobec
``cms.0021`` czy ``competitions.0019``. Puste znaczy „sprawa do operatora platformy”
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.2): osoba, która nie może się zalogować pod adresem
nieprzypisanym do żadnego konkursu, ma mieć dokąd napisać. Domknięcia na ``NOT NULL`` dla tej
tabeli więc nie będzie.

``SET_NULL``, a nie ``PROTECT``: ślad korespondencji nie może zniknąć razem z konkursem ani
zablokować jego usunięcia (§ 3.2, zdanie o ``AuditLog`` i ``SupportTicket``).

Backfill jest osobno, w ``0003`` – tak samo jak w ``cms.0021``/``0022``, żeby dało się cofnąć dane
bez cofania schematu.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0001_support_tickets"),
        # ``0002``, a nie ``0001``: backfill w ``0003`` liczy na to, że Konkurs #1 jest już w bazie.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                help_text="Puste = zgłoszenie do operatora platformy.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="support_tickets",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
