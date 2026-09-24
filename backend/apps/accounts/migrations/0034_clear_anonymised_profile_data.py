"""Profile wytarte **przed** v0.34.0 tracą resztę danych osobowych (decyzja organizatora z 24.09.2026).

Od v0.34.0 ``apps.accounts.profile.anonymise_account`` czyści także ``guardian_email``,
``supervisor_email``, ``institution_name`` i ``custom_institution_ref``. Konta zanonimizowane
wcześniej mają te pola nadal wypełnione – a to jest dokładnie ten stan, który zmiana naprawia:
adres rodzica (osoby trzeciej bez konta) przy koncie, którego właściciela już nie ma, i adres
opiekuna szkolnego, po którym panel „Moi uczniowie” dopasowuje uczniów, więc usunięty uczeń
dalej stał na liście nauczyciela. Migracja wyrównuje stare wiersze do nowej reguły, żeby
odpowiedź „co zostaje po usunięciu konta” była jedna, a nie zależna od daty usunięcia.

Rozpoznanie konta po anonimizacji jest to samo, co w ``apps.accounts.anonymised`` (domena adresu
``@invalid.``) – przepisane tutaj jako stała, bo migracja nie może importować kodu aplikacji,
który zmieni się później. Jedno ``UPDATE`` bez pętli; wstecz nic (dane wytarte nie wracają).
"""

from django.db import migrations

ANONYMISED_EMAIL_MARKER = "@invalid."


def clear_anonymised_profiles(apps, schema_editor):
    Participant = apps.get_model("accounts", "Participant")
    Participant.objects.filter(user__email__icontains=ANONYMISED_EMAIL_MARKER).update(
        guardian_email="",
        supervisor_email="",
        institution_name="",
        custom_institution_ref=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0033_broadcast_target"),
    ]

    operations = [
        migrations.RunPython(clear_anonymised_profiles, migrations.RunPython.noop, elidable=True),
    ]
