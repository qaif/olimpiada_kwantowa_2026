"""``User.blocked_at`` – blokada konta odróżnialna od „czeka na aktywację” (audyt 10.10.2026, S5).

Do tej zmiany blokada przez organizatora była wyłącznie ``is_active=False``. Dla konta
z potwierdzonym adresem to wystarczało, ale konto **bez** ``email_verified_at`` (zablokowane przed
``accounts.0010``, które świadomie zostawiła je puste, albo założone w ``/admin/``) wyglądało
dokładnie jak świeża rejestracja: formularz „wyślij link ponownie” wysyłał mu link aktywacyjny,
a kliknięcie ustawiało ``is_active=True`` – zablokowany uczestnik odblokowywał się sam.

Migracja danych oznacza jako zablokowane konta ``is_active=False``, ``email_verified_at IS NULL``
i ``last_login IS NOT NULL``. Warunek ``last_login`` jest tu rozstrzygający: konto czekające na
aktywację nie mogło się nigdy zalogować (logowanie wymaga aktywnego konta), więc nieaktywne konto,
które ma za sobą logowanie, było kiedyś czynne i ktoś je wyłączył. Konta bez logowania zostają
nietknięte – to rejestracje i zaproszenia z importu, a oznaczenie ich jako zablokowanych odcięłoby
ich właścicieli od linku, na który czekają.

Znacznikiem jest chwila migracji: daty blokady nie znamy, a ``last_login`` jest tylko jej dolną
granicą. Wstecz – nic do odtworzenia, kolumna znika razem z ``AddField``.
"""

from django.db import migrations, models
from django.utils import timezone


def mark_blocked_accounts(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_active=False, email_verified_at__isnull=True, last_login__isnull=False).update(
        blocked_at=timezone.now()
    )


def noop(apps, schema_editor):
    """Wstecz: kolumna znika razem z ``AddField``."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0038_delegations_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="blocked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="zablokowane"),
        ),
        migrations.RunPython(mark_blocked_accounts, noop),
    ]
