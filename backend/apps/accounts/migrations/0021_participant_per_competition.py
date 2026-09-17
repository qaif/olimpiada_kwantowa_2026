"""Profil uczestnika przestaje być jeden na osobę: ``OneToOne`` → ``ForeignKey`` per konkurs.

Trzy zmiany, jedna decyzja (``docs/UNIWERSALNY-ETAP-1.md`` § 3.3):

1. ``Participant.user`` jest ``ForeignKey`` z ``related_name="participations"``. Nazwy
   ``participant`` w relacji odwrotnej **nie ma celowo**: ``user.participant`` ma od tej migracji
   podnosić ``AttributeError``, a nie po cichu oddawać profil z przypadkowego konkursu (§ 6, T2
   „Mitygacja”). Jedno przeoczone ``hasattr(user, "participant")`` znaczyłoby 403 na własnym
   panelu uczestnika – z zerwaną nazwą widać je przy pierwszym wywołaniu, a nie u uczestnika.
2. Para (użytkownik, konkurs) jest unikalna. Tyle zostaje z dawnego ``OneToOne``: jedno konto ma
   w jednym konkursie najwyżej jeden profil, a powtórzona rejestracja czy powtórzony import listy
   klasowej kończą się błędem, a nie drugim kompletem zgód tej samej osoby.
3. ``public_code`` traci unikalność **globalną** na rzecz unikalności w konkursie. Prefiks kodu
   jest od ``tenancy.0003_prefixes`` własnością konkursu, a tabela wyników, w której ten kod stoi,
   należy do jednego konkursu – globalny więz kazałby drugiemu organizatorowi omijać kody
   pierwszego, choć nigdy nie stoją obok siebie.

Czego ta migracja **nie** robi: nie przepisuje ani jednego ``public_code`` (uczestnik stoi pod
swoim kodem w ogłoszonych wynikach i w korespondencji) i nie zakłada ani nie kasuje żadnego
profilu. Dla Konkursu #1 jest zmianą wyłącznie schematu: dotąd każdy użytkownik miał najwyżej
jeden profil i miał go w jedynym konkursie, więc oba nowe więzy są już spełnione.

Kolejność operacji jest wymuszona przez bazę: więz na (konkurs, kod) wchodzi **po** zdjęciu
``unique`` z samej kolumny, a więz na (użytkownik, konkurs) **po** zamianie ``OneToOne`` na
``ForeignKey`` – inaczej przez chwilę stałyby obok siebie dwa więzy opisujące tę samą kolumnę.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.accounts.models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0020_backfill_competition_and_memberships"),
        # Prefiks kodu publicznego mieszka od teraz w konkursie; bez tej migracji ``generate_public_code``
        # nie miałoby skąd go wziąć na bazie, na której obie wchodzą za jednym ``migrate``.
        ("tenancy", "0003_prefixes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="participant",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="participations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="participant",
            name="public_code",
            field=models.CharField(
                default=apps.accounts.models.generate_public_code,
                max_length=16,
                verbose_name="kod publiczny",
            ),
        ),
        migrations.AddConstraint(
            model_name="participant",
            constraint=models.UniqueConstraint(
                fields=("user", "competition"), name="accounts_participant_unique_per_competition"
            ),
        ),
        migrations.AddConstraint(
            model_name="participant",
            constraint=models.UniqueConstraint(
                fields=("competition", "public_code"),
                name="accounts_participant_public_code_per_competition",
            ),
        ),
    ]
