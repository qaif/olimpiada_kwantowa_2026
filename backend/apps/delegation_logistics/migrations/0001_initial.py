"""Logistyka finału dla delegacji (LOG-01): tabele puste i puste zostają poza trybem delegacji.

Konkurs bez flagi ``onsite_logistics`` albo bez trybu ``DELEGATIONS`` nie zapisuje tu ani jednego
wiersza – migracja niczego nie przenosi i niczego nie wypełnia.
"""

import apps.delegation_logistics.crypto
import apps.delegation_logistics.models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0037_team_leader_rbac_group'),
        ('competitions', '0033_video_room'),
        ('tenancy', '0013_competition_registration_mode'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Checkpoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='nazwa')),
                ('position', models.PositiveSmallIntegerField(default=0, verbose_name='kolejność')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.competition', verbose_name='konkurs')),
                ('edition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='final_checkpoints', to='competitions.edition', verbose_name='edycja')),
            ],
            options={
                'verbose_name': 'punkt kontroli obecności',
                'verbose_name_plural': 'punkty kontroli obecności',
                'ordering': ('position', 'id'),
            },
        ),
        migrations.CreateModel(
            name='DelegationGuest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_name', models.CharField(max_length=150, verbose_name='imię')),
                ('last_name', models.CharField(max_length=150, verbose_name='nazwisko')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='adres e-mail')),
                ('role', models.CharField(choices=[('OBSERVER', 'Obserwator'), ('GUEST', 'Gość')], default='OBSERVER', max_length=16, verbose_name='rola')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='dodany')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('delegation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guests', to='accounts.delegation', verbose_name='delegacja')),
            ],
            options={
                'verbose_name': 'gość delegacji',
                'verbose_name_plural': 'goście delegacji',
                'ordering': ('last_name', 'first_name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='DelegationMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('STUDENT', 'Uczeń'), ('LEADER', 'Opiekun drużyny'), ('GUEST', 'Gość')], max_length=16, verbose_name='rola w delegacji')),
                ('badge_token', models.CharField(default=apps.delegation_logistics.models.new_badge_token, max_length=32, unique=True, verbose_name='token identyfikatora')),
                ('passport_name', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='imię i nazwisko jak w paszporcie')),
                ('nationality', models.CharField(blank=True, max_length=2, verbose_name='obywatelstwo (ISO)')),
                ('date_of_birth', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='data urodzenia')),
                ('passport_number', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='numer paszportu')),
                ('passport_expiry', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='paszport ważny do')),
                ('arrival_date', models.DateField(blank=True, null=True, verbose_name='przyjazd – dzień')),
                ('arrival_time', models.TimeField(blank=True, null=True, verbose_name='przyjazd – godzina')),
                ('arrival_mode', models.CharField(blank=True, choices=[('FLIGHT', 'Samolot'), ('TRAIN', 'Pociąg'), ('BUS', 'Autobus'), ('CAR', 'Samochód'), ('OTHER', 'Inny')], max_length=8, verbose_name='przyjazd – środek')),
                ('arrival_number', models.CharField(blank=True, max_length=40, verbose_name='przyjazd – nr lotu / pociągu')),
                ('arrival_place', models.CharField(blank=True, max_length=120, verbose_name='przyjazd – lotnisko / dworzec')),
                ('departure_date', models.DateField(blank=True, null=True, verbose_name='wyjazd – dzień')),
                ('departure_time', models.TimeField(blank=True, null=True, verbose_name='wyjazd – godzina')),
                ('departure_mode', models.CharField(blank=True, choices=[('FLIGHT', 'Samolot'), ('TRAIN', 'Pociąg'), ('BUS', 'Autobus'), ('CAR', 'Samochód'), ('OTHER', 'Inny')], max_length=8, verbose_name='wyjazd – środek')),
                ('departure_number', models.CharField(blank=True, max_length=40, verbose_name='wyjazd – nr lotu / pociągu')),
                ('departure_place', models.CharField(blank=True, max_length=120, verbose_name='wyjazd – lotnisko / dworzec')),
                ('needs_accommodation', models.BooleanField(default=True, verbose_name='potrzebuje noclegu')),
                ('gender', models.CharField(blank=True, choices=[('F', 'Kobieta'), ('M', 'Mężczyzna'), ('X', 'Inna / wolę nie podawać')], max_length=1, verbose_name='płeć (przydział pokoi)')),
                ('roommate_preference', models.CharField(blank=True, max_length=200, verbose_name='preferowany współlokator')),
                ('accommodation_notes', models.CharField(blank=True, max_length=300, verbose_name='uwagi do zakwaterowania')),
                ('diet', models.CharField(blank=True, choices=[('NONE', 'Bez ograniczeń'), ('VEGETARIAN', 'Wegetariańska'), ('VEGAN', 'Wegańska'), ('HALAL', 'Halal'), ('KOSHER', 'Koszerna'), ('GLUTEN_FREE', 'Bezglutenowa'), ('OTHER', 'Inna (opis w uwagach)')], max_length=16, verbose_name='dieta')),
                ('diet_notes', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='uwagi do diety')),
                ('allergies', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='alergie')),
                ('medical_notes', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='uwagi medyczne')),
                ('health_consent_at', models.DateTimeField(blank=True, null=True, verbose_name='zgoda na dane o zdrowiu')),
                ('health_consent_version', models.CharField(blank=True, max_length=20, verbose_name='wersja zgody')),
                ('tshirt_size', models.CharField(blank=True, choices=[('XS', 'XS'), ('S', 'S'), ('M', 'M'), ('L', 'L'), ('XL', 'XL'), ('XXL', 'XXL'), ('XXXL', '3XL')], max_length=5, verbose_name='rozmiar koszulki')),
                ('emergency_name', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='kontakt alarmowy – imię i nazwisko')),
                ('emergency_phone', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='kontakt alarmowy – telefon')),
                ('photo_key', models.CharField(blank=True, max_length=300, verbose_name='zdjęcie – klucz')),
                ('photo_mime', models.CharField(blank=True, max_length=40, verbose_name='zdjęcie – typ')),
                ('photo_size', models.PositiveIntegerField(default=0, verbose_name='zdjęcie – rozmiar')),
                ('photo_scan', models.CharField(blank=True, choices=[('PENDING', 'oczekuje na skan'), ('CLEAN', 'czysty'), ('INFECTED', 'zainfekowany'), ('ERROR', 'błąd skanu')], max_length=16, verbose_name='zdjęcie – skan')),
                ('photo_uploaded_at', models.DateTimeField(blank=True, null=True, verbose_name='zdjęcie – przesłane')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='utworzony')),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmieniony')),
                ('delegation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logistics_members', to='accounts.delegation', verbose_name='delegacja')),
                ('guest', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='member', to='delegation_logistics.delegationguest')),
                ('health_consent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('participant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.participant')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'członek delegacji (logistyka)',
                'verbose_name_plural': 'członkowie delegacji (logistyka)',
                'ordering': ('delegation', 'kind', 'id'),
            },
        ),
        migrations.CreateModel(
            name='CheckIn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='odhaczono')),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('checkpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='check_ins', to='delegation_logistics.checkpoint')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='check_ins', to='delegation_logistics.delegationmember')),
            ],
            options={
                'verbose_name': 'odhaczenie',
                'verbose_name_plural': 'odhaczenia',
                'ordering': ('-at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='FinalEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=200, verbose_name='nazwa wydarzenia')),
                ('city', models.CharField(blank=True, max_length=120, verbose_name='miasto')),
                ('venue', models.CharField(blank=True, max_length=255, verbose_name='miejsce (adres)')),
                ('starts_on', models.DateField(blank=True, null=True, verbose_name='pierwszy dzień')),
                ('ends_on', models.DateField(blank=True, null=True, verbose_name='ostatni dzień')),
                ('retention_days', models.PositiveSmallIntegerField(default=30, verbose_name='usunięcie danych po zakończeniu (dni)')),
                ('letter_prefix', models.CharField(blank=True, max_length=20, verbose_name='prefiks numeru listów')),
                ('deadline_identity', models.DateTimeField(blank=True, null=True, verbose_name='termin: dokument podróży')),
                ('deadline_travel', models.DateTimeField(blank=True, null=True, verbose_name='termin: przyjazd i wyjazd')),
                ('deadline_accommodation', models.DateTimeField(blank=True, null=True, verbose_name='termin: zakwaterowanie')),
                ('deadline_health', models.DateTimeField(blank=True, null=True, verbose_name='termin: wyżywienie i zdrowie')),
                ('deadline_personal', models.DateTimeField(blank=True, null=True, verbose_name='termin: identyfikator i kontakt')),
                ('purged_at', models.DateTimeField(blank=True, null=True, verbose_name='dane usunięte')),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='zmienione')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.competition', verbose_name='konkurs')),
                ('edition', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='final_event', to='competitions.edition', verbose_name='edycja')),
            ],
            options={
                'verbose_name': 'finał (logistyka)',
                'verbose_name_plural': 'finały (logistyka)',
            },
        ),
        migrations.CreateModel(
            name='InvitationLetter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(max_length=40, verbose_name='numer')),
                ('year', models.PositiveSmallIntegerField(verbose_name='rok')),
                ('sequence', models.PositiveIntegerField(verbose_name='numer kolejny')),
                ('scope', models.CharField(choices=[('PERSON', 'imienny'), ('DELEGATION', 'dla delegacji')], max_length=16, verbose_name='rodzaj')),
                ('country_name', models.CharField(blank=True, max_length=120, verbose_name='kraj')),
                ('people_count', models.PositiveSmallIntegerField(default=0, verbose_name='liczba osób')),
                ('content', apps.delegation_logistics.crypto.EncryptedTextField(blank=True, verbose_name='migawka treści')),
                ('content_purged_at', models.DateTimeField(blank=True, null=True, verbose_name='migawka usunięta')),
                ('template_version', models.CharField(blank=True, max_length=100, verbose_name='wersja szablonu')),
                ('issued_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='wystawiono')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.competition', verbose_name='konkurs')),
                ('delegation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.delegation')),
                ('edition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='competitions.edition', verbose_name='edycja')),
                ('issued_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('member', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='letters', to='delegation_logistics.delegationmember')),
            ],
            options={
                'verbose_name': 'list zapraszający',
                'verbose_name_plural': 'listy zapraszające',
                'ordering': ('-year', '-sequence'),
            },
        ),
        migrations.CreateModel(
            name='LogisticsAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('OFFICER', 'oficer logistyki (pełny wgląd)'), ('CHECKIN', 'obsługa rejestracji (skanowanie identyfikatorów)')], max_length=16, verbose_name='przydział')),
                ('granted_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='nadano')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.competition', verbose_name='konkurs')),
                ('granted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logistics_access', to=settings.AUTH_USER_MODEL, verbose_name='konto')),
            ],
            options={
                'verbose_name': 'dostęp do logistyki finału',
                'verbose_name_plural': 'dostępy do logistyki finału',
                'ordering': ('competition', 'role', 'granted_at', 'id'),
            },
        ),
        migrations.CreateModel(
            name='LogisticsReminder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sent_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='wysłano')),
                ('missing_count', models.PositiveSmallIntegerField(default=0, verbose_name='braki')),
                ('recipients', models.PositiveSmallIntegerField(default=0, verbose_name='adresaci')),
                ('delegation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='accounts.delegation', verbose_name='delegacja')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'przypomnienie o brakach',
                'verbose_name_plural': 'przypomnienia o brakach',
                'ordering': ('-sent_at', '-id'),
            },
        ),
        migrations.CreateModel(
            name='Room',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60, verbose_name='numer / nazwa')),
                ('building', models.CharField(blank=True, max_length=120, verbose_name='budynek / hotel')),
                ('capacity', models.PositiveSmallIntegerField(verbose_name='liczba miejsc')),
                ('gender', models.CharField(choices=[('F', 'kobiety'), ('M', 'mężczyźni'), ('ANY', 'dowolna (tylko dorośli)')], max_length=3, verbose_name='płeć pokoju')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='uwagi')),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenancy.competition', verbose_name='konkurs')),
                ('edition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='final_rooms', to='competitions.edition', verbose_name='edycja')),
            ],
            options={
                'verbose_name': 'pokój',
                'verbose_name_plural': 'pokoje',
                'ordering': ('building', 'name', 'id'),
            },
        ),
        migrations.AddField(
            model_name='delegationmember',
            name='room',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='occupants', to='delegation_logistics.room', verbose_name='pokój'),
        ),
        migrations.AddConstraint(
            model_name='checkpoint',
            constraint=models.UniqueConstraint(fields=('edition', 'name'), name='delegation_logistics_checkpoint_unique'),
        ),
        migrations.AddConstraint(
            model_name='checkin',
            constraint=models.UniqueConstraint(fields=('checkpoint', 'member'), name='delegation_logistics_checkin_once'),
        ),
        migrations.AddConstraint(
            model_name='finalevent',
            constraint=models.CheckConstraint(condition=models.Q(('starts_on__isnull', True), ('ends_on__isnull', True), ('ends_on__gte', models.F('starts_on')), _connector='OR'), name='delegation_logistics_event_dates_order'),
        ),
        migrations.AddConstraint(
            model_name='invitationletter',
            constraint=models.UniqueConstraint(fields=('competition', 'number'), name='delegation_logistics_letter_number'),
        ),
        migrations.AddConstraint(
            model_name='invitationletter',
            constraint=models.UniqueConstraint(fields=('competition', 'year', 'sequence'), name='delegation_logistics_letter_sequence'),
        ),
        migrations.AddConstraint(
            model_name='logisticsaccess',
            constraint=models.UniqueConstraint(fields=('competition', 'user', 'role'), name='delegation_logistics_access_unique'),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.UniqueConstraint(fields=('edition', 'building', 'name'), name='delegation_logistics_room_unique'),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.CheckConstraint(condition=models.Q(('capacity__gte', 1)), name='delegation_logistics_room_capacity'),
        ),
        migrations.AddConstraint(
            model_name='delegationmember',
            constraint=models.UniqueConstraint(condition=models.Q(('participant__isnull', False)), fields=('delegation', 'participant'), name='delegation_logistics_member_student_once'),
        ),
        migrations.AddConstraint(
            model_name='delegationmember',
            constraint=models.UniqueConstraint(condition=models.Q(('user__isnull', False)), fields=('delegation', 'user'), name='delegation_logistics_member_leader_once'),
        ),
        migrations.AddConstraint(
            model_name='delegationmember',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('guest__isnull', True), ('kind', 'STUDENT'), ('participant__isnull', False), ('user__isnull', True)), models.Q(('guest__isnull', True), ('kind', 'LEADER'), ('participant__isnull', True), ('user__isnull', False)), models.Q(('guest__isnull', False), ('kind', 'GUEST'), ('participant__isnull', True), ('user__isnull', True)), _connector='OR'), name='delegation_logistics_member_kind_link'),
        ),
    ]
