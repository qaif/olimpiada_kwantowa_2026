"""Zaproszenia wysyłane e-mailem: adres odbiorcy, chwila wysyłki i unieważnienie kodu.

Wszystkie trzy pola są ``null``, bo dotychczasowe kody powstawały „do ręki” (``manage.py
create_invitation``, sekcja „Kod zaproszenia” w panelu) i o odbiorcy nie wiadomo nic – kod
przekazywał człowiek. Wypełnienie ich czymkolwiek zastępczym byłoby wpisaniem do bazy adresu,
którego nikt nigdy nie podał, a lista wysłanych zaproszeń w panelu pokazałaby kody, które nigdy
nie pojechały listem.

Indeks na ``email`` jest po to, żeby „czy ta osoba już dostała zaproszenie” było jednym
zapytaniem: koordynator wkleja listę adresów po raz drugi (dopisał kilka osób) i musi zobaczyć,
komu kod już poszedł, zanim wyśle drugi.

Kodu jawnego ta migracja nie dotyka i dotykać nie może – w bazie był i zostaje wyłącznie sha256.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_activation_and_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="invitationcode",
            name="email",
            field=models.EmailField(
                blank=True,
                db_index=True,
                max_length=254,
                null=True,
                verbose_name="adres, na który wysłano",
            ),
        ),
        migrations.AddField(
            model_name="invitationcode",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="wysłano"),
        ),
        migrations.AddField(
            model_name="invitationcode",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="unieważniono"),
        ),
    ]
