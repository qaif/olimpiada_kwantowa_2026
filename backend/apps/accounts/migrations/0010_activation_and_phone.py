"""Aktywacja konta e-mailem (``User.email_verified_at``) i telefon uczestnika (``Participant.phone``).

Migracja danych na końcu jest tu **warunkiem wdrożenia**, a nie kosmetyką: od tej zmiany logowanie
jest zamknięte dla kont z ``email_verified_at IS NULL``, a listę „konta oczekujące na aktywację”
w panelu koordynatora buduje dokładnie ten warunek. Gdyby pole zostało puste dla kont, które już
działają, cała dotychczasowa baza uczestników i recenzentów zostałaby w jednej chwili odcięta od
panelu i wylądowała na liście do aktywacji – z niczyjej winy i bez linku w skrzynce, bo listy
aktywacyjne nigdy nie zostały do nich wysłane.

Znacznikiem zostaje ``date_joined``, a nie „teraz”: czas potwierdzenia adresu jest datą zdarzenia,
a jedyne, co o starych kontach wiemy, to moment ich powstania. Wpisanie tam chwili wdrożenia
sugerowałoby, że w tej sekundzie ktoś ten adres potwierdził.

Warunkiem jest ``is_active=True``: konto zablokowane przez organizatora nie ma tu nic dostać –
jego ``is_active=False`` znaczy „zablokowane”, a nie „niepotwierdzone”, i tak zostaje. Po tej
migracji jedyne konta bez ``email_verified_at`` to konta zablokowane (celowo) i te zakładane już
po wdrożeniu (czekające na link).

``phone`` jest dokładane jako ``blank=True`` – istniejące profile numeru nie mają i wypełnienie go
wartością zastępczą byłoby wpisaniem uczestnikowi danych, których nie podał. Numer jest wymagany
wyłącznie od nowych rejestracji i przy zapisie formularza edycji profilu.
"""

from django.db import migrations, models


def mark_existing_accounts_verified(apps, schema_editor):
    """Konta działające przed wdrożeniem dostają potwierdzenie z datą rejestracji."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_active=True, email_verified_at__isnull=True).update(
        email_verified_at=models.F("date_joined")
    )


def noop(apps, schema_editor):
    """Wstecz: pole i tak znika razem z ``AddField``, nie ma czego odtwarzać."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_participant_consents"),
    ]

    operations = [
        migrations.AddField(
            model_name="participant",
            name="phone",
            field=models.CharField(blank=True, max_length=32, verbose_name="telefon"),
        ),
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="adres e-mail potwierdzony"),
        ),
        migrations.RunPython(mark_existing_accounts_verified, noop),
    ]
