"""VISA-01: wnioski o listy zapraszające, kod weryfikacyjny, język, migawka wydarzenia, unieważnienie.

Listy wystawione przed tą migracją dostają losowy kod weryfikacyjny (``_codes``), a migawkę wydarzenia
z ustawień finału swojej edycji; adres weryfikacji (``verification_base_url``) zostaje pusty – liczy się
wtedy z bieżącego adresowania konkursu – inaczej strona weryfikacji nie miałaby ich czym pokazać. Kod liczony
w migracji, a nie importem z aplikacji: alfabet i długość są kopią ``apps.results.models`` z dnia
migracji (migracja ma działać tak samo za rok, choćby tamten moduł się zmienił).

Cofnięcie usuwa kolumny razem z kodami, a ponowne zastosowanie nadaje kody **nowe** – kody na listach
wydrukowanych wcześniej przestają wtedy działać (OPERACJE § 31.8). Na produkcji tej migracji nie cofa się
po wystawieniu pierwszego listu.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LENGTH = 12


def _codes(apps, schema_editor):
    import secrets

    letter_model = apps.get_model("delegation_logistics", "InvitationLetter")
    event_model = apps.get_model("delegation_logistics", "FinalEvent")
    taken = set(letter_model.objects.exclude(verification_code=None).values_list("verification_code", flat=True))
    events = {event.edition_id: event for event in event_model.objects.all()}
    for letter in letter_model.objects.filter(verification_code=None):
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        while code in taken:
            code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        taken.add(code)
        letter.verification_code = code
        event = events.get(letter.edition_id)
        if event is not None:
            letter.event_name = event.name
            letter.event_city = event.city
            letter.event_starts_on = event.starts_on
            letter.event_ends_on = event.ends_on
        letter.save(
            update_fields=["verification_code", "event_name", "event_city", "event_starts_on", "event_ends_on"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0038_delegations_review'),
        ('delegation_logistics', '0002_encrypted_diet'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='invitationletter',
            name='event_city',
            field=models.CharField(blank=True, max_length=120, verbose_name='miasto (migawka)'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='event_ends_on',
            field=models.DateField(blank=True, null=True, verbose_name='ostatni dzień (migawka)'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='event_name',
            field=models.CharField(blank=True, max_length=200, verbose_name='wydarzenie (migawka)'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='event_starts_on',
            field=models.DateField(blank=True, null=True, verbose_name='pierwszy dzień (migawka)'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='language',
            field=models.CharField(default='en', max_length=10, verbose_name='język listu'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='revoke_reason',
            field=models.CharField(blank=True, max_length=300, verbose_name='powód unieważnienia'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='unieważniono'),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='revoked_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='verification_code',
            field=models.CharField(blank=True, editable=False, max_length=12, null=True, unique=True, verbose_name='kod weryfikacyjny'),
        ),
        migrations.CreateModel(
            name='LetterRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(default='en', max_length=10, verbose_name='język listu')),
                ('status', models.CharField(choices=[('PENDING', 'oczekuje na decyzję'), ('APPROVED', 'zatwierdzony – list wystawiony'), ('REJECTED', 'odrzucony'), ('WITHDRAWN', 'wycofany')], default='PENDING', max_length=16, verbose_name='stan')),
                ('requested_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='złożony')),
                ('decided_at', models.DateTimeField(blank=True, null=True, verbose_name='rozstrzygnięty')),
                ('reject_reason', models.CharField(blank=True, max_length=500, verbose_name='powód odrzucenia')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('delegation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.delegation', verbose_name='delegacja')),
                ('letter', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requests', to='delegation_logistics.invitationletter')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='letter_requests', to='delegation_logistics.delegationmember', verbose_name='osoba')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'wniosek o list zapraszający',
                'verbose_name_plural': 'wnioski o listy zapraszające',
                'ordering': ('-requested_at', '-id'),
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'PENDING')), fields=('member',), name='delegation_logistics_letter_request_one_pending')],
            },
        ),
        migrations.AddField(
            model_name='invitationletter',
            name='verification_base_url',
            field=models.CharField(blank=True, max_length=300, verbose_name='adres weryfikacji (z chwili wystawienia)'),
        ),
        migrations.RunPython(_codes, migrations.RunPython.noop),
    ]
